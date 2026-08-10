"""Whose cards a merchant accepts.

Time-of-day lives in `timedensity`, which estimates a proper circular density
rather than a smoothed hour histogram.

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

from compliance.detection.windows import (
    HOME_COUNTRY,
    _window_bounds,
    has_card_origin,
    settled_sale,
)
from compliance.models import Merchant, Transaction

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
class OriginMix:
    """A merchant's historical distribution of *overseas* card issuers.

    Home cards are excluded. Hong Kong is the overwhelming majority for almost
    every merchant here, and while it sat in the distribution it set the scale:
    an unfamiliar overseas country had to overcome a bucket holding most of the
    mass before it registered, and two merchants with identical overseas
    patterns scored differently according to how much domestic trade sat
    underneath. The question this detector exists to ask is which *foreign*
    countries are paying and whether that has changed, so the home country is
    not in the sample.

    Wallet rails are excluded too, for the plainer reason that they are not
    cards and have no issuer.

    The cost is coverage: measured on overseas cards alone, far fewer merchants
    clear `MIN_OBSERVATIONS`, and one that does not is skipped rather than
    guessed at.
    """

    counts: dict[str, int]
    n: int

    @property
    def usable(self) -> bool:
        return self.n >= MIN_OBSERVATIONS

    def share(self, origin: str) -> float:
        return self.counts.get(origin, 0) / self.n if self.n else 0.0


def fit_origin_mix(
    session: Session, merchant_id: str, as_of: datetime, window_days: int,
    lag_days: int = 0,
) -> OriginMix:
    """Count the overseas issuing countries the merchant normally sees.

    Taken straight from `card_issuing_country`; no BIN lookup is needed. Home
    cards and wallet rails are both outside the sample — see `OriginMix`.
    """
    start, end = _window_bounds(as_of, window_days, lag_days)
    origins = list(session.scalars(
        select(Transaction.card_issuing_country).where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            *settled_sale(),
            Transaction.card_issuing_country != HOME_COUNTRY,
            *has_card_origin(),
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
