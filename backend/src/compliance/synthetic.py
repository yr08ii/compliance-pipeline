"""Synthetic merchant history with known ground truth.

We generate the data, so we know which merchants were given an anomaly. That
makes it the only dataset where detection quality can be measured exactly —
and it lets the detectors be exercised long before real data is involved.

What it cannot tell us: whether detection works on real merchants. Synthetic
data contains exactly the patterns we thought to inject, so results here are a
lower bound on difficulty, not an estimate of production performance. Shadow
mode against real data remains the real test.

Deterministic for a given seed, so tests and reruns are reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from compliance.models import Merchant, Transaction

# Ground truth: what each generated merchant is meant to represent.
STEADY = "STEADY"  # mature, well-behaved — must NOT be flagged
SPIKE = "SPIKE"  # mature, single huge ticket today — MUST be flagged
FIXED = "FIXED"  # mature, one fixed price — degenerate baseline
NEWBIE = "NEWBIE"  # too little history — belongs in Lane B


@dataclass(frozen=True)
class MerchantSpec:
    merchant_id: str
    mcc: str
    subdistrict: str
    typical_ticket: float
    spread: float
    txns_per_day: int
    history_days: int


SPECS = (
    MerchantSpec(STEADY, "5411", "Mong Kok", 120.0, 25.0, 6, 90),
    MerchantSpec(SPIKE, "5944", "Central", 3000.0, 400.0, 4, 90),
    MerchantSpec(FIXED, "5814", "Sham Shui Po", 38.0, 0.0, 8, 90),
    MerchantSpec(NEWBIE, "5732", "Tsim Sha Tsui", 800.0, 150.0, 3, 4),
)

# The injected anomaly: a ticket far outside SPIKE's own normal, on the day
# being scored. Large enough that a correct detector cannot miss it.
SPIKE_MULTIPLE = 25.0


def _ticket(rng: random.Random, spec: MerchantSpec) -> float:
    if spec.spread == 0:
        return spec.typical_ticket
    # Right-skewed: most tickets near typical, occasional larger ones — the
    # shape that makes mean/stdev baselines drift and robust ones hold.
    value = rng.lognormvariate(0.0, 0.35) * spec.typical_ticket
    return round(max(value, 1.0), 2)


def generate_history(session: Session, *, as_of: datetime, seed: int = 7) -> None:
    """Populate merchants and their transaction history up to `as_of`.

    History spans [as_of - history_days, as_of], so the day at `as_of` is the
    day the pipeline will score.
    """
    rng = random.Random(seed)
    counter = 0

    for spec in SPECS:
        session.add(
            Merchant(
                merchant_id=spec.merchant_id,
                mcc=spec.mcc,
                registered_address=spec.subdistrict,
                onboarded_at=as_of - timedelta(days=spec.history_days),
            )
        )

        for day in range(spec.history_days, -1, -1):
            occurred_day = as_of - timedelta(days=day)
            for n in range(spec.txns_per_day):
                counter += 1
                amount = _ticket(rng, spec)

                # Inject the anomaly on the scored day only.
                if spec.merchant_id == SPIKE and day == 0 and n == 0:
                    amount = round(spec.typical_ticket * SPIKE_MULTIPLE, 2)

                session.add(
                    Transaction(
                        source_txn_id=f"SYN{counter:07d}",
                        merchant_id=spec.merchant_id,
                        total_amount=amount,
                        net_amount=round(amount * 0.97, 2),
                        occurred_at=occurred_day + timedelta(hours=9 + n),
                        is_refund=False,
                        card_bin="457896",
                        geo=spec.subdistrict,
                    )
                )
