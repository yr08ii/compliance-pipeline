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
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from compliance.models import Merchant, Transaction

# Merchants keep Hong Kong hours, so history is generated in HKT. Building
# it in UTC would make every '9am to 8pm' shop a night trader straddling
# midnight once the detectors read local time.
HKT = timezone(timedelta(hours=8))

# Ground truth: what each generated merchant is meant to represent.
STEADY = "STEADY"  # mature, well-behaved — must NOT be flagged
SPIKE = "SPIKE"  # mature, single huge ticket today — MUST be flagged
FIXED = "FIXED"  # mature, one fixed price — degenerate baseline
NEWBIE = "NEWBIE"  # too little history — belongs in Lane B
RAMP = "RAMP"  # grows a few percent daily — invisible to a self-baseline
PEER_OUT = "PEEROUT"  # stable for itself, far above its cohort
FLOOD = "FLOOD"  # ordinary tickets, but a sudden burst of them
BURST = "BURST"  # ordinary daily total, all of it in minutes
NIGHT = "NIGHT"  # a grocer trading at 3am, when grocers do not
TOURIST = "TOURIST"  # a residential shop suddenly all foreign cards


@dataclass(frozen=True)
class MerchantSpec:
    merchant_id: str
    mcc: str
    subdistrict: str
    typical_ticket: float
    spread: float
    txns_per_day: int
    history_days: int
    daily_growth: float = 0.0
    # Operating window, independent of volume: a busier shop does not
    # thereby stay open later.
    open_hour: int = 9
    close_hour: int = 20


# Cohort members give each MCC enough merchants for a peer baseline; without
# them every cohort has one member and peer comparison cannot run.
SPECS = (
    MerchantSpec(STEADY, "5411", "Mong Kok", 120.0, 25.0, 6, 90),
    MerchantSpec("GROCER2", "5411", "Mong Kok", 95.0, 20.0, 5, 90),
    MerchantSpec("GROCER3", "5411", "Sham Shui Po", 140.0, 30.0, 5, 90),
    MerchantSpec("GROCER4", "5411", "Central", 110.0, 22.0, 6, 90),
    # Its own history is perfectly stable, so a self-baseline never fires.
    # Only the cohort fence can see that it is an outlier for a grocer.
    MerchantSpec(PEER_OUT, "5411", "Central", 4_200.0, 60.0, 5, 90),
    MerchantSpec(SPIKE, "5944", "Central", 3000.0, 400.0, 4, 90),
    MerchantSpec("JEWEL2", "5944", "Tsim Sha Tsui", 2600.0, 350.0, 4, 90),
    MerchantSpec("JEWEL3", "5944", "Mong Kok", 3400.0, 450.0, 4, 90),
    MerchantSpec(FIXED, "5814", "Sham Shui Po", 38.0, 0.0, 8, 90),
    # A cold-start grocer whose tickets are ordinary, plus one outrageous
    # ticket on the scored day. It has no baseline of its own, and its
    # median is unmoved by one large sale, so ONLY the transaction-level
    # peer test can catch it.
    MerchantSpec(NEWBIE, "5411", "Tsim Sha Tsui", 110.0, 20.0, 3, 4),
    # 4% a day compounds to ~10x over 60 days, yet no single day is an
    # outlier against its own trailing window.
    MerchantSpec(RAMP, "5814", "Central", 60.0, 12.0, 5, 90, daily_growth=0.04),
    # Every ticket is unremarkable; only the COUNT gives it away.
    MerchantSpec(FLOOD, "5411", "Mong Kok", 115.0, 20.0, 5, 90),
    # Same number of transactions as any day, but crammed into one
    # hour. Daily volume looks normal; only the rate exposes it.
    MerchantSpec(BURST, "5944", "Central", 2900.0, 300.0, 12, 90),
    # Trades at an hour its whole trade is shut. Its own pattern would allow
    # it; only the cohort's hours say otherwise.
    MerchantSpec(NIGHT, "5411", "Tin Shui Wai", 110.0, 20.0, 4, 90),
    # A residential district is local-card territory. This one goes all
    # foreign on the scored day.
    MerchantSpec(TOURIST, "5411", "Tin Shui Wai", 100.0, 18.0, 5, 90),
    MerchantSpec("LOCAL2", "5411", "Tin Shui Wai", 95.0, 15.0, 5, 90),
    MerchantSpec("LOCAL3", "5411", "Tin Shui Wai", 105.0, 17.0, 5, 90),
)

# The injected anomaly: a ticket far outside SPIKE's own normal, on the day
# being scored. Large enough that a correct detector cannot miss it.
SPIKE_MULTIPLE = 25.0

# `day` counts backwards from as_of, so the scored day — the completed day a
# run evaluates — is one step back.
SCORED_OFFSET = 1


def _hour_in_window(spec: MerchantSpec, n: int, per_day: int) -> float:
    """Spread the day's transactions across the merchant's operating window,
    so the window is a property of the business rather than of its volume."""
    span = (spec.close_hour - spec.open_hour) % 24 or 24
    return (spec.open_hour + span * (n / max(per_day, 1))) % 24


def _ticket(rng: random.Random, spec: MerchantSpec) -> float:
    if spec.spread == 0:
        return spec.typical_ticket
    # Right-skewed: most tickets near typical, occasional larger ones — the
    # shape that makes mean/stdev baselines drift and robust ones hold.
    value = rng.lognormvariate(0.0, 0.35) * spec.typical_ticket
    return round(max(value, 1.0), 2)


def generate_history(session: Session, *, as_of: datetime, seed: int = 7) -> None:
    """Populate merchants and their transaction history up to `as_of`.

    Injected anomalies land on the **scored day** — `as_of` minus one — because
    that is the completed day a run evaluates. Putting them on `as_of` itself
    would place them outside the scored window, where no detector looks.
    """
    rng = random.Random(seed)
    counter = 0
    # Anchor to local midnight, so an hour in a spec is that hour in HK.
    # Converting the instant alone is not enough: midnight UTC is 08:00 HKT,
    # and every generated hour would stack on top of that offset.
    as_of = (as_of.astimezone(HKT) if as_of.tzinfo else as_of.replace(tzinfo=HKT)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    for spec in SPECS:
        session.add(
            Merchant(
                merchant_id=spec.merchant_id,
                mcc=spec.mcc,
                registered_address=spec.subdistrict,
                merchant_subdistrict=spec.subdistrict,
                onboarded_at=as_of - timedelta(days=spec.history_days),
            )
        )

        for day in range(spec.history_days, -1, -1):
            # A burst of ordinary-sized tickets on the scored day.
            per_day = spec.txns_per_day
            if spec.merchant_id == FLOOD and day == SCORED_OFFSET:
                per_day = spec.txns_per_day * 12
            occurred_day = as_of - timedelta(days=day)
            # `day` counts backwards, so elapsed time is history_days - day.
            growth = (1.0 + spec.daily_growth) ** (spec.history_days - day)

            for n in range(per_day):
                counter += 1
                amount = round(_ticket(rng, spec) * growth, 2)

                # Inject the anomaly on the scored day only.
                if spec.merchant_id == SPIKE and day == SCORED_OFFSET and n == 0:
                    amount = round(spec.typical_ticket * SPIKE_MULTIPLE, 2)
                if spec.merchant_id == NEWBIE and day == SCORED_OFFSET and n == 0:
                    amount = 48_000.0

                session.add(
                    Transaction(
                        source_txn_id=f"SYN{counter:07d}",
                        merchant_id=spec.merchant_id,
                        total_amount=amount,
                        net_amount=round(amount * 0.97, 2),
                        occurred_at=occurred_day
                        + (
                            timedelta(hours=10, minutes=n * 4)
                            if spec.merchant_id == BURST and day == SCORED_OFFSET
                            else timedelta(hours=3)
                            if spec.merchant_id == NIGHT and day == SCORED_OFFSET
                            else timedelta(hours=_hour_in_window(spec, n, per_day))
                        ),
                        is_refund=False,
                        card_issuing_country=(
                            "US" if spec.merchant_id == TOURIST and day == SCORED_OFFSET else "HK"
                        ),
                        card_bin="457896",
                        geo=spec.subdistrict,
                    )
                )
