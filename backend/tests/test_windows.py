from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.detection.baselines import DispersionMethod
from compliance.detection.windows import amounts_in_window, fit_all_baselines
from compliance.models import Base, Merchant, Transaction

AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)
WINDOW = 30


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _txn(session, merchant_id: str, amount: float, days_before: float, *, refund=False, seq=[0]):
    seq[0] += 1
    session.add(
        Transaction(
            source_txn_id=f"T{seq[0]:06d}",
            merchant_id=merchant_id,
            total_amount=amount,
            net_amount=amount * 0.97,
            occurred_at=AS_OF - timedelta(days=days_before),
            is_refund=refund,
        )
    )


class TestAmountsInWindow:
    def test_returns_total_amount_for_the_merchant(self, session):
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        _txn(session, "M1", 100.0, days_before=1)
        _txn(session, "M1", 250.0, days_before=2)
        session.flush()

        assert sorted(amounts_in_window(session, "M1", AS_OF, WINDOW)) == [100.0, 250.0]

    def test_excludes_transactions_older_than_the_window(self, session):
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        _txn(session, "M1", 100.0, days_before=5)
        _txn(session, "M1", 999.0, days_before=45)
        session.flush()

        assert amounts_in_window(session, "M1", AS_OF, WINDOW) == [100.0]

    def test_excludes_transactions_at_or_after_as_of(self, session):
        """The nightly run scores the prior day; data at or after the cutoff
        must not leak into the baseline it is scored against."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        _txn(session, "M1", 100.0, days_before=1)
        _txn(session, "M1", 777.0, days_before=0)
        _txn(session, "M1", 888.0, days_before=-1)
        session.flush()

        assert amounts_in_window(session, "M1", AS_OF, WINDOW) == [100.0]

    def test_isolates_merchants(self, session):
        session.add_all([Merchant(merchant_id="M1", mcc="5411"),
                         Merchant(merchant_id="M2", mcc="5944")])
        _txn(session, "M1", 100.0, days_before=1)
        _txn(session, "M2", 500.0, days_before=1)
        session.flush()

        assert amounts_in_window(session, "M1", AS_OF, WINDOW) == [100.0]

    def test_excludes_refunds(self, session):
        """Refunds are value moving the other way. Mixing them into the ticket
        baseline distorts the merchant's normal; the refund-ratio rule handles
        them instead."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        _txn(session, "M1", 100.0, days_before=1)
        _txn(session, "M1", 60.0, days_before=2, refund=True)
        session.flush()

        assert amounts_in_window(session, "M1", AS_OF, WINDOW) == [100.0]


class TestFitAllBaselines:
    def test_fits_one_baseline_per_merchant_in_a_single_pass(self, session):
        session.add_all([Merchant(merchant_id="M1", mcc="5411"),
                         Merchant(merchant_id="M2", mcc="5944")])
        for i in range(20):
            _txn(session, "M1", 90.0 if i % 2 else 110.0, days_before=i % 28 + 1)
            _txn(session, "M2", 900.0 if i % 2 else 1100.0, days_before=i % 28 + 1)
        session.flush()

        baselines = fit_all_baselines(session, AS_OF, WINDOW, min_observations=12)

        assert set(baselines) == {"M1", "M2"}
        assert baselines["M1"].center == pytest.approx(100.0)
        assert baselines["M2"].center == pytest.approx(1000.0)
        assert baselines["M1"].usable

    def test_merchant_with_thin_history_is_unusable_not_missing(self, session):
        """A new merchant must appear with an explicit unusable baseline so the
        router can send it to Lane B, rather than silently vanishing."""
        session.add(Merchant(merchant_id="NEW", mcc="5732"))
        _txn(session, "NEW", 100.0, days_before=1)
        session.flush()

        baselines = fit_all_baselines(session, AS_OF, WINDOW, min_observations=12)

        assert baselines["NEW"].usable is False
        assert baselines["NEW"].method is DispersionMethod.INSUFFICIENT_DATA

    def test_merchant_with_no_transactions_still_appears(self, session):
        session.add(Merchant(merchant_id="QUIET", mcc="5411"))
        session.flush()

        baselines = fit_all_baselines(session, AS_OF, WINDOW, min_observations=12)

        assert baselines["QUIET"].usable is False
        assert baselines["QUIET"].n == 0
