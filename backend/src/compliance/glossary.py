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
        "Unusually large sale",
        "A sale far bigger than this merchant's own typical sale.",
        "Its own history",
    ),
    Term(
        "count_vs_own_baseline",
        "Unusually busy day",
        "Far more sales today than this merchant normally makes in a day.",
        "Its own history",
    ),
    Term(
        "burst_rate_vs_own_baseline",
        "Sudden burst of sales",
        "Many sales crammed into a short window, well beyond this merchant's usual pace — "
        "the day's total can look ordinary.",
        "Its own history",
    ),
    Term(
        "level_shift_ramp",
        "Steadily climbing",
        "Takings have grown week after week. No single day looks odd, but the level has "
        "shifted well above where it was.",
        "Its own history",
    ),
    Term(
        "hour_vs_own_pattern",
        "Trading outside its usual hours",
        "A sale at a time of day this merchant almost never trades.",
        "Its own history",
    ),
    Term(
        "card_origin_vs_own_mix",
        "Unfamiliar card origin",
        "Cards issued in a country this merchant rarely or never sees.",
        "Its own history",
    ),
    Term(
        "ticket_vs_mcc_peers",
        "Large sale for this trade",
        "A sale far bigger than merchants in the same line of business normally take.",
        "Same trade",
    ),
    Term(
        "merchant_level_vs_mcc_peers",
        "Prices out of line for its trade",
        "This merchant's typical sale is far higher than others in the same line of "
        "business — not one odd sale, but its whole price level.",
        "Same trade",
    ),
    Term(
        "count_vs_mcc_peers",
        "Busier than its trade",
        "Far more sales today than merchants in the same line of business normally make.",
        "Same trade",
    ),
    Term(
        "hour_vs_mcc_peers",
        "Trading when its trade is shut",
        "A sale at a time of day this line of business almost never operates.",
        "Same trade",
    ),
    Term(
        "ticket_vs_subdistrict_peers",
        "Large sale for this area",
        "A sale far bigger than merchants in the same district normally take.",
        "Same district",
    ),
    Term(
        "foreign_card_ratio_vs_subdistrict",
        "More overseas cards than its area",
        "A far higher share of overseas-issued cards than other merchants in the same "
        "district see.",
        "Same district",
    ),
)


FEATURES: tuple[Term, ...] = (
    Term("ticket_amount", "Sale amount", "The value of a single sale.", ""),
    Term("daily_transaction_count", "Sales today", "How many sales the merchant made.", ""),
    Term("peak_transactions_per_hour", "Busiest hour",
         "The most sales made within any single hour.", ""),
    Term("level_shift_7d_vs_90d", "Recent vs long-run level",
         "This week's typical sale against the last three months'.", ""),
    Term("transaction_hour", "Time of sale", "When the sale happened, local time.", ""),
    Term("hour_vs_trade_hours", "Time of sale", "When the sale happened, local time.", ""),
    Term("typical_ticket_vs_mcc_peers", "Typical sale", "This merchant's usual sale value.", ""),
    Term("ticket_vs_mcc_peers", "Sale amount", "The value of a single sale.", ""),
    Term("ticket_vs_subdistrict_peers", "Sale amount", "The value of a single sale.", ""),
    Term("daily_count_vs_mcc_peers", "Sales today", "How many sales the merchant made.", ""),
    Term("foreign_card_share_vs_district", "Overseas card share",
         "The share of sales paid with cards issued outside Hong Kong.", ""),
)


LANES: tuple[Term, ...] = (
    Term("A", "Established",
         "Has enough trading history to be judged against its own past.",
         "Its own history and its peers"),
    Term("B", "New or quiet",
         "Too little history for a reliable pattern of its own, so it is judged against "
         "similar merchants instead.",
         "Its peers only"),
)


BASELINE_METHODS: tuple[Term, ...] = (
    Term("mad", "Established pattern",
         "Enough varied history to describe what is normal for this merchant.", ""),
    Term("scaled_iqr", "Established pattern (narrow)",
         "Mostly one price point, with enough variation elsewhere to still judge it.", ""),
    Term("constant", "Single fixed price",
         "Every sale is the same amount, so there is no spread to measure. Handled by "
         "rules rather than by comparison.", ""),
    Term("insufficient_data", "Not enough history",
         "Too few sales, or over too short a period, to say what normal looks like yet.", ""),
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
