"""Nightly pipeline stages.

Stages 2-4 now run Family A's robust amount baseline. Stage 1 (pull) is still
stood in for by generated data, and Family B/C detectors are not built yet.

Everything here is deterministic: same input, same output, every run. That is
an audit requirement, not a preference.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.baselines import Baseline, DispersionMethod, score_value
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
    time_is_unusual,
)
from compliance.detection.windows import (
    _window_bounds,
    _peak_rate,
    fit_all_baselines,
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


def _scored_day_amounts(session: Session, merchant_id: str, as_of: datetime) -> list[float]:
    """Gross amounts on the day being scored."""
    start, end = scored_day_bounds(as_of)
    return list(
        session.scalars(
            select(Transaction.total_amount).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
                Transaction.is_refund.is_(False),
            )
        )
    )


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

    for p in session.scalars(select(MerchantProfile)):
        # Peer and trend detectors run in BOTH lanes. A cohort fence needs no
        # history of the merchant's own, so it is the only Family A signal a
        # cold-start merchant can be scored against — and the only one immune
        # to that merchant's own baseline being contaminated.
        day_amounts = _scored_day_amounts(session, p.merchant_id, as_of)

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
                })

        # Speed: how tightly today's transactions cluster. A structuring
        # burst can put many through in minutes while the day's total stays
        # unremarkable, so daily volume alone would miss it.
        if p.metrics.get("velocity_usable"):
            today_peak = _scored_day_peak_rate(session, p.merchant_id, as_of)
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
                    })

        # When: trading at an hour this merchant never trades.
        if p.metrics.get("hours_usable"):
            hours = TimeDensity(
                tuple(p.metrics["hours_density"]), p.metrics["hours_threshold"],
                p.metrics["hours_bandwidth"], p.metrics["hours_n"],
            )
            odd = [h for h in _scored_day_hours(session, p.merchant_id, as_of)
                   if time_is_unusual(h, hours)]
            if odd:
                worst_hour = min(odd, key=hours.density_at)
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": HOUR_DETECTOR,
                    "sub_score": 0.5,
                    "feature": {
                        "feature_name": "transaction_hour",
                        "merchant_value": round(worst_hour, 2),
                        "baseline_value": round(hours.peak_hour(), 2),
                        "deviation": round(hours.density_at(worst_hour), 4),
                    },
                })

        # Whose cards: a shift in issuing countries. Foreignness itself is not
        # the signal — an airport shop always sees tourists; a change is.
        if p.metrics.get("origin_usable"):
            mix = OriginMix(dict(p.metrics["origin_counts"]), p.metrics["origin_n"])
            today = _scored_day_origins(session, p.merchant_id, as_of)
            scored = [(o, origin_surprisal(o, mix)) for o in set(today)]
            flagged = [(o, s) for o, s in scored if s > SURPRISAL_FLAG]
            if flagged:
                origin, surprisal = max(flagged, key=lambda pair: pair[1])
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
                })

        # Cohort hours — the only thing that can call 3am odd for a merchant
        # with no hours pattern of its own.
        if p.metrics.get("cohort_hours_usable"):
            ch = TimeDensity(
                tuple(p.metrics["cohort_hours_density"]),
                p.metrics["cohort_hours_threshold"], 0.0, p.metrics["cohort_hours_n"],
            )
            odd = [h for h in _scored_day_hours(session, p.merchant_id, as_of)
                   if time_is_unusual(h, ch)]
            if odd:
                worst_hour = min(odd, key=ch.density_at)
                hits.append({
                    "merchant_id": p.merchant_id,
                    "lane": lanes.get(p.merchant_id, "B"),
                    "detector": PEER_HOUR_DETECTOR,
                    "sub_score": 0.5,
                    "feature": {
                        "feature_name": "hour_vs_trade_hours",
                        "merchant_value": round(worst_hour, 2),
                        "baseline_value": round(ch.peak_hour(), 2),
                        "deviation": round(ch.density_at(worst_hour), 4),
                    },
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
            worst_d = max((score_value(a, dist_base) for a in day_amounts),
                          key=lambda s: s.deviation)
            if worst_d.is_outlier:
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
            worst = max(
                (score_value(a, cohort_txn) for a in day_amounts),
                key=lambda s: s.deviation,
            )
            if worst.is_outlier:
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
                })

        # Peer test 2 — is this MERCHANT unusual for its cohort? Catches the
        # systematic case test 1 cannot: every ticket sits inside the cohort's
        # range, but the merchant's whole level is shifted.
        if p.metrics.get("peer_usable") and p.metrics.get("baseline_center"):
            cohort = Baseline(
                center=p.metrics["peer_center"],
                dispersion=p.metrics["peer_dispersion"],
                method=DispersionMethod.MAD,
                n=p.metrics["peer_merchants"],
            )
            peer_score = score_value(p.metrics["baseline_center"], cohort)
            if peer_score.is_outlier:
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


def score_and_rank(
    session: Session, hits: list[dict], *, as_of: datetime | None = None
) -> list[Alert]:
    """Stage 5: one alert per flagged merchant, ranked, with its snapshot.

    The feature snapshot is written once and never recomputed — training on
    features rebuilt later would leak the future into the model.
    """
    ordered = sorted(hits, key=lambda h: h["sub_score"], reverse=True)
    alerts: list[Alert] = []
    for rank, h in enumerate(ordered, start=1):
        alert = Alert(
            merchant_id=h["merchant_id"],
            as_of=as_of,
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
