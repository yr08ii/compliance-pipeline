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

# Rails that settle from a stored balance and therefore carry no card number.
#
# Over half the real extract is one of these, and the source writes
# `hashed_pan` as an empty string rather than omitting it — there is nothing
# to hash. Every rule below follows *one card* across merchants, and a rail
# with no card cannot participate in that: an Octopus balance is not a card
# that can be in two places at once.
#
# Excluding them by name as well as by the blank check is deliberate
# redundancy. The blank check alone would silently start producing rings the
# day the source began emitting a per-wallet token, and "one Alipay account
# used at many shops" is a customer, not a ring.
WALLET_RAILS = frozenset(
    {
        "ALIPAY",
        "ALIPAYHK",
        "OCTOPUS",
        "WECHAT",
        "WECHATPAY",
        "PAYME",
        "FPS",
        "TAPNGO",
        "TAP&GO",
    }
)


def is_card_rail(card_type: str | None) -> bool:
    """Whether a rail carries a card number the linkage rules can follow.

    Unknown rails are treated as cards. A new card scheme must keep being
    detected; a new *wallet* slipping through is caught by the blank-PAN check
    that runs alongside this, because a wallet has no PAN to supply.
    """
    if not card_type:
        return True
    return " ".join(card_type.split()).strip().upper() not in WALLET_RAILS


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
    # The rail, so a wallet can be excluded by name rather than only by the
    # absence of a card number. See `WALLET_RAILS`.
    card_type: str | None = None


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
    """One chronological chain per card, wallets and blank keys dropped.

    The two exclusions are the whole reason this is a function rather than a
    `groupby`. A blank `pan_key` is not a card: every wallet transaction in
    the portfolio carries the same empty string, so grouping on it produces
    one chain containing more than half the estate — a single "card" at
    thousands of merchants, which every rule below then reports as a ring.
    """
    grouped: dict[str, list[CardEvent]] = defaultdict(list)
    for e in events:
        if not e.pan_key or not e.pan_key.strip():
            continue
        if not is_card_rail(e.card_type):
            continue
        grouped[e.pan_key].append(e)
    for chain in grouped.values():
        chain.sort(key=lambda e: (e.occurred_at, e.source_txn_id))
    return grouped


def _card_refs(events: list[CardEvent]) -> dict[str, str]:
    """A display label per card: "card 1", "card 2", …

    An analyst needs to see that two findings concern the same card. They must
    not be handed anything derived from the PAN hash to do it with — that hash
    is brute-forceable back to a card number, and a truncation or re-hash of it
    is still a function of cardholder data. A position in this run's sort order
    carries no information about the card at all, which is the point.
    """
    order = sorted(
        {e.pan_key for e in events if e.pan_key and e.pan_key.strip()}
    )
    return {key: f"card {i}" for i, key in enumerate(order, start=1)}


def _linked_contributions(
    trail: list[CardEvent], focus: set[str], reason: str
) -> tuple[Contribution, ...]:
    """The card's whole day, across every merchant, with the rule's own
    transactions marked.

    Two decisions here, both about what an analyst opening the case needs.

    **The trail is the card's whole day, not only the transactions that
    tripped the rule.** A finding that shows two transactions 20 minutes and
    35 km apart raises the immediate question "what else did this card do?",
    and the answer has to be on the page. Assembling it here rather than at
    read time is what keeps `hashed_pan` inside this module: the case page
    resolves transaction ids and never touches the card.

    **The transactions the rule actually fired on are still distinguished**,
    via `focus`. Widening the evidence must not blur what the accusation
    rests on.
    """
    return tuple(
        Contribution(
            source_txn_id=e.source_txn_id,
            field="merchant_id",
            value=e.merchant_id,
            reason=(
                reason
                if e.source_txn_id in focus
                else "same card, same day — context for the trail"
            ),
        )
        for e in sorted(trail, key=lambda e: (e.occurred_at, e.source_txn_id))
    )


def _busiest(events: list[CardEvent]) -> str:
    """Which merchant carries the alert.

    One card is one investigation, so it raises one alert, and an alert has to
    sit in some merchant's queue to be anybody's responsibility. The merchant
    with the most of the card's transactions is the one with the most to
    explain. Ties break on merchant id so two runs over the same data agree.
    """
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        counts[e.merchant_id] += 1
    return min(counts, key=lambda m: (-counts[m], m))


def _branch_structuring(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    """One card across more related merchants in a day than a customer would.

    A hard count rather than a statistic. The source schema has no terminal id,
    so "branches of the same merchant" is distinct merchant ids sharing an
    identity hash — the only expression of the idea the data supports.
    """
    max_branches = int(inst.value("max_branches"))
    related = related_merchants(data.merchants)
    refs = _card_refs(data.card_events)

    hits: list[RingHit] = []
    for pan_key, chain in _by_card(data.card_events).items():
        # Split the card's day by which identity group each merchant belongs
        # to, so visiting three shops of one chain and three of another is not
        # added together into a false six.
        per_day: dict[tuple, list[CardEvent]] = defaultdict(list)
        for event in chain:
            group = related.get(event.merchant_id, frozenset()) | {event.merchant_id}
            per_day[(event.occurred_at.date(), tuple(sorted(group)))].append(event)

        for (day, group), events in sorted(per_day.items()):
            if len(group) < 2:
                continue  # not a chain — no branches to spread across
            touched = sorted({e.merchant_id for e in events})
            if len(touched) <= max_branches:
                continue

            # One finding for the card's day, not one per branch. The branches
            # are the finding; reporting it once per branch turned a single
            # investigation into four alerts saying the same thing.
            carrier = _busiest(events)
            others = [m for m in touched if m != carrier]
            hits.append(
                RingHit(
                    merchant_id=carrier,
                    hit=RuleHit(
                        rule_id=inst.instance_id,
                        template=inst.template,
                        label=inst.display_label(),
                        reason_code=_reason_code(inst, max_branches=max_branches),
                        sub_score=min(0.5 + 0.1 * (len(touched) - max_branches), 1.0),
                        message=(
                            f"One card ({refs.get(pan_key, 'card')}) was used at "
                            f"{len(touched)} merchants under common ownership on "
                            f"{day.isoformat()} (limit {max_branches}): "
                            f"{', '.join(others)} and this one. They share a "
                            f"registration, address or trading name, so these are "
                            f"branches of one chain rather than separate shops."
                        ),
                        feature={
                            "feature_name": "related_merchants_per_card_per_day",
                            "merchant_value": float(len(touched)),
                            "baseline_value": float(max_branches),
                            "deviation": float(len(touched) - max_branches),
                        },
                        contributions=_linked_contributions(
                            chain,
                            {e.source_txn_id for e in events},
                            "same card, same owner group, same day",
                        ),
                        linkage={
                            "card_ref": refs.get(pan_key, "card"),
                            "merchants": touched,
                            # The distinguishing fact of this rule against card
                            # swarming, and the thing the analyst is asked to
                            # notice: these merchants are one owner.
                            "related": True,
                            "day": day.isoformat(),
                            "legs": [],
                            "focus_txn_ids": sorted(
                                e.source_txn_id for e in events
                            ),
                        },
                    ),
                )
            )
    return hits


def _card_swarm(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    """One card touching many *unrelated* merchants inside a short window."""
    min_merchants = int(inst.value("min_merchants"))
    window = timedelta(minutes=inst.value("window_minutes"))
    related = related_merchants(data.merchants)
    refs = _card_refs(data.card_events)

    hits: list[RingHit] = []
    for pan_key, chain in _by_card(data.card_events).items():
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

            # One finding for the card, attributed to one merchant. Raising it
            # once per merchant in the swarm reported the same burst five
            # times, which is five times the queue for one investigation.
            carrier = _busiest(window_events)
            hits.append(
                RingHit(
                    merchant_id=carrier,
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
                            f"One card ({refs.get(pan_key, 'card')}) was used at "
                            f"{len(touched)} unrelated merchants within "
                            f"{int(inst.value('window_minutes'))} minutes: "
                            f"{', '.join(sorted(touched))}. They share no "
                            f"registration, address or trading name, so common "
                            f"ownership does not explain the spread."
                        ),
                        feature={
                            "feature_name": "unrelated_merchants_per_card_window",
                            "merchant_value": float(len(touched)),
                            "baseline_value": float(min_merchants),
                            "deviation": float(len(touched) - min_merchants),
                        },
                        contributions=_linked_contributions(
                            chain,
                            {e.source_txn_id for e in window_events},
                            "part of a same-card burst across merchants",
                        ),
                        linkage={
                            "card_ref": refs.get(pan_key, "card"),
                            "merchants": sorted(touched),
                            "related": False,
                            "window_minutes": int(inst.value("window_minutes")),
                            "legs": [],
                            "focus_txn_ids": sorted(
                                e.source_txn_id for e in window_events
                            ),
                        },
                    ),
                )
            )
            # One finding per card is enough; the analyst does not need the
            # same swarm reported once per sliding window position.
            break
    return hits


def _geo_velocity(data: RingInput, inst: RuleInstance) -> list[RingHit]:
    """One card in two places the time between them does not allow.

    Reported once per card, not once per merchant per hop. A card that
    ping-pongs between two districts all afternoon is one story, and the
    previous shape told it once for each end of each leg — a dozen alerts
    describing a single card.

    The evidence is the arithmetic rather than the verdict: every impossible
    leg carries its two places, the distance between their centroids, the
    elapsed minutes, the implied speed, and how many times over the limit that
    is. An analyst asked to accept "impossible" is owed the sum that says so.
    """
    max_kmh = inst.value("max_kmh")
    min_km = inst.value("min_km")
    max_minutes = inst.value("max_minutes")

    places = {
        m.merchant_id: (m.subdistrict, m.district) for m in data.merchants
    }

    def place_name(merchant_id: str) -> str:
        sub, dist = places.get(merchant_id, (None, None))
        return sub or dist or "unknown"

    refs = _card_refs(data.card_events)

    hits: list[RingHit] = []
    for pan_key, chain in _by_card(data.card_events).items():
        legs: list[dict] = []
        involved: dict[str, CardEvent] = {}

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

            legs.append({
                "from_merchant": first.merchant_id,
                "from_place": place_name(first.merchant_id),
                "from_txn_id": first.source_txn_id,
                "from_time": first.occurred_at.isoformat(),
                "to_merchant": second.merchant_id,
                "to_place": place_name(second.merchant_id),
                "to_txn_id": second.source_txn_id,
                "to_time": second.occurred_at.isoformat(),
                "distance_km": round(km, 2),
                "minutes": round(minutes, 1),
                "kmh": round(kmh, 1),
                "limit_kmh": round(max_kmh, 1),
                "over_limit_multiple": round(kmh / max_kmh, 2),
            })
            involved[first.source_txn_id] = first
            involved[second.source_txn_id] = second

        if not legs:
            continue

        worst = max(legs, key=lambda leg: leg["kmh"])
        events = list(involved.values())
        # The arrival end of the fastest leg carries the alert: that is the
        # merchant that accepted a card which could not have been present.
        carrier = worst["to_merchant"]
        merchants = sorted({e.merchant_id for e in events})

        hits.append(
            RingHit(
                merchant_id=carrier,
                hit=RuleHit(
                    rule_id=inst.instance_id,
                    template=inst.template,
                    label=inst.display_label(),
                    reason_code=_reason_code(inst, max_kmh=max_kmh, min_km=min_km),
                    sub_score=min(worst["kmh"] / (max_kmh * 4), 1.0),
                    message=(
                        f"One card ({refs.get(pan_key, 'card')}) was used at "
                        f"{worst['from_merchant']} in {worst['from_place']} and "
                        f"{worst['to_merchant']} in {worst['to_place']} "
                        f"{worst['minutes']:.0f} minutes apart — "
                        f"{worst['distance_km']:.1f} km, an implied "
                        f"{worst['kmh']:.0f} km/h against a "
                        f"{max_kmh:.0f} km/h limit "
                        f"({worst['over_limit_multiple']:.1f}x over)."
                        + (
                            f" {len(legs)} legs of this card's day are impossible."
                            if len(legs) > 1
                            else ""
                        )
                        + " Distance is between subdistrict centroids and so "
                        "understates the real journey."
                    ),
                    feature={
                        "feature_name": "implied_travel_speed_kmh",
                        "merchant_value": worst["kmh"],
                        "baseline_value": max_kmh,
                        "deviation": worst["over_limit_multiple"],
                    },
                    contributions=_linked_contributions(
                        chain,
                        set(involved),
                        f"same card at {len(merchants)} merchants, at times the "
                        f"journey between them does not allow",
                    ),
                    linkage={
                        "card_ref": refs.get(pan_key, "card"),
                        "merchants": merchants,
                        "related": False,
                        "legs": legs,
                        # The transactions the accusation rests on, as against
                        # the rest of the card's day that surrounds them.
                        "focus_txn_ids": sorted(involved),
                    },
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
