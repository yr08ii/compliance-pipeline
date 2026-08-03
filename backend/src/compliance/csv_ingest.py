"""CSV ingestion — stream-parse uploaded CSV into the standard row format.

For large files (millions of rows), this module streams line by line instead
of loading the entire file into memory.  ``iter_csv_rows`` yields one dict
per row; callers process and discard rows in batches.

Column mapping is flexible: common header variations are resolved to the
internal field names.  Unrecognised columns are silently ignored.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# ── Column name mapping ──────────────────────────────────────────────────────

_COLUMN_MAP: dict[str, str] = {
    # Primary keys
    "payment_id": "payment_id",
    "txn_id": "payment_id",
    "transaction_id": "payment_id",
    "source_txn_id": "payment_id",
    "id": "payment_id",
    # Merchant
    "merchant_id": "merchant_id",
    "merchant": "merchant_id",
    "merchantid": "merchant_id",
    # Amount
    "total_amount": "total_amount",
    "amount": "total_amount",
    "txn_amount": "total_amount",
    "transaction_amount": "total_amount",
    "gross_amount": "total_amount",
    # Net amount
    "net_amount": "net_amount",
    "net": "net_amount",
    # Timestamp
    "hkt_transaction_time": "hkt_transaction_time",
    "transaction_time": "hkt_transaction_time",
    "txn_time": "hkt_transaction_time",
    "occurred_at": "hkt_transaction_time",
    "datetime": "hkt_transaction_time",
    "date": "hkt_transaction_time",
    "timestamp": "hkt_transaction_time",
    "time": "hkt_transaction_time",
    # Merchant metadata
    "mcc": "mcc",
    "merchant_category_code": "mcc",
    "mcc_description": "mcc_description",
    "merchant_name": "hashed_merchant_name",
    "hashed_merchant_name": "hashed_merchant_name",
    "city": "city",
    "merchant_area": "merchant_area",
    "area": "merchant_area",
    "merchant_district": "merchant_district",
    "district": "merchant_district",
    "merchant_subdistrict": "merchant_subdistrict",
    "subdistrict": "merchant_subdistrict",
    "business_plan": "business_plan",
    "business_nature": "business_nature",
    "ownership_or_business_type": "ownership_or_business_type",
    "merchant_status": "merchant_status",
    "status": "merchant_status",
    "agent_id": "agent_id",
    "hashed_br_number": "hashed_br_number",
    "hashed_merchant_address": "hashed_merchant_address",
    "registered_address": "registered_address",
    # Card metadata
    "card_type": "card_type",
    "card_origin": "card_origin",
    "card_issuing_country": "card_issuing_country",
    "issuing_country": "card_issuing_country",
    "country": "card_issuing_country",
    "card_issuing_bank": "card_issuing_bank",
    "card_bin": "card_bin",
    "bin": "card_bin",
    "payment_gateway": "payment_gateway",
    "gateway": "payment_gateway",
    "currency": "currency",
    "transaction_status": "transaction_status",
    "hashed_pan": "hashed_pan",
    "pan_hash": "hashed_pan",
    "is_refund": "is_refund",
    "refund": "is_refund",
    "geo": "geo",
    "location": "geo",
}

# Required fields — rows missing any of these are skipped.
_REQUIRED = {"payment_id", "merchant_id", "total_amount", "hkt_transaction_time"}


def _normalise_header(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_").replace("-", "_")


def _map_columns(header: list[str]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for i, col in enumerate(header):
        key = _normalise_header(col)
        internal = _COLUMN_MAP.get(key)
        if internal:
            mapping[i] = internal
    return mapping


def validate_csv(path: str) -> tuple[dict[int, str], int]:
    """Read just the header row and return (column_map, estimated_row_count).

    Does NOT load data rows into memory.
    """
    p = Path(path)
    # Quick row count by counting newlines (fast, no memory)
    with open(p, "rb") as f:
        row_count = sum(1 for _ in f) - 1  # subtract header

    with open(p, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)

    col_map = _map_columns(header)
    mapped_fields = set(col_map.values())
    missing = _REQUIRED - mapped_fields
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {', '.join(sorted(missing))}. "
            f"Found: {', '.join(h.strip() for h in header)}"
        )
    return col_map, row_count


def iter_csv_rows(
    path: str, col_map: dict[int, str] | None = None
) -> "csv.DictReader":
    """Yield one dict per CSV row, streaming from disk.

    Memory usage is O(1) regardless of file size.  Caller must iterate
    and process rows without accumulating them.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)

    if col_map is None:
        col_map = _map_columns(header)

    # Build fieldnames where mapped columns use internal names
    fieldnames = []
    for i, col in enumerate(header):
        key = _normalise_header(col)
        internal = col_map.get(i)
        fieldnames.append(internal if internal else f"_skip_{i}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        dict_reader = csv.DictReader(f, fieldnames=fieldnames)
        # DictReader with explicit fieldnames treats the first row as data,
        # so we must skip the header row manually.
        next(dict_reader, None)
        for row in dict_reader:
            # Drop skip fields
            record = {k: v.strip() for k, v in row.items() if not k.startswith("_skip_")}
            # Skip rows where required fields are empty
            if not all(record.get(f) for f in _REQUIRED):
                continue
            yield record
