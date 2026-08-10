"""Nightly pipeline stages.

Stage 1 (pull) is still stood in for by generated data. Stages 2-6 run the
full detection layer: Family A's robust baselines, Family B's typology
ruleset, and Family C's portfolio-wide ring detection.

Everything here is deterministic: same input, same output, every run. That is
an audit requirement, not a preference.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.baselines import Baseline, DispersionMethod, score_value
from compliance.detection.evidence import Contribution
from compliance.detection.profiles import (
    OriginMix,
    fit_origin_mix,
    origin_surprisal,
    SURPRISAL_FLAG,
)
from compliance.detection.timedensity import (
    TimeDensity,
    fit_cohort_time_density,
    fit_time_density,
    local_time_of_day,
    temporal_severity,
    time_is_unusual,
    unusualness_depth,
)
from compliance.detection.windows import (
    _window_bounds,
    _peak_rate,
    fit_all_baselines,
    fit_payment_method_baselines,
    fit_peer_baselines,
    fit_peer_foreign_ratio_baselines,
    fit_peer_transaction_baselines,
    merchant_foreign_ratio,
    fit_peer_volume_baselines,
    fit_trend,
    fit_velocity_baselines,
    fit_volume_baselines,
    quarantined_days,
)
from compliance.detection.rings import (
    CardEvent,
    MerchantNode,
    RingInput,
    evaluate as evaluate_rings,
)
from compliance.detection.ruleset import Family
from compliance.detection.typology import (
    Txn,
    TypologyInput,
    evaluate as evaluate_typologies,
)
from compliance.glossary import alert_type_for
from compliance.models import Alert, Disposition, Merchant, MerchantProfile, Transaction
from compliance.pipeline.merchant_study import merchant_level_is_comparable
from compliance.rules_store import active_rules
from compliance.settings_store import load_settings

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

# Merchants keep Hong Kong hours, so a trading day is midnight-to-midnight
# local, not whatever moment the run started.
HKT = timezone(timedelta(hours=8))

AMOUNT_DETECTOR = "amount_vs_own_baseline"
PEER_TXN_DETECTOR = "ticket_vs_mcc_peers"
PEER_MERCHANT_DETECTOR = "merchant_level_vs_mcc_peers"
TREND_DETECTOR = "level_shift_ramp"
VOLUME_DETECTOR = "count_vs_own_baseline"
PEER_VOLUME_DETECTOR = "count_vs_mcc_peers"
VELOCITY_DETECTOR = "burst_rate_vs_own_baseline"
HOUR_DETECTOR = "hour_vs_own_pattern"
ORIGIN_DETECTOR = "card_origin_vs_own_mix"
PEER_HOUR_DETECTOR = "hour_vs_mcc_peers"
PEER_DISTRICT_AMOUNT_DETECTOR = "ticket_vs_subdistrict_peers"
PEER_FOREIGN_DETECTOR = "foreign_card_ratio_vs_subdistrict"
RAIL_DETECTOR = "amount_vs_payment_method_baseline"


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

    # Thresholds are read from the store so the compliance lead can retune
    # without a deploy; the constants above remain the shipped defaults.
    cfg = load_settings(session)
    window_days = cfg.window_days
    lag = cfg.lag_days

    baselines = fit_all_baselines(
        session,
        as_of,
        window_days,
        min_observations=cfg.min_observations,
        min_span_days=cfg.min_span_days,
        lag_days=lag,
    )
    rails = fit_payment_method_baselines(
        session, as_of, window_days, min_observations=cfg.min_observations, lag_days=lag
    )
    peers = fit_peer_baselines(session, as_of, WINDOW_DAYS, lag_days=LAG_DAYS)
    peer_txns = fit_peer_transaction_baselines(
        session, as_of, WINDOW_DAYS, lag_days=LAG_DAYS
    )
    volumes = fit_volume_baselines(
        session, as_of, WINDOW_DAYS, min_observations=MIN_OBSERVATIONS, lag_days=LAG_DAYS
    )
    peer_volumes = fit_peer_volume_baselines(session, as_of, WINDOW_DAYS, lag_days=LAG_DAYS)
    cohort_hours = fit_cohort_time_density(session, as_of, WINDOW_DAYS, LAG_DAYS)
    district_txns = fit_peer_transaction_baselines(
        session, as_of, WINDOW_DAYS, lag_days=LAG_DAYS, dimension="subdistrict"
    )
    foreign_ratios = fit_peer_foreign_ratio_baselines(
        session, as_of, WINDOW_DAYS, lag_days=LAG_DAYS
    )
    velocities = fit_velocity_baselines(
        session, as_of, WINDOW_DAYS, min_observations=MIN_OBSERVATIONS, lag_days=LAG_DAYS
    )
    quarantined = quarantined_days(session)
    window_start, window_end = _window_bounds(as_of, WINDOW_DAYS, LAG_DAYS)

    for merchant_id, baseline in baselines.items():
        merchant = session.get(Merchant, merchant_id)
        peer = peers.get(merchant.mcc) if merchant else None
        peer_txn = peer_txns.get(merchant.mcc) if merchant else None
        volume = volumes.get(merchant_id)
        velocity = velocities.get(merchant_id)
        peer_volume = peer_volumes.get(merchant.mcc) if merchant else None
        district = merchant.merchant_subdistrict if merchant else None
        cohort_hour = cohort_hours.get(merchant.mcc) if merchant else None
        district_txn = district_txns.get(district) if district else None
        foreign = foreign_ratios.get(district) if district else None
        trend = fit_trend(
            session,
            merchant_id,
            as_of,
            short_days=TREND_SHORT_DAYS,
            long_days=TREND_LONG_DAYS,
            lag_days=LAG_DAYS,
        )
        hours = fit_time_density(session, merchant_id, as_of, WINDOW_DAYS, LAG_DAYS)
        origins = fit_origin_mix(session, merchant_id, as_of, WINDOW_DAYS, LAG_DAYS)
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
                    "peer_center": peer.center if peer else None,
                    "peer_dispersion": peer.dispersion if peer else None,
                    "peer_fence": peer.upper_fence() if peer and peer.usable else None,
                    "peer_merchants": peer.n_merchants if peer else 0,
                    "peer_usable": bool(peer and peer.usable),
                    "peer_txn_center": peer_txn.center if peer_txn else None,
                    "peer_txn_dispersion": peer_txn.dispersion if peer_txn else None,
                    "peer_txn_usable": bool(peer_txn and peer_txn.usable),
                    "volume_center": volume.center if volume else None,
                    "volume_dispersion": volume.dispersion if volume else None,
                    "volume_usable": bool(volume and volume.usable),
                    "velocity_center": velocity.center if velocity else None,
                    "velocity_dispersion": velocity.dispersion if velocity else None,
                    "velocity_usable": bool(velocity and velocity.usable),
                    "peer_volume_center": peer_volume.center if peer_volume else None,
                    "peer_volume_dispersion": peer_volume.dispersion if peer_volume else None,
                    "peer_volume_usable": bool(peer_volume and peer_volume.usable),
                    "subdistrict": district,
                    "cohort_hours_density": (
                        [round(v, 6) for v in cohort_hour.density] if cohort_hour else None
                    ),
                    "cohort_hours_threshold": (
                        round(cohort_hour.threshold, 6) if cohort_hour else None
                    ),
                    "cohort_hours_n": cohort_hour.n if cohort_hour else 0,
                    "cohort_hours_usable": bool(cohort_hour and cohort_hour.usable),
                    "district_txn_center": district_txn.center if district_txn else None,
                    "district_txn_dispersion": district_txn.dispersion if district_txn else None,
                    "district_txn_usable": bool(district_txn and district_txn.usable),
                    "foreign_center": foreign.center if foreign else None,
                    "foreign_dispersion": foreign.dispersion if foreign else None,
                    "foreign_usable": bool(foreign and foreign.usable),
                    # The estimated density is stored so an auditor can see the
                    # pattern an alert was judged against. At full merchant
                    # scale this should be handed between stages in memory
                    # rather than round-tripped through the row.
                    "hours_density": [round(v, 6) for v in hours.density],
                    "hours_threshold": round(hours.threshold, 6),
                    "hours_bandwidth": hours.bandwidth,
                    "hours_n": hours.n,
                    "hours_usable": hours.usable,
                    "origin_counts": origins.counts,
                    "origin_n": origins.n,
                    "origin_usable": origins.usable,
                    "rails": {
                        rail: {
                            "center": b.center,
                            "dispersion": b.dispersion,
                            "n": b.n,
                            "usable": b.usable,
                        }
                        for rail, b in (rails.get(merchant_id) or {}).items()
                        if b.usable
                    },
                    "outlier_z": cfg.for_mcc(merchant.mcc if merchant else None).outlier_z,
                    "materiality_floor": cfg.for_mcc(
                        merchant.mcc if merchant else None
                    ).materiality_floor,
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


def scored_day_bounds(as_of: datetime) -> tuple[datetime, datetime]:
    """The half-open local day a run evaluates: [as_of - 1 day, as_of).

    A nightly run fires at 00:00 and judges the day that just completed, so
    `as_of = 2026-05-01` scores 2026-04-30. Anchored to local midnight so the
    window is a trading day rather than a 24-hour slice starting whenever the
    job happened to run.

    Bounded deliberately. An open-ended `>= as_of` swept in every later
    transaction, and — worse — produced an empty scored window whenever the
    data ended before `as_of`. Only the detectors that ignore scored-day data
    could then fire, which reads as "no single-transaction anomalies" when the
    truth is that nothing was examined.
    """
    local = as_of.astimezone(HKT) if as_of.tzinfo else as_of.replace(tzinfo=HKT)
    end = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=1), end


@dataclass(frozen=True)
class ScoredTxn:
    """One transaction on the day being scored.

    Carries `source_txn_id` because a detector must be able to name the
    transactions that drove it — a deviation with no way back to the rows that
    caused it leaves the analyst scanning the ledger by hand.
    """

    source_txn_id: str
    amount: float
    occurred_at: datetime
    is_refund: bool
    status: str | None
    card_type: str | None
    country: str | None

    @property
    def hour(self) -> float:
        return local_time_of_day(self.occurred_at)


def _scored_day_rows(
    session: Session, merchant_id: str, as_of: datetime
) -> list[ScoredTxn]:
    """Every transaction a merchant processed on the day being scored.

    Fetched once per merchant and shared by every detector. Previously each
    detector ran its own narrow query for just the column it needed, which
    meant five round trips for one day's data and — more importantly — no
    detector could see the transaction ids it was judging.

    Refunds and declines are included here and filtered per detector, because
    the typology rules need exactly the rows the amount baselines exclude.
    """
    start, end = scored_day_bounds(as_of)
    rows = session.execute(
        select(
            Transaction.source_txn_id,
            Transaction.total_amount,
            Transaction.occurred_at,
            Transaction.is_refund,
            Transaction.transaction_status,
            Transaction.card_type,
            Transaction.card_issuing_country,
        )
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
        .order_by(Transaction.occurred_at)
    )
    return [ScoredTxn(*row) for row in rows]


def _contributions(rows: list[ScoredTxn], field: str, reason: str) -> list[dict]:
    """Point at a set of transactions with one shared cause."""
    return [
        Contribution(
            source_txn_id=r.source_txn_id,
            field=field,
            value=_field_value(r, field),
            reason=reason,
        ).as_dict()
        for r in rows
    ]


def _field_value(row: ScoredTxn, field: str) -> str:
    """The displayed value of the column a detector is pointing at."""
    if field == "total_amount":
        return f"HKD {row.amount:,.2f}"
    if field == "occurred_at":
        return row.occurred_at.isoformat()
    if field == "card_issuing_country":
        return row.country or "unknown"
    if field == "card_type":
        return row.card_type or "unknown"
    if field == "transaction_status":
        return row.status or "unknown"
    return ""


def _scored_day_amounts(session: Session, merchant_id: str, as_of: datetime) -> list[float]:
    """Gross amounts on the day being scored."""
    return [r.amount for r in _scored_day_rows(session, merchant_id, as_of) if not r.is_refund]


def _peak_window(rows: list[ScoredTxn], window_minutes: int = 60) -> list[ScoredTxn]:
    """The transactions inside the busiest window of that length.

    Returns the rows rather than the count, so the velocity detector can point
    at the burst itself. Same sliding-window logic as `_peak_rate`, which still
    scores the baseline fitting where only the count is needed.
    """
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: r.occurred_at)
    span = timedelta(minutes=window_minutes)
    best = (0, 1)
    start = 0
    for end in range(len(ordered)):
        while ordered[end].occurred_at - ordered[start].occurred_at >= span:
            start += 1
        if end - start + 1 > best[1] - best[0]:
            best = (start, end + 1)
    return ordered[best[0] : best[1]]


def _scored_day_peak_rate(
    session: Session, merchant_id: str, as_of: datetime, window_minutes: int = 60
) -> float:
    """Busiest `window_minutes` on the day being scored."""
    start, end = scored_day_bounds(as_of)
    stamps = list(
        session.scalars(
            select(Transaction.occurred_at).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                Transaction.is_refund.is_(False),
            )
        )
    )
    return float(_peak_rate(stamps, window_minutes))


def _scored_day_hours(session: Session, merchant_id: str, as_of: datetime) -> list[float]:
    """Times of day on the scored day, with minutes — the density resolves
    below the hour, so rounding here would throw that away."""
    return [
        local_time_of_day(s) for s in session.scalars(
            select(Transaction.occurred_at).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= as_of,
                Transaction.is_refund.is_(False),
            )
        )
    ]


def _scored_day_origins(session: Session, merchant_id: str, as_of: datetime) -> list[str]:
    start, end = scored_day_bounds(as_of)
    return [
        o for o in session.scalars(
            select(Transaction.card_issuing_country).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                Transaction.is_refund.is_(False),
                Transaction.card_issuing_country.is_not(None),
            )
        )
    ]


def _scored_day_amounts_for_rail(
    session: Session, merchant_id: str, as_of: datetime, rail: str
) -> list[float]:
    """Scored-day amounts on one payment rail."""
    start, end = scored_day_bounds(as_of)
    return list(
        session.scalars(
            select(Transaction.total_amount).where(
                Transaction.merchant_id == merchant_id,
                Transaction.card_type == rail,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
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

    hits: list[dict] = []
    cfg = load_settings(session)

    for p in session.scalars(select(MerchantProfile)):
        # Thresholds in force for this merchant's category. Persisted on the
        # profile too, so an auditor can see what was applied on the night.
        tune = cfg.for_mcc(p.metrics.get("peer_mcc"))
        # Peer and trend detectors run in BOTH lanes. A cohort fence needs no
        # history of the merchant's own, so it is the only Family A signal a
        # cold-start merchant can be scored against — and the only one immune
        # to that merchant's own baseline being contaminated.
        day_rows = _scored_day_rows(session, p.merchant_id, as_of)
        sales = [r for r in day_rows if not r.is_refund]
        day_amounts = [r.amount for r in sales]

        # Volume: transacting far more than usual, or far more than peers,
        # is a signal in its own right — and it is what makes an attempt to
        # drag a cohort's amount distribution self-defeating, since the volume
        # required to move it is itself an outlier here.
        day_count = float(len(day_amounts))

        if p.metrics.get("volume_usable") and day_count:
            own_vol = Baseline(
                center=p.metrics["volume_center"],
                dispersion=p.metrics["volume_dispersion"],
                method=DispersionMethod.MAD,
                n=p.metrics["baseline_n"],
            )
            vol_score = score_value(day_count, own_vol)
            if vol_score.is_outlier:
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": VOLUME_DETECTOR,
                    "sub_score": min(vol_score.deviation / 10.0, 1.0),
                    "feature": {
                        "feature_name": "daily_transaction_count",
                        "merchant_value": day_count,
                        "baseline_value": own_vol.center,
                        "deviation": round(vol_score.deviation, 2),
                    },
                    # A count has no single driving transaction — every one of
                    # them is the evidence, so all are marked.
                    "contributions": _contributions(
                        sales, "occurred_at",
                        f"one of {int(day_count)} transactions today, against a "
                        f"usual {own_vol.center:,.0f}",
                    ),
                })

        if p.metrics.get("peer_volume_usable") and day_count:
            cohort_vol = Baseline(
                center=p.metrics["peer_volume_center"],
                dispersion=p.metrics["peer_volume_dispersion"],
                method=DispersionMethod.MAD,
                n=p.metrics["peer_merchants"],
            )
            pv_score = score_value(day_count, cohort_vol)
            if pv_score.is_outlier:
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": PEER_VOLUME_DETECTOR,
                    "sub_score": min(pv_score.deviation / 10.0, 1.0),
                    "feature": {
                        "feature_name": "daily_count_vs_mcc_peers",
                        "merchant_value": day_count,
                        "baseline_value": cohort_vol.center,
                        "deviation": round(pv_score.deviation, 2),
                    },
                    "contributions": _contributions(
                        sales, "occurred_at",
                        f"one of {int(day_count)} transactions today, against "
                        f"{cohort_vol.center:,.0f} for this trade",
                    ),
                })

        # Speed: how tightly today's transactions cluster. A structuring
        # burst can put many through in minutes while the day's total stays
        # unremarkable, so daily volume alone would miss it.
        if p.metrics.get("velocity_usable"):
            burst = _peak_window(sales)
            today_peak = float(len(burst))
            if today_peak:
                own_vel = Baseline(
                    center=p.metrics["velocity_center"],
                    dispersion=p.metrics["velocity_dispersion"],
                    method=DispersionMethod.MAD,
                    n=p.metrics["baseline_n"],
                )
                vel_score = score_value(today_peak, own_vel)
                if vel_score.is_outlier:
                    hits.append({
                        "merchant_id": p.merchant_id,
                        "lane": lanes.get(p.merchant_id, "B"),
                        "detector": VELOCITY_DETECTOR,
                        "sub_score": min(vel_score.deviation / 10.0, 1.0),
                        "feature": {
                            "feature_name": "peak_transactions_per_hour",
                            "merchant_value": today_peak,
                            "baseline_value": own_vel.center,
                            "deviation": round(vel_score.deviation, 2),
                        },
                        # Only the transactions inside the busiest hour, not
                        # the whole day: the burst is the finding, and marking
                        # quiet trading either side of it would bury it.
                        "contributions": _contributions(
                            burst, "occurred_at",
                            f"one of {int(today_peak)} transactions inside the "
                            f"busiest hour",
                        ),
                    })

        # When: trading at an hour this merchant never trades.
        if p.metrics.get("hours_usable"):
            hours = TimeDensity(
                tuple(p.metrics["hours_density"]), p.metrics["hours_threshold"],
                p.metrics["hours_bandwidth"], p.metrics["hours_n"],
            )
            odd = [r for r in sales if time_is_unusual(r.hour, hours)]
            if odd:
                worst = min(odd, key=lambda r: hours.density_at(r.hour))
                worst_hour = worst.hour
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": HOUR_DETECTOR,
                    # Ranked on how much of the day fell outside the pattern
                    # and how far outside it sat. A flat score left every
                    # temporal alert tied, so their order in the queue was
                    # whatever the tie-break produced.
                    "sub_score": temporal_severity(
                        odd_count=len(odd),
                        day_count=len(sales),
                        depth=unusualness_depth(worst_hour, hours),
                    ),
                    "feature": {
                        "feature_name": "transaction_hour",
                        "merchant_value": round(worst_hour, 2),
                        "baseline_value": round(hours.peak_hour(), 2),
                        "deviation": round(hours.density_at(worst_hour), 4),
                    },
                    "contributions": _contributions(
                        odd, "occurred_at",
                        f"outside this merchant's trading pattern "
                        f"(usual peak {hours.peak_hour():.0f}:00)",
                    ),
                })

        # Whose cards: a shift in issuing countries. Foreignness itself is not
        # the signal — an airport shop always sees tourists; a change is.
        if p.metrics.get("origin_usable"):
            mix = OriginMix(dict(p.metrics["origin_counts"]), p.metrics["origin_n"])
            with_origin = [r for r in sales if r.country]
            today = [r.country for r in with_origin]
            scored = [(o, origin_surprisal(o, mix)) for o in set(today)]
            flagged = [(o, s) for o, s in scored if s > SURPRISAL_FLAG]
            if flagged:
                origin, surprisal = max(flagged, key=lambda pair: pair[1])
                # Every transaction on a surprising origin is implicated, and
                # the issuing country is the field that implicates it — so the
                # ledger can highlight that cell rather than the whole row.
                driving = [r for r in with_origin if r.country in {o for o, _ in flagged}]
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": ORIGIN_DETECTOR,
                    "sub_score": min(surprisal / 10.0, 1.0),
                    "feature": {
                        "feature_name": f"card_origin_{origin}",
                        "merchant_value": round(today.count(origin) / len(today), 3),
                        "baseline_value": round(mix.share(origin), 3),
                        "deviation": round(surprisal, 2),
                    },
                    "contributions": [
                        Contribution(
                            source_txn_id=r.source_txn_id,
                            field="card_issuing_country",
                            value=r.country or "unknown",
                            reason=(
                                f"cards issued in {r.country} are "
                                f"{mix.share(r.country):.1%} of this merchant's "
                                f"normal mix"
                            ),
                        ).as_dict()
                        for r in driving
                    ],
                })

        # Cohort hours — the only thing that can call 3am odd for a merchant
        # with no hours pattern of its own.
        if p.metrics.get("cohort_hours_usable"):
            ch = TimeDensity(
                tuple(p.metrics["cohort_hours_density"]),
                p.metrics["cohort_hours_threshold"], 0.0, p.metrics["cohort_hours_n"],
            )
            odd = [r for r in sales if time_is_unusual(r.hour, ch)]
            if odd:
                worst_hour = min(odd, key=lambda r: ch.density_at(r.hour)).hour
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": PEER_HOUR_DETECTOR,
                    "sub_score": temporal_severity(
                        odd_count=len(odd),
                        day_count=len(sales),
                        depth=unusualness_depth(worst_hour, ch),
                    ),
                    "feature": {
                        "feature_name": "hour_vs_trade_hours",
                        "merchant_value": round(worst_hour, 2),
                        "baseline_value": round(ch.peak_hour(), 2),
                        "deviation": round(ch.density_at(worst_hour), 4),
                    },
                    "contributions": _contributions(
                        odd, "occurred_at",
                        f"outside the hours this trade normally operates "
                        f"(cohort peak {ch.peak_hour():.0f}:00)",
                    ),
                })

        # District amount — a ticket ordinary for Central can be remarkable in
        # Sham Shui Po, and MCC alone cannot see that.
        if p.metrics.get("district_txn_usable") and day_amounts:
            dist_base = Baseline(
                center=p.metrics["district_txn_center"],
                dispersion=p.metrics["district_txn_dispersion"],
                method=DispersionMethod.MAD,
                n=p.metrics["peer_merchants"],
            )
            scored_rows = [(r, score_value(r.amount, dist_base)) for r in sales]
            worst_d = max((s for _, s in scored_rows), key=lambda s: s.deviation)
            if tune.fires(deviation=worst_d.deviation, amount=worst_d.value):
                breaching = [
                    r for r, s in scored_rows
                    if tune.fires(deviation=s.deviation, amount=r.amount)
                ]
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": PEER_DISTRICT_AMOUNT_DETECTOR,
                    "sub_score": min(worst_d.deviation / 10.0, 1.0),
                    "feature": {
                        "feature_name": "ticket_vs_subdistrict_peers",
                        "merchant_value": worst_d.value,
                        "baseline_value": dist_base.center,
                        "deviation": round(worst_d.deviation, 2),
                    },
                    "contributions": _contributions(
                        breaching, "total_amount",
                        f"above what merchants in this district normally take "
                        f"(typical {dist_base.center:,.0f})",
                    ),
                })

        # Foreign-card share against the district norm. Foreignness is not the
        # signal — an airport shop sees tourists all day; sitting far from the
        # norm for where you trade is.
        if p.metrics.get("foreign_usable"):
            ratio = merchant_foreign_ratio(session, p.merchant_id, as_of)
            if ratio is not None:
                fbase = Baseline(
                    center=p.metrics["foreign_center"],
                    dispersion=p.metrics["foreign_dispersion"],
                    method=DispersionMethod.MAD,
                    n=p.metrics["peer_merchants"],
                )
                fscore = score_value(ratio, fbase)
                if fscore.is_outlier:
                    hits.append({
                        "merchant_id": p.merchant_id,
                        "lane": lanes.get(p.merchant_id, "B"),
                        "detector": PEER_FOREIGN_DETECTOR,
                        "sub_score": min(fscore.deviation / 10.0, 1.0),
                        "feature": {
                            "feature_name": "foreign_card_share_vs_district",
                            "merchant_value": round(ratio, 3),
                            "baseline_value": round(fbase.center, 3),
                            "deviation": round(fscore.deviation, 2),
                        },
                        # The overseas-issued cards are what lifted the share,
                        # and the issuing country is the field that says so.
                        "contributions": _contributions(
                            [r for r in sales if r.country and r.country != "HK"],
                            "card_issuing_country",
                            f"overseas-issued, against a district norm of "
                            f"{fbase.center:.1%}",
                        ),
                    })

        # Per rail: HKD 3,000 on Octopus is remarkable, the same on Visa is
        # not. The pooled baseline has a spread wide enough to swallow the
        # Octopus case entirely, so each rail is judged on its own pattern.
        for rail, spec in (p.metrics.get("rails") or {}).items():
            rail_base = Baseline(
                center=spec["center"],
                dispersion=spec["dispersion"],
                method=DispersionMethod.MAD,
                n=spec["n"],
            )
            if not rail_base.usable:
                continue
            rail_rows = [r for r in sales if r.card_type == rail]
            if not rail_rows:
                continue
            scored_rail = [(r, score_value(r.amount, rail_base)) for r in rail_rows]
            worst_rail = max((s for _, s in scored_rail), key=lambda s: s.deviation)
            if tune.fires(deviation=worst_rail.deviation, amount=worst_rail.value):
                breaching = [
                    r for r, s in scored_rail
                    if tune.fires(deviation=s.deviation, amount=r.amount)
                ]
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": RAIL_DETECTOR,
                    "sub_score": min(worst_rail.deviation / 10.0, 1.0),
                    "feature": {
                        "feature_name": f"amount_on_{rail.lower()}",
                        "merchant_value": worst_rail.value,
                        "baseline_value": rail_base.center,
                        "deviation": round(worst_rail.deviation, 2),
                    },
                    "contributions": _contributions(
                        breaching, "total_amount",
                        f"large for {rail} at this merchant "
                        f"(typical {rail_base.center:,.0f} on this rail)",
                    ),
                })

        # Peer test 1 — is this merchant's TRANSACTION unusual for its trade?
        # This is the one that covers cold start: a median does not move for a
        # single huge ticket, so neither the merchant's own baseline (which it
        # may not have) nor the merchant-level cohort test can see it.
        if p.metrics.get("peer_txn_usable") and day_amounts:
            cohort_txn = Baseline(
                center=p.metrics["peer_txn_center"],
                dispersion=p.metrics["peer_txn_dispersion"],
                method=DispersionMethod.MAD,
                n=p.metrics["peer_merchants"],
            )
            scored_peer = [(r, score_value(r.amount, cohort_txn)) for r in sales]
            worst = max((s for _, s in scored_peer), key=lambda s: s.deviation)
            if tune.fires(deviation=worst.deviation, amount=worst.value):
                breaching = [
                    r for r, s in scored_peer
                    if tune.fires(deviation=s.deviation, amount=r.amount)
                ]
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": PEER_TXN_DETECTOR,
                    "sub_score": min(worst.deviation / 10.0, 1.0),
                    "feature": {
                        "feature_name": "ticket_vs_mcc_peers",
                        "merchant_value": worst.value,
                        "baseline_value": cohort_txn.center,
                        "deviation": round(worst.deviation, 2),
                    },
                    "contributions": _contributions(
                        breaching, "total_amount",
                        f"above what this trade normally takes "
                        f"(typical {cohort_txn.center:,.0f})",
                    ),
                })

        # Peer test 2 — is this MERCHANT unusual for its cohort? Catches the
        # systematic case test 1 cannot: every ticket sits inside the cohort's
        # range, but the merchant's whole level is shifted.
        #
        # The gate is shared with the case page rather than restated here. Two
        # copies drifted once already, and the queue then carried alerts whose
        # own evidence page reported the check as never having run.
        if merchant_level_is_comparable(p.metrics, day_count=len(sales)) is None:
            cohort = Baseline(
                center=p.metrics["peer_center"],
                dispersion=p.metrics["peer_dispersion"],
                method=DispersionMethod.MAD,
                n=p.metrics["peer_merchants"],
            )
            peer_score = score_value(p.metrics["baseline_center"], cohort)
            if tune.fires(
                deviation=peer_score.deviation, amount=p.metrics["baseline_center"]
            ):
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": PEER_MERCHANT_DETECTOR,
                    "sub_score": min(peer_score.deviation / 10.0, 1.0),
                    "feature": {
                        "feature_name": "typical_ticket_vs_mcc_peers",
                        "merchant_value": p.metrics["baseline_center"],
                        "baseline_value": cohort.center,
                        "deviation": round(peer_score.deviation, 2),
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
        driving: list[ScoredTxn] = []
        for row in sales:
            score = score_value(row.amount, baseline)
            # Statistical AND practical significance. A HKD 150 transaction can
            # be a genuine four-sigma outlier at a convenience store and still
            # be worthless to a launderer; firing on the z-score alone is what
            # buried the actionable alerts.
            if not tune.fires(deviation=score.deviation, amount=row.amount):
                continue
            driving.append(row)
            if worst is None or score.deviation > worst.deviation:
                worst = score

        if worst is None:
            continue
        breaches = len(driving)

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
                "contributions": _contributions(
                    driving, "total_amount",
                    f"far above this merchant's typical transaction "
                    f"({baseline.center:,.0f})",
                ),
                "breaches": breaches,
            }
        )

    return hits


def _daily_value(
    session: Session, merchant_id: str, as_of: datetime, window_days: int
) -> list[tuple[date, float]]:
    """Gross settled value per calendar day over the history window.

    Includes the scored day, unlike the baseline windows: bust-out is a
    trajectory ending in the day being judged, so excluding that day would
    remove the very point the shape is about.
    """
    start, _ = _window_bounds(as_of, window_days)
    _, end = scored_day_bounds(as_of)
    rows = session.execute(
        select(Transaction.occurred_at, Transaction.total_amount).where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            Transaction.is_refund.is_(False),
        )
    )
    per_day: dict[date, float] = {}
    for occurred_at, amount in rows:
        day = occurred_at.astimezone(HKT).date()
        per_day[day] = per_day.get(day, 0.0) + amount
    return [(day, per_day[day]) for day in sorted(per_day)]


def _last_active_before(
    session: Session, merchant_id: str, as_of: datetime
) -> date | None:
    """The last day the merchant traded before the day being scored."""
    start, _ = scored_day_bounds(as_of)
    latest = session.scalars(
        select(Transaction.occurred_at)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.occurred_at < start,
        )
        .order_by(Transaction.occurred_at.desc())
        .limit(1)
    ).first()
    return latest.astimezone(HKT).date() if latest else None


def typology_input_for(
    session: Session, merchant_id: str, as_of: datetime, metrics: dict
) -> TypologyInput | None:
    """Assemble one merchant's Family B input.

    Shared by the nightly run and the case-review screen so a rule can never
    read one thing in the pipeline and another on the page explaining it.
    """
    rows = _scored_day_rows(session, merchant_id, as_of)
    if not rows:
        return None
    merchant = session.get(Merchant, merchant_id)
    return TypologyInput(
        merchant_id=merchant_id,
        mcc=merchant.mcc if merchant else None,
        day=[
            Txn(
                source_txn_id=r.source_txn_id,
                amount=r.amount,
                occurred_at=r.occurred_at,
                hour=r.hour,
                is_refund=r.is_refund,
                status=r.status,
                card_type=r.card_type,
            )
            for r in rows
        ],
        daily_value=_daily_value(session, merchant_id, as_of, WINDOW_DAYS * 3),
        last_active=_last_active_before(session, merchant_id, as_of),
        scored_day=scored_day_bounds(as_of)[0].date(),
        baseline_center=metrics.get("baseline_center"),
        cohort_center=metrics.get("peer_center"),
        cohort_hours=metrics.get("cohort_hours_density") or [],
        cohort_hours_threshold=metrics.get("cohort_hours_threshold") or 0.0,
    )


def typologies(session: Session, *, as_of: datetime) -> list[dict]:
    """Stage 4b: run Family B's typology ruleset over every merchant.

    Runs in BOTH lanes, unlike the own-history baselines. A rule needs no
    fitted history — that is exactly why the cold-start merchants Family A
    cannot score are the ones this family exists to cover.
    """
    rules = active_rules(session, Family.B)
    if not rules:
        return []

    hits: list[dict] = []

    for p in session.scalars(select(MerchantProfile)):
        # A merchant with no activity on the scored day gives no typology
        # anything to match against.
        data = typology_input_for(session, p.merchant_id, as_of, p.metrics)
        if data is None:
            continue

        for hit in evaluate_typologies(data, rules):
            hits.append({
                "merchant_id": p.merchant_id,
                "lane": lanes_for(p),
                "detector": hit.template,
                "rule_id": hit.rule_id,
                "reason_code": hit.reason_code,
                "sub_score": hit.sub_score,
                "message": hit.message,
                "feature": hit.feature,
                "contributions": [c.as_dict() for c in hit.contributions],
            })

    return hits


def lanes_for(profile: MerchantProfile) -> str:
    return "A" if profile.metrics.get("baseline_usable") else "B"


def rings(
    session: Session, *, as_of: datetime, flagged_now: frozenset[str] = frozenset()
) -> list[dict]:
    """Stage 4c: run Family C's ring detection across the whole portfolio.

    Portfolio-wide rather than per-merchant by necessity — coordination
    between merchants is structurally invisible from inside any one of them.

    `hashed_pan` is read here and nowhere else. It never enters a hit, a
    contribution, or an alert: the evidence names the counterpart merchant and
    the transaction ids, which is what an analyst needs, without handing anyone
    a brute-forceable card identifier.
    """
    rules = active_rules(session, Family.C)
    if not rules:
        return []

    merchants = [
        MerchantNode(
            merchant_id=m.merchant_id,
            mcc=m.mcc,
            agent_id=m.agent_id,
            hashed_br_number=m.hashed_br_number,
            hashed_merchant_address=m.hashed_merchant_address,
            hashed_merchant_name=m.hashed_merchant_name,
            subdistrict=m.merchant_subdistrict,
            district=m.merchant_district,
        )
        for m in session.scalars(select(Merchant))
    ]

    start, end = scored_day_bounds(as_of)
    # `!= ""` is not redundant with `is_not(None)`. The source writes
    # `hashed_pan` as an empty string on every wallet rail — a wallet has no
    # card number to hash — and over half the real extract is a wallet. An
    # empty string is not NULL, so those rows pass a null check and then all
    # compare *equal to one another*: one "card" appearing at thousands of
    # merchants, which every card-linkage rule below reports as a ring.
    # Ingestion now folds blank to NULL, and this guard covers rows loaded
    # before it did.
    events = [
        CardEvent(
            pan_key=pan,
            merchant_id=merchant_id,
            source_txn_id=txn_id,
            occurred_at=occurred_at,
            card_type=card_type,
        )
        for pan, merchant_id, txn_id, occurred_at, card_type in session.execute(
            select(
                Transaction.hashed_pan,
                Transaction.merchant_id,
                Transaction.source_txn_id,
                Transaction.occurred_at,
                Transaction.card_type,
            ).where(
                Transaction.hashed_pan.is_not(None),
                Transaction.hashed_pan != "",
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                Transaction.is_refund.is_(False),
            )
        )
    ]

    # "Already flagged" spans two things: merchants carrying an alert nobody
    # has cleared, and merchants Family A or B flagged earlier in *this* run.
    # The second half matters — alerts are not written until the final stage,
    # so reading the table alone leaves a first run seeing nothing flagged and
    # every ring silently below threshold.
    #
    # A merchant whose alert was dispositioned as a false positive drops out,
    # rather than inflating its agent's rate or its ring's severity forever.
    undecided = ~select(Disposition.id).where(
        Disposition.alert_id == Alert.id
    ).exists()
    flagged = frozenset(
        session.scalars(select(Alert.merchant_id).where(undecided).distinct())
    ) | flagged_now

    lanes = {p.merchant_id: lanes_for(p) for p in session.scalars(select(MerchantProfile))}

    return [
        {
            "merchant_id": r.merchant_id,
            "lane": lanes.get(r.merchant_id, "B"),
            "detector": r.hit.template,
            "rule_id": r.hit.rule_id,
            "reason_code": r.hit.reason_code,
            "sub_score": r.hit.sub_score,
            "message": r.hit.message,
            "feature": r.hit.feature,
            "contributions": [c.as_dict() for c in r.hit.contributions],
            # The cross-merchant picture: which merchants the card touched,
            # and for geo-velocity the arithmetic of each impossible leg. One
            # alert now stands for a whole card, so the alert has to carry
            # what the other merchants were.
            "linkage": r.hit.linkage,
        }
        for r in evaluate_rings(
            RingInput(merchants=merchants, card_events=events, flagged=flagged),
            rules,
        )
    ]


def score_and_rank(
    session: Session, hits: list[dict], *, as_of: datetime | None = None
) -> list[Alert]:
    """Stage 5: one alert per flagged merchant, ranked, with its snapshot.

    The feature snapshot is written once and never recomputed — training on
    features rebuilt later would leak the future into the model.
    """
    # Ties broken on merchant then detector so two runs over the same data
    # produce the same ranks. Sorting on score alone leaves equal-scoring hits
    # in whatever order the query returned them, which makes `rank` — a column
    # the audit trail carries — non-reproducible.
    ordered = sorted(
        hits,
        key=lambda h: (-h["sub_score"], h["merchant_id"], h["detector"]),
    )
    alerts: list[Alert] = []
    for rank, h in enumerate(ordered, start=1):
        detector_hit = {"detector": h["detector"], "sub_score": h["sub_score"]}
        # Rules carry the parameters that fired them; baselines do not have
        # any, so the keys are only written when they mean something.
        for key in ("rule_id", "reason_code", "message"):
            if h.get(key):
                detector_hit[key] = h[key]
        # The evidence is frozen onto the alert rather than recomputed on
        # read, for the same reason `feature_snapshot` is: a later run with
        # retuned thresholds would otherwise rewrite the past.
        if h.get("contributions"):
            detector_hit["contributions"] = h["contributions"]
        # Family C only: the merchants a card connected, and the journey
        # between them. Frozen for the same reason as everything else here —
        # a later run's portfolio is a different portfolio.
        if h.get("linkage"):
            detector_hit["linkage"] = h["linkage"]

        alert = Alert(
            merchant_id=h["merchant_id"],
            as_of=as_of,
            lane=h["lane"],
            blended_score=h["sub_score"],
            rank=rank,
            triggering_detectors=[detector_hit],
            feature_snapshot=[h["feature"]] if h.get("feature") else [],
            # Cached at write time so the queue filters and counts in SQL.
            # Derived from the same map the badge on the row uses, so the
            # column cannot come to mean something the analyst does not see.
            alert_type=alert_type_for(h["detector"]),
        )
        session.add(alert)
        alerts.append(alert)
    return alerts
