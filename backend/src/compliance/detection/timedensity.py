"""Circular kernel density estimation over time-of-day.

Time is circular, so a linear density is wrong at midnight: 23:30 and 00:30 are
an hour apart, not twenty-three. The kernel here is the **von Mises**
distribution — the circular analogue of a Gaussian — evaluated on the 24-hour
clock mapped to a circle.

Three things this gets that a smoothed hour histogram does not:

* **Sub-hour resolution.** Density is estimated on a fine grid, so 03:15 and
  03:45 are different distances from a 04:00 cluster.
* **Bandwidth that adapts to the data.** A merchant with sparse history needs
  more smoothing or its density is noise; one with dense history supports a
  finer estimate. A fixed bandwidth under-smooths the first and over-smooths
  the second.
* **A threshold calibrated per merchant.** A fixed "below 1% of trade" cutoff
  punishes merchants whose trade is spread thinly across many hours: a
  round-the-clock forecourt has no hour above ~4%, and would flag constantly.
  The cutoff is a low percentile of *this merchant's own* density instead.

Estimation is binned — observations are accumulated into a fine grid and
convolved once with the kernel — so cost is independent of transaction count
past the binning step. Evaluating a naive KDE at every observation would be
quadratic and unaffordable across a whole merchant base.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.windows import _cohort_column, _window_bounds
from compliance.models import Merchant, Transaction

HOURS_IN_DAY = 24.0
# 15-minute resolution. Fine enough that sub-hour structure survives, coarse
# enough that the convolution stays cheap.
BINS = 96
BIN_HOURS = HOURS_IN_DAY / BINS

# Trading hours are a local-time question: "3am" means 3am where the merchant
# is. Hong Kong has observed no DST since 1979, so a fixed offset is exact.
HKT = timezone(timedelta(hours=8))

# Silverman-style bandwidth: h ∝ n^(-1/5), scaled so a merchant with ~100
# observations gets roughly an hour of smoothing.
BANDWIDTH_SCALE = 2.5
MIN_BANDWIDTH_HOURS = 0.25
MAX_BANDWIDTH_HOURS = 4.0

# A transaction is anomalous if it lands below this percentile of the
# merchant's own density. Starting point, calibrated against dispositions.
THRESHOLD_PERCENTILE = 0.05
# A cohort pools members that keep genuinely different hours, so its density
# has heavier legitimate tails than any single merchant's. Applying the same
# percentile would flag a merchant for closing an hour later than most of its
# trade, which is variation rather than anomaly.
COHORT_THRESHOLD_PERCENTILE = 0.01

MIN_OBSERVATIONS = 20
# A cohort member needs this much of its own trade before it votes on the
# cohort's hours; one transaction would otherwise normalise to a full vote.
MIN_MEMBER_OBSERVATIONS = 10
MIN_COHORT_MERCHANTS = 3


def local_time_of_day(moment: datetime) -> float:
    """Hours past local midnight, with minutes — not rounded to the hour."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(HKT)
    return moment.hour + moment.minute / 60.0 + moment.second / 3600.0


def bandwidth_hours(n: int) -> float:
    """Smoothing width in hours for `n` observations.

    Narrows as data accumulates, clamped so a tiny sample cannot demand
    absurd smoothing and a huge one cannot collapse the kernel to a spike.
    """
    if n <= 1:
        return MAX_BANDWIDTH_HOURS
    raw = BANDWIDTH_SCALE * (n ** -0.2)
    return min(max(raw, MIN_BANDWIDTH_HOURS), MAX_BANDWIDTH_HOURS)


def _concentration(bandwidth: float) -> float:
    """von Mises κ giving a circular spread equivalent to `bandwidth` hours.

    For a concentrated von Mises the angular standard deviation is ≈ 1/√κ, so
    matching it to the desired width in radians gives κ = 1/σ².
    """
    sigma_radians = 2 * math.pi * bandwidth / HOURS_IN_DAY
    return 1.0 / (sigma_radians * sigma_radians)


def _kernel_weights(kappa: float) -> list[float]:
    """One period of the von Mises kernel, sampled on the bin grid.

    The normalising constant (involving a Bessel function) is omitted: the
    density is normalised numerically at the end, so the constant cancels.
    """
    weights = []
    for offset in range(BINS):
        angle = 2 * math.pi * offset / BINS
        # Subtracting kappa keeps the exponent ≤ 0, so a large kappa cannot
        # overflow; the constant factor divides out in normalisation.
        weights.append(math.exp(kappa * (math.cos(angle) - 1.0)))
    return weights


@dataclass(frozen=True)
class TimeDensity:
    """A merchant's estimated density of trading times over the clock."""

    density: tuple[float, ...]
    threshold: float
    bandwidth: float
    n: int

    @property
    def usable(self) -> bool:
        return self.n >= MIN_OBSERVATIONS and any(self.density)

    def density_at(self, hour: float) -> float:
        """Density at a time of day, wrapping around midnight."""
        index = int((hour % HOURS_IN_DAY) / BIN_HOURS) % BINS
        return self.density[index]

    def peak_hour(self) -> float:
        """The merchant's busiest time — used to explain an alert."""
        best = max(range(BINS), key=lambda i: self.density[i])
        return best * BIN_HOURS


def _estimate(
    counts: list[float], n: int, percentile: float = THRESHOLD_PERCENTILE
) -> tuple[tuple[float, ...], float, float]:
    """Convolve binned observations with the kernel and calibrate a threshold."""
    bandwidth = bandwidth_hours(n)
    weights = _kernel_weights(_concentration(bandwidth))

    density = [0.0] * BINS
    for source, weight in enumerate(counts):
        if not weight:
            continue
        for offset in range(BINS):
            density[(source + offset) % BINS] += weight * weights[offset]

    # Normalise so the density integrates to 1 over the 24-hour circle.
    total = sum(density) * BIN_HOURS
    if total > 0:
        density = [value / total for value in density]

    # The threshold is a low percentile of the density *where this merchant
    # actually trades*, weighted by how often. Taking a percentile over the
    # bare grid instead would be dominated by the empty hours every merchant
    # has, and would sit near zero for everyone.
    observed = sorted(
        (density[i], counts[i]) for i in range(BINS) if counts[i]
    )
    threshold = 0.0
    if observed:
        cumulative = 0.0
        target = percentile * sum(weight for _, weight in observed)
        for value, weight in observed:
            cumulative += weight
            if cumulative >= target:
                threshold = value
                break

    return tuple(density), threshold, bandwidth


def _bin_times(times: list[float]) -> list[float]:
    counts = [0.0] * BINS
    for hour in times:
        counts[int((hour % HOURS_IN_DAY) / BIN_HOURS) % BINS] += 1.0
    return counts


def fit_time_density(
    session: Session,
    merchant_id: str,
    as_of: datetime,
    window_days: int,
    lag_days: int = 0,
) -> TimeDensity:
    """Estimate one merchant's trading-time density over the lagged window."""
    start, end = _window_bounds(as_of, window_days, lag_days)
    stamps = list(
        session.scalars(
            select(Transaction.occurred_at).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                Transaction.is_refund.is_(False),
            )
        )
    )
    times = [local_time_of_day(s) for s in stamps]
    density, threshold, bandwidth = _estimate(_bin_times(times), len(times))
    return TimeDensity(density, threshold, bandwidth, len(times))


def fit_cohort_time_density(
    session: Session,
    as_of: datetime,
    window_days: int,
    lag_days: int = 0,
    dimension: str = "mcc",
) -> dict[str, TimeDensity]:
    """Estimate each cohort's trading-time density.

    Members are equal-weighted: each member's binned counts are normalised to
    sum to one before they are pooled, so a high-volume merchant cannot define
    when its whole trade is considered open.

    This is the only estimate available for a merchant with no history of its
    own — a cold-start merchant has no density, but its trade does.
    """
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
            merchant_id, [0.0] * BINS
        )
        counts[int(local_time_of_day(occurred_at) / BIN_HOURS) % BINS] += 1.0

    cohorts: dict[str, TimeDensity] = {}
    for group, members in per_member.items():
        pooled = [0.0] * BINS
        voters = 0
        observations = 0
        for counts in members.values():
            total = sum(counts)
            if total < MIN_MEMBER_OBSERVATIONS:
                continue
            voters += 1
            observations += int(total)
            for index in range(BINS):
                pooled[index] += counts[index] / total

        if voters < MIN_COHORT_MERCHANTS:
            cohorts[group] = TimeDensity(tuple([0.0] * BINS), 0.0, 0.0, 0)
            continue

        density, threshold, bandwidth = _estimate(
            pooled, observations, COHORT_THRESHOLD_PERCENTILE
        )
        cohorts[group] = TimeDensity(density, threshold, bandwidth, observations)
    return cohorts


def time_is_unusual(hour: float, density: TimeDensity) -> bool:
    """Whether this time of day sits in the tail of the merchant's pattern."""
    if not density.usable or density.threshold <= 0:
        return False
    return density.density_at(hour) < density.threshold
