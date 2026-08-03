"""Inspect fitted baselines for all merchants.

Run with:
    uv run python -m compliance.inspect_baselines
    uv run python -m compliance.inspect_baselines --merchant STEADY
    uv run python -m compliance.inspect_baselines --merchant NIGHT --kde

The --kde flag draws the KDE probability distribution curve for trading-hour
density as an ASCII plot in the terminal.
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from compliance.db import SessionLocal
from compliance.detection.baselines import (
    fit_baseline, fit_peer_baseline, DispersionMethod,
    score_value, OUTLIER_BAND, MODERATE_BAND,
)
from compliance.detection.timedensity import (
    TimeDensity,
    fit_time_density,
    fit_cohort_time_density,
    BINS,
    BIN_HOURS,
    MIN_OBSERVATIONS,
)
from compliance.detection.windows import (
    _window_bounds,
    fit_all_baselines,
    fit_peer_baselines,
    fit_volume_baselines,
    fit_velocity_baselines,
    fit_trend,
)
from compliance.models import Merchant, MerchantProfile, Transaction
from compliance.pipeline.stages import (
    WINDOW_DAYS,
    MIN_OBSERVATIONS as STAGE_MIN_OBS,
    MIN_SPAN_DAYS,
    LAG_DAYS,
    TREND_SHORT_DAYS,
    TREND_LONG_DAYS,
)
from compliance.synthetic import SPECS

HKT = timezone(timedelta(hours=8))

# Ground truth labels (same as inspect_data)
LABELS = {
    "STEADY":   "mature, well-behaved. Must NOT be flagged.",
    "SPIKE":    "mature, one huge ticket today. MUST be flagged.",
    "FIXED":    "one fixed price. Degenerate baseline.",
    "NEWBIE":   "too little history. Lane B only.",
    "RAMP":     "grows 4%/day. Invisible to self-baseline; trend catches it.",
    "PEEROUT":  "stable internally, far above its grocer cohort.",
    "FLOOD":    "ordinary tickets, 12× count on scored day.",
    "BURST":    "same daily total, crammed into one hour.",
    "NIGHT":    "trades at 3am when grocers don't.",
    "TOURIST":  "all-foreign cards in a residential district.",
}


# ─── ASCII KDE plot ──────────────────────────────────────────────────────────

def _kde_plot(td: TimeDensity, title: str, width: int = 72, height: int = 12) -> None:
    """Render a KDE probability density curve as an ASCII bar chart."""
    density = td.density
    if not any(density):
        print("  (density is zero — not enough data)")
        return

    max_density = max(density)
    # Downsample the 96-bin density to `width` columns.
    step = BINS / width
    cols: list[float] = []
    for col in range(width):
        start_bin = int(col * step)
        end_bin = int((col + 1) * step)
        cols.append(max(density[b % BINS] for b in range(start_bin, max(end_bin, start_bin + 1))))

    col_max = max(cols) if cols else 1.0

    print(f"\n  ┌── {title} ({'bandwidth %.2f hr' % td.bandwidth if td.bandwidth else 'n/a'}) ──")
    print(f"  │  n={td.n} obs  |  threshold={td.threshold:.4f}")
    print(f"  │")

    # Draw rows top-to-bottom
    for row in range(height, 0, -1):
        cutoff = col_max * row / height
        line = ""
        for val in cols:
            if val >= cutoff:
                line += "█"
            elif val >= cutoff - col_max / height * 0.5:
                line += "▄"
            else:
                line += " "
        label = f"{col_max * row / height:.4f}" if row == height else ""
        print(f"  │ {line} {label}")

    # X-axis hour labels
    print(f"  └─{'─' * width}─")
    tick_positions = [0, 6, 12, 18, 23]
    label_line = " " * 4
    prev_pos = 0
    for tick in tick_positions:
        col = int(tick / 24.0 * width)
        lbl = f"{tick:02d}:00"
        gap = col - prev_pos
        label_line += " " * max(gap - len(lbl) + 1, 1) + lbl
        prev_pos = col + len(lbl)
    print(f"  {label_line}")
    print(f"       {'hour of day (HKT)':^{width}}")

    # Show where threshold falls visually
    threshold_density = td.threshold
    if threshold_density > 0 and col_max > 0:
        fraction = threshold_density / col_max
        row_of_threshold = int(fraction * height)
        print(f"\n  ⟶ Threshold at density={threshold_density:.4f} "
              f"(row {row_of_threshold}/{height}) — transactions below this are flagged as unusual hour.")


# ─── Baseline summary ────────────────────────────────────────────────────────

def _method_tag(m: DispersionMethod) -> str:
    tags = {
        DispersionMethod.MAD: "MAD       ✓",
        DispersionMethod.SCALED_IQR: "scaled IQR ✓",
        DispersionMethod.CONSTANT: "CONSTANT  ✗ (fixed price — rule, not score)",
        DispersionMethod.INSUFFICIENT_DATA: "INSUFFICIENT ✗ (Lane B)",
    }
    return tags.get(m, str(m))


def print_all_baselines(session, as_of: datetime, show_kde: bool = False) -> None:
    print(f"\n{'═'*80}")
    print(f"  Fitting baselines  as_of={as_of.astimezone(HKT).date()}"
          f"  window={WINDOW_DAYS}d  lag={LAG_DAYS}d")
    print(f"{'─'*80}")

    amount_baselines = fit_all_baselines(
        session, as_of, WINDOW_DAYS,
        min_observations=STAGE_MIN_OBS, min_span_days=MIN_SPAN_DAYS, lag_days=LAG_DAYS,
    )
    peer_baselines   = fit_peer_baselines(session, as_of, WINDOW_DAYS, lag_days=LAG_DAYS)
    volume_baselines = fit_volume_baselines(
        session, as_of, WINDOW_DAYS, min_observations=STAGE_MIN_OBS, lag_days=LAG_DAYS,
    )
    cohort_hours     = fit_cohort_time_density(session, as_of, WINDOW_DAYS, LAG_DAYS)

    merchants = session.scalars(select(Merchant).order_by(Merchant.merchant_id)).all()

    for m in merchants:
        ab = amount_baselines.get(m.merchant_id)
        vb = volume_baselines.get(m.merchant_id)
        pb = peer_baselines.get(m.mcc)
        label = LABELS.get(m.merchant_id, "cohort filler")

        print(f"\n  ┌─ {m.merchant_id:<12}  MCC={m.mcc}  Lane={m.lane}  {label}")

        # Amount baseline
        if ab:
            print(f"  │  Amount baseline:  center={ab.center:>10.2f}  dispersion={ab.dispersion:>8.3f}"
                  f"  n={ab.n:<4}  method={_method_tag(ab.method)}")
        else:
            print(f"  │  Amount baseline:  (not fitted)")

        # Volume baseline
        if vb:
            print(f"  │  Volume baseline:  center={vb.center:>10.2f}  dispersion={vb.dispersion:>8.3f}"
                  f"  n={vb.n:<4}  method={_method_tag(vb.method)}")
        else:
            print(f"  │  Volume baseline:  (not fitted)")

        # Peer (MCC cohort) amount
        if pb:
            print(f"  │  Peer (MCC={m.mcc}):   center={pb.center:>10.2f}  dispersion={pb.dispersion:>8.3f}"
                  f"  n_merchants={pb.n_merchants}  fence={pb.upper_fence():.2f}")

        # Time density KDE
        td = fit_time_density(session, m.merchant_id, as_of, WINDOW_DAYS, LAG_DAYS)
        ch = cohort_hours.get(m.mcc)
        if td.usable:
            peak = td.peak_hour()
            print(f"  │  Time density:     n={td.n}  bw={td.bandwidth:.2f}hr  "
                  f"peak={peak:.1f}hr ({int(peak):02d}:{int((peak%1)*60):02d})  "
                  f"threshold={td.threshold:.4f}")
        else:
            print(f"  │  Time density:     n={td.n}  (unusable — needs ≥{MIN_OBSERVATIONS} obs)")

        if ch and ch.usable:
            peak_c = ch.peak_hour()
            print(f"  │  Cohort hours:     n={ch.n}  bw={ch.bandwidth:.2f}hr  "
                  f"peak={peak_c:.1f}hr ({int(peak_c):02d}:{int((peak_c%1)*60):02d})  "
                  f"threshold={ch.threshold:.4f}")

        print(f"  └{'─'*76}")

        if show_kde:
            if td.usable:
                _kde_plot(td, f"{m.merchant_id} — own hours")
            if ch and ch.usable:
                _kde_plot(ch, f"MCC {m.mcc} cohort hours")


def print_merchant_baselines(session, merchant_id: str, as_of: datetime, show_kde: bool) -> None:
    m = session.get(Merchant, merchant_id)
    if not m:
        print(f"\n  ✗ Merchant '{merchant_id}' not found.")
        return

    label = LABELS.get(merchant_id, "cohort filler")
    print(f"\n{'═'*80}")
    print(f"  Baseline detail for: {merchant_id}  ({label})")
    print(f"  MCC={m.mcc}  Subdistrict={m.merchant_subdistrict}  Lane={m.lane}")
    print(f"  as_of={as_of.astimezone(HKT).date()}  window={WINDOW_DAYS}d  lag={LAG_DAYS}d")
    print(f"{'─'*80}")

    window_start, window_end = _window_bounds(as_of, WINDOW_DAYS, LAG_DAYS)
    txn_amounts = list(
        session.scalars(
            select(Transaction.total_amount).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= window_start,
                Transaction.occurred_at < window_end,
                Transaction.is_refund.is_(False),
            )
        )
    )

    print(f"\n  ── Amount Baseline (Family A · own history)")
    print(f"     Window: {window_start.date()} → {window_end.date()}  ({len(txn_amounts)} transactions)")
    ab = fit_baseline(txn_amounts, min_observations=STAGE_MIN_OBS)
    print(f"     Center (median): {ab.center:.2f} HKD")
    print(f"     Dispersion:      {ab.dispersion:.3f}")
    print(f"     Method:          {_method_tag(ab.method)}")
    print(f"     Usable?          {'YES' if ab.usable else 'NO'}")
    if ab.usable and txn_amounts:
        today_txns = list(session.scalars(
            select(Transaction.total_amount).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= as_of - timedelta(days=1),
                Transaction.occurred_at < as_of + timedelta(days=1),
                Transaction.is_refund.is_(False),
            )
        ))
        if today_txns:
            print(f"\n     Today's amounts vs baseline:")
            for amt in sorted(today_txns, reverse=True)[:5]:
                try:
                    sc = score_value(amt, ab)
                    flag = " ◄ OUTLIER" if sc.is_outlier else (" ◄ moderate" if sc.band == "moderate" else "")
                    print(f"       HKD {amt:>10,.2f}  →  z={sc.deviation:+.2f}  [{sc.band}]{flag}")
                except ValueError as e:
                    print(f"       HKD {amt:>10,.2f}  →  (cannot score: {e})")

    # KDE
    print(f"\n  ── Time-of-Day KDE (Family A · own hours)")
    td = fit_time_density(session, merchant_id, as_of, WINDOW_DAYS, LAG_DAYS)
    print(f"     Observations: {td.n}  |  Bandwidth: {td.bandwidth:.2f} hr  |  Usable: {'YES' if td.usable else f'NO (needs {MIN_OBSERVATIONS})'}")
    if td.usable:
        print(f"     Threshold: {td.threshold:.4f}  |  Peak: {td.peak_hour():.1f}hr")
    if show_kde:
        if td.usable:
            _kde_plot(td, f"{merchant_id} — own trading hours")
        else:
            print("     (not enough obs for KDE plot)")

    # Cohort KDE
    print(f"\n  ── Cohort Time-of-Day KDE (MCC {m.mcc})")
    cohort_hours = fit_cohort_time_density(session, as_of, WINDOW_DAYS, LAG_DAYS)
    ch = cohort_hours.get(m.mcc)
    if ch and ch.usable:
        print(f"     Observations: {ch.n}  |  Bandwidth: {ch.bandwidth:.2f} hr  |  Threshold: {ch.threshold:.4f}")
        if show_kde:
            _kde_plot(ch, f"MCC {m.mcc} cohort hours")
    else:
        print("     (not enough cohort members for KDE)")

    print(f"\n{'═'*80}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect fitted baselines for synthetic merchants."
    )
    parser.add_argument(
        "--merchant", "-m",
        default=None,
        help="Focus on a single merchant ID (e.g. NIGHT, STEADY).",
    )
    parser.add_argument(
        "--kde",
        action="store_true",
        help="Draw ASCII KDE probability distribution curves for trading-hour baselines.",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Score date YYYY-MM-DD (default: today in HKT).",
    )
    args = parser.parse_args()

    as_of_hkt = (
        datetime.strptime(args.as_of, "%Y-%m-%d").replace(tzinfo=HKT)
        if args.as_of
        else datetime.now(HKT).replace(hour=0, minute=0, second=0, microsecond=0)
    )

    print("\n╔══════════════════════════════════════════════╗")
    print("║  Compliance Pipeline — Baseline Inspector    ║")
    print("╚══════════════════════════════════════════════╝")

    with SessionLocal() as session:
        from sqlalchemy import func as sqlfunc
        txn_count = session.scalar(select(sqlfunc.count()).select_from(Transaction)) or 0
        if txn_count == 0:
            print("\n  ✗ No transactions found. Run 'make seed-run' first to generate data.")
            return

        print(f"  Transactions in store: {txn_count}")

        if args.merchant:
            print_merchant_baselines(session, args.merchant.upper(), as_of_hkt, args.kde)
        else:
            print_all_baselines(session, as_of_hkt, show_kde=args.kde)

        if not args.kde:
            print("\n  Tip: pass --kde to draw the probability distribution curves.")
        if not args.merchant:
            print("  Tip: pass --merchant NIGHT (or any ID) to zoom in on a single merchant.\n")


if __name__ == "__main__":
    main()
