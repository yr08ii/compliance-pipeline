"""One-off backfill: fit baselines over history so merchants start in Lane A
rather than every merchant sitting in Lane B for the first month.

Deliberately a command, not a feature. It is run once at launch (and after a
data correction), never on a schedule, and it has no UI: the nightly pipeline
is the only thing that should be fitting baselines routinely.

The baselines it produces are PROVISIONAL by definition. They are fitted on
history nobody has reviewed, so they encode whatever was already happening,
including undetected crime. What corrects them is the loop: shadow mode, peer
comparison rather than self comparison in the early period, and re-fitting as
dispositions land. This is stated in the output because a number that arrives
without that caveat gets trusted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from compliance.db import SessionLocal
from compliance.models import Transaction
from compliance.pipeline import stages


def run_backfill(session, as_of: datetime, days: int, step: int = 1) -> int:
    """Replay the profile stage across history, ending at `as_of`.

    Each pass overwrites the profile table, so the final state is the baseline
    as of the last date. Intermediate passes exist so a run can be pointed at
    an earlier date to reproduce what the system would have known then.
    """
    passes = 0
    for offset in range(days, -1, -step):
        stages.profile(session, as_of=as_of - timedelta(days=offset))
        passes += 1
    return passes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-off baseline backfill over historical data."
    )
    parser.add_argument("--days", type=int, default=90,
                        help="how far back to replay (default 90)")
    parser.add_argument("--step", type=int, default=30,
                        help="days between passes (default 30; use 1 to replay daily)")
    parser.add_argument("--as-of", default=None,
                        help="end date YYYY-MM-DD (default today)")
    args = parser.parse_args()

    as_of = (
        datetime.strptime(args.as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.as_of
        else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    )

    with SessionLocal() as session:
        available = session.scalar(select(func.count()).select_from(Transaction)) or 0
        if not available:
            print("no transactions in the store — ingest before backfilling")
            return

        passes = run_backfill(session, as_of, args.days, args.step)
        session.commit()

        print(f"backfill complete: {passes} pass(es) over {args.days} days to {as_of.date()}")
        print(f"transactions in store: {available}")
        print(
            "baselines are PROVISIONAL — fitted on unreviewed history. Run in "
            "shadow mode and lean on peer comparison until dispositions accumulate."
        )


if __name__ == "__main__":
    main()
