"""Six deterministic stub stages. Real logic arrives in later plans.

Stage 1 (pull) and Stage 2 (profile) are represented by the seed data and a
simple in-place profile computation; this skeleton exercises the shape, not
the detection quality.
"""
from statistics import mean
from sqlalchemy import select
from sqlalchemy.orm import Session
from compliance.models import Merchant, Transaction, MerchantProfile, Alert


def profile(session: Session) -> None:
    """Stage 2: compute a trivial per-merchant daily-volume profile.

    Idempotent: clears any prior profile rows first so repeated pipeline runs
    (e.g. within the same session) don't accumulate duplicates and drift the
    output non-deterministically.
    """
    for stale in session.scalars(select(MerchantProfile)):
        session.delete(stale)
    session.flush()
    for m in session.scalars(select(Merchant)):
        amounts = [t.amount for t in session.scalars(
            select(Transaction).where(Transaction.merchant_id == m.merchant_id))]
        daily_volume = sum(amounts)
        avg_ticket = mean(amounts) if amounts else 0.0
        session.add(MerchantProfile(
            merchant_id=m.merchant_id,
            as_of=max((t.occurred_at for t in session.scalars(
                select(Transaction).where(Transaction.merchant_id == m.merchant_id))),
                default=None),
            metrics={"daily_volume": daily_volume, "avg_ticket": avg_ticket,
                     "txn_count": len(amounts)},
        ))


def route(session: Session) -> dict[str, str]:
    """Stage 3: lane by txn count. <5 txns -> Lane B, else Lane A. Stub threshold."""
    lanes: dict[str, str] = {}
    for p in session.scalars(select(MerchantProfile)):
        lane = "A" if p.metrics.get("txn_count", 0) >= 5 else "B"
        lanes[p.merchant_id] = lane
        m = session.get(Merchant, p.merchant_id)
        if m is not None:
            m.lane = lane
    return lanes


def detect(session: Session, lanes: dict[str, str]) -> list[dict]:
    """Stage 4: one stub detector — flag daily_volume above a flat baseline of 8000."""
    baseline = 8000.0
    hits: list[dict] = []
    for p in session.scalars(select(MerchantProfile)):
        volume = p.metrics.get("daily_volume", 0.0)
        if volume > baseline:
            deviation = round(volume / baseline, 2)
            hits.append({
                "merchant_id": p.merchant_id,
                "lane": lanes.get(p.merchant_id, "B"),
                "detector": "daily_volume_over_baseline",
                "sub_score": min(deviation / 10.0, 1.0),
                "feature": {"feature_name": "daily_volume", "merchant_value": volume,
                            "baseline_value": baseline, "deviation": deviation},
            })
    return hits


def score_and_rank(session: Session, hits: list[dict]) -> list[Alert]:
    """Stage 5: one alert per hit, ranked by sub_score desc, with feature snapshot."""
    ordered = sorted(hits, key=lambda h: h["sub_score"], reverse=True)
    alerts: list[Alert] = []
    for rank, h in enumerate(ordered, start=1):
        alert = Alert(
            merchant_id=h["merchant_id"],
            lane=h["lane"],
            blended_score=h["sub_score"],
            rank=rank,
            triggering_detectors=[{"detector": h["detector"], "sub_score": h["sub_score"]}],
            feature_snapshot=[h["feature"]],
        )
        session.add(alert)
        alerts.append(alert)
    return alerts
