"""Load a delivered payload from disk. The automated pull calls the same
`ingest_payload`; this exists for manual replay and for testing a delivery
before wiring the scheduler to it."""

from __future__ import annotations

import argparse
from pathlib import Path

from compliance.db import SessionLocal
from compliance.ingest import ingest_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a delivered JSON payload.")
    parser.add_argument("path", type=Path, help="file to load")
    args = parser.parse_args()

    with SessionLocal() as session:
        result = ingest_payload(session, args.path.read_text())
        session.commit()

    print(
        f"ingested {result.inserted} transaction(s), "
        f"skipped {result.skipped} already present, "
        f"{result.merchants} merchant(s) touched"
    )


if __name__ == "__main__":
    main()
