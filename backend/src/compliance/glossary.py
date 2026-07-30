"""Plain-English names for the internal detector identifiers.

The identifiers stay as they are everywhere that matters for audit — in the
database, in `triggering_detectors`, in the immutable feature snapshot — so a
record written today still reads the same in two years. This module is the
single place they are translated for people, and it lives next to the
detectors rather than in the frontend so a new detector cannot ship with no
label.

The wording aims at an analyst opening a queue, not at whoever wrote the
statistics: it says what happened and why it was worth raising, never how it
was computed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    """One internal identifier and how to say it out loud."""

    key: str
    label: str
    meaning: str
    # What the detector compares against — the distinction analysts most need,
    # because "unusual for this merchant" and "unusual for this trade" call for
    # different follow-up questions.
    compared_against: str


DETECTORS: tuple[Term, ...] = (
    Term(
        "amount_vs_own_baseline",
        "Amount anomaly vs own baseline",
        "A transaction far larger than this merchant's typical transaction amount.",
        "Own history",
    ),
    Term(
        "count_vs_own_baseline",
        "Transaction count vs own baseline",
        "Far more transactions today than this merchant normally processes in a day.",
        "Own history",
    ),
    Term(
        "burst_rate_vs_own_baseline",
        "Transaction velocity vs own baseline",
        "Many transactions inside a single hour, well beyond this merchant's usual rate. "
        "The day's total can still look ordinary.",
        "Own history",
    ),
    Term(
        "level_shift_ramp",
        "Sustained level shift (7d vs 90d)",
        "This merchant's typical transaction amount over the last 7 days is well above its "
        "level over the last 90 days. No single day is unusual on its own.",
        "Own history",
    ),
    Term(
        "hour_vs_own_pattern",
        "Trading outside own operating hours",
        "A transaction at a time of day this merchant almost never processes transactions.",
        "Own history",
    ),
    Term(
        "card_origin_vs_own_mix",
        "Card origin vs own history",
        "Transactions on cards issued in a country this merchant rarely or never sees.",
        "Own history",
    ),
    Term(
        "ticket_vs_mcc_peers",
        "Amount anomaly vs MCC baseline",
        "A transaction far larger than merchants in the same category normally process.",
        "MCC peer group",
    ),
    Term(
        "merchant_level_vs_mcc_peers",
        "Merchant level vs MCC baseline",
        "This merchant's typical transaction amount is far above others in the same "
        "category. Not one transaction — its whole level.",
        "MCC peer group",
    ),
    Term(
        "count_vs_mcc_peers",
        "Transaction count vs MCC baseline",
        "Far more transactions today than merchants in the same category normally process.",
        "MCC peer group",
    ),
    Term(
        "hour_vs_mcc_peers",
        "Trading outside MCC operating hours",
        "A transaction at a time of day this merchant category almost never operates.",
        "MCC peer group",
    ),
    Term(
        "ticket_vs_subdistrict_peers",
        "Amount anomaly vs subdistrict baseline",
        "A transaction far larger than merchants in the same district normally process.",
        "Subdistrict peer group",
    ),
    Term(
        "foreign_card_ratio_vs_subdistrict",
        "Overseas card share vs subdistrict baseline",
        "A far higher share of transactions on overseas-issued cards than other merchants "
        "in the same district.",
        "Subdistrict peer group",
    ),
)


FEATURES: tuple[Term, ...] = (
    Term("ticket_amount", "Transaction amount",
         "The value of a single transaction.", ""),
    Term("daily_transaction_count", "Number of transactions",
         "How many transactions the merchant processed that day.", ""),
    Term("peak_transactions_per_hour", "Transactions per hour (peak)",
         "The most transactions processed within any single hour — transaction velocity.", ""),
    Term("level_shift_7d_vs_90d", "Typical amount: 7 days vs 90 days",
         "Median transaction amount over the last 7 days, against the last 90.", ""),
    Term("transaction_hour", "Time of transaction",
         "When the transaction was processed, Hong Kong time.", ""),
    Term("hour_vs_trade_hours", "Time of transaction",
         "When the transaction was processed, Hong Kong time.", ""),
    Term("typical_ticket_vs_mcc_peers", "Typical transaction amount",
         "This merchant's median transaction amount.", ""),
    Term("ticket_vs_mcc_peers", "Transaction amount",
         "The value of a single transaction.", ""),
    Term("ticket_vs_subdistrict_peers", "Transaction amount",
         "The value of a single transaction.", ""),
    Term("daily_count_vs_mcc_peers", "Number of transactions",
         "How many transactions the merchant processed that day.", ""),
    Term("foreign_card_share_vs_district", "Overseas card share",
         "The share of transactions on cards issued outside Hong Kong.", ""),
)


LANES: tuple[Term, ...] = (
    Term("A", "Established history",
         "Enough transaction history to be judged against its own past.",
         "Its own history and its peers"),
    Term("B", "Limited history",
         "Too few transactions, or over too short a period, to judge against its own past. "
         "Judged against similar merchants instead.",
         "Its peers only"),
)


BASELINE_METHODS: tuple[Term, ...] = (
    Term("mad", "Established",
         "Enough varied transaction history to describe what is normal for this merchant.", ""),
    Term("scaled_iqr", "Established (narrow spread)",
         "Mostly one transaction amount, with enough variation elsewhere to still judge it.", ""),
    Term("constant", "Single fixed amount",
         "Every transaction is the same amount, so there is no spread to measure. Handled by "
         "rules rather than by comparison.", ""),
    Term("insufficient_data", "Not enough history",
         "Too few transactions, or over too short a period, to establish what is normal.", ""),
)


# The four families an analyst triages differently. A single-transaction
# outlier is one checkout to examine; a peer discrepancy is a business-profile
# question; a temporal or geographic anomaly points somewhere else again.
ALERT_TYPES: tuple[Term, ...] = (
    Term("single_txn_spike", "Single txn spike",
         "One transaction breached this merchant's own bounds on the scored day.", ""),
    Term("mcc_peer_discrepancy", "MCC peer discrepancy",
         "The merchant's profile diverges from others sharing its MCC.", ""),
    Term("subdistrict_anomaly", "Subdistrict anomaly",
         "Amounts or card origins diverge from the local area baseline.", ""),
    Term("temporal_anomaly", "Temporal anomaly",
         "Transactions fell outside established operating hours.", ""),
)

# Which badge each detector belongs to. Deliberately exhaustive: a detector
# with no alert type would render an unlabelled badge in the queue.
DETECTOR_ALERT_TYPE: dict[str, str] = {
    "amount_vs_own_baseline": "single_txn_spike",
    "count_vs_own_baseline": "single_txn_spike",
    "burst_rate_vs_own_baseline": "single_txn_spike",
    "level_shift_ramp": "mcc_peer_discrepancy",
    "hour_vs_own_pattern": "temporal_anomaly",
    "card_origin_vs_own_mix": "subdistrict_anomaly",
    "ticket_vs_mcc_peers": "mcc_peer_discrepancy",
    "merchant_level_vs_mcc_peers": "mcc_peer_discrepancy",
    "count_vs_mcc_peers": "mcc_peer_discrepancy",
    "hour_vs_mcc_peers": "temporal_anomaly",
    "ticket_vs_subdistrict_peers": "subdistrict_anomaly",
    "foreign_card_ratio_vs_subdistrict": "subdistrict_anomaly",
}


def alert_type_for(detector: str) -> str:
    """The badge a detector maps to. Falls back so a new detector is visible
    rather than silently unbadged."""
    return DETECTOR_ALERT_TYPE.get(detector, "single_txn_spike")


def as_dicts(terms: tuple[Term, ...]) -> list[dict]:
    return [
        {
            "key": t.key,
            "label": t.label,
            "meaning": t.meaning,
            "compared_against": t.compared_against,
        }
        for t in terms
    ]
