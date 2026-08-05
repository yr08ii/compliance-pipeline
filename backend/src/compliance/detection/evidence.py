"""The shape every detector uses to point at its evidence.

Family A answers "how far from normal" with a number. That was enough to rank
a queue and never enough to work a case: an analyst told a merchant's day was
anomalous still had to scan the ledger to find *which* transactions said so,
and *which field* of those transactions was the reason. A high-amount alert
and an unusual-card-origin alert can highlight the same row for completely
different reasons, and the row alone cannot say which.

So a detector reports, alongside its score, the transactions that drove it and
the column of each that carries the cause. The ledger then highlights exactly
those rows and annotates exactly that field.

Detectors with no single driving transaction — a daily count, a velocity
burst, a ring — return contributions covering the whole set they judged, or
none at all where the merchant rather than any transaction is the subject.
Silence here is meaningful: it says "no transaction is the reason", which is
different from "we did not look".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Contribution:
    """One transaction's part in one detector firing."""

    source_txn_id: str
    # The column that carries the cause, using the source schema's own name so
    # the UI can highlight the exact cell and an auditor can trace it back.
    field: str
    # That column's value, already formatted for display.
    value: str
    # Why this transaction is implicated, in one phrase.
    reason: str

    def as_dict(self) -> dict:
        return {
            "source_txn_id": self.source_txn_id,
            "field": self.field,
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RuleHit:
    """One rule instance firing for one merchant on one scored day."""

    rule_id: str
    template: str
    label: str
    # Names the template and the parameters in force, so the reason on the
    # alert stays meaningful after somebody retunes the rule.
    reason_code: str
    sub_score: float
    message: str
    # Present only where the rule has a natural numeric basis (a ratio, a
    # count). Rules whose signal is a shape rather than a magnitude leave it
    # empty rather than inventing a number to fill the panel.
    feature: dict | None = None
    contributions: tuple[Contribution, ...] = ()
