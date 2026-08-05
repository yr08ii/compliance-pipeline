"""Family C — cross-merchant and ring detection.

Families A and B look inside one merchant. Nothing inside a merchant can see
coordination *between* merchants, so this layer runs portfolio-wide and
attributes its findings back to each participant.

Two sub-layers with very different cost, in the order the design spec sets:

* **Merchant-identity rings (§5.1)** — equality joins on the business
  registration, address and name hashes we already store. We never reverse
  anything, so the hashes being reversible is irrelevant, and linking
  merchants to each other is exactly our job. Cheap, in scope, built first.
* **Card-linkage (§5.2)** — follows one `hashed_pan` across merchants. Real
  detection value, but a genuine privacy cost, so it is gated and its evidence
  is written carefully.

**The PAN hash never leaves this module.** It is a 1:1 unsalted hash and
therefore brute-forceable back to a card number, so it is cardholder data.
Every piece of evidence produced here names *merchants* and *transaction ids*
— the things an analyst needs — and never the card identifier that linked
them. An analyst can see that one card connected two merchants without ever
being handed the card.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from compliance.detection.evidence import Contribution, RuleHit
from compliance.detection.geo import distance_km, implied_speed
from compliance.detection.ruleset import Family, RuleInstance

# The identity columns that can bind two merchant ids into one group, in the
# order an investigator would find most convincing.
IDENTITY_FIELDS = (
    ("hashed_br_number", "business registration"),
    ("hashed_merchant_address", "registered address"),
    ("hashed_merchant_name", "trading name"),
)


@dataclass(frozen=True)
class MerchantNode:
    """One merchant's identity and location, for the portfolio-wide pass."""

    merchant_id: str
    mcc: str | None = None
    agent_id: str | None = None
    hashed_br_number: str | None = None
    hashed_merchant_address: str | None = None
    hashed_merchant_name: str | None = None
    subdistrict: str | None = None
    district: str | None = None

    def identity(self, attribute: str) -> str | None:
        return getattr(self, attribute, None)


@dataclass(frozen=True)
class CardEvent:
    """One card's appearance at one merchant.

    `pan_key` is an opaque grouping token. It is used to group and is never
    copied into any output.
    """

    pan_key: str
    merchant_id: str
    source_txn_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class RingInput:
    merchants: list[MerchantNode]
    card_events: list[CardEvent] = field(default_factory=list)
    # Merchants already carrying an alert, for the identity-ring and agent
    # concentration tests.
    flagged: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RingHit:
    """A ring finding, attributed to one merchant so it can join that
    merchant's alert rather than floating free of the queue."""

    merchant_id: str
    hit: RuleHit


def _reason_code(inst: RuleInstance, **params: float) -> str:
    if not params:
        return inst.template
    rendered = ",".join(f"{k}={v:g}" for k, v in sorted(params.items()))
    return f"{inst.template}({rendered})"


def _identity_groups(
    merchants: list[MerchantNode],
) -> dict[tuple[str, str], list[MerchantNode]]:
    """Merchants grouped by each shared identity hash.

    Grouped per attribute rather than merged into one connected component:
    "these four share a business registration" is a specific, checkable claim,
    where a transitive blob joined through three different attributes is not
    something an analyst can act on.
    """
    groups: dict[tuple[str, str], list[MerchantNode]] = defaultdict(list)
    for node in merchants:
        for attribute, _ in IDENTITY_FIELDS:
            value = node.identity(attribute)
            if value:
                groups[(attribute, value)].append(node)
    return groups


def related_merchants(merchants: list[MerchantNode]) -> dict[str, frozenset[str]]:
    """For each merchant, the other merchants sharing any identity hash.

    Used both by the ring rule and by the branch-structuring rule, which needs
    to tell "three branches of one chain" from "three unrelated shops".
    """
    linked: dict[str, set[str]] = defaultdict(set)
    for members in _identity_groups(merchants).values():
        if len(members) < 2:
            continue
        ids = {m.merchant_id for m in members}
        for merchant_id in ids:
            linked[merchant_id] |= ids - {merchant_id}
    return {k: frozenset(v) for k, v in linked.items()}


def _shared_identity(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    min_members = int(inst.value("min_members"))
    min_flagged = int(inst.value("min_flagged"))

    hits: list[RingHit] = []
    for (attribute, _value), members in _identity_groups(data.merchants).items():
        ids = sorted({m.merchant_id for m in members})
        if len(ids) < min_members:
            continue
        already = [m for m in ids if m in data.flagged]
        if len(already) < min_flagged:
            continue

        label = dict(IDENTITY_FIELDS)[attribute]
        for merchant_id in ids:
            others = [m for m in ids if m != merchant_id]
            hits.append(
                RingHit(
                    merchant_id=merchant_id,
                    hit=RuleHit(
                        rule_id=inst.instance_id,
                        template=inst.template,
                        label=inst.display_label(),
                        reason_code=_reason_code(
                            inst, min_members=min_members, min_flagged=min_flagged
                        ),
                        sub_score=min(0.4 + 0.1 * len(ids) + 0.1 * len(already), 1.0),
                        message=(
                            f"Shares a {label} with {len(others)} other "
                            f"merchant(s): {', '.join(others[:6])}"
                            f"{'…' if len(others) > 6 else ''}. "
                            f"{len(already)} of the group already carry an alert."
                        ),
                        feature={
                            "feature_name": f"merchants_sharing_{attribute}",
                            "merchant_value": float(len(ids)),
                            "baseline_value": float(min_members),
                            "deviation": float(len(ids) - min_members),
                        },
                        # A ring is a property of the merchant, not of any
                        # transaction, so no transaction is highlighted. The
                        # empty tuple says that positively.
                        contributions=(),
                    ),
                )
            )
    return hits


def _agent_concentration(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    min_merchants = int(inst.value("min_merchants"))
    rate_multiple = inst.value("rate_multiple")

    books: dict[str, list[MerchantNode]] = defaultdict(list)
    for node in data.merchants:
        if node.agent_id:
            books[node.agent_id].append(node)

    total = len(data.merchants)
    if not total:
        return []
    portfolio_rate = len(data.flagged) / total
    if portfolio_rate <= 0:
        return []

    hits: list[RingHit] = []
    for agent_id, book in books.items():
        if len(book) < min_merchants:
            continue
        flagged = [m for m in book if m.merchant_id in data.flagged]
        rate = len(flagged) / len(book)
        if rate < portfolio_rate * rate_multiple:
            continue

        for node in flagged:
            hits.append(
                RingHit(
                    merchant_id=node.merchant_id,
                    hit=RuleHit(
                        rule_id=inst.instance_id,
                        template=inst.template,
                        label=inst.display_label(),
                        reason_code=_reason_code(
                            inst, min_merchants=min_merchants,
                            rate_multiple=rate_multiple,
                        ),
                        sub_score=min(rate, 1.0),
                        message=(
                            f"Onboarded by agent {agent_id}, whose book of "
                            f"{len(book)} merchants alerts at {rate:.0%} "
                            f"against a portfolio rate of {portfolio_rate:.0%}."
                        ),
                        feature={
                            "feature_name": "agent_book_alert_rate",
                            "merchant_value": round(rate, 4),
                            "baseline_value": round(portfolio_rate, 4),
                            "deviation": round(rate / portfolio_rate, 2),
                        },
                        contributions=(),
                    ),
                )
            )
    return hits


def _by_card(events: list[CardEvent]) -> dict[str, list[CardEvent]]:
    grouped: dict[str, list[CardEvent]] = defaultdict(list)
    for e in events:
        grouped[e.pan_key].append(e)
    for chain in grouped.values():
        chain.sort(key=lambda e: e.occurred_at)
    return grouped


def _branch_structuring(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    """One card across more related merchants in a day than a customer would.

    A hard count rather than a statistic. The source schema has no terminal id,
    so "branches of the same merchant" is distinct merchant ids sharing an
    identity hash — the only expression of the idea the data supports.
    """
    max_branches = int(inst.value("max_branches"))
    related = related_merchants(data.merchants)

    hits: list[RingHit] = []
    for chain in _by_card(data.card_events).values():
        # Split the card's day by which identity group each merchant belongs
        # to, so visiting three shops of one chain and three of another is not
        # added together into a false six.
        per_day: dict[tuple, list[CardEvent]] = defaultdict(list)
        for event in chain:
            group = related.get(event.merchant_id, frozenset()) | {event.merchant_id}
            per_day[(event.occurred_at.date(), tuple(sorted(group)))].append(event)

        for (_day, group), events in per_day.items():
            if len(group) < 2:
                continue  # not a chain — no branches to spread across
            touched = sorted({e.merchant_id for e in events})
            if len(touched) <= max_branches:
                continue

            for merchant_id in touched:
                others = [m for m in touched if m != merchant_id]
                hits.append(
                    RingHit(
                        merchant_id=merchant_id,
                        hit=RuleHit(
                            rule_id=inst.instance_id,
                            template=inst.template,
                            label=inst.display_label(),
                            reason_code=_reason_code(inst, max_branches=max_branches),
                            sub_score=min(0.5 + 0.1 * (len(touched) - max_branches), 1.0),
                            message=(
                                f"One card was used at {len(touched)} merchants "
                                f"under common ownership in a single day "
                                f"(limit {max_branches}): "
                                f"{', '.join(others)} and this one."
                            ),
                            feature={
                                "feature_name": "related_merchants_per_card_per_day",
                                "merchant_value": float(len(touched)),
                                "baseline_value": float(max_branches),
                                "deviation": float(len(touched) - max_branches),
                            },
                            contributions=tuple(
                                Contribution(
                                    source_txn_id=e.source_txn_id,
                                    # The linking field is the counterpart
                                    # merchant, never the card identifier that
                                    # actually did the linking.
                                    field="merchant_id",
                                    value=e.merchant_id,
                                    reason=(
                                        "same card, same owner group, same day"
                                    ),
                                )
                                for e in events
                                if e.merchant_id == merchant_id
                            ),
                        ),
                    )
                )
    return hits


def _card_swarm(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    """One card touching many *unrelated* merchants inside a short window."""
    min_merchants = int(inst.value("min_merchants"))
    window = timedelta(minutes=inst.value("window_minutes"))
    related = related_merchants(data.merchants)

    hits: list[RingHit] = []
    for chain in _by_card(data.card_events).values():
        start = 0
        for end in range(len(chain)):
            while chain[end].occurred_at - chain[start].occurred_at > window:
                start += 1
            window_events = chain[start : end + 1]
            touched = {e.merchant_id for e in window_events}
            if len(touched) < min_merchants:
                continue
            # Common ownership explains the spread; branch structuring is the
            # rule for that case. This one is about merchants with no link.
            unrelated = {
                m for m in touched if not (related.get(m, frozenset()) & touched)
            }
            if len(unrelated) < min_merchants:
                continue

            for merchant_id in sorted(unrelated):
                hits.append(
                    RingHit(
                        merchant_id=merchant_id,
                        hit=RuleHit(
                            rule_id=inst.instance_id,
                            template=inst.template,
                            label=inst.display_label(),
                            reason_code=_reason_code(
                                inst, min_merchants=min_merchants,
                                window_minutes=inst.value("window_minutes"),
                            ),
                            sub_score=min(0.4 + 0.1 * len(unrelated), 1.0),
                            message=(
                                f"One card was used at {len(touched)} unrelated "
                                f"merchants within "
                                f"{int(inst.value('window_minutes'))} minutes."
                            ),
                            feature={
                                "feature_name": "unrelated_merchants_per_card_window",
                                "merchant_value": float(len(touched)),
                                "baseline_value": float(min_merchants),
                                "deviation": float(len(touched) - min_merchants),
                            },
                            contributions=tuple(
                                Contribution(
                                    source_txn_id=e.source_txn_id,
                                    field="occurred_at",
                                    value=e.occurred_at.isoformat(),
                                    reason="part of a same-card burst across merchants",
                                )
                                for e in window_events
                                if e.merchant_id == merchant_id
                            ),
                        ),
                    )
                )
            # One finding per card is enough; the analyst does not need the
            # same swarm reported once per sliding window position.
            break
    return hits


def _geo_velocity(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    """One card in two places the time between them does not allow."""
    max_kmh = inst.value("max_kmh")
    min_km = inst.value("min_km")
    max_minutes = inst.value("max_minutes")

    places = {
        m.merchant_id: (m.subdistrict, m.district) for m in data.merchants
    }

    hits: list[RingHit] = []
    for chain in _by_card(data.card_events).values():
        for first, second in zip(chain, chain[1:]):
            if first.merchant_id == second.merchant_id:
                continue
            minutes = (second.occurred_at - first.occurred_at).total_seconds() / 60.0
            if minutes > max_minutes:
                continue
            here = places.get(first.merchant_id)
            there = places.get(second.merchant_id)
            if here is None or there is None:
                continue
            km = distance_km(here, there)
            if km is None or km < min_km:
                continue
            kmh = implied_speed(km, minutes)
            if kmh is None or kmh <= max_kmh:
                continue

            for event, other in ((first, second), (second, first)):
                hits.append(
                    RingHit(
                        merchant_id=event.merchant_id,
                        hit=RuleHit(
                            rule_id=inst.instance_id,
                            template=inst.template,
                            label=inst.display_label(),
                            reason_code=_reason_code(
                                inst, max_kmh=max_kmh, min_km=min_km
                            ),
                            sub_score=min(kmh / (max_kmh * 4), 1.0),
                            message=(
                                f"The same card was used at {other.merchant_id} "
                                f"{minutes:.0f} minutes "
                                f"{'later' if event is first else 'earlier'}, "
                                f"{km:.1f} km away — an implied "
                                f"{kmh:.0f} km/h, above the "
                                f"{max_kmh:.0f} km/h limit. Distance is between "
                                f"subdistrict centroids and so understates the "
                                f"real journey."
                            ),
                            feature={
                                "feature_name": "implied_travel_speed_kmh",
                                "merchant_value": round(kmh, 1),
                                "baseline_value": max_kmh,
                                "deviation": round(kmh / max_kmh, 2),
                            },
                            contributions=(
                                Contribution(
                                    source_txn_id=event.source_txn_id,
                                    field="occurred_at",
                                    value=event.occurred_at.isoformat(),
                                    reason=(
                                        f"{km:.1f} km from {other.merchant_id} "
                                        f"in {minutes:.0f} minutes"
                                    ),
                                ),
                            ),
                        ),
                    )
                )
    return hits


_EVALUATORS = {
    "shared_identity_ring": _shared_identity,
    "agent_alert_concentration": _agent_concentration,
    "card_across_related_merchants": _branch_structuring,
    "card_swarm": _card_swarm,
    "impossible_geo_velocity": _geo_velocity,
}


def evaluate(data: RingInput, rules: list[RuleInstance]) -> list[RingHit]:
    """Run every enabled Family C rule across the whole portfolio."""
    hits: list[RingHit] = []
    for inst in rules:
        if inst.spec().family is not Family.C or not inst.enabled:
            continue
        evaluator = _EVALUATORS.get(inst.template)
        if evaluator is None:
            continue
        hits.extend(evaluator(data, inst))
    return hits
