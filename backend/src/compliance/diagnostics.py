"""Assembling the evidence behind one alert.

An analyst opening a case needs three things the alert row alone cannot give:
who the merchant is, which transactions ran that day, and the statistics that
made the detector fire. This module builds that payload.

The detector verdicts come from `pipeline.merchant_study`, which already runs
all twelve detectors as a pure function — the same code the terminal tool
uses, so the screen and the CLI can never disagree about why an alert fired.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance import glossary
from compliance.detection.baselines import CONSISTENCY_CONSTANT
from compliance.models import (
    Alert,
    CohortSnapshot,
    Merchant,
    MerchantProfile,
    Transaction,
)
from compliance.pipeline.merchant_study import (
    fetch_day_data,
    root_cause,
    study_merchant,
)

HKT = timezone(timedelta(hours=8))


def scored_date(alert: Alert) -> str:
    """The day an alert evaluated.

    A run with `as_of = 2026-05-01` scores the completed day before it, so the
    label an analyst needs is 2026-04-30. Falls back to `created_at` only for
    rows written before `as_of` was recorded.
    """
    stamp = alert.as_of or alert.created_at
    return (stamp.astimezone(HKT) - timedelta(days=1)).date().isoformat()


def alert_type(alert: Alert) -> str:
    """The triage badge for an alert.

    Prefers the column the run cached — the queue filters and counts on it, so
    the badge must be the same value or the filter hides rows from the analyst
    who asked for exactly them. Falls back to the derivation for rows written
    before the column existed.
    """
    if alert.alert_type:
        return alert.alert_type
    detectors = alert.triggering_detectors or []
    first = detectors[0].get("detector", "") if detectors else ""
    return glossary.alert_type_for(first)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=HKT)
    return start, start + timedelta(days=1)


def ledger(session: Session, merchant_id: str, day: date) -> dict:
    """Every transaction a merchant processed on one local day.

    `hashed_pan` is deliberately not selected: a 1:1 PAN hash is
    brute-forceable, so it is cardholder data and never leaves the store.
    """
    start, end = _day_bounds(day)
    rows = list(
        session.scalars(
            select(Transaction)
            .where(
                Transaction.merchant_id == merchant_id,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
            )
            .order_by(Transaction.occurred_at)
        )
    )

    profile = session.scalars(
        select(MerchantProfile)
        .where(MerchantProfile.merchant_id == merchant_id)
        .order_by(MerchantProfile.as_of.desc())
    ).first()

    # Mark the rows that breached the merchant's own amount baseline, so the
    # analyst can find the driving checkout instead of scanning the column.
    threshold: float | None = None
    if profile and profile.metrics.get("baseline_usable"):
        center = profile.metrics.get("baseline_center") or 0.0
        dispersion = profile.metrics.get("baseline_dispersion") or 0.0
        if dispersion > 0:
            threshold = center + 3.5 * dispersion / CONSISTENCY_CONSTANT

    transactions = []
    outliers = 0
    for t in rows:
        is_outlier = bool(
            threshold is not None and not t.is_refund and t.total_amount > threshold
        )
        outliers += is_outlier
        transactions.append(
            {
                "source_txn_id": t.source_txn_id,
                "occurred_at": t.occurred_at,
                "total_amount": t.total_amount,
                "net_amount": t.net_amount,
                "is_refund": t.is_refund,
                "card_type": t.card_type,
                "card_issuing_country": t.card_issuing_country,
                "card_issuing_bank": t.card_issuing_bank,
                "transaction_status": t.transaction_status,
                "currency": t.currency,
                "is_outlier": is_outlier,
            }
        )

    return {
        "merchant_id": merchant_id,
        "date": day.isoformat(),
        "count": len(transactions),
        "total_amount": sum(t["total_amount"] for t in transactions if not t["is_refund"]),
        "outlier_count": outliers,
        "transactions": transactions,
    }


def card_linkage(session: Session, alert: Alert) -> dict:
    """The cross-merchant view behind a ring alert.

    A card-linkage finding is the one case where the merchant's own day ledger
    cannot show the evidence. The claim is "this card was at these merchants at
    these times", and half of it happened somewhere else — so an analyst
    working the case had to accept the verdict or open the other merchants by
    hand and line the timestamps up themselves.

    This returns the card's transactions across every merchant it touched, in
    time order, with the gap between consecutive ones. For impossible travel it
    also returns the arithmetic: the two places, the distance between their
    centroids, the elapsed minutes, the implied speed, and how far over the
    limit that lands — so the analyst checks a calculation rather than trusting
    a label.

    The transaction ids come from the evidence frozen on the alert, not from a
    fresh query against the card. Nothing here reads `hashed_pan`, so the card
    that did the linking never approaches this boundary.
    """
    from compliance.detection.rings import MerchantNode, related_merchants
    from compliance.detection.ruleset import BY_KEY

    links: list[dict] = []

    for entry in alert.triggering_detectors or []:
        linkage = entry.get("linkage")
        if not linkage:
            continue

        txn_ids = [
            c.get("source_txn_id")
            for c in (entry.get("contributions") or [])
            if c.get("source_txn_id")
        ]
        rows = list(
            session.execute(
                select(Transaction, Merchant)
                .join(Merchant, Merchant.merchant_id == Transaction.merchant_id)
                .where(Transaction.source_txn_id.in_(txn_ids))
                .order_by(Transaction.occurred_at, Transaction.source_txn_id)
            )
        ) if txn_ids else []

        # Which of these merchants are branches of one chain. The analyst is
        # asked to read the same card at four merchants differently depending
        # on whether those merchants are one owner, so it is labelled per row
        # rather than left to be inferred from the message.
        nodes = [
            MerchantNode(
                merchant_id=m.merchant_id,
                hashed_br_number=m.hashed_br_number,
                hashed_merchant_address=m.hashed_merchant_address,
                hashed_merchant_name=m.hashed_merchant_name,
            )
            for _t, m in rows
        ]
        related = related_merchants(nodes)
        groups: dict[str, str] = {}
        for merchant_id in sorted(related):
            if merchant_id in groups:
                continue
            members = sorted(related[merchant_id] | {merchant_id})
            label = f"Chain {chr(ord('A') + len(set(groups.values())))}"
            for member in members:
                groups.setdefault(member, label)

        names = mcc_descriptions(session)
        focus = set(linkage.get("focus_txn_ids") or [])
        # The leg a transaction *arrives* on, so the speed that condemns it can
        # sit on its own row rather than only in a separate table.
        arriving_leg = {
            leg["to_txn_id"]: leg for leg in (linkage.get("legs") or [])
        }

        transactions = []
        previous: datetime | None = None
        for txn, merchant in rows:
            gap = (
                round((txn.occurred_at - previous).total_seconds() / 60.0, 1)
                if previous is not None
                else None
            )
            previous = txn.occurred_at
            leg = arriving_leg.get(txn.source_txn_id)
            transactions.append(
                {
                    "source_txn_id": txn.source_txn_id,
                    "merchant_id": txn.merchant_id,
                    "occurred_at": txn.occurred_at,
                    "total_amount": txn.total_amount,
                    "card_type": txn.card_type,
                    "mcc": merchant.mcc,
                    "mcc_description": (
                        merchant.mcc_description or names.get(merchant.mcc)
                    ),
                    "merchant_district": merchant.merchant_district,
                    "merchant_subdistrict": merchant.merchant_subdistrict,
                    # The merchant whose queue this alert sits in, so the
                    # analyst can see their side against the counterparties'.
                    "is_alert_merchant": txn.merchant_id == alert.merchant_id,
                    "owner_group": groups.get(txn.merchant_id),
                    "minutes_since_previous": gap,
                    # Whether the rule actually fired on this transaction, as
                    # against it being the rest of the card's day around it.
                    "is_focus": txn.source_txn_id in focus,
                    "arrived_at_kmh": leg["kmh"] if leg else None,
                    "arrived_from_km": leg["distance_km"] if leg else None,
                }
            )

        # Roll the trail up per merchant for the case header. A card-linkage
        # case is not about a merchant, so it cannot be headed by one: the
        # subject is the card, and the header has to say where it went.
        by_merchant: dict[str, dict] = {}
        for row in transactions:
            summary = by_merchant.get(row["merchant_id"])
            if summary is None:
                by_merchant[row["merchant_id"]] = {
                    "merchant_id": row["merchant_id"],
                    "mcc": row["mcc"],
                    "mcc_description": row["mcc_description"],
                    "district": row["merchant_district"],
                    "subdistrict": row["merchant_subdistrict"],
                    "owner_group": row["owner_group"],
                    "is_alert_merchant": row["is_alert_merchant"],
                    "transactions": 1,
                    "total_amount": row["total_amount"],
                    "first_seen": row["occurred_at"],
                    "last_seen": row["occurred_at"],
                }
                continue
            summary["transactions"] += 1
            summary["total_amount"] += row["total_amount"]
            summary["last_seen"] = row["occurred_at"]

        template = BY_KEY.get(entry.get("detector", ""))
        links.append(
            {
                "detector": entry.get("detector", ""),
                "label": template.label if template else entry.get("detector", ""),
                "card_ref": linkage.get("card_ref", "card"),
                "related": bool(linkage.get("related")),
                "merchants": list(linkage.get("merchants") or []),
                "legs": list(linkage.get("legs") or []),
                "transactions": transactions,
                "trail": sorted(
                    by_merchant.values(), key=lambda s: s["first_seen"]
                ),
                "first_seen": transactions[0]["occurred_at"] if transactions else None,
                "last_seen": transactions[-1]["occurred_at"] if transactions else None,
                "total_amount": sum(t["total_amount"] for t in transactions),
                "rails": sorted(
                    {t["card_type"] for t in transactions if t["card_type"]}
                ),
            }
        )

    return {
        "alert_id": alert.id,
        "merchant_id": alert.merchant_id,
        "links": links,
    }


def _stat_block(values: list[float], n: int) -> dict:
    """Mean alongside median and MAD.

    The detectors use the median — mean is shown next to it precisely so an
    analyst can see the gap. A mean far above the median is the right-skew
    that makes mean-based baselines unusable here, which is worth seeing
    rather than asserting.
    """
    if not values:
        return {"mean": None, "median": None, "mad": None, "n": n}
    med = median(values)
    return {
        "mean": mean(values),
        "median": med,
        "mad": median([abs(v - med) for v in values]),
        "n": n,
    }


def frozen_contributions(alert: Alert) -> dict[str, list[dict]]:
    """The evidence recorded on the alert, keyed by detector.

    Read from the alert rather than recomputed. A later run with retuned
    thresholds would highlight a different set of transactions, and the
    analyst must see what the detector actually acted on that night — the same
    reason `feature_snapshot` is frozen.
    """
    out: dict[str, list[dict]] = {}
    for entry in alert.triggering_detectors or []:
        rows = entry.get("contributions") or []
        if rows:
            out[entry.get("detector", "")] = rows
    return out


def _typology_verdicts(
    session: Session, alert: Alert, as_of: datetime, metrics: dict, *, start_rank: int
):
    """Family B verdicts, recomputed against the rules currently in force."""
    from compliance.detection.ruleset import Family
    from compliance.pipeline.merchant_study import study_typologies
    from compliance.pipeline.stages import typology_input_for
    from compliance.rules_store import active_rules

    rules = active_rules(session, Family.B)
    if not rules:
        return []
    data = typology_input_for(session, alert.merchant_id, as_of, metrics)
    if data is None:
        return []
    return study_typologies(data, rules, start_rank=start_rank)


def _ring_verdicts(alert: Alert, *, start_rank: int):
    """Family C findings, read back from the alert.

    Not recomputed: a ring is a portfolio-wide result, and re-running the whole
    cross-merchant pass to render one case page would cost far more than it
    tells the analyst. What the run found is what the alert carries.
    """
    from compliance.detection.ruleset import BY_KEY, Family
    from compliance.pipeline.merchant_study import DetectorResult

    results = []
    n = start_rank
    for entry in alert.triggering_detectors or []:
        template = BY_KEY.get(entry.get("detector", ""))
        if template is None or template.family is not Family.C:
            continue
        n += 1
        feature = (alert.feature_snapshot or [{}])[0]
        results.append(
            DetectorResult(
                rank=n,
                detector=template.key,
                label=template.label,
                status="FAIL",
                merchant_value=feature.get("merchant_value"),
                baseline_value=feature.get("baseline_value"),
                deviation=feature.get("deviation"),
                band="outlier",
                feature_name=feature.get("feature_name", template.key),
                message=entry.get("message", template.description),
                family="C",
                contributions=tuple(entry.get("contributions") or []),
            )
        )
    return results


def diagnostics(session: Session, alert: Alert) -> dict:
    """Everything behind one alert: verdicts, statistics, and plot data."""
    merchant = session.get(Merchant, alert.merchant_id)
    profile = session.scalars(
        select(MerchantProfile)
        .where(MerchantProfile.merchant_id == alert.merchant_id)
        .order_by(MerchantProfile.as_of.desc())
    ).first()
    metrics = dict(profile.metrics) if profile else {}

    as_of = alert.as_of or alert.created_at
    day = fetch_day_data(session, alert.merchant_id, as_of)
    results = study_merchant(metrics, day, lane=alert.lane)
    results += _typology_verdicts(session, alert, as_of, metrics, start_rank=len(results))
    results += _ring_verdicts(alert, start_rank=len(results))

    labels = {t.key: t for t in glossary.DETECTORS}
    verdicts = [
        {
            "rank": r.rank,
            "detector": r.detector,
            "label": r.label or labels.get(r.detector, None) and labels[r.detector].label,
            "alert_type": glossary.alert_type_for(r.detector),
            "compared_against": (
                labels[r.detector].compared_against if r.detector in labels else ""
            ),
            "status": r.status,
            "merchant_value": r.merchant_value,
            "baseline_value": r.baseline_value,
            "deviation": r.deviation,
            "band": r.band,
            "message": r.message,
            "family": r.family,
            "contributions": list(r.contributions),
        }
        for r in results
    ]

    # Family A recomputes its verdicts live but has no transaction ids in its
    # inputs, so its evidence comes from the alert. Where both exist the
    # frozen set wins: it is what the detector acted on that night.
    frozen = frozen_contributions(alert)
    for verdict in verdicts:
        recorded = frozen.get(verdict["detector"])
        if recorded:
            verdict["contributions"] = recorded

    # Statistics: the merchant's own scored-day amounts against the two peer
    # cohorts. Peer values are the cohort's fitted centre and spread, which is
    # what the detector compared against — not a fresh recomputation.
    merchant_stats = _stat_block(day.amounts, metrics.get("baseline_n") or len(day.amounts))
    merchant_stats["median"] = metrics.get("baseline_center", merchant_stats["median"])
    merchant_stats["mad"] = metrics.get("baseline_dispersion", merchant_stats["mad"])

    peer_mcc = {
        "mean": None,
        "median": metrics.get("peer_center"),
        "mad": metrics.get("peer_dispersion"),
        "n": metrics.get("peer_merchants") or 0,
    }
    peer_district = {
        "mean": None,
        "median": metrics.get("district_txn_center"),
        "mad": metrics.get("district_txn_dispersion"),
        "n": metrics.get("peer_merchants") or 0,
    }

    modified_z = None
    snapshot = alert.feature_snapshot or []
    if snapshot:
        modified_z = snapshot[0].get("deviation")

    # Peer amounts for the box plot: each cohort member's typical transaction,
    # so the analyst sees the distribution rather than only its summary.
    #
    # Read back from the run's fit rather than rebuilt here. Rebuilding cost a
    # query per member, which is why it had capped itself at 200 of a cohort
    # that runs to hundreds — and it read all-time history with no quarantine
    # exclusion, so the points drawn were not the ones the median and fence
    # were cut from. One row now carries the whole cohort, exactly as fitted.
    cohort = (
        session.scalars(
            select(CohortSnapshot).where(CohortSnapshot.mcc == merchant.mcc)
        ).first()
        if merchant
        else None
    )
    peer_values: list[float] = list(cohort.members) if cohort else []

    return {
        "alert_id": alert.id,
        "merchant_id": alert.merchant_id,
        "scored_date": scored_date(alert),
        "root_cause": root_cause(results),
        "detectors": verdicts,
        "statistics": {
            "merchant": merchant_stats,
            "peer_mcc": peer_mcc,
            "peer_subdistrict": peer_district,
            "modified_z": modified_z,
            "method": metrics.get("baseline_method", "unknown"),
        },
        "hour_density": {
            "merchant": metrics.get("hours_density") or [],
            "cohort": metrics.get("cohort_hours_density") or [],
            "threshold": metrics.get("hours_threshold") or 0.0,
            "cohort_threshold": metrics.get("cohort_hours_threshold") or 0.0,
            "scored_day_hours": day.hours,
        },
        "peer_distribution": {
            "merchant_value": metrics.get("baseline_center"),
            "peer_median": metrics.get("peer_center"),
            "peer_dispersion": metrics.get("peer_dispersion"),
            # The fitted quartiles, not an index into a sample of the cohort.
            "peer_q1": cohort.q1 if cohort else None,
            "peer_q3": cohort.q3 if cohort else None,
            "peer_upper_fence": metrics.get("peer_fence"),
            # Reported from the same set that is drawn, so the count on the
            # screen and the points on the plot cannot describe different
            # cohorts.
            "n_merchants": len(peer_values),
            "peer_values": peer_values,
        },
        "window": {
            "window_start": metrics.get("window_start"),
            "window_end": metrics.get("window_end"),
            "window_days": metrics.get("window_days"),
            "lag_days": metrics.get("lag_days"),
            "quarantined_days": metrics.get("quarantined_days"),
        },
    }


def mcc_descriptions(session: Session) -> dict[str, str]:
    """MCC code to description, resolved across the whole portfolio.

    A description belongs to the code, not to the merchant, so a merchant row
    missing one can borrow the description its peers carry. Without this a
    synthetic or partially-loaded merchant shows a bare code, which is exactly
    the ambiguity the labelling work set out to remove.
    """
    rows = session.execute(
        select(Merchant.mcc, Merchant.mcc_description).where(
            Merchant.mcc_description.is_not(None)
        )
    )
    # First non-null wins; codes are stable so any member's wording will do.
    resolved: dict[str, str] = {}
    for mcc, description in rows:
        resolved.setdefault(mcc, description)
    return resolved
