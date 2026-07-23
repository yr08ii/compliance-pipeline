"""Robust per-merchant baselines (Family A, detector 3.1 — amount).

These are *fitted descriptive statistics*, not a trained model: given the same
window of history they always produce the same baseline. That determinism is an
audit requirement, so nothing here may depend on ordering, randomness, or
wall-clock time.

Transaction amounts are heavily right-skewed, so mean/standard-deviation
baselines get dragged by a handful of legitimate large sales and under-flag.
We use the median and the Median Absolute Deviation instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median

# Makes MAD comparable to a standard deviation under normality:
# 0.6745 is the 0.75 quantile of the standard normal.
CONSISTENCY_CONSTANT = 0.6745

# Scales the IQR onto the same footing as MAD when MAD collapses to zero.
# For a normal distribution IQR ~= 2 * 1.349 * MAD, so half the IQR is the
# comparable quantity.
IQR_TO_MAD = 0.5

MODERATE_BAND = 2.5
OUTLIER_BAND = 3.5


class DispersionMethod(str, Enum):
    """How the baseline's spread was measured, or why it could not be."""

    MAD = "mad"
    SCALED_IQR = "scaled_iqr"
    CONSTANT = "constant"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class Baseline:
    """A merchant's fitted amount baseline for one rolling window."""

    center: float
    dispersion: float
    method: DispersionMethod
    n: int

    @property
    def usable(self) -> bool:
        """Whether this baseline can score a value.

        Unusable baselines are not failures — they are the signal that the
        merchant belongs in Lane B (too little history) or needs a rule rather
        than a statistical score (a single fixed price).
        """
        return self.method in (DispersionMethod.MAD, DispersionMethod.SCALED_IQR)


@dataclass(frozen=True)
class Score:
    """The result of scoring one value against a baseline."""

    value: float
    deviation: float
    band: str

    @property
    def is_outlier(self) -> bool:
        return self.band == "outlier"


@dataclass(frozen=True)
class PeerBaseline:
    """A cohort's amount distribution (MCC, or MCC x subdistrict).

    A merchant can corrupt its own baseline by its own behaviour; it cannot
    corrupt its cohort's unless the whole cohort is complicit. That makes peer
    comparison the structural defence against self-contamination.
    """

    center: float
    dispersion: float
    q1: float
    q3: float
    n_merchants: int
    n_observations: int

    @property
    def iqr(self) -> float:
        return self.q3 - self.q1

    @property
    def usable(self) -> bool:
        """A handful of merchants is not a distribution."""
        return self.n_merchants >= MIN_COHORT_MERCHANTS and self.dispersion > 0

    def upper_fence(self, k: float = OUTLIER_BAND) -> float:
        """The amount at which a member becomes an outlier for its cohort.

        Built on the cohort's median and MAD rather than a Tukey IQR fence.
        The IQR has a 25% breakdown point, so in a small cohort a single
        deviant member lands inside the upper quartile and drags Q3 up far
        enough to cover itself — the contamination this detector exists to
        catch. Median/MAD tolerates up to half the cohort.
        """
        return self.center + k * self.dispersion / CONSISTENCY_CONSTANT


@dataclass(frozen=True)
class Trend:
    """Short-window level against long-window level.

    A trailing self-baseline cannot see a slow ramp: no single day is an
    outlier against its own recent history, yet the level multiplies over
    months. Comparing a short window to a long one makes the shift visible.
    """

    short_center: float
    long_center: float
    ratio: float
    is_ramp: bool


MIN_COHORT_MERCHANTS = 3
RAMP_RATIO = 1.75


def _quartiles(ordered: list[float]) -> tuple[float, float]:
    """Q1 and Q3 by the median-of-halves rule, excluding the middle value for
    odd-length input. Deterministic and dependency-free.

    Fewer than two values has no spread to measure, so both quartiles collapse
    to the same point and callers see an IQR of zero (i.e. unusable) rather
    than an exception.
    """
    n = len(ordered)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return ordered[0], ordered[0]
    half = n // 2
    lower = ordered[:half]
    upper = ordered[half + 1 :] if n % 2 else ordered[half:]
    return median(lower), median(upper)


def fit_peer_baseline(centers: list[float], *, n_merchants: int) -> PeerBaseline:
    """Fit a cohort distribution from its members' typical tickets.

    Takes one value per merchant, not pooled transactions: pooling weights
    members by transaction volume, letting a single high-volume merchant
    dominate its own cohort.
    """
    if not centers:
        return PeerBaseline(0.0, 0.0, 0.0, 0.0, n_merchants, 0)
    center = median(centers)
    dispersion = median([abs(c - center) for c in centers])
    q1, q3 = _quartiles(sorted(centers))
    return PeerBaseline(center, dispersion, q1, q3, n_merchants, len(centers))


def detect_ramp(short_center: float, long_center: float, *, ratio: float = RAMP_RATIO) -> Trend:
    """Compare a short-window level against a long-window one.

    Guards against a zero long-term level, which would make any activity look
    like an infinite ramp.
    """
    if long_center <= 0:
        return Trend(short_center, long_center, 0.0, False)
    observed = short_center / long_center
    return Trend(short_center, long_center, observed, observed >= ratio)


def fit_baseline(values: list[float], *, min_observations: int) -> Baseline:
    """Fit a robust baseline over one merchant's rolling window.

    Falls back through three levels of dispersion so a degenerate window can
    never produce a divide-by-zero score:
      MAD -> scaled IQR -> constant (unusable).
    """
    n = len(values)
    if n < min_observations:
        return Baseline(
            center=median(values) if values else 0.0,
            dispersion=0.0,
            method=DispersionMethod.INSUFFICIENT_DATA,
            n=n,
        )

    center = median(values)
    mad = median([abs(v - center) for v in values])
    if mad > 0:
        return Baseline(center, mad, DispersionMethod.MAD, n)

    # More than half the window sits on one value, so MAD collapsed. There may
    # still be real spread in the tails — measure it with the IQR instead.
    q1, q3 = _quartiles(sorted(values))
    iqr = q3 - q1
    if iqr > 0:
        return Baseline(center, iqr * IQR_TO_MAD, DispersionMethod.SCALED_IQR, n)

    # Genuinely constant history: every robust measure of spread is zero.
    # Scoring this would divide by zero and flag every transaction, so we
    # refuse and let a rule handle it.
    return Baseline(center, 0.0, DispersionMethod.CONSTANT, n)


def score_value(value: float, baseline: Baseline) -> Score:
    """Score a value as a modified z-score against a fitted baseline.

    Amount risk is one-sided: an unusually *small* ticket is not an AML signal,
    so only upward deviations reach the outlier band.
    """
    if not baseline.usable:
        raise ValueError(
            f"cannot score against a {baseline.method.value} baseline; "
            "route this merchant to rules instead"
        )

    deviation = CONSISTENCY_CONSTANT * (value - baseline.center) / baseline.dispersion

    if deviation > OUTLIER_BAND:
        band = "outlier"
    elif deviation > MODERATE_BAND:
        band = "moderate"
    else:
        band = "normal"

    return Score(value=value, deviation=deviation, band=band)
