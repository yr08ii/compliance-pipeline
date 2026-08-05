"""Family B — the typology ruleset.

Family A asks whether a number is unusual. These rules ask whether a *shape*
matches a known laundering pattern, and the difference matters: every
transaction in a structuring run is individually unremarkable, which is
precisely why no baseline can see it. That is why the ruleset carries more
weight than its size suggests.

Every rule here is a pure function of pre-fetched data — no database, no
clock — so the same day always produces the same verdicts. The parameters
come from a `RuleInstance`, never from a constant in this file, so a
compliance officer can retune without a deploy and an auditor can read the
parameters that were in force from the alert itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median

from compliance.detection.evidence import Contribution, RuleHit
from compliance.detection.ruleset import Family, RuleInstance

# Statuses that mean the authorisation was attempted and refused. Separated
# from the refund set: a decline moved no money and must not count as value
# taken, while a refund moved value back out and must.
DECLINED_STATUSES = frozenset({"DECLINED"})
# Attempted but not completed for a reason other than refusal. Counted as an
# attempt for the decline ratio's denominator, never as value.
ABANDONED_STATUSES = frozenset({"CANCELLED", "VOIDED"})


@dataclass(frozen=True)
class Txn:
    """One transaction, reduced to what the typology rules read."""

    source_txn_id: str
    amount: float
    occurred_at: datetime
    hour: float
    is_refund: bool
    status: str | None
    card_type: str | None

    @property
    def declined(self) -> bool:
        return (self.status or "").upper() in DECLINED_STATUSES

    @property
    def settled(self) -> bool:
        """Whether this transaction actually moved value to the merchant."""
        status = (self.status or "").upper()
        return not self.is_refund and status not in (
            DECLINED_STATUSES | ABANDONED_STATUSES
        )


@dataclass(frozen=True)
class TypologyInput:
    """Everything Family B needs about one merchant on one scored day."""

    merchant_id: str
    mcc: str | None
    day: list[Txn]
    # Gross settled value per calendar day over the history window, most
    # recent last. Drives the bust-out and dormancy tests.
    daily_value: list[tuple[date, float]]
    # The last day the merchant traded before the scored day, if any.
    last_active: date | None = None
    scored_day: date | None = None
    # The merchant's own fitted median ticket, and its MCC cohort's, from the
    # profile. Reused rather than recomputed so Family A and Family B can never
    # disagree about what this merchant's normal was.
    baseline_center: float | None = None
    cohort_center: float | None = None
    # The cohort's hour-of-day density and its low-density cutoff, for the
    # declared-vs-actual test.
    cohort_hours: list[float] = field(default_factory=list)
    cohort_hours_threshold: float = 0.0

    @property
    def gross(self) -> float:
        return sum(t.amount for t in self.day if t.settled)

    @property
    def refunded(self) -> float:
        return sum(t.amount for t in self.day if t.is_refund)


def _money(v: float) -> str:
    return f"HKD {v:,.2f}"


def _reason_code(inst: RuleInstance, **params: float) -> str:
    """A reason code that carries the parameters that produced it.

    `structuring_below_threshold(threshold=8000,min_count=3)` still explains
    itself after somebody retunes the rule, where a bare template name would
    silently come to mean something else.
    """
    if not params:
        return inst.template
    rendered = ",".join(f"{k}={v:g}" for k, v in sorted(params.items()))
    return f"{inst.template}({rendered})"


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def _structuring(data: TypologyInput, inst: RuleInstance) -> RuleHit | None:
    threshold = inst.value("threshold")
    band = inst.value("band_fraction")
    min_count = int(inst.value("min_count"))
    max_ratio = inst.value("max_baseline_ratio")

    # A merchant whose ordinary sale is already near the line has no "just
    # under" to cluster at. Without this gate a jeweller fires every day.
    if data.baseline_center is None or data.baseline_center >= threshold * max_ratio:
        return None

    floor = threshold * (1.0 - band)
    in_band = [t for t in data.day if t.settled and floor <= t.amount < threshold]
    if len(in_band) < min_count:
        return None

    return RuleHit(
        rule_id=inst.instance_id,
        template=inst.template,
        label=inst.display_label(),
        reason_code=_reason_code(
            inst, threshold=threshold, band_fraction=band, min_count=min_count
        ),
        # Scaled by how far past the minimum the cluster runs, capped so one
        # extraordinary day cannot dominate the whole queue's ranking.
        sub_score=min(0.55 + 0.05 * (len(in_band) - min_count), 1.0),
        message=(
            f"{len(in_band)} transactions between {_money(floor)} and "
            f"{_money(threshold)} — just under the {_money(threshold)} "
            f"reporting threshold, at a merchant whose typical transaction is "
            f"{_money(data.baseline_center)}."
        ),
        feature={
            "feature_name": "near_threshold_transaction_count",
            "merchant_value": float(len(in_band)),
            "baseline_value": float(min_count),
            "deviation": float(len(in_band) - min_count),
        },
        contributions=tuple(
            Contribution(
                source_txn_id=t.source_txn_id,
                field="total_amount",
                value=_money(t.amount),
                reason=(
                    f"{(1 - t.amount / threshold) * 100:.1f}% below the "
                    f"{_money(threshold)} threshold"
                ),
            )
            for t in in_band
        ),
    )


def _refund_abuse(data: TypologyInput, inst: RuleInstance) -> RuleHit | None:
    min_refunds = int(inst.value("min_refunds"))
    ratio_limit = inst.value("ratio")

    refunds = [t for t in data.day if t.is_refund]
    gross = data.gross
    if len(refunds) < min_refunds or gross <= 0:
        return None

    ratio = data.refunded / gross
    if ratio < ratio_limit:
        return None

    return RuleHit(
        rule_id=inst.instance_id,
        template=inst.template,
        label=inst.display_label(),
        reason_code=_reason_code(inst, min_refunds=min_refunds, ratio=ratio_limit),
        sub_score=min(ratio, 1.0),
        message=(
            f"{len(refunds)} refunds returning {_money(data.refunded)} against "
            f"{_money(gross)} taken — {ratio:.0%} of the day's value."
        ),
        feature={
            "feature_name": "refund_value_share",
            "merchant_value": round(ratio, 4),
            "baseline_value": ratio_limit,
            "deviation": round(ratio - ratio_limit, 4),
        },
        contributions=tuple(
            Contribution(
                source_txn_id=t.source_txn_id,
                field="transaction_status",
                value=t.status or "REFUND",
                reason=f"refund of {_money(t.amount)}",
            )
            for t in refunds
        ),
    )


def _bust_out(data: TypologyInput, inst: RuleInstance) -> RuleHit | None:
    spike_ratio = inst.value("spike_ratio")
    recent_days = int(inst.value("recent_days"))
    refund_limit = inst.value("refund_ratio")

    if len(data.daily_value) < recent_days * 2:
        # Not enough history to have an "earlier level" to climb away from.
        return None

    recent = [v for _, v in data.daily_value[-recent_days:]]
    earlier = [v for _, v in data.daily_value[:-recent_days]]
    if not recent or not earlier:
        return None

    recent_level = median(recent)
    earlier_level = median(earlier)
    if earlier_level <= 0:
        return None

    observed = recent_level / earlier_level
    if observed < spike_ratio:
        return None

    gross = data.gross
    day_refund_ratio = (data.refunded / gross) if gross > 0 else 0.0
    if day_refund_ratio < refund_limit:
        return None

    return RuleHit(
        rule_id=inst.instance_id,
        template=inst.template,
        label=inst.display_label(),
        reason_code=_reason_code(
            inst, spike_ratio=spike_ratio, recent_days=recent_days,
            refund_ratio=refund_limit,
        ),
        sub_score=min(observed / 10.0, 1.0),
        message=(
            f"Daily value over the last {recent_days} days is {observed:.1f}x "
            f"the earlier level ({_money(recent_level)} against "
            f"{_money(earlier_level)}), with {day_refund_ratio:.0%} of the "
            f"scored day refunded."
        ),
        feature={
            "feature_name": "recent_vs_earlier_daily_value",
            "merchant_value": round(recent_level, 2),
            "baseline_value": round(earlier_level, 2),
            "deviation": round(observed, 2),
        },
        # The pattern is the merchant's trajectory, not any one checkout, so
        # the refunds that complete the shape are what gets pointed at.
        contributions=tuple(
            Contribution(
                source_txn_id=t.source_txn_id,
                field="transaction_status",
                value=t.status or "REFUND",
                reason="refund during the spike",
            )
            for t in data.day
            if t.is_refund
        ),
    )


def _dormant_reactivation(data: TypologyInput, inst: RuleInstance) -> RuleHit | None:
    silence_days = int(inst.value("silence_days"))
    return_count = int(inst.value("return_count"))

    if data.last_active is None or data.scored_day is None:
        return None

    gap = (data.scored_day - data.last_active).days
    active = [t for t in data.day if t.settled]
    if gap < silence_days or len(active) < return_count:
        return None

    return RuleHit(
        rule_id=inst.instance_id,
        template=inst.template,
        label=inst.display_label(),
        reason_code=_reason_code(
            inst, silence_days=silence_days, return_count=return_count
        ),
        sub_score=min(0.5 + gap / 365.0, 1.0),
        message=(
            f"Silent for {gap} days (last traded {data.last_active}), then "
            f"{len(active)} transactions worth {_money(data.gross)} in a "
            f"single day."
        ),
        feature={
            "feature_name": "days_dormant_before_return",
            "merchant_value": float(gap),
            "baseline_value": float(silence_days),
            "deviation": float(gap - silence_days),
        },
        # The whole day is the evidence: it is the volume on return, not any
        # particular sale, that makes the reactivation abrupt.
        contributions=tuple(
            Contribution(
                source_txn_id=t.source_txn_id,
                field="occurred_at",
                value=t.occurred_at.isoformat(),
                reason=f"first activity in {gap} days",
            )
            for t in active
        ),
    )


def _rapid_movement(data: TypologyInput, inst: RuleInstance) -> RuleHit | None:
    min_value = inst.value("min_value")
    tolerance = inst.value("match_tolerance")

    gross = data.gross
    out = data.refunded
    if gross < min_value or gross <= 0 or out <= 0:
        return None

    mismatch = abs(gross - out) / gross
    if mismatch > tolerance:
        return None

    return RuleHit(
        rule_id=inst.instance_id,
        template=inst.template,
        label=inst.display_label(),
        reason_code=_reason_code(inst, min_value=min_value, match_tolerance=tolerance),
        sub_score=min(0.6 + (tolerance - mismatch), 1.0),
        message=(
            f"{_money(gross)} taken and {_money(out)} returned the same day — "
            f"matching within {mismatch:.1%}, leaving no resting balance. "
            f"Read from the transaction record only; a settlement view would "
            f"be stronger evidence."
        ),
        feature={
            "feature_name": "same_day_in_out_mismatch",
            "merchant_value": round(mismatch, 4),
            "baseline_value": tolerance,
            "deviation": round(tolerance - mismatch, 4),
        },
        contributions=tuple(
            Contribution(
                source_txn_id=t.source_txn_id,
                field="net_amount" if t.is_refund else "total_amount",
                value=_money(t.amount),
                reason="value out" if t.is_refund else "value in",
            )
            for t in data.day
            if t.settled or t.is_refund
        ),
    )


def _declared_mismatch(data: TypologyInput, inst: RuleInstance) -> RuleHit | None:
    ticket_ratio = inst.value("ticket_ratio")
    min_overlap = inst.value("hours_overlap")

    if not data.baseline_center or not data.cohort_center or data.cohort_center <= 0:
        return None

    observed_ratio = data.baseline_center / data.cohort_center
    if observed_ratio < ticket_ratio:
        return None

    # Hours are the second leg: being expensive for your trade is legal, and
    # only becomes a category question when the trading pattern disagrees too.
    if not data.cohort_hours or not data.day:
        return None

    bins = len(data.cohort_hours)
    inside = 0
    outside: list[Txn] = []
    for t in data.day:
        idx = int(t.hour / 24.0 * bins) % bins
        if data.cohort_hours[idx] >= data.cohort_hours_threshold:
            inside += 1
        else:
            outside.append(t)

    overlap = inside / len(data.day)
    if overlap >= min_overlap:
        return None

    return RuleHit(
        rule_id=inst.instance_id,
        template=inst.template,
        label=inst.display_label(),
        reason_code=_reason_code(
            inst, ticket_ratio=ticket_ratio, hours_overlap=min_overlap
        ),
        sub_score=min(observed_ratio / 20.0 + (1.0 - overlap) / 2, 1.0),
        message=(
            f"Typical transaction is {observed_ratio:.1f}x its MCC "
            f"{data.mcc or ''} cohort ({_money(data.baseline_center)} against "
            f"{_money(data.cohort_center)}), and only {overlap:.0%} of trading "
            f"falls inside the category's normal hours. The declared category "
            f"does not describe how this merchant trades."
        ),
        feature={
            "feature_name": "declared_category_divergence",
            "merchant_value": round(observed_ratio, 2),
            "baseline_value": ticket_ratio,
            "deviation": round(observed_ratio - ticket_ratio, 2),
        },
        contributions=tuple(
            Contribution(
                source_txn_id=t.source_txn_id,
                field="occurred_at",
                value=t.occurred_at.isoformat(),
                reason="outside the declared category's normal hours",
            )
            for t in outside
        ),
    )


def _decline_spike(data: TypologyInput, inst: RuleInstance) -> RuleHit | None:
    min_attempts = int(inst.value("min_attempts"))
    ratio_limit = inst.value("ratio")

    # Refunds are not authorisation attempts, so they are not in the
    # denominator: a heavy refund day would otherwise dilute the ratio and
    # hide a card-testing run.
    attempts = [t for t in data.day if not t.is_refund and t.status]
    if len(attempts) < min_attempts:
        return None

    declined = [t for t in attempts if t.declined]
    ratio = len(declined) / len(attempts)
    if ratio < ratio_limit:
        return None

    return RuleHit(
        rule_id=inst.instance_id,
        template=inst.template,
        label=inst.display_label(),
        reason_code=_reason_code(inst, min_attempts=min_attempts, ratio=ratio_limit),
        sub_score=min(ratio, 1.0),
        message=(
            f"{len(declined)} of {len(attempts)} authorisations declined "
            f"({ratio:.0%}) — far above ordinary retail, the merchant-side "
            f"signature of card testing."
        ),
        feature={
            "feature_name": "declined_authorisation_share",
            "merchant_value": round(ratio, 4),
            "baseline_value": ratio_limit,
            "deviation": round(ratio - ratio_limit, 4),
        },
        contributions=tuple(
            Contribution(
                source_txn_id=t.source_txn_id,
                field="transaction_status",
                value=t.status or "DECLINED",
                reason="declined authorisation",
            )
            for t in declined
        ),
    )


_EVALUATORS = {
    "structuring_below_threshold": _structuring,
    "refund_ratio_spike": _refund_abuse,
    "bust_out": _bust_out,
    "dormant_reactivation": _dormant_reactivation,
    "rapid_movement": _rapid_movement,
    "declared_vs_actual_mismatch": _declared_mismatch,
    "decline_ratio_spike": _decline_spike,
}


def evaluate(data: TypologyInput, rules: list[RuleInstance]) -> list[RuleHit]:
    """Run every applicable Family B rule against one merchant's scored day.

    Instances scoped to other MCCs are skipped silently; an instance whose
    template has no evaluator is skipped too, so a half-added rule cannot stop
    the nightly run.
    """
    hits: list[RuleHit] = []
    for inst in rules:
        if inst.spec().family is not Family.B or not inst.enabled:
            continue
        if not inst.applies_to(data.mcc):
            continue
        evaluator = _EVALUATORS.get(inst.template)
        if evaluator is None:
            continue
        hit = evaluator(data, inst)
        if hit is not None:
            hits.append(hit)
    return hits
