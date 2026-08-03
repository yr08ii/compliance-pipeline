"""Amount baselines per payment rail.

HKD 3,000 on Octopus is remarkable — it is a stored-value card for transit and
small retail. The same amount on Visa is a Tuesday. Pooling every rail into one
baseline hides the first and, because the pooled spread is wide, waves it
through.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.detection.windows import fit_payment_method_baselines
from compliance.models import Base, Merchant, Transaction

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)
_seq = [0]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Merchant(merchant_id="M1", mcc="5411"))
        yield s


def _txn(session, amount, days_before, card_type):
    _seq[0] += 1
    session.add(Transaction(
        source_txn_id=f"PM{_seq[0]:06d}", merchant_id="M1", total_amount=amount,
        occurred_at=AS_OF - timedelta(days=days_before), is_refund=False,
        card_type=card_type,
    ))


class TestPerRailBaselines:
    def test_each_rail_gets_its_own_baseline(self, session):
        for day in range(8, 30):
            _txn(session, 45.0, day, "OCTOPUS")
            _txn(session, 1200.0, day, "VISA")
        session.flush()

        fitted = fit_payment_method_baselines(
            session, AS_OF, 30, min_observations=12, lag_days=7
        )

        assert fitted["M1"]["OCTOPUS"].center == pytest.approx(45.0)
        assert fitted["M1"]["VISA"].center == pytest.approx(1200.0)

    def test_a_large_octopus_transaction_stands_out_on_its_own_rail(self, session):
        """The point of the split: judged against Octopus it is extreme, and
        judged against the merchant's pooled history it would not be."""
        from compliance.detection.baselines import score_value

        for day in range(8, 30):
            _txn(session, 40.0, day, "OCTOPUS")
            _txn(session, 50.0, day, "OCTOPUS")
            _txn(session, 3500.0, day, "VISA")
        session.flush()

        fitted = fit_payment_method_baselines(
            session, AS_OF, 30, min_observations=12, lag_days=7
        )

        octopus = fitted["M1"]["OCTOPUS"]
        assert score_value(3000.0, octopus).is_outlier is True

    def test_a_rail_with_too_little_history_is_unusable(self, session):
        """A merchant that takes Alipay twice a month has no Alipay pattern,
        and inventing one would flag it for using Alipay."""
        for day in range(8, 30):
            _txn(session, 95.0 + day, day, "VISA")
        _txn(session, 900.0, 9, "ALIPAY")
        session.flush()

        fitted = fit_payment_method_baselines(
            session, AS_OF, 30, min_observations=12, lag_days=7
        )

        assert fitted["M1"]["ALIPAY"].usable is False
        assert fitted["M1"]["VISA"].usable is True

    def test_transactions_without_a_rail_are_skipped(self, session):
        for day in range(8, 30):
            _txn(session, 100.0, day, None)
        session.flush()

        fitted = fit_payment_method_baselines(
            session, AS_OF, 30, min_observations=12, lag_days=7
        )

        assert fitted.get("M1", {}) == {}
