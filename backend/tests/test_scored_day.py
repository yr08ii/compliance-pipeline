"""What `as_of` means.

A nightly run fires at 00:00 and evaluates the day that just completed, so
`as_of = 2026-05-01` scores 2026-04-30. Two things follow, and both were wrong:

* the scored window must be the *previous* day, not `as_of` itself, and
* it must be bounded to that one day, not open-ended from `as_of` onward.

The open-ended version silently produced an empty scored window whenever the
data ended before `as_of`, so only the detectors that ignore scored-day data
could fire. The queue then looked like it held nothing but systemic
discrepancies — a real detection gap that reads as a display quirk.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.models import Base, Merchant, Transaction
from compliance.pipeline.stages import scored_day_bounds

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestScoredDayBounds:
    def test_scores_the_day_before_as_of(self):
        start, end = scored_day_bounds(AS_OF)

        assert start.date().isoformat() == "2026-04-30"
        assert end.date().isoformat() == "2026-05-01"

    def test_is_exactly_one_day(self):
        start, end = scored_day_bounds(AS_OF)

        assert end - start == timedelta(days=1)

    def test_is_anchored_to_local_midnight(self):
        """A trading day runs midnight to midnight where the merchant is, not
        from whatever time of day the run happened to start."""
        start, end = scored_day_bounds(datetime(2026, 5, 1, 17, 42, tzinfo=HKT))

        assert start.astimezone(HKT).hour == 0
        assert end.astimezone(HKT).hour == 0
        assert start.date().isoformat() == "2026-04-30"


class TestScoredDaySelection:
    def _txn(self, session, txn_id, moment, amount=100.0):
        session.add(
            Transaction(
                source_txn_id=txn_id,
                merchant_id="M1",
                total_amount=amount,
                occurred_at=moment,
                is_refund=False,
            )
        )

    def test_picks_up_the_previous_days_transactions(self, session):
        from compliance.pipeline.stages import _scored_day_amounts

        session.add(Merchant(merchant_id="M1", mcc="5411"))
        self._txn(session, "IN", datetime(2026, 4, 30, 14, tzinfo=HKT), 500.0)
        session.flush()

        assert _scored_day_amounts(session, "M1", AS_OF) == [500.0]

    def test_excludes_as_of_itself_and_anything_after(self, session):
        """The open-ended window swept in every later transaction, so a
        re-run over historical data mixed unrelated days together."""
        from compliance.pipeline.stages import _scored_day_amounts

        session.add(Merchant(merchant_id="M1", mcc="5411"))
        self._txn(session, "PREV", datetime(2026, 4, 30, 9, tzinfo=HKT), 100.0)
        self._txn(session, "SAME", datetime(2026, 5, 1, 9, tzinfo=HKT), 999.0)
        self._txn(session, "LATER", datetime(2026, 6, 9, 9, tzinfo=HKT), 777.0)
        session.flush()

        assert _scored_day_amounts(session, "M1", AS_OF) == [100.0]

    def test_excludes_earlier_days(self, session):
        from compliance.pipeline.stages import _scored_day_amounts

        session.add(Merchant(merchant_id="M1", mcc="5411"))
        self._txn(session, "OLD", datetime(2026, 4, 29, 9, tzinfo=HKT), 42.0)
        session.flush()

        assert _scored_day_amounts(session, "M1", AS_OF) == []

    def test_the_baseline_window_still_ends_before_the_scored_day(self, session):
        """The lag must keep the scored day out of its own baseline, or the
        day being judged helps set the standard it is judged by."""
        from compliance.pipeline.stages import LAG_DAYS, WINDOW_DAYS
        from compliance.detection.windows import _window_bounds

        _, window_end = _window_bounds(AS_OF, WINDOW_DAYS, LAG_DAYS)
        scored_start, _ = scored_day_bounds(AS_OF)

        assert window_end <= scored_start
