"""Per-merchant detector breakdown for terminal inspection.

Runs all 12 Family A detectors against a single merchant and returns a
result for every one — passing, failing, or skipped — so an analyst (or a
developer debugging synthetic data) can see exactly where a merchant sits
relative to each baseline.

The core function ``study_merchant`` is pure: it takes pre-fetched data and
returns ``DetectorResult`` objects with no database dependency.  The
convenience wrapper ``fetch_day_data`` handles the queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.detection.baselines import (
    Baseline,
    DispersionMethod,
    score_value,
)
from compliance.detection.profiles import (
    OriginMix,
    origin_surprisal,
    SURPRISAL_FLAG,
)
from compliance.detection.timedensity import (
    TimeDensity,
    local_time_of_day,
    time_is_unusual,
)
from compliance.detection.windows import (
    HOME_COUNTRY,
    merchant_foreign_ratio,
    merchant_unsettled_ratio,
    settled_sale,
)
from compliance.glossary import DETECTORS as GLOSSARY_DETECTORS
from compliance.models import Merchant, MerchantProfile, Transaction

# ---------------------------------------------------------------------------
# Detector ordering and display labels (pulled from the glossary)
# ---------------------------------------------------------------------------

_DETECTOR_ORDER: list[str] = [
    "amount_vs_own_baseline",
    "count_vs_own_baseline",
    "burst_rate_vs_own_baseline",
    "level_shift_ramp",
    "hour_vs_own_pattern",
    "card_origin_vs_own_mix",
    "ticket_vs_mcc_peers",
    "merchant_level_vs_mcc_peers",
    "count_vs_mcc_peers",
    "hour_vs_mcc_peers",
    "ticket_vs_subdistrict_peers",
    "foreign_card_ratio_vs_subdistrict",
    "unsettled_ratio_vs_own_baseline",
    "unsettled_ratio_vs_mcc_peers",
]

_LABELS: dict[str, str] = {t.key: t.label for t in GLOSSARY_DETECTORS}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DetectorResult:
    """One detector's verdict for a single merchant on a single scored day."""

    rank: int  # display order
    detector: str  # internal key
    label: str  # human-readable from glossary
    status: str  # "OK" | "FAIL" | "SKIP"
    merchant_value: float | str | None  # what the merchant did
    baseline_value: float | str | None  # what was expected
    deviation: float | None  # z-score, ratio, density, or surprisal
    band: str | None  # "normal" | "moderate" | "outlier"
    feature_name: str  # for root-cause messaging
    message: str  # plain-English explanation
    # Which family produced it. The three answer different questions and an
    # analyst triages them differently, so the panel groups by this rather
    # than presenting one undifferentiated list of checks.
    family: str = "A"
    # The transactions that drove this verdict, and the column of each that
    # carries the cause. Empty where the merchant rather than any transaction
    # is the subject.
    contributions: tuple = ()


@dataclass(frozen=True)
class DayData:
    """Pre-fetched transaction data for the scored day."""

    amounts: list[float]
    count: int
    peak_rate: float
    hours: list[float]
    origins: list[str]
    foreign_ratio: float | None
    # Share of the day's attempts that never settled. None when the merchant
    # attempted nothing — no measurement, rather than a rate of zero.
    unsettled_ratio: float | None = None
    # (source_txn_id, amount, hour, issuing country) per transaction, so a
    # detector can name the rows it fired on. Optional and defaulted, because
    # an alert written before this existed still has to render — it falls back
    # to the evidence frozen on the alert row.
    records: tuple[tuple[str, float, float, str | None], ...] = ()


# ---------------------------------------------------------------------------
# Gates shared with the pipeline
# ---------------------------------------------------------------------------


def merchant_level_is_comparable(metrics: dict, *, day_count: int) -> str | None:
    """Why the merchant-vs-cohort level test cannot run, or None if it can.

    Read by both the nightly pipeline, deciding whether to raise the alert,
    and this module, explaining it on the case page. They held separate copies
    of the condition and drifted: the pipeline fired on a peer cohort plus any
    `baseline_center`, while the page also demanded `baseline_usable`. The
    result was an alert titled "Merchant level vs MCC baseline" whose own
    detail panel reported the check as skipped — the queue asserting something
    the evidence page denied.

    Returning the reason rather than a boolean is what keeps them honest: the
    sentence the analyst reads is produced by the same call that decided not
    to fire.

    The three conditions, and why each is a condition:

    * **A usable peer cohort.** There is nothing to compare against otherwise.
    * **A usable baseline of the merchant's own.** This test compares one
      *level* against another. A merchant with two transactions in its window
      has a median — the median of two numbers is always defined — and no
      level. Firing on it says "your typical ticket is 50x your trade's" about
      a merchant that has no typical ticket yet.
    * **Trade on the scored day.** The baseline window is lagged and does not
      move overnight, so a silent merchant produces the identical finding every
      night until somebody dispositions it. That is not a daily signal; it is
      the same alert re-queued, and it is why the queue filled with merchants
      showing zero transactions.
    """
    if not metrics.get("peer_usable"):
        return "MCC peer cohort baseline not usable"
    if not metrics.get("baseline_usable") or not metrics.get("baseline_center"):
        return "this merchant has no usable baseline of its own to compare"
    if not day_count:
        return "the merchant did not trade on the scored day"
    return None


# ---------------------------------------------------------------------------
# Pure core — no DB, no side effects
# ---------------------------------------------------------------------------


def study_merchant(
    metrics: dict, day: DayData, *, lane: str = "A"
) -> list[DetectorResult]:
    """Run all 12 detectors against one merchant and return every result.

    Parameters
    ----------
    metrics:
        The ``MerchantProfile.metrics`` JSON dict (fitted baselines).
    day:
        Pre-fetched scored-day data (amounts, hours, origins, etc.).
    lane:
        "A" or "B".  Only affects whether the own-baseline amount detector
        runs; peer detectors always run.

    Returns
    -------
    A list of exactly 12 ``DetectorResult`` objects, one per detector,
    in display order.
    """
    from compliance.detection.baselines import (
        CONSISTENCY_CONSTANT,
        OUTLIER_BAND,
    )

    results: list[DetectorResult] = []
    n = 0  # display counter

    def _add(
        detector: str,
        status: str,
        m_val: float | str | None,
        b_val: float | str | None,
        dev: float | None,
        band: str | None,
        feature: str,
        msg: str,
        contributions: tuple = (),
    ) -> None:
        nonlocal n
        n += 1
        results.append(
            DetectorResult(
                rank=n,
                detector=detector,
                label=_LABELS.get(detector, detector),
                status=status,
                merchant_value=m_val,
                baseline_value=b_val,
                deviation=dev,
                band=band,
                feature_name=feature,
                message=msg,
                family="A",
                contributions=contributions,
            )
        )

    def _cite(matching, field: str, reason: str) -> tuple:
        """Name the transactions a detector fired on, and the column that says
        why. Empty when the day's records were not supplied, in which case the
        evidence frozen on the alert is used instead."""
        formatted = {
            "total_amount": lambda r: f"HKD {r[1]:,.2f}",
            "occurred_at": lambda r: _fmt_hour(r[2]),
            "card_issuing_country": lambda r: r[3] or "unknown",
        }[field]
        return tuple(
            {
                "source_txn_id": r[0],
                "field": field,
                "value": formatted(r),
                "reason": reason,
            }
            for r in matching
        )

    # Shorthand
    m = metrics
    day_amounts = day.amounts
    day_count = float(day.count)

    # ── 1. amount_vs_own_baseline ───────────────────────────────────────────
    if lane != "A" or not m.get("baseline_usable"):
        reason = "lane B" if lane != "A" else "baseline not usable"
        _add(
            "amount_vs_own_baseline", "SKIP", None, None, None, None,
            "ticket_amount",
            f"Skipped ({reason})",
        )
    else:
        baseline = Baseline(
            center=m["baseline_center"],
            dispersion=m["baseline_dispersion"],
            method=DispersionMethod(m["baseline_method"]),
            n=m["baseline_n"],
        )
        if not day_amounts:
            _add(
                "amount_vs_own_baseline", "OK", 0.0, baseline.center, 0.0,
                "normal", "ticket_amount",
                "No transactions on scored day",
            )
        else:
            worst = None
            breaches = 0
            for amount in day_amounts:
                score = score_value(amount, baseline)
                if score.is_outlier:
                    breaches += 1
                    if worst is None or score.deviation > worst.deviation:
                        worst = score
            if worst is None:
                _add(
                    "amount_vs_own_baseline", "OK",
                    max(day_amounts), baseline.center,
                    round(
                        CONSISTENCY_CONSTANT
                        * (max(day_amounts) - baseline.center)
                        / baseline.dispersion,
                        2,
                    ),
                    "normal", "ticket_amount",
                    "All transactions within own baseline",
                )
            else:
                _add(
                    "amount_vs_own_baseline", "FAIL",
                    worst.value, baseline.center,
                    round(worst.deviation, 2), worst.band,
                    "ticket_amount",
                    f"{breaches} transaction(s) outlier vs own baseline",
                    _cite(
                        [
                            r for r in day.records
                            if score_value(r[1], baseline).is_outlier
                        ],
                        "total_amount",
                        f"far above this merchant's typical transaction "
                        f"({baseline.center:,.0f})",
                    ),
                )

    # ── 2. count_vs_own_baseline ────────────────────────────────────────────
    if not m.get("volume_usable") or not day_count:
        _add(
            "count_vs_own_baseline", "SKIP", day_count or None,
            m.get("volume_center"), None, None,
            "daily_transaction_count",
            "Skipped (volume baseline not usable or no transactions)",
        )
    else:
        own_vol = Baseline(
            center=m["volume_center"],
            dispersion=m["volume_dispersion"],
            method=DispersionMethod.MAD,
            n=m["baseline_n"],
        )
        vol_score = score_value(day_count, own_vol)
        status = "FAIL" if vol_score.is_outlier else "OK"
        _add(
            "count_vs_own_baseline", status,
            day_count, own_vol.center,
            round(vol_score.deviation, 2), vol_score.band,
            "daily_transaction_count",
            "Daily count outlier vs own baseline"
            if vol_score.is_outlier
            else "Daily count within own baseline",
        )

    # ── 3. burst_rate_vs_own_baseline ───────────────────────────────────────
    if not m.get("velocity_usable"):
        _add(
            "burst_rate_vs_own_baseline", "SKIP", None, None, None, None,
            "peak_transactions_per_hour",
            "Skipped (velocity baseline not usable)",
        )
    else:
        today_peak = day.peak_rate
        if not today_peak:
            _add(
                "burst_rate_vs_own_baseline", "OK", 0.0,
                m["velocity_center"], 0.0, "normal",
                "peak_transactions_per_hour",
                "No transactions to measure velocity",
            )
        else:
            own_vel = Baseline(
                center=m["velocity_center"],
                dispersion=m["velocity_dispersion"],
                method=DispersionMethod.MAD,
                n=m["baseline_n"],
            )
            vel_score = score_value(today_peak, own_vel)
            status = "FAIL" if vel_score.is_outlier else "OK"
            _add(
                "burst_rate_vs_own_baseline", status,
                today_peak, own_vel.center,
                round(vel_score.deviation, 2), vel_score.band,
                "peak_transactions_per_hour",
                "Peak hourly rate outlier vs own baseline"
                if vel_score.is_outlier
                else "Peak hourly rate within own baseline",
            )

    # ── 4. level_shift_ramp ─────────────────────────────────────────────────
    if m.get("trend_is_ramp"):
        ratio = m["trend_ratio"]
        long_center = m["baseline_center"] / ratio if ratio else 0.0
        _add(
            "level_shift_ramp", "FAIL",
            m["baseline_center"], round(long_center, 2),
            round(ratio, 3), "outlier",
            "level_shift_7d_vs_90d",
            f"7-day level {ratio:.1f}x the 90-day level (ramp detected)",
        )
    else:
        ratio = m.get("trend_ratio", 1.0)
        long_center = m["baseline_center"] / ratio if ratio else 0.0
        _add(
            "level_shift_ramp", "OK",
            m["baseline_center"], round(long_center, 2),
            round(ratio, 3), "normal",
            "level_shift_7d_vs_90d",
            "7-day and 90-day levels are consistent",
        )

    # ── 5. hour_vs_own_pattern ──────────────────────────────────────────────
    if not m.get("hours_usable"):
        _add(
            "hour_vs_own_pattern", "SKIP", None, None, None, None,
            "transaction_hour",
            "Skipped (own hours density not usable)",
        )
    else:
        hours = TimeDensity(
            tuple(m["hours_density"]),
            m["hours_threshold"],
            m["hours_bandwidth"],
            m["hours_n"],
        )
        odd = [h for h in day.hours if time_is_unusual(h, hours)]
        if odd:
            worst_hour = min(odd, key=hours.density_at)
            _add(
                "hour_vs_own_pattern", "FAIL",
                round(worst_hour, 2), round(hours.peak_hour(), 2),
                round(hours.density_at(worst_hour), 4), "outlier",
                "transaction_hour",
                f"Transaction at {_fmt_hour(worst_hour)} is outside "
                f"own trading pattern (peak: {_fmt_hour(hours.peak_hour())})",
                _cite(
                    [r for r in day.records if time_is_unusual(r[2], hours)],
                    "occurred_at",
                    f"outside this merchant's trading pattern "
                    f"(usual peak {_fmt_hour(hours.peak_hour())})",
                ),
            )
        else:
            _add(
                "hour_vs_own_pattern", "OK",
                round(day.hours[0], 2) if day.hours else None,
                round(hours.peak_hour(), 2),
                0.0, "normal", "transaction_hour",
                "All transaction times within own pattern",
            )

    # ── 6. card_origin_vs_own_mix ───────────────────────────────────────────
    if not m.get("origin_usable"):
        _add(
            "card_origin_vs_own_mix", "SKIP", None, None, None, None,
            "card_origin",
            "Skipped (own origin mix not usable)",
        )
    else:
        mix = OriginMix(dict(m["origin_counts"]), m["origin_n"])
        if not day.origins:
            _add(
                "card_origin_vs_own_mix", "OK", "N/A", "N/A", 0.0,
                "normal", "card_origin",
                "No card origins on scored day",
            )
        else:
            scored = [
                (o, origin_surprisal(o, mix)) for o in set(day.origins)
            ]
            flagged = [(o, s) for o, s in scored if s > SURPRISAL_FLAG]
            if flagged:
                origin, surprisal = max(flagged, key=lambda pair: pair[1])
                today_share = day.origins.count(origin) / len(day.origins)
                _add(
                    "card_origin_vs_own_mix", "FAIL",
                    f"{origin} ({today_share:.0%})",
                    f"{origin} ({mix.share(origin):.0%})",
                    round(surprisal, 2), "outlier",
                    "card_origin",
                    f"Card origin {origin} at {today_share:.0%} is "
                    f"unexpected (normal: {mix.share(origin):.0%}, "
                    f"surprisal: {surprisal:.1f} bits)",
                    # Every transaction on a surprising origin, with the
                    # issuing country named as the cause — so the ledger
                    # highlights that cell and not merely the row.
                    _cite(
                        [
                            r for r in day.records
                            if r[3] in {o for o, _ in flagged}
                        ],
                        "card_issuing_country",
                        f"issued in a country outside this merchant's "
                        f"normal card mix",
                    ),
                )
            else:
                _add(
                    "card_origin_vs_own_mix", "OK",
                    ", ".join(sorted(set(day.origins))), "N/A", 0.0,
                    "normal", "card_origin",
                    "All card origins within expected mix",
                )

    # ── 7. ticket_vs_mcc_peers ──────────────────────────────────────────────
    if not m.get("peer_txn_usable") or not day_amounts:
        _add(
            "ticket_vs_mcc_peers", "SKIP", None, None, None, None,
            "ticket_vs_mcc_peers",
            "Skipped (MCC peer txn baseline not usable or no transactions)",
        )
    else:
        cohort_txn = Baseline(
            center=m["peer_txn_center"],
            dispersion=m["peer_txn_dispersion"],
            method=DispersionMethod.MAD,
            n=m["peer_merchants"],
        )
        worst = max(
            (score_value(a, cohort_txn) for a in day_amounts),
            key=lambda s: s.deviation,
        )
        status = "FAIL" if worst.is_outlier else "OK"
        _add(
            "ticket_vs_mcc_peers", status,
            worst.value, cohort_txn.center,
            round(worst.deviation, 2), worst.band,
            "ticket_vs_mcc_peers",
            "Transaction outlier vs MCC peers"
            if worst.is_outlier
            else "Transactions within MCC peer range",
            _cite(
                [
                    r for r in day.records
                    if score_value(r[1], cohort_txn).is_outlier
                ],
                "total_amount",
                f"above what this trade normally takes "
                f"(typical {cohort_txn.center:,.0f})",
            ) if worst.is_outlier else (),
        )

    # ── 8. merchant_level_vs_mcc_peers ──────────────────────────────────────
    blocked = merchant_level_is_comparable(m, day_count=day.count)
    if blocked:
        _add(
            "merchant_level_vs_mcc_peers", "SKIP", None, None, None, None,
            "typical_ticket_vs_mcc_peers",
            f"Skipped — {blocked}",
        )
    else:
        cohort = Baseline(
            center=m["peer_center"],
            dispersion=m["peer_dispersion"],
            method=DispersionMethod.MAD,
            n=m["peer_merchants"],
        )
        peer_score = score_value(m["baseline_center"], cohort)
        status = "FAIL" if peer_score.is_outlier else "OK"
        # No contributions by design: this compares the merchant's whole level
        # against its cohort, so no individual transaction is the reason and
        # highlighting any would misattribute the finding.
        _add(
            "merchant_level_vs_mcc_peers", status,
            m["baseline_center"], cohort.center,
            round(peer_score.deviation, 2), peer_score.band,
            "typical_ticket_vs_mcc_peers",
            "Merchant's typical ticket is outlier vs MCC cohort"
            if peer_score.is_outlier
            else "Merchant's typical ticket is within MCC cohort",
        )

    # ── 9. count_vs_mcc_peers ───────────────────────────────────────────────
    if not m.get("peer_volume_usable") or not day_count:
        _add(
            "count_vs_mcc_peers", "SKIP", day_count or None,
            m.get("peer_volume_center"), None, None,
            "daily_count_vs_mcc_peers",
            "Skipped (MCC peer volume baseline not usable or no transactions)",
        )
    else:
        cohort_vol = Baseline(
            center=m["peer_volume_center"],
            dispersion=m["peer_volume_dispersion"],
            method=DispersionMethod.MAD,
            n=m["peer_merchants"],
        )
        pv_score = score_value(day_count, cohort_vol)
        status = "FAIL" if pv_score.is_outlier else "OK"
        _add(
            "count_vs_mcc_peers", status,
            day_count, cohort_vol.center,
            round(pv_score.deviation, 2), pv_score.band,
            "daily_count_vs_mcc_peers",
            "Daily count outlier vs MCC peers"
            if pv_score.is_outlier
            else "Daily count within MCC peer range",
        )

    # ── 10. hour_vs_mcc_peers ───────────────────────────────────────────────
    if not m.get("cohort_hours_usable"):
        _add(
            "hour_vs_mcc_peers", "SKIP", None, None, None, None,
            "hour_vs_trade_hours",
            "Skipped (MCC cohort hours not usable)",
        )
    else:
        ch = TimeDensity(
            tuple(m["cohort_hours_density"]),
            m["cohort_hours_threshold"],
            0.0,
            m["cohort_hours_n"],
        )
        odd = [h for h in day.hours if time_is_unusual(h, ch)]
        if odd:
            worst_hour = min(odd, key=ch.density_at)
            _add(
                "hour_vs_mcc_peers", "FAIL",
                round(worst_hour, 2), round(ch.peak_hour(), 2),
                round(ch.density_at(worst_hour), 4), "outlier",
                "hour_vs_trade_hours",
                f"Transaction at {_fmt_hour(worst_hour)} is outside "
                f"MCC cohort hours (peak: {_fmt_hour(ch.peak_hour())})",
            )
        else:
            _add(
                "hour_vs_mcc_peers", "OK",
                round(day.hours[0], 2) if day.hours else None,
                round(ch.peak_hour(), 2),
                0.0, "normal", "hour_vs_trade_hours",
                "All transaction times within MCC cohort hours",
            )

    # ── 11. ticket_vs_subdistrict_peers ─────────────────────────────────────
    if not m.get("district_txn_usable") or not day_amounts:
        _add(
            "ticket_vs_subdistrict_peers", "SKIP", None, None, None, None,
            "ticket_vs_subdistrict_peers",
            "Skipped (district txn baseline not usable or no transactions)",
        )
    else:
        dist_base = Baseline(
            center=m["district_txn_center"],
            dispersion=m["district_txn_dispersion"],
            method=DispersionMethod.MAD,
            n=m["peer_merchants"],
        )
        worst_d = max(
            (score_value(a, dist_base) for a in day_amounts),
            key=lambda s: s.deviation,
        )
        status = "FAIL" if worst_d.is_outlier else "OK"
        _add(
            "ticket_vs_subdistrict_peers", status,
            worst_d.value, dist_base.center,
            round(worst_d.deviation, 2), worst_d.band,
            "ticket_vs_subdistrict_peers",
            "Transaction outlier vs district peers"
            if worst_d.is_outlier
            else "Transactions within district peer range",
            _cite(
                [
                    r for r in day.records
                    if score_value(r[1], dist_base).is_outlier
                ],
                "total_amount",
                f"above what merchants in this district normally take "
                f"(typical {dist_base.center:,.0f})",
            ) if worst_d.is_outlier else (),
        )

    # ── 12. foreign_card_ratio_vs_subdistrict ───────────────────────────────
    if not m.get("foreign_usable") or day.foreign_ratio is None:
        _add(
            "foreign_card_ratio_vs_subdistrict", "SKIP", None, None, None,
            None, "foreign_card_share_vs_district",
            "Skipped (foreign ratio baseline not usable or no data)",
        )
    else:
        fbase = Baseline(
            center=m["foreign_center"],
            dispersion=m["foreign_dispersion"],
            method=DispersionMethod.MAD,
            n=m["peer_merchants"],
        )
        fscore = score_value(day.foreign_ratio, fbase)
        status = "FAIL" if fscore.is_outlier else "OK"
        _add(
            "foreign_card_ratio_vs_subdistrict", status,
            f"{day.foreign_ratio:.1%}", f"{fbase.center:.1%}",
            round(fscore.deviation, 2), fscore.band,
            "foreign_card_share_vs_district",
            "Foreign card share outlier vs district norm"
            if fscore.is_outlier
            else "Foreign card share within district norm",
        )

    # ── 13-14. unsettled share, against own history and against the cohort ──
    for detector, usable_key, center_key, dispersion_key, n_key, feature, subject in (
        (
            "unsettled_ratio_vs_own_baseline", "unsettled_usable",
            "unsettled_center", "unsettled_dispersion", "unsettled_n",
            "unsettled_share_vs_own_history", "this merchant's usual rate",
        ),
        (
            "unsettled_ratio_vs_mcc_peers", "peer_unsettled_usable",
            "peer_unsettled_center", "peer_unsettled_dispersion", "peer_merchants",
            "unsettled_share_vs_mcc_peers", "the MCC norm",
        ),
    ):
        if not m.get(usable_key) or day.unsettled_ratio is None:
            _add(
                detector, "SKIP", None, None, None, None, feature,
                "Skipped (failed-transaction baseline not usable or no attempts)",
            )
            continue
        ubase = Baseline(
            center=m[center_key],
            dispersion=m[dispersion_key],
            method=DispersionMethod.MAD,
            n=m.get(n_key) or 0,
        )
        uscore = score_value(day.unsettled_ratio, ubase)
        # One-sided, as the pipeline is: failing less than usual is not a
        # finding, and reporting it as one would fill the panel with merchants
        # having a good day.
        failing = uscore.is_outlier and day.unsettled_ratio > ubase.center
        _add(
            detector, "FAIL" if failing else "OK",
            f"{day.unsettled_ratio:.1%}", f"{ubase.center:.1%}",
            round(uscore.deviation, 2), uscore.band, feature,
            f"Failed-transaction rate above {subject}"
            if failing
            else f"Failed-transaction rate in line with {subject}",
        )

    return results


# ---------------------------------------------------------------------------
# Root cause
# ---------------------------------------------------------------------------


def study_typologies(
    data, rules, *, start_rank: int = 0
) -> list[DetectorResult]:
    """Family B verdicts in the same shape as the Family A ones.

    Every configured rule is reported, including the ones that did not fire.
    A rule that stayed silent is information — it tells the analyst that
    structuring was checked for and not found, which is the difference between
    evidence of absence and nobody having looked.
    """
    from compliance.detection.ruleset import Family
    from compliance.detection.typology import evaluate as evaluate_typologies

    fired = {h.rule_id: h for h in evaluate_typologies(data, rules)}
    results: list[DetectorResult] = []
    n = start_rank

    for inst in rules:
        if inst.spec().family is not Family.B or not inst.enabled:
            continue
        n += 1
        hit = fired.get(inst.instance_id)
        if hit is None:
            skipped = not inst.applies_to(data.mcc)
            results.append(
                DetectorResult(
                    rank=n,
                    detector=inst.template,
                    label=inst.display_label(),
                    status="SKIP" if skipped else "OK",
                    merchant_value=None,
                    baseline_value=None,
                    deviation=None,
                    band=None,
                    feature_name=inst.template,
                    message=(
                        f"Not applicable — this rule is scoped to MCC "
                        f"{', '.join(inst.mcc_scope)}"
                        if skipped
                        else "No match on the scored day"
                    ),
                    family="B",
                )
            )
            continue

        feature = hit.feature or {}
        results.append(
            DetectorResult(
                rank=n,
                detector=inst.template,
                label=hit.label,
                status="FAIL",
                merchant_value=feature.get("merchant_value"),
                baseline_value=feature.get("baseline_value"),
                deviation=feature.get("deviation"),
                band="outlier",
                feature_name=feature.get("feature_name", inst.template),
                message=hit.message,
                family="B",
                contributions=tuple(c.as_dict() for c in hit.contributions),
            )
        )
    return results


def root_cause(results: list[DetectorResult]) -> str | None:
    """Return a plain-English root-cause string, or None if all passed."""
    for r in results:
        if r.status == "FAIL":
            return (
                f"{r.label}\n"
                f"  → {r.message}"
            )
    return None


# ---------------------------------------------------------------------------
# DB convenience wrapper
# ---------------------------------------------------------------------------


def fetch_day_data(
    session: Session, merchant_id: str, as_of: datetime
) -> DayData:
    """Query the scored-day transactions for one merchant."""
    from compliance.pipeline.stages import scored_day_bounds

    _day_start, _day_end = scored_day_bounds(as_of)
    rows = list(
        session.execute(
            select(
                Transaction.total_amount,
                Transaction.occurred_at,
                Transaction.card_issuing_country,
                Transaction.source_txn_id,
            ).where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= _day_start,
                Transaction.occurred_at < _day_end,
                *settled_sale(),
            )
        )
    )
    amounts = [r[0] for r in rows]
    hours = [local_time_of_day(r[1]) for r in rows]
    # Overseas cards only, matching the sample `fit_origin_mix` is built on.
    # A wallet tap has no issuing country and a home card is not what this
    # detector watches; scoring either against a foreign-only distribution
    # would report both as unfamiliar.
    origins = [r[2] for r in rows if r[2] and r[2] != HOME_COUNTRY]
    records = tuple(
        (r[3], r[0], local_time_of_day(r[1]), r[2]) for r in rows
    )

    # Peak hourly rate (reuse the same window as the pipeline)
    from compliance.detection.windows import _peak_rate

    stamps = [r[1] for r in rows]
    peak_rate = float(_peak_rate(stamps, 60))

    # Foreign ratio
    foreign_ratio = merchant_foreign_ratio(session, merchant_id, as_of)
    unsettled_ratio = merchant_unsettled_ratio(session, merchant_id, as_of)

    return DayData(
        amounts=amounts,
        count=len(amounts),
        peak_rate=peak_rate,
        hours=hours,
        origins=origins,
        foreign_ratio=foreign_ratio,
        unsettled_ratio=unsettled_ratio,
        records=records,
    )


def fetch_profile(
    session: Session, merchant_id: str, as_of: datetime | None = None
) -> tuple[MerchantProfile, Merchant | None]:
    """Fetch the merchant profile (latest, or for a specific as_of)."""
    stmt = select(MerchantProfile).where(
        MerchantProfile.merchant_id == merchant_id
    )
    if as_of is not None:
        stmt = stmt.where(MerchantProfile.as_of == as_of)
    else:
        stmt = stmt.order_by(MerchantProfile.as_of.desc())
    profile = session.scalars(stmt).first()
    if profile is None:
        raise SystemExit(
            f"No profile found for merchant '{merchant_id}'"
            + (f" as of {as_of}" if as_of else "")
        )
    merchant = session.get(Merchant, merchant_id)
    return profile, merchant


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_STATUS_ICONS = {"OK": "✓", "FAIL": "✗", "SKIP": "–"}


def _fmt_hour(h: float) -> str:
    """Format a fractional hour as HH:MM."""
    total_minutes = int(round(h * 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def _fmt_value(v: float | str | None, feature: str) -> str:
    if v is None:
        return "–"
    if isinstance(v, str):
        return v
    if "foreign" in feature:
        return f"{v:.1%}"
    if "hour" in feature or "transaction_hour" in feature:
        return _fmt_hour(v)
    if "count" in feature or "peak" in feature:
        return f"{v:.1f}"
    return f"${v:,.2f}"


def format_report(
    merchant_id: str,
    mcc: str | None,
    lane: str,
    subdistrict: str | None,
    as_of: datetime,
    results: list[DetectorResult],
) -> str:
    """Render the results as a terminal-friendly table."""
    lines: list[str] = []
    lines.append("")
    lines.append(
        f"  Merchant: {merchant_id}"
        + (f"  (MCC {mcc})" if mcc else "")
        + f"  ·  Lane {lane}"
        + (f"  ·  {subdistrict}" if subdistrict else "")
    )
    lines.append(f"  Scored:   {as_of.strftime('%Y-%m-%d')}")
    lines.append("")

    # Column widths
    det_w = max(len(r.detector) for r in results)
    det_w = max(det_w, 10)

    hdr = (
        f"  {'#':>2}  "
        f"{'Detector':<{det_w}}  "
        f"{'Status':<6}  "
        f"{'Merchant':>14}  "
        f"{'Baseline':>14}  "
        f"{'Dev':>8}  "
        f"{'Band':<8}"
    )
    sep = "  " + "─" * (len(hdr) - 2)
    lines.append(hdr)
    lines.append(sep)

    for r in results:
        icon = _STATUS_ICONS.get(r.status, "?")
        status_str = f"{icon} {r.status}"
        m_str = _fmt_value(r.merchant_value, r.feature_name)
        b_str = _fmt_value(r.baseline_value, r.feature_name)
        dev_str = f"{r.deviation:.2f}" if r.deviation is not None else "–"
        band_str = r.band or "–"

        lines.append(
            f"  {r.rank:>2}  "
            f"{r.detector:<{det_w}}  "
            f"{status_str:<6}  "
            f"{m_str:>14}  "
            f"{b_str:>14}  "
            f"{dev_str:>8}  "
            f"{band_str:<8}"
        )

    # Root cause
    rc = root_cause(results)
    lines.append("")
    if rc:
        lines.append(f"  ROOT CAUSE: {rc}")
    else:
        lines.append("  ALL DETECTORS PASSED — no anomaly detected.")
    lines.append("")

    return "\n".join(lines)
