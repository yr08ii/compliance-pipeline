"""Inspect generated merchants and their transactions.

Run with:
    uv run python -m compliance.inspect_data
    uv run python -m compliance.inspect_data --merchant SPIKE
    uv run python -m compliance.inspect_data --merchant SPIKE --tail 10
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta

from sqlalchemy import func, select

from compliance.db import SessionLocal
from compliance.models import Merchant, Transaction
from compliance.synthetic import SPECS, STEADY, SPIKE, FIXED, NEWBIE, RAMP, PEER_OUT, FLOOD, BURST, NIGHT, TOURIST

HKT = timezone(timedelta(hours=8))

# Ground-truth labels for each synthetic merchant id.
GROUND_TRUTH = {
    STEADY:   "STEADY   — mature, well-behaved. Must NOT be flagged.",
    SPIKE:    "SPIKE    — mature, one huge ticket today. MUST be flagged.",
    FIXED:    "FIXED    — one fixed price. Degenerate baseline (Lane B rule).",
    NEWBIE:   "NEWBIE   — too little history. Lane B only.",
    RAMP:     "RAMP     — grows 4%/day. Invisible to self-baseline; trend catches it.",
    PEER_OUT: "PEEROUT  — stable internally, far above its grocer cohort.",
    FLOOD:    "FLOOD    — ordinary tickets, 12× normal count on scored day.",
    BURST:    "BURST    — same day total, crammed into one hour. Speed detector.",
    NIGHT:    "NIGHT    — trades at 3am when grocers don't. Cohort hours.",
    TOURIST:  "TOURIST  — all-foreign cards in a residential district.",
}


def _bar(value: float, max_value: float, width: int = 30) -> str:
    filled = int(round(width * value / max_value)) if max_value > 0 else 0
    return "█" * filled + "░" * (width - filled)


def print_merchants(session) -> None:
    merchants = session.scalars(select(Merchant).order_by(Merchant.merchant_id)).all()
    if not merchants:
        print("  (no merchants in database — run: make seed-run)")
        return

    print(f"\n{'─'*90}")
    print(f"  {'Merchant ID':<14} {'MCC':<6} {'Lane':<5} {'Subdistrict':<18} {'Txns':>6} {'Avg Amt':>10}  Ground truth")
    print(f"{'─'*90}")

    for m in merchants:
        txn_count = session.scalar(
            select(func.count()).where(Transaction.merchant_id == m.merchant_id)
        ) or 0
        avg_amt = session.scalar(
            select(func.avg(Transaction.total_amount)).where(
                Transaction.merchant_id == m.merchant_id,
                Transaction.is_refund.is_(False),
            )
        ) or 0.0
        label = GROUND_TRUTH.get(m.merchant_id, "Cohort filler merchant")
        # Shorten the label for table display
        short_label = label.split("—")[0].strip() if "—" in label else label[:20]
        print(
            f"  {m.merchant_id:<14} {m.mcc:<6} {m.lane:<5} "
            f"{(m.merchant_subdistrict or ''):<18} {txn_count:>6} {avg_amt:>10.2f}  {short_label}"
        )

    print(f"{'─'*90}")
    print(f"  Total merchants: {len(merchants)}")


def print_merchant_detail(session, merchant_id: str, tail: int | None) -> None:
    merchant = session.get(Merchant, merchant_id)
    if not merchant:
        print(f"\n  ✗ Merchant '{merchant_id}' not found.")
        return

    label = GROUND_TRUTH.get(merchant_id, "Cohort filler merchant")
    print(f"\n{'═'*80}")
    print(f"  Merchant: {merchant.merchant_id}")
    print(f"  MCC: {merchant.mcc}  |  Lane: {merchant.lane}  |  Subdistrict: {merchant.merchant_subdistrict}")
    print(f"  Onboarded: {merchant.onboarded_at.astimezone(HKT).date() if merchant.onboarded_at else 'n/a'}")
    print(f"  Ground truth: {label}")
    print(f"{'─'*80}")

    stmt = (
        select(Transaction)
        .where(Transaction.merchant_id == merchant_id)
        .order_by(Transaction.occurred_at)
    )
    txns = session.scalars(stmt).all()

    if not txns:
        print("  (no transactions)")
        return

    amounts = [t.total_amount for t in txns if not t.is_refund]
    max_amt = max(amounts) if amounts else 1.0

    if tail:
        txns_to_show = txns[-tail:]
        print(f"  Showing last {len(txns_to_show)} of {len(txns)} transactions\n")
    else:
        txns_to_show = txns
        print(f"  All {len(txns)} transactions\n")

    print(f"  {'Date (HKT)':<22} {'Amount HKD':>12}  Distribution")
    print(f"  {'─'*22} {'─'*12}  {'─'*32}")
    for t in txns_to_show:
        ts = t.occurred_at.astimezone(HKT).strftime("%Y-%m-%d %H:%M")
        bar = _bar(t.total_amount, max_amt)
        flag = " ◄ SPIKE" if t.total_amount == max_amt and merchant_id == SPIKE else ""
        print(f"  {ts:<22} {t.total_amount:>12,.2f}  {bar}{flag}")

    print(f"\n  Count: {len(amounts)}  |  Min: {min(amounts):,.2f}  |  "
          f"Max: {max(amounts):,.2f}  |  Avg: {sum(amounts)/len(amounts):,.2f}")
    print(f"{'═'*80}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect generated merchants and transactions."
    )
    parser.add_argument(
        "--merchant", "-m",
        default=None,
        help="Show transaction history for a single merchant ID (e.g. SPIKE, STEADY).",
    )
    parser.add_argument(
        "--tail", "-n",
        type=int,
        default=None,
        help="Limit to the last N transactions when showing a single merchant.",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        print("\n╔══════════════════════════════════════════╗")
        print("║   Compliance Pipeline — Data Inspector   ║")
        print("╚══════════════════════════════════════════╝")

        if args.merchant:
            print_merchant_detail(session, args.merchant.upper(), args.tail)
        else:
            print_merchants(session)
            print("\n  Tip: pass --merchant SPIKE (or any ID above) to see transaction history.")
            print(f"  Available IDs: {', '.join(s.merchant_id for s in SPECS)}\n")


if __name__ == "__main__":
    main()
