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
        "Unusually large transaction",
        "A transaction far larger than this merchant's typical transaction amount.",
        "Its own history",
    ),
    Term(
        "count_vs_own_baseline",
        "Unusual number of transactions",
        "Far more transactions today than this merchant normally processes in a day.",
        "Its own history",
    ),
    Term(
        "burst_rate_vs_own_baseline",
        "Unusual transaction velocity",
        "Many transactions inside a single hour, well beyond this merchant's usual rate. "
        "The day's total can still look ordinary.",
        "Its own history",
    ),
    Term(
        "level_shift_ramp",
        "Transaction amounts trending up",
        "This merchant's typical transaction amount over the last 7 days is well above its "
        "level over the last 90 days. No single day is unusual on its own.",
        "Its own history",
    ),
    Term(
        "hour_vs_own_pattern",
        "Transaction outside usual hours",
        "A transaction at a time of day this merchant almost never processes transactions.",
        "Its own history",
    ),
    Term(
        "card_origin_vs_own_mix",
        "Unfamiliar card issuing country",
        "Transactions on cards issued in a country this merchant rarely or never sees.",
        "Its own history",
    ),
    Term(
        "ticket_vs_mcc_peers",
        "Large transaction for this category",
        "A transaction far larger than merchants in the same category normally process.",
        "Same merchant category",
    ),
    Term(
        "merchant_level_vs_mcc_peers",
        "Typical amount high for this category",
        "This merchant's typical transaction amount is far above others in the same "
        "category. Not one transaction — its whole level.",
        "Same merchant category",
    ),
    Term(
        "count_vs_mcc_peers",
        "More transactions than its category",
        "Far more transactions today than merchants in the same category normally process.",
        "Same merchant category",
    ),
    Term(
        "hour_vs_mcc_peers",
        "Transaction outside category hours",
        "A transaction at a time of day this merchant category almost never operates.",
        "Same merchant category",
    ),
    Term(
        "ticket_vs_subdistrict_peers",
        "Large transaction for this district",
        "A transaction far larger than merchants in the same district normally process.",
        "Same district",
    ),
    Term(
        "foreign_card_ratio_vs_subdistrict",
        "Overseas card share high for district",
        "A far higher share of transactions on overseas-issued cards than other merchants "
        "in the same district.",
        "Same district",
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
