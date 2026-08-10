"""Local bootstrap: generate synthetic history and run the pipeline once."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from compliance.db import SessionLocal
from compliance.models import (
    Alert,
    CaseEvent,
    Disposition,
    Merchant,
    MerchantProfile,
    TrainingBatch,
    Transaction,
)
from compliance.pipeline.flow import run_pipeline_direct
from compliance.synthetic import generate_history


def _reset_demo_data(session) -> None:
    for model in (CaseEvent, Disposition, Alert, TrainingBatch, MerchantProfile,
                  Transaction, Merchant):
        session.execute(delete(model))


# The scored day is the Hong Kong business day. Anchoring to UTC midnight
# would cut the day at 08:00 local, splitting a merchant's trading in two.
HKT = timezone(timedelta(hours=8))


def main() -> None:
    """CLI entry point: ``compliance-run [--as-of YYYY-MM-DD] [--force]``.

    DESTRUCTIVE. Clears every table, generates synthetic demo history, then
    runs the pipeline. Intended for a fresh store or a demo reset.

    To score data that is already loaded, use ``compliance-pipeline``, which
    deletes nothing. This command refuses to run over a non-empty store unless
    ``--force`` is given.
    """
    as_of_str: str | None = None
    force = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--as-of" and i + 1 < len(args):
            as_of_str = args[i + 1]
            i += 2
        elif args[i] == "--force":
            force = True
            i += 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if as_of_str:
        as_of = datetime.strptime(as_of_str, "%Y-%m-%d").replace(
            tzinfo=HKT, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        as_of = datetime.now(HKT).replace(hour=0, minute=0, second=0, microsecond=0)

    with SessionLocal() as session:
        # This command WIPES every table before generating demo data. That is
        # fine on an empty store and catastrophic on a loaded one, so it
        # refuses to run over real data unless the caller says so explicitly.
        existing = session.scalar(select(func.count()).select_from(Transaction)) or 0
        if existing and not force:
            merchants = session.scalar(select(func.count()).select_from(Merchant)) or 0
            print(
                f"refusing to run: the store holds {existing:,} transactions across "
                f"{merchants:,} merchants.\n"
                "\n"
                "`compliance-run` deletes everything and regenerates synthetic demo "
                "data. To score the data you already have, use:\n"
                f"    compliance-pipeline --as-of {as_of.date()}\n"
                "\n"
                "If you really do want to discard it and reseed, pass --force."
            )
            sys.exit(1)

        _reset_demo_data(session)
        session.commit()
        generate_history(session, as_of=as_of)
        session.commit()
        alert_count = run_pipeline_direct(session, as_of=as_of)
        session.commit()
        print(f"pipeline complete: {alert_count} alert(s) written")


def pipeline_main() -> None:
    """CLI entry point: ``compliance-pipeline [--as-of YYYY-MM-DD]``.

    Scores the data already in the store. Unlike ``compliance-run`` this
    destroys nothing — it is what a nightly run, a backfill, or a re-score
    after fixing a threshold should call.
    """
    as_of_str: str | None = None
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--as-of" and i + 1 < len(argv):
            as_of_str = argv[i + 1]

    if as_of_str:
        as_of = datetime.strptime(as_of_str, "%Y-%m-%d").replace(
            tzinfo=HKT, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        as_of = datetime.now(HKT).replace(hour=0, minute=0, second=0, microsecond=0)

    with SessionLocal() as session:
        scored = as_of - timedelta(days=1)
        alert_count = run_pipeline_direct(session, as_of=as_of)
        session.commit()
        print(
            f"scored {scored.date()} (as-of {as_of.date()}): "
            f"{alert_count} alert(s) written"
        )


def study_main() -> None:
    """CLI entry point: ``compliance-study <MERCHANT_ID> [--as-of YYYY-MM-DD]``.

    Runs all 12 detectors against the given merchant and prints a
    pass/fail report to the terminal.
    """
    from compliance.pipeline.merchant_study import (
        fetch_day_data,
        fetch_profile,
        format_report,
        study_merchant,
    )

    args = sys.argv[1:]
    merchant_id: str | None = None
    as_of_str: str | None = None

    i = 0
    while i < len(args):
        if args[i] == "--as-of" and i + 1 < len(args):
            as_of_str = args[i + 1]
            i += 2
        elif not args[i].startswith("-"):
            merchant_id = args[i]
            i += 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if merchant_id is None:
        print("Usage: compliance-study <MERCHANT_ID> [--as-of YYYY-MM-DD]",
              file=sys.stderr)
        sys.exit(1)

    if as_of_str:
        as_of = datetime.strptime(as_of_str, "%Y-%m-%d").replace(
            tzinfo=HKT, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        as_of = datetime.now(HKT).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    with SessionLocal() as session:
        profile, merchant = fetch_profile(session, merchant_id, as_of)
        day = fetch_day_data(session, merchant_id, as_of)
        lane = "A" if profile.metrics.get("baseline_usable") else "B"

        results = study_merchant(profile.metrics, day, lane=lane)

        report = format_report(
            merchant_id=merchant_id,
            mcc=merchant.mcc if merchant else None,
            lane=lane,
            subdistrict=merchant.merchant_subdistrict if merchant else None,
            as_of=as_of,
            results=results,
        )
        print(report)


def upload_main() -> None:
    """CLI entry point: ``compliance-upload <file.csv> [--run] [--as-of YYYY-MM-DD]``.

    Upload a CSV file, ingest its transactions, and optionally run the
    full pipeline.  Streams from disk — handles files of any size.

    Flags:
        --run       Run the pipeline after ingestion (profiles, detects, scores).
        --as-of     Score date for the pipeline (default: today in HKT).
        --dry-run   Parse and validate the CSV without writing to the database.
        --batch N   Commit every N rows (default: 5000).
    """
    from compliance.csv_ingest import iter_csv_rows, validate_csv
    from compliance.db import SessionLocal
    from compliance.ingest import (
        _parse_time,
        _text,
        _MERCHANT_FIELDS,
        _TXN_FIELDS,
        REFUND_STATUSES,
    )
    from compliance.models import Merchant, Transaction
    from sqlalchemy import select, text

    args = sys.argv[1:]
    csv_path: str | None = None
    run_pipeline_flag = False
    as_of_str: str | None = None
    dry_run = False
    batch_size = 5000

    i = 0
    while i < len(args):
        if args[i] == "--run":
            run_pipeline_flag = True
            i += 1
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        elif args[i] == "--batch" and i + 1 < len(args):
            batch_size = int(args[i + 1])
            i += 2
        elif args[i] == "--as-of" and i + 1 < len(args):
            as_of_str = args[i + 1]
            i += 2
        elif not args[i].startswith("-"):
            csv_path = args[i]
            i += 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if csv_path is None:
        print(
            "Usage: compliance-upload <file.csv> [--run] [--as-of YYYY-MM-DD] "
            "[--dry-run] [--batch N]",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate CSV header
    try:
        col_map, estimated_rows = validate_csv(csv_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    mapped_fields = set(col_map.values())
    print(f"File: {csv_path}")
    print(f"Estimated rows: ~{estimated_rows:,}")
    print(f"Columns mapped: {', '.join(sorted(mapped_fields))}")

    if dry_run:
        # Count valid rows without loading them
        valid = 0
        skipped = 0
        merchants: dict[str, int] = {}
        for row in iter_csv_rows(csv_path, col_map):
            mid = row.get("merchant_id", "?")
            merchants[mid] = merchants.get(mid, 0) + 1
            valid += 1
            if valid % 100_000 == 0:
                print(f"  ... scanned {valid:,} rows", flush=True)

        print(f"\nDry run complete:")
        print(f"  Valid rows: {valid:,}")
        print(f"  Merchants: {len(merchants)}")
        for mid, count in sorted(merchants.items(), key=lambda x: -x[1])[:20]:
            print(f"    {mid}: {count:,} transaction(s)")
        if len(merchants) > 20:
            print(f"    ... and {len(merchants) - 20} more")
        return

    # Streaming ingest with batching
    print(f"\nIngesting (batch size: {batch_size})...")
    sys.stdout.flush()

    inserted = 0
    skipped = 0
    touched: set[str] = set()
    txn_batch: list[dict] = []

    with SessionLocal() as session:
        # Pre-load known merchants for upsert
        known_merchants = {
            m.merchant_id: m for m in session.scalars(select(Merchant))
        }

        for row in iter_csv_rows(csv_path, col_map):
            payment_id = str(row["payment_id"])
            merchant_id = str(row["merchant_id"])

            # Upsert merchant
            if merchant_id not in known_merchants:
                merchant = Merchant(
                    merchant_id=merchant_id,
                    mcc=str(row.get("mcc") or ""),
                )
                session.add(merchant)
                known_merchants[merchant_id] = merchant
            else:
                merchant = known_merchants[merchant_id]
            for field in _MERCHANT_FIELDS:
                value = _text(row.get(field))
                if value is not None:
                    setattr(merchant, field, value)
            touched.add(merchant_id)

            total = float(row["total_amount"])
            net = row.get("net_amount")
            status = str(row.get("transaction_status") or "").upper()
            is_refund = status in REFUND_STATUSES or total < 0

            txn_values = {
                "source_txn_id": payment_id,
                "merchant_id": merchant_id,
                "total_amount": abs(total),
                "net_amount": abs(float(net)) if net not in (None, "") else None,
                "occurred_at": _parse_time(row["hkt_transaction_time"]),
                "is_refund": is_refund,
                "card_type": None,
                "card_origin": None,
                "card_issuing_country": None,
                "card_issuing_bank": None,
                "payment_gateway": None,
                "currency": None,
                "transaction_status": None,
                "hashed_pan": None,
            }
            # `_text` folds blank to NULL. The CSV writes `hashed_pan=""` on
            # every wallet rail, and an empty string compares equal to every
            # other empty string — which is how one "card" ends up at thousands
            # of merchants. See `ingest._text`.
            for field in _TXN_FIELDS:
                txn_values[field] = _text(row.get(field))
            txn_batch.append(txn_values)

            # Commit in batches
            if len(txn_batch) >= batch_size:
                session.flush()
                conn = session.connection()
                conn.execute(
                    text("INSERT INTO transactions "
                         "(source_txn_id, merchant_id, total_amount, net_amount, "
                         "occurred_at, is_refund, card_type, card_origin, "
                         "card_issuing_country, card_issuing_bank, "
                         "payment_gateway, currency, transaction_status, hashed_pan) "
                         "VALUES (:source_txn_id, :merchant_id, :total_amount, :net_amount, "
                         ":occurred_at, :is_refund, :card_type, :card_origin, "
                         ":card_issuing_country, :card_issuing_bank, "
                         ":payment_gateway, :currency, :transaction_status, :hashed_pan) "
                         "ON CONFLICT DO NOTHING"),
                    txn_batch,
                )
                session.commit()
                inserted += len(txn_batch)
                txn_batch.clear()
                print(f"  ... {inserted:,} inserted, {skipped:,} skipped, "
                      f"{len(touched)} merchants", flush=True)

        # Final commit
        if txn_batch:
            session.flush()
            conn = session.connection()
            conn.execute(
                text("INSERT INTO transactions "
                     "(source_txn_id, merchant_id, total_amount, net_amount, "
                     "occurred_at, is_refund, card_type, card_origin, "
                     "card_issuing_country, card_issuing_bank, "
                     "payment_gateway, currency, transaction_status, hashed_pan) "
                     "VALUES (:source_txn_id, :merchant_id, :total_amount, :net_amount, "
                     ":occurred_at, :is_refund, :card_type, :card_origin, "
                     ":card_issuing_country, :card_issuing_bank, "
                     ":payment_gateway, :currency, :transaction_status, :hashed_pan) "
                     "ON CONFLICT DO NOTHING"),
                txn_batch,
            )
            session.commit()
            inserted += len(txn_batch)

    print(f"\nIngestion complete:")
    print(f"  Inserted: {inserted:,} transaction(s)")
    print(f"  Skipped:  {skipped:,} (already present)")
    print(f"  Merchants: {len(touched)}")

    # Show merchant summary
    if touched:
        print(f"\n  Merchant breakdown:")
        # Re-count from the stream for summary
        merchant_counts: dict[str, int] = {}
        for row in iter_csv_rows(csv_path, col_map):
            mid = row.get("merchant_id", "?")
            merchant_counts[mid] = merchant_counts.get(mid, 0) + 1
        for mid, count in sorted(merchant_counts.items(), key=lambda x: -x[1])[:20]:
            print(f"    {mid}: {count:,} transaction(s)")
        if len(merchant_counts) > 20:
            print(f"    ... and {len(merchant_counts) - 20} more")

    # Optionally run pipeline
    if run_pipeline_flag:
        if as_of_str:
            as_of = datetime.strptime(as_of_str, "%Y-%m-%d").replace(
                tzinfo=HKT, hour=0, minute=0, second=0, microsecond=0
            )
        else:
            as_of = datetime.now(HKT).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        print(f"\nRunning pipeline (as_of={as_of.date()})...")
        sys.stdout.flush()
        with SessionLocal() as session:
            alert_count = run_pipeline_direct(session, as_of=as_of)
            session.commit()
        print(f"Pipeline complete: {alert_count} alert(s) written")

    print(f"\nNext: compliance-study <MERCHANT_ID> to inspect any merchant.")


if __name__ == "__main__":
    main()
