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

# Family B — typology shapes. Each is individually unremarkable per
# transaction, which is the point: no baseline can see any of them.
STRUCT = "STRUCT"  # several tickets parked just under the reporting threshold
REFUNDER = "REFUNDER"  # takes money and gives most of it straight back
DORMANT = "DORMANT"  # silent for months, then a full day's trade at once
DECLINER = "DECLINER"  # a terminal being used to test card numbers

# Family C — ring shapes. These need two or more merchants to exist at all.
RING_A = "RINGA"  # four storefronts behind one business registration
RING_B = "RINGB"
RING_C = "RINGC"
RING_D = "RINGD"
FAR_A = "FARA"  # two merchants a card cannot reach in the time between
FAR_B = "FARB"


# Real MCC wording, so a demo run exercises the same header the real data
# fills in rather than showing a bare code.
MCC_NAMES = {
    "5411": "Grocery Stores and Supermarkets",
    "5944": "Jewelry, Watches, Clocks and Silverware Stores",
    "5814": "Fast Food Restaurants",
    "5813": "Drinking Places (Alcoholic Beverages)",
    "5812": "Eating Places and Restaurants",
    "5732": "Electronics Stores",
    "5309": "Duty Free Stores",
    "6011": "Financial Institutions - Automated Cash Disbursements",
    "5541": "Service Stations",
}


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
    # --- Family B ------------------------------------------------------
    # An electronics shop whose ordinary ticket is far below the reporting
    # threshold, so a cluster just underneath it cannot be its normal trade.
    MerchantSpec(STRUCT, "5732", "Mong Kok", 600.0, 120.0, 6, 90),
    MerchantSpec("ELEC2", "5732", "Sham Shui Po", 700.0, 140.0, 6, 90),
    MerchantSpec("ELEC3", "5732", "Wan Chai", 550.0, 110.0, 5, 90),
    MerchantSpec(REFUNDER, "5812", "Causeway Bay", 900.0, 180.0, 8, 90),
    # Trades normally, then stops dead, then returns all at once. The gap is
    # generated by skipping days, not by a shorter history.
    MerchantSpec(DORMANT, "5812", "Wan Chai", 400.0, 80.0, 6, 90),
    # Enough authorisations a day that a decline ratio is a ratio rather than
    # a bad afternoon — the rule's own minimum-attempts guard requires it.
    MerchantSpec(DECLINER, "5732", "Kwun Tong", 300.0, 60.0, 30, 90),
    # --- Family C ------------------------------------------------------
    # Four "independent" shops sharing one business registration. Four rather
    # than three so one card touching all of them exceeds the shipped
    # three-branches-a-day limit instead of sitting exactly on it.
    MerchantSpec(RING_A, "5814", "Tsim Sha Tsui", 85.0, 15.0, 6, 90),
    MerchantSpec(RING_B, "5814", "Mong Kok", 90.0, 16.0, 6, 90),
    MerchantSpec(RING_C, "5814", "Jordan", 88.0, 15.0, 6, 90),
    MerchantSpec(RING_D, "5814", "Causeway Bay", 92.0, 16.0, 6, 90),
    # Opposite ends of the territory: Tung Chung to Sai Kung is ~40 km, which
    # no card can cross in the minutes the generator puts between them.
    MerchantSpec(FAR_A, "5541", "Tung Chung", 400.0, 70.0, 5, 90),
    MerchantSpec(FAR_B, "5541", "Sai Kung", 420.0, 75.0, 5, 90),
)

# Merchants sharing one business registration hash — the ring's ground truth.
RING_MEMBERS = frozenset({RING_A, RING_B, RING_C, RING_D})
SHARED_BR_HASH = "brhash-shell-ring-001"
# The agent whose whole book is the ring, for agent-concentration.
RING_AGENT = "AGT-RING"
DEFAULT_AGENT = "AGT-NORMAL"

# The reporting threshold the structuring merchant parks its tickets under.
# Matches the rule's shipped default, so the demo exercises the shipped
# configuration rather than a value chosen to make it pass.
STRUCTURING_THRESHOLD = 8000.0
# How long DORMANT goes quiet before returning.
DORMANT_GAP_DAYS = 60

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


# Subdistrict to district, so the geo table can resolve a coordinate. The
# generator uses title-case names; the geo lookup folds case, so these match
# the real extract's spellings without having to duplicate them.
DISTRICT_OF = {
    "Mong Kok": "Yau tsim mong",
    "Tsim Sha Tsui": "Yau tsim mong",
    "Jordan": "Yau tsim mong",
    "Central": "Central and western",
    "Sham Shui Po": "Sham shui po",
    "Tin Shui Wai": "Yuen long",
    "Causeway Bay": "Wan chai",
    "Wan Chai": "Wan chai",
    "Kwun Tong": "Kwun tong",
    "Tung Chung": "Islands",
    "Sai Kung": "Sai kung",
}


def _add_card_linked_activity(
    session: Session, as_of: datetime, counter: int
) -> int:
    """Card activity that only Family C can see.

    Two shapes, both needing more than one merchant to exist at all:

    * One card touching all three shell-ring storefronts in a single day —
      structuring across branches, which is invisible at any one of them.
    * One card at Tung Chung and then Sai Kung twenty minutes apart. Those are
      roughly 40 km apart, so the implied speed is far past anything the
      territory allows and at least one of the two acceptances did not happen
      as recorded.
    """
    scored_day = as_of - timedelta(days=SCORED_OFFSET)

    for n, merchant_id in enumerate(sorted(RING_MEMBERS)):
        counter += 1
        session.add(
            Transaction(
                source_txn_id=f"RING{counter:07d}",
                merchant_id=merchant_id,
                total_amount=1_800.0,
                net_amount=1_746.0,
                occurred_at=scored_day + timedelta(hours=12, minutes=25 * n),
                is_refund=False,
                transaction_status="SUCCESS",
                card_type="VISA",
                card_issuing_country="HK",
                hashed_pan="panhash-ring-card-001",
                geo="ring",
            )
        )

    for n, merchant_id in enumerate((FAR_A, FAR_B)):
        counter += 1
        session.add(
            Transaction(
                source_txn_id=f"GEO{counter:07d}",
                merchant_id=merchant_id,
                total_amount=520.0,
                net_amount=504.4,
                occurred_at=scored_day + timedelta(hours=14, minutes=20 * n),
                is_refund=False,
                transaction_status="SUCCESS",
                card_type="VISA",
                card_issuing_country="HK",
                hashed_pan="panhash-teleport-002",
                geo="geo",
            )
        )

    return counter


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
        in_ring = spec.merchant_id in RING_MEMBERS
        session.add(
            Merchant(
                merchant_id=spec.merchant_id,
                mcc=spec.mcc,
                mcc_description=MCC_NAMES.get(spec.mcc),
                registered_address=spec.subdistrict,
                merchant_subdistrict=spec.subdistrict,
                merchant_district=DISTRICT_OF.get(spec.subdistrict),
                # The ring shares one registration hash; everyone else gets a
                # distinct one, so the equality join has something to reject
                # as well as something to find.
                hashed_br_number=(
                    SHARED_BR_HASH if in_ring else f"brhash-{spec.merchant_id}"
                ),
                agent_id=RING_AGENT if in_ring else DEFAULT_AGENT,
                onboarded_at=as_of - timedelta(days=spec.history_days),
            )
        )

        for day in range(spec.history_days, -1, -1):
            # A burst of ordinary-sized tickets on the scored day.
            per_day = spec.txns_per_day
            if spec.merchant_id == FLOOD and day == SCORED_OFFSET:
                per_day = spec.txns_per_day * 12
            # DORMANT falls silent for a stretch ending just before the scored
            # day, then returns with a full day's trade at once.
            if spec.merchant_id == DORMANT:
                if SCORED_OFFSET < day <= SCORED_OFFSET + DORMANT_GAP_DAYS:
                    continue
                if day == SCORED_OFFSET:
                    per_day = 14
            occurred_day = as_of - timedelta(days=day)
            # `day` counts backwards, so elapsed time is history_days - day.
            growth = (1.0 + spec.daily_growth) ** (spec.history_days - day)

            scored = day == SCORED_OFFSET

            for n in range(per_day):
                counter += 1
                amount = round(_ticket(rng, spec) * growth, 2)
                is_refund = False
                status = "SUCCESS"

                # Inject the anomaly on the scored day only.
                if spec.merchant_id == SPIKE and scored and n == 0:
                    amount = round(spec.typical_ticket * SPIKE_MULTIPLE, 2)
                if spec.merchant_id == NEWBIE and scored and n == 0:
                    amount = 48_000.0
                # Four tickets parked in the band just below the threshold.
                # Individually ordinary for an electronics shop; together, and
                # against a typical ticket of HKD 600, they are the pattern.
                if spec.merchant_id == STRUCT and scored and n < 4:
                    amount = round(STRUCTURING_THRESHOLD * (1 - 0.02 * (n + 1)), 2)
                # Most of the day's takings handed straight back.
                if spec.merchant_id == REFUNDER and scored and n >= 4:
                    is_refund = True
                    status = "REFUNDED"
                # A run of refused authorisations — card testing, seen from
                # the merchant's side.
                if spec.merchant_id == DECLINER and scored and n >= 12:
                    status = "DECLINED"

                session.add(
                    Transaction(
                        source_txn_id=f"SYN{counter:07d}",
                        merchant_id=spec.merchant_id,
                        total_amount=amount,
                        net_amount=round(amount * 0.97, 2),
                        occurred_at=occurred_day
                        + (
                            timedelta(hours=10, minutes=n * 4)
                            if spec.merchant_id == BURST and scored
                            else timedelta(hours=3)
                            if spec.merchant_id == NIGHT and scored
                            else timedelta(hours=_hour_in_window(spec, n, per_day))
                        ),
                        is_refund=is_refund,
                        transaction_status=status,
                        card_type="VISA",
                        card_issuing_country=(
                            "US" if spec.merchant_id == TOURIST and scored else "HK"
                        ),
                        card_bin="457896",
                        geo=spec.subdistrict,
                    )
                )

    counter = _add_card_linked_activity(session, as_of, counter)
