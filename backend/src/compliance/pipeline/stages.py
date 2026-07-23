"""Nightly pipeline stages.

Stages 2-4 now run Family A's robust amount baseline. Stage 1 (pull) is still
stood in for by generated data, and Family B/C detectors are not built yet.

Everything here is deterministic: same input, same output, every run. That is
an audit requirement, not a preference.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.baselines import DispersionMethod, score_value
from compliance.detection.windows import (
    _window_bounds,
    fit_all_baselines,
    fit_peer_baselines,
    fit_trend,
    quarantined_days,
)
from compliance.models import Alert, Merchant, MerchantProfile, Transaction

# Rolling window and minimum history for a usable baseline. Starting points —
# the maturity threshold is meant to be set empirically against the real
# merchant base, not guessed. Config, not constants, once that calibration runs.
WINDOW_DAYS = 30
MIN_OBSERVATIONS = 12
# Maturity is count AND elapsed days: a merchant trading heavily for a few days
# can clear the count while having no weekly shape to be a baseline.
MIN_SPAN_DAYS = 14
# Hold the baseline window back from the present. Not a tuning knob: a
# disposition takes days to arrive, so this is the interval in which analysts
# can still rule on activity before it is absorbed into the baseline.
LAG_DAYS = 7
# Short vs long level comparison — the only thing that sees a slow ramp.
TREND_SHORT_DAYS = 7
TREND_LONG_DAYS = 90

AMOUNT_DETECTOR = "amount_vs_own_baseline"
PEER_DETECTOR = "amount_vs_mcc_peers"
TREND_DETECTOR = "level_shift_ramp"


def profile(session: Session, *, as_of: datetime) -> None:
    """Stage 2: fit each merchant's rolling baseline and persist it.

    Idempotent: clears prior rows so repeated runs cannot accumulate duplicates
    and drift the output.

    The fitted parameters are stored, not just the alert-time values, so an
    auditor can see what baseline was in force on a given night.
    """
    for stale in session.scalars(select(MerchantProfile)):
        session.delete(stale)
    session.flush()

    baselines = fit_all_baselines(
        session,
        as_of,
        WINDOW_DAYS,
        min_observations=MIN_OBSERVATIONS,
        min_span_days=MIN_SPAN_DAYS,
        lag_days=LAG_DAYS,
    )
    peers = fit_peer_baselines(session, as_of, WINDOW_DAYS, lag_days=LAG_DAYS)
    quarantined = quarantined_days(session)
    window_start, window_end = _window_bounds(as_of, WINDOW_DAYS, LAG_DAYS)

    for merchant_id, baseline in baselines.items():
        merchant = session.get(Merchant, merchant_id)
        peer = peers.get(merchant.mcc) if merchant else None
        trend = fit_trend(
            session,
            merchant_id,
            as_of,
            short_days=TREND_SHORT_DAYS,
            long_days=TREND_LONG_DAYS,
            lag_days=LAG_DAYS,
        )
        excluded = sum(1 for m, _ in quarantined if m == merchant_id)

        session.add(
            MerchantProfile(
                merchant_id=merchant_id,
                as_of=as_of,
                metrics={
                    "baseline_center": baseline.center,
                    "baseline_dispersion": baseline.dispersion,
                    "baseline_method": baseline.method.value,
                    "baseline_n": baseline.n,
                    "baseline_usable": baseline.usable,
                    # Provenance: which data formed this baseline, and what was
                    # withheld from it. Surfaced on the baseline dashboard.
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "window_days": WINDOW_DAYS,
                    "lag_days": LAG_DAYS,
                    "min_observations": MIN_OBSERVATIONS,
                    "min_span_days": MIN_SPAN_DAYS,
                    "quarantined_days": excluded,
                    "peer_mcc": merchant.mcc if merchant else None,
                    "peer_q1": peer.q1 if peer else None,
                    "peer_q3": peer.q3 if peer else None,
                    "peer_fence": peer.upper_fence() if peer and peer.usable else None,
                    "peer_merchants": peer.n_merchants if peer else 0,
                    "peer_usable": bool(peer and peer.usable),
                    "trend_ratio": round(trend.ratio, 3),
                    "trend_is_ramp": trend.is_ramp,
                },
            )
        )
    session.flush()


def route(session: Session) -> dict[str, str]:
    """Stage 3: Lane A if the merchant has a usable baseline, else Lane B.

    Routing on baseline usability rather than a raw transaction count means a
    merchant only enters Lane A when there is genuinely something to score it
    against — including the fixed-price case, whose history is long but has no
    measurable spread.
    """
    lanes: dict[str, str] = {}
    for p in session.scalars(select(MerchantProfile)):
        lane = "A" if p.metrics.get("baseline_usable") else "B"
        lanes[p.merchant_id] = lane
        merchant = session.get(Merchant, p.merchant_id)
        if merchant is not None:
            merchant.lane = lane
    return lanes


def _scored_day_amounts(session: Session, merchant_id: str, as_of: datetime) -> list[float]:
    """Gross amounts on the day being scored (`as_of` onward)."""
    return list(
        session.scalars(
            select(Transaction.total_amount).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= as_of,
                Transaction.is_refund.is_(False),
            )
        )
    )


def detect(session: Session, lanes: dict[str, str], *, as_of: datetime) -> list[dict]:
    """Stage 4: score the day's tickets against each Lane A merchant's baseline.

    A merchant's daily output is its single most extreme breach, not one hit per
    transaction: the queue is a list of merchants to investigate, and ten hits
    for one merchant is still one investigation.

    Lane B merchants are skipped here by design — they have no baseline, and
    inventing one would flag merchants for being new. Their static ruleset is
    Family B, not yet built.
    """
    from compliance.detection.baselines import Baseline

    hits: list[dict] = []

    for p in session.scalars(select(MerchantProfile)):
        # Peer and trend detectors run in BOTH lanes. A cohort fence needs no
        # history of the merchant's own, so it is the only Family A signal a
        # cold-start merchant can be scored against — and the only one immune
        # to that merchant's own baseline being contaminated.
        day_amounts = _scored_day_amounts(session, p.merchant_id, as_of)

        if p.metrics.get("peer_usable") and day_amounts:
            fence = p.metrics["peer_fence"]
            worst_peer = max(day_amounts)
            if worst_peer > fence:
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": PEER_DETECTOR,
                    "sub_score": min((worst_peer / fence) / 10.0, 1.0),
                    "feature": {
                        "feature_name": "ticket_vs_mcc_peers",
                        "merchant_value": worst_peer,
                        "baseline_value": fence,
                        "deviation": round(worst_peer / fence, 2),
                    },
                })

        if p.metrics.get("trend_is_ramp"):
            ratio = p.metrics["trend_ratio"]
            hits.append({
                "merchant_id": p.merchant_id,
                "lane": lanes.get(p.merchant_id, "B"),
                "detector": TREND_DETECTOR,
                "sub_score": min(ratio / 10.0, 1.0),
                "feature": {
                    "feature_name": "level_shift_7d_vs_90d",
                    "merchant_value": p.metrics["baseline_center"],
                    "baseline_value": p.metrics["baseline_center"] / ratio if ratio else 0.0,
                    "deviation": ratio,
                },
            })

        if lanes.get(p.merchant_id) != "A":
            continue

        baseline = Baseline(
            center=p.metrics["baseline_center"],
            dispersion=p.metrics["baseline_dispersion"],
            method=DispersionMethod(p.metrics["baseline_method"]),
            n=p.metrics["baseline_n"],
        )
        if not baseline.usable:
            continue

        worst = None
        breaches = 0
        for amount in _scored_day_amounts(session, p.merchant_id, as_of):
            score = score_value(amount, baseline)
            if not score.is_outlier:
                continue
            breaches += 1
            if worst is None or score.deviation > worst.deviation:
                worst = score

        if worst is None:
            continue

        hits.append(
            {
                "merchant_id": p.merchant_id,
                "lane": "A",
                "detector": AMOUNT_DETECTOR,
                # Squash the unbounded z-score into [0,1] so families stay
                # comparable when Family B and C join the blend.
                "sub_score": min(worst.deviation / 10.0, 1.0),
                "feature": {
                    "feature_name": "ticket_amount",
                    "merchant_value": worst.value,
                    "baseline_value": baseline.center,
                    "deviation": round(worst.deviation, 2),
                },
                "breaches": breaches,
            }
        )

    return hits


def score_and_rank(session: Session, hits: list[dict]) -> list[Alert]:
    """Stage 5: one alert per flagged merchant, ranked, with its snapshot.

    The feature snapshot is written once and never recomputed — training on
    features rebuilt later would leak the future into the model.
    """
    ordered = sorted(hits, key=lambda h: h["sub_score"], reverse=True)
    alerts: list[Alert] = []
    for rank, h in enumerate(ordered, start=1):
        alert = Alert(
            merchant_id=h["merchant_id"],
            lane=h["lane"],
            blended_score=h["sub_score"],
            rank=rank,
            triggering_detectors=[
                {"detector": h["detector"], "sub_score": h["sub_score"]}
            ],
            feature_snapshot=[h["feature"]],
        )
        session.add(alert)
        alerts.append(alert)
    return alerts
