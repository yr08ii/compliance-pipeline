"""Stage 1 — pull. Parse a delivered payload into the local store.

Parsing is kept behind a narrow boundary (`parse_json` returns plain dicts)
so a different delivery format slots in without touching anything downstream.
The exact format is still being confirmed with the source team; JSON is
supported here, and an HTML-table parser would only need to satisfy the same
`list[dict]` contract.

Idempotency is the property that matters most: an automated pull can deliver
the same file twice, or be re-run after a failure, and neither may double-count
a transaction. `payment_id` is the key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.models import Merchant, Transaction

# Statuses that represent value moving back to the cardholder. Refunds are
# excluded from ticket baselines and drive the refund-ratio rule instead.
REFUND_STATUSES = {"REFUNDED", "REFUND", "REVERSED", "CHARGEBACK"}

# Never copied: display-only, and treating it as an identifier is how a masked
# PAN turns into a linkage key by accident.
NEVER_STORE = {"masked_pan"}

_MERCHANT_FIELDS = (
    "mcc", "mcc_description", "agent_id", "hashed_merchant_name",
    "hashed_br_number", "hashed_merchant_address", "city", "merchant_area",
    "merchant_district", "merchant_subdistrict", "business_plan",
    "business_nature", "ownership_or_business_type", "merchant_status",
)

_TXN_FIELDS = (
    "card_type", "card_origin", "card_issuing_country", "card_issuing_bank",
    "payment_gateway", "currency", "transaction_status", "hashed_pan",
)


@dataclass(frozen=True)
class IngestResult:
    inserted: int
    skipped: int
    merchants: int


def parse_json(payload: str) -> list[dict]:
    """Accept the shapes an export realistically arrives in.

    A bare array, an object wrapping the rows, or newline-delimited objects —
    all reduce to a list of dicts so the caller never branches on format.
    """
    text = payload.strip()
    if not text:
        return []

    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        # Newline-delimited JSON.
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    if isinstance(loaded, list):
        return loaded
    if isinstance(loaded, dict):
        for key in ("data", "rows", "transactions", "records"):
            if isinstance(loaded.get(key), list):
                return loaded[key]
        return [loaded]
    raise ValueError("unrecognised JSON payload shape")


# The source column is hkt_transaction_time — Hong Kong local. Hong Kong has
# observed no DST since 1979, so a fixed offset is exact rather than an
# approximation.
HKT = timezone(timedelta(hours=8))


def _parse_time(value: str) -> datetime:
    """Source times are Hong Kong local, and are stored as such.

    Stamping them UTC would shift every transaction eight hours: day
    boundaries would fall mid-afternoon and "trading at 3am" would mean
    something else entirely.
    """
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=HKT)
        except ValueError:
            continue
    raise ValueError(f"unparseable transaction time: {value!r}")


def ingest_payload(session: Session, payload: str) -> IngestResult:
    """Load a delivered payload, skipping anything already stored."""
    rows = parse_json(payload)
    if not rows:
        return IngestResult(0, 0, 0)

    known = set(
        session.scalars(
            select(Transaction.source_txn_id).where(
                Transaction.source_txn_id.in_([str(r["payment_id"]) for r in rows])
            )
        )
    )
    merchants = {m.merchant_id: m for m in session.scalars(select(Merchant))}

    inserted = skipped = 0
    touched: set[str] = set()

    for row in rows:
        payment_id = str(row["payment_id"])
        merchant_id = str(row["merchant_id"])

        merchant = merchants.get(merchant_id)
        if merchant is None:
            merchant = Merchant(merchant_id=merchant_id, mcc=str(row.get("mcc") or ""))
            session.add(merchant)
            merchants[merchant_id] = merchant
        # Merchant attributes are re-stated on every row; the latest delivery
        # wins, so status changes and re-registrations land without a separate feed.
        for field in _MERCHANT_FIELDS:
            if row.get(field) is not None:
                setattr(merchant, field, str(row[field]))
        touched.add(merchant_id)

        if payment_id in known:
            skipped += 1
            continue

        total = float(row["total_amount"])
        net = row.get("net_amount")
        status = str(row.get("transaction_status") or "").upper()
        # Refunds appear either as a status or as a negative amount; the exact
        # encoding is still being confirmed, so both are honoured. The amount is
        # stored as a magnitude with the direction carried by the flag.
        is_refund = status in REFUND_STATUSES or total < 0

        txn = Transaction(
            source_txn_id=payment_id,
            merchant_id=merchant_id,
            total_amount=abs(total),
            net_amount=abs(float(net)) if net not in (None, "") else None,
            occurred_at=_parse_time(row["hkt_transaction_time"]),
            is_refund=is_refund,
        )
        for field in _TXN_FIELDS:
            if row.get(field) is not None:
                setattr(txn, field, str(row[field]))
        session.add(txn)
        known.add(payment_id)
        inserted += 1

    return IngestResult(inserted=inserted, skipped=skipped, merchants=len(touched))
