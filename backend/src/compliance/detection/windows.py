"""Fitting merchant baselines from the local transaction store.

Baselines are recomputed on every nightly run from a rolling window, not
refreshed periodically: a merchant that legitimately grows would otherwise be
scored against a stale baseline — and flagged for it — until the next refresh.

Every merchant is fitted in one pass over the window rather than fetched
individually, because per-merchant queries scale with the merchant count and
cannot build the peer cohorts that Family A's peer baselines need.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.baselines import Baseline, fit_baseline
from compliance.models import Merchant, Transaction


def _window_start(as_of: datetime, window_days: int) -> datetime:
    return as_of - timedelta(days=window_days)


def amounts_in_window(
    session: Session, merchant_id: str, as_of: datetime, window_days: int
) -> list[float]:
    """Gross transaction amounts for one merchant over [as_of - window, as_of).

    `as_of` is exclusive so a nightly run scoring the prior day cannot leak the
    day being scored into the baseline it is scored against.

    Refunds are excluded: they are value moving the other way, and mixing them
    into the ticket baseline distorts the merchant's normal. The refund-ratio
    rule covers them instead.
    """
    stmt = (
        select(Transaction.total_amount)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at >= _window_start(as_of, window_days),
            Transaction.occurred_at < as_of,
            Transaction.is_refund.is_(False),
        )
        .order_by(Transaction.occurred_at)
    )
    return list(session.scalars(stmt))


def fit_all_baselines(
    session: Session,
    as_of: datetime,
    window_days: int,
    *,
    min_observations: int,
) -> dict[str, Baseline]:
    """Fit an amount baseline for every merchant in one pass.

    Merchants with too little history are returned with an explicitly unusable
    baseline rather than omitted — the router needs to see them to send them
    down Lane B, and a silently missing merchant is a merchant nobody monitors.
    """
    rows = session.execute(
        select(Transaction.merchant_id, Transaction.total_amount)
        .where(
            Transaction.occurred_at >= _window_start(as_of, window_days),
            Transaction.occurred_at < as_of,
            Transaction.is_refund.is_(False),
        )
        .order_by(Transaction.merchant_id, Transaction.occurred_at)
    )

    by_merchant: dict[str, list[float]] = {}
    for merchant_id, amount in rows:
        by_merchant.setdefault(merchant_id, []).append(amount)

    return {
        merchant_id: fit_baseline(
            by_merchant.get(merchant_id, []), min_observations=min_observations
        )
        for merchant_id in session.scalars(select(Merchant.merchant_id))
    }
