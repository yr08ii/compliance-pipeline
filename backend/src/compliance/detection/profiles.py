"""Family A's non-numeric baselines: when a merchant trades, and whose cards
it accepts.

Neither is a magnitude, so neither takes a modified z-score. They still emit
the same `feature_snapshot` shape, so the divergence panel renders them
alongside the amount, volume and speed detectors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.windows import _window_bounds
from compliance.models import Merchant, Transaction

HOURS = 24
# Trading hours are a local-time question: "3am" means 3am where the merchant
# is. Read the hour in Hong Kong time regardless of what the driver returns.
HKT = timezone(timedelta(hours=8))


def local_hour(moment: datetime) -> int:
    if moment.tzinfo is None:
        return moment.hour
    return moment.astimezone(HKT).hour
# Weight given to each neighbouring hour. Trade rarely stops dead on the hour,
# so a little smoothing stops a merchant's quiet-but-normal hours reading as
# anomalous.
NEIGHBOUR_WEIGHT = 0.5
# An hour holding less than this share of a merchant's trade is outside its
# working pattern. Starting point, calibrated against dispositions.
QUIET_HOUR_SHARE = 0.01
MIN_OBSERVATIONS = 20
# Laplace smoothing, so an origin never seen is improbable rather than
# impossible — an unseen country must not produce an infinite score.
ORIGIN_PRIOR = 0.5
SURPRISAL_FLAG = 3.0
# A member needs this much of its own trade before it votes on its cohort's
# hours. One transaction normalises to a full vote otherwise, letting a
# cold-start merchant's single odd-hour sale define what its whole trade
# considers normal — the opposite of what the cohort is for.
MIN_MEMBER_OBSERVATIONS = 10


@dataclass(frozen=True)
class ActiveHours:
    """A merchant's trade distributed over the 24-hour clock."""

    density: tuple[float, ...]
    n: int

    @property
    def usable(self) -> bool:
        return self.n >= MIN_OBSERVATIONS and sum(self.density) > 0

    def share(self, hour: int) -> float:
        total = sum(self.density)
        return self.density[hour % HOURS] / total if total else 0.0


@dataclass(frozen=True)
class OriginMix:
    """A merchant's historical distribution of card issuing countries."""

    counts: dict[str, int]
    n: int

    @property
    def usable(self) -> bool:
        return self.n >= MIN_OBSERVATIONS

    def share(self, origin: str) -> float:
        return self.counts.get(origin, 0) / self.n if self.n else 0.0


def fit_active_hours(
    session: Session, merchant_id: str, as_of: datetime, window_days: int,
    lag_days: int = 0,
) -> ActiveHours:
    """Build the merchant's hour-of-day density.

    Smoothing wraps around the clock, because time is circular: 23:00 and
    01:00 are two hours apart, not twenty-two. A linear model splits a bar
    that trades across midnight into two clusters and calls its busiest hour
    an outlier.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    stamps = list(session.scalars(
        select(Transaction.occurred_at).where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            Transaction.is_refund.is_(False),
        )
    ))

    raw = [0.0] * HOURS
    for stamp in stamps:
        raw[local_hour(stamp)] += 1.0

    smoothed = [
        raw[h]
        + NEIGHBOUR_WEIGHT * raw[(h - 1) % HOURS]
        + NEIGHBOUR_WEIGHT * raw[(h + 1) % HOURS]
        for h in range(HOURS)
    ]
    return ActiveHours(density=tuple(smoothed), n=len(stamps))


def hour_is_unusual(hour: int, hours: ActiveHours) -> bool:
    """Whether the merchant essentially never trades at this hour."""
    if not hours.usable:
        return False
    return hours.share(hour) < QUIET_HOUR_SHARE


def fit_origin_mix(
    session: Session, merchant_id: str, as_of: datetime, window_days: int,
    lag_days: int = 0,
) -> OriginMix:
    """Count the issuing countries the merchant normally sees.

    Taken straight from `card_issuing_country`; no BIN lookup is needed.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    origins = list(session.scalars(
        select(Transaction.card_issuing_country).where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            Transaction.is_refund.is_(False),
            Transaction.card_issuing_country.is_not(None),
        )
    ))

    counts: dict[str, int] = {}
    for origin in origins:
        counts[origin] = counts.get(origin, 0) + 1
    return OriginMix(counts=counts, n=len(origins))


def origin_surprisal(origin: str, mix: OriginMix) -> float:
    """How improbable this origin is for this merchant, in bits.

    Smoothed so a never-before-seen country scores high but finite. An airport
    shop that always sees foreign cards has low surprisal for them — the
    signal is a *change* in who is paying, not foreignness itself.
    """
    if not mix.usable:
        return 0.0
    distinct = max(len(mix.counts), 1)
    probability = (mix.counts.get(origin, 0) + ORIGIN_PRIOR) / (
        mix.n + ORIGIN_PRIOR * (distinct + 1)
    )
    return -math.log2(probability)


@dataclass(frozen=True)
class CohortHours:
    """When a trade normally operates, averaged across its members."""

    density: tuple[float, ...]
    n_merchants: int

    @property
    def usable(self) -> bool:
        from compliance.detection.baselines import MIN_COHORT_MERCHANTS

        return self.n_merchants >= MIN_COHORT_MERCHANTS and sum(self.density) > 0

    def share(self, hour: int) -> float:
        total = sum(self.density)
        return self.density[hour % HOURS] / total if total else 0.0


def fit_cohort_hours(
    session: Session, as_of: datetime, window_days: int, lag_days: int = 0,
    dimension: str = "mcc",
) -> dict[str, CohortHours]:
    """Fit each trade's operating pattern over the clock.

    Each member's density is normalised before averaging, so one busy merchant
    cannot define the cohort's hours — the same one-vote-per-merchant rule the
    amount and volume cohorts use.

    This is the only thing that can say 3am is odd for a merchant with no hours
    pattern of its own: a cold-start merchant has no density to compare against,
    but its trade does.
    """
    from compliance.detection.windows import _cohort_column, _window_bounds

    key = _cohort_column(dimension)
    start, end = _window_bounds(as_of, window_days, lag_days)
    rows = session.execute(
        select(key, Transaction.merchant_id, Transaction.occurred_at)
        .join(Transaction, Transaction.merchant_id == Merchant.merchant_id)
        .where(
            key.is_not(None),
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            Transaction.is_refund.is_(False),
        )
    )

    per_member: dict[str, dict[str, list[float]]] = {}
    for group, merchant_id, occurred_at in rows:
        counts = per_member.setdefault(group, {}).setdefault(
            merchant_id, [0.0] * HOURS
        )
        counts[local_hour(occurred_at)] += 1.0

    cohorts: dict[str, CohortHours] = {}
    for group, members in per_member.items():
        averaged = [0.0] * HOURS
        voters = 0
        for counts in members.values():
            total = sum(counts)
            if total < MIN_MEMBER_OBSERVATIONS:
                continue
            voters += 1
            for hour in range(HOURS):
                averaged[hour] += counts[hour] / total
        smoothed = [
            averaged[h]
            + NEIGHBOUR_WEIGHT * averaged[(h - 1) % HOURS]
            + NEIGHBOUR_WEIGHT * averaged[(h + 1) % HOURS]
            for h in range(HOURS)
        ]
        cohorts[group] = CohortHours(tuple(smoothed), voters)
    return cohorts


def hour_is_unusual_for_cohort(hour: int, cohort: CohortHours) -> bool:
    """Whether this trade essentially never operates at this hour."""
    if not cohort.usable:
        return False
    return cohort.share(hour) < QUIET_HOUR_SHARE
