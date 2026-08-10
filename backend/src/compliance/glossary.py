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
        "Overseas card origin vs own history",
        "Transactions on cards issued in an overseas country this merchant rarely or "
        "never sees. Home-issued cards and wallet rails are outside this check — it "
        "watches which foreign countries pay, and whether that has changed.",
        "Own history of overseas cards",
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
        "amount_vs_payment_method_baseline",
        "Amount anomaly for this payment method",
        "A transaction far larger than this merchant normally takes on that payment "
        "method. HKD 3,000 on Octopus is remarkable; the same on Visa is not.",
        "Own history, per payment method",
    ),
    Term(
        "foreign_card_ratio_vs_subdistrict",
        "Overseas card share vs subdistrict baseline",
        "A far higher share of card transactions on overseas-issued cards than other "
        "merchants in the same district. Measured over cards only — wallet rails have "
        "no issuing country and are not counted on either side of the share.",
        "Subdistrict peer group",
    ),
    # ---- Family B: typology rules. These match a pattern rather than measure
    # a deviation, so "compared against" names the rule's own threshold.
    Term(
        "structuring_below_threshold",
        "Structuring below a reporting threshold",
        "Several transactions in one day sitting just under a reporting threshold, at a "
        "merchant whose normal transaction is nowhere near it. No single transaction is "
        "unusual — the clustering is.",
        "Reporting threshold",
    ),
    Term(
        "refund_ratio_spike",
        "Refund / credit abuse",
        "An unusually high share of the day's value refunded. Refunds move value back out "
        "with far less scrutiny than a payout.",
        "Rule threshold",
    ),
    Term(
        "bust_out",
        "Bust-out pattern",
        "A quiet history, then a sharp climb in daily value, then refunds — a merchant "
        "maximising takings before disappearing. Each phase alone is ordinary.",
        "Own history",
    ),
    Term(
        "dormant_reactivation",
        "Dormant account reactivation",
        "A merchant silent for a long stretch that returns with immediate volume rather "
        "than easing in. Genuine reactivation ramps; a takeover starts at full speed.",
        "Own history",
    ),
    Term(
        "rapid_movement",
        "Rapid movement / pass-through",
        "Value arriving and leaving the same day at near-parity, leaving no resting "
        "balance. Read from transactions only — a settlement view would be stronger.",
        "Rule threshold",
    ),
    Term(
        "declared_vs_actual_mismatch",
        "Declared vs actual business mismatch",
        "The registered category does not match how the merchant actually trades, on both "
        "ticket size and operating hours — the transaction-laundering signature.",
        "MCC peer group",
    ),
    Term(
        "decline_ratio_spike",
        "Declined authorisation spike",
        "An unusually high share of failed authorisations. This is the merchant-side read "
        "of card testing; we measure the merchant's decline rate, not stolen cards.",
        "Rule threshold",
    ),
    # ---- Family C: ring detection. Portfolio-wide, so these compare a
    # merchant against other merchants rather than against any baseline.
    Term(
        "shared_identity_ring",
        "Shared merchant identity",
        "Several distinct merchant IDs sharing a business registration, address, or "
        "trading name — the shell-merchant ring.",
        "Other merchants",
    ),
    Term(
        "agent_alert_concentration",
        "Onboarding agent concentration",
        "The agent who onboarded this merchant has a book that alerts far more often than "
        "the portfolio as a whole.",
        "Portfolio",
    ),
    Term(
        "card_across_related_merchants",
        "One card across related merchants",
        "A single card used at more merchants under common ownership in one day than a "
        "customer plausibly would.",
        "Other merchants",
    ),
    Term(
        "card_swarm",
        "Card swarming across merchants",
        "One card appearing at many unrelated merchants inside a short window — a ring "
        "moving through an area.",
        "Other merchants",
    ),
    Term(
        "impossible_geo_velocity",
        "Impossible travel between merchants",
        "One card used at two places further apart than the time between the transactions "
        "allows. Distances are between subdistrict centroids, so the implied speed is a "
        "lower bound.",
        "Other merchants",
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
    Term("amount_on_visa", "Transaction amount (Visa)", "Value of a Visa transaction.", ""),
    Term("foreign_card_share_vs_district", "Overseas card share",
         "The share of transactions on cards issued outside Hong Kong.", ""),
    # ---- Family B
    Term("near_threshold_transaction_count", "Transactions just under the threshold",
         "How many of the day's transactions sat inside the band just below the "
         "reporting threshold.", ""),
    Term("refund_value_share", "Refunded share of the day",
         "Value refunded divided by value taken, on the scored day.", ""),
    Term("recent_vs_earlier_daily_value", "Recent daily value vs earlier",
         "Median daily value over the recent window, against the level before it.", ""),
    Term("days_dormant_before_return", "Days dormant before returning",
         "How long the merchant was inactive before the scored day.", ""),
    Term("same_day_in_out_mismatch", "Gap between value in and value out",
         "How far the day's refunds differ from the day's takings. Near zero means "
         "nothing was left behind.", ""),
    Term("declared_category_divergence", "Divergence from declared MCC",
         "How many times its MCC cohort's typical transaction this merchant's own is.", ""),
    Term("declined_authorisation_share", "Declined share of authorisations",
         "Declined authorisations divided by all attempts on the scored day.", ""),
    # ---- Family C
    Term("merchants_sharing_hashed_br_number", "Merchants sharing a registration",
         "How many merchant IDs share this business registration number.", ""),
    Term("merchants_sharing_hashed_merchant_address", "Merchants sharing an address",
         "How many merchant IDs share this registered address.", ""),
    Term("merchants_sharing_hashed_merchant_name", "Merchants sharing a name",
         "How many merchant IDs share this trading name.", ""),
    Term("agent_book_alert_rate", "Agent's book alert rate",
         "The share of this onboarding agent's merchants currently carrying an alert.", ""),
    Term("related_merchants_per_card_per_day", "Related merchants per card per day",
         "How many commonly-owned merchants one card was used at in a single day.", ""),
    Term("unrelated_merchants_per_card_window", "Unrelated merchants per card",
         "How many merchants with no ownership link one card touched inside the window.", ""),
    Term("implied_travel_speed_kmh", "Implied travel speed",
         "Distance between the two merchants divided by the time between the "
         "transactions, in km/h.", ""),
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
    # Family B and C get their own badges rather than being folded into the
    # Family A four. A typology match and a statistical outlier call for
    # completely different follow-up, and a queue that shows them as the same
    # kind of thing makes an analyst open each one to find out which it is.
    Term("typology_match", "Typology match",
         "The day's activity matches a known laundering pattern. Individual "
         "transactions may each look ordinary — the shape is the signal.", ""),
    Term("ring_signal", "Ring signal",
         "This merchant is linked to others: shared identity, a shared "
         "onboarding agent, or a card connecting them.", ""),
)

# Which badge each detector belongs to. Deliberately exhaustive: a detector
# with no alert type would render an unlabelled badge in the queue.
DETECTOR_ALERT_TYPE: dict[str, str] = {
    "amount_vs_own_baseline": "single_txn_spike",
    "amount_vs_payment_method_baseline": "single_txn_spike",
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
    # Family B — every typology rule.
    "structuring_below_threshold": "typology_match",
    "refund_ratio_spike": "typology_match",
    "bust_out": "typology_match",
    "dormant_reactivation": "typology_match",
    "rapid_movement": "typology_match",
    "declared_vs_actual_mismatch": "typology_match",
    "decline_ratio_spike": "typology_match",
    # Family C — every ring rule.
    "shared_identity_ring": "ring_signal",
    "agent_alert_concentration": "ring_signal",
    "card_across_related_merchants": "ring_signal",
    "card_swarm": "ring_signal",
    "impossible_geo_velocity": "ring_signal",
}


def alert_type_for(detector: str) -> str:
    """The badge a detector maps to. Falls back so a new detector is visible
    rather than silently unbadged."""
    return DETECTOR_ALERT_TYPE.get(detector, "single_txn_spike")


def stage_label(key: str) -> str:
    for term in CASE_STAGES:
        if term.key == key:
            return term.label
    return key


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


# The stages a confirmed case moves through. A controlled vocabulary rather
# than free text: the follow-through board sorts and ages by stage, and an
# auditor needs the same words to mean the same thing every time.
CASE_STAGES: tuple[Term, ...] = (
    Term("OPENED", "Opened", "Confirmed as a true alert and opened for follow-up.", ""),
    Term("MERCHANT_CONTACTED", "Merchant contacted",
         "The merchant has been asked to explain the activity.", ""),
    Term("DOCUMENTS_REQUESTED", "Documents requested",
         "Supporting records have been requested.", ""),
    Term("DOCUMENTS_RECEIVED", "Documents received",
         "The merchant supplied records; not yet checked.", ""),
    Term("DOCUMENTS_VERIFIED", "Documents verified",
         "Records checked and consistent with the transactions.", ""),
    Term("NO_RESPONSE", "No response",
         "The merchant could not be reached or did not reply.", ""),
    Term("ESCALATED_LEGAL", "Escalated to legal",
         "Referred for legal review. Recorded here; acted on elsewhere.", ""),
    Term("STR_FILED", "STR filed",
         "A suspicious transaction report was filed externally. Do not disclose "
         "this to the merchant.", ""),
    Term("CLEARED", "Cleared",
         "Resolved: the merchant was verified as legitimate.", ""),
    Term("CONFIRMED_ILLICIT", "Confirmed illicit",
         "Resolved: the activity was confirmed as illicit.", ""),
    Term("OFFBOARDED", "Offboarded",
         "Resolved: the merchant was removed from the platform. Recorded here; "
         "executed elsewhere.", ""),
)

# Stages that end a case. Anything else leaves it open on the board, where a
# case with no update for too long is surfaced as stale.
RESOLVED_STAGES = frozenset({"CLEARED", "CONFIRMED_ILLICIT", "OFFBOARDED"})

VERDICTS: tuple[Term, ...] = (
    Term("TRUE_POSITIVE", "True alert",
         "The activity is genuinely suspicious. Opens a case, and the day is "
         "excluded from future baselines so the system does not learn it as normal.",
         ""),
    Term("FALSE_POSITIVE", "False alert",
         "The activity was legitimate. Stays in the baseline data — removing it "
         "would teach the system that normal trading is abnormal.", ""),
    Term("INCONCLUSIVE", "Inconclusive",
         "Not enough information to decide. Kept as a distinct outcome rather "
         "than forced into either bucket, which would corrupt the training data.",
         ""),
)
