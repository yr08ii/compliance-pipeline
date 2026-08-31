"""Transactions that never settled, on both sides of the line.

DECLINED, CANCELLED, REVERSED and VOIDED rows record value that did not move.
They sat in every baseline as though it had. The distortion is not uniform: a
declined transaction averages HKD 1,355 against HKD 253 for a successful one,
so they pulled ticket baselines upward, and they counted as ordinary trade in
the volume baseline — which is the concerning one, because a burst of declines
is the card-testing pattern Family B's decline rule exists to catch. The
baseline was learning that card testing is normal.

So they come out of every other calculation, and they get a baseline of their
own: a merchant's unsettled share against its own history and against its MCC
cohort. Family B already carries `decline_ratio_spike`, a fixed threshold on
the absolute rate; this is the complement, and answers a different question —
not "is this rate bad" but "is this rate a change".

Unknown outcomes are not failures. The extract also holds AUTHORIZED, PENDING
and 8,078 rows with no status at all, and treating those as unsettled would be
a claim the data does not make.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.detection.windows import (
    UNSETTLED_STATUSES,
    fit_all_baselines,
    fit_peer_unsettled_ratio_baselines,
    fit_unsettled_ratio_baselines,
    fit_volume_baselines,
    merchant_unsettled_ratio,
)
from compliance.models import Base, Merchant, Transaction

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)
SCORED_DAY = AS_OF - timedelta(days=1)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


_seq = [0]


def _txn(s, merchant_id, *, amount=100.0, at=None, status="SUCCESS", refund=False):
    _seq[0] += 1
    s.add(
        Transaction(
            source_txn_id=f"U{_seq[0]:07d}",
            merchant_id=merchant_id,
            total_amount=amount,
            occurred_at=at if at is not None else AS_OF - timedelta(days=5),
            is_refund=refund,
            transaction_status=status,
        )
    )


class TestExclusionFromOtherBaselines:
    def test_declines_do_not_lift_the_ticket_baseline(self, session):
        """A decline averages five times a sale here; it must not set normal."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        for day in range(1, 21):
            for offset in (-5.0, 0.0, 5.0):
                _txn(
                    session, "M1", amount=100.0 + offset,
                    at=AS_OF - timedelta(days=day),
                )
            _txn(
                session, "M1", amount=50_000.0,
                at=AS_OF - timedelta(days=day), status="DECLINED",
            )
        session.commit()

        baselines = fit_all_baselines(session, AS_OF, 30, min_observations=12)

        assert baselines["M1"].usable
        assert baselines["M1"].center == pytest.approx(100.0)

    @pytest.mark.parametrize("status", UNSETTLED_STATUSES)
    def test_every_unsettled_status_is_excluded(self, session, status):
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        for day in range(1, 21):
            for _ in range(3):
                _txn(session, "M1", amount=100.0, at=AS_OF - timedelta(days=day))
            _txn(
                session, "M1", amount=50_000.0,
                at=AS_OF - timedelta(days=day), status=status,
                # REVERSED rows arrive flagged as refunds; the others do not.
                refund=status in {"REVERSED"},
            )
        session.commit()

        baselines = fit_all_baselines(session, AS_OF, 30, min_observations=12)
        assert baselines["M1"].center == pytest.approx(100.0)

    def test_declines_are_not_counted_as_trade_volume(self, session):
        """The baseline must not learn that card testing is ordinary volume."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        for day in range(1, 21):
            for _ in range(4):
                _txn(session, "M1", at=AS_OF - timedelta(days=day))
            for _ in range(20):
                _txn(
                    session, "M1",
                    at=AS_OF - timedelta(days=day), status="DECLINED",
                )
        session.commit()

        volumes = fit_volume_baselines(session, AS_OF, 30, min_observations=12)

        assert volumes["M1"].center == pytest.approx(4.0)

    def test_an_unknown_outcome_is_not_treated_as_failure(self, session):
        """No status is not the same statement as a failed one."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        for day in range(1, 21):
            _txn(session, "M1", at=AS_OF - timedelta(days=day), status=None)
            _txn(session, "M1", at=AS_OF - timedelta(days=day), status="")
        session.commit()

        volumes = fit_volume_baselines(session, AS_OF, 30, min_observations=12)

        assert volumes["M1"].center == pytest.approx(2.0)


class TestUnsettledRatioBaseline:
    def test_fits_a_merchants_own_unsettled_share(self, session):
        """One in five attempts fails, every day, and that is this merchant's
        normal — the detector exists to catch a change from it."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        for day in range(1, 21):
            for _ in range(8):
                _txn(session, "M1", at=AS_OF - timedelta(days=day))
            for _ in range(2):
                _txn(
                    session, "M1",
                    at=AS_OF - timedelta(days=day), status="DECLINED",
                )
        session.commit()

        baselines = fit_unsettled_ratio_baselines(
            session, AS_OF, 30, min_observations=12
        )

        assert baselines["M1"].usable
        assert baselines["M1"].center == pytest.approx(0.2)

    def test_a_clean_merchant_has_a_zero_baseline(self, session):
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        for day in range(1, 21):
            for _ in range(10):
                _txn(session, "M1", at=AS_OF - timedelta(days=day))
        session.commit()

        baselines = fit_unsettled_ratio_baselines(
            session, AS_OF, 30, min_observations=12
        )

        assert baselines["M1"].center == pytest.approx(0.0)

    def test_the_cohort_has_its_own_normal(self, session):
        """Some trades decline more than others; the cohort says how much."""
        for n in range(6):
            merchant_id = f"P{n}"
            session.add(Merchant(merchant_id=merchant_id, mcc="5411"))
            session.flush()
            for day in range(1, 21):
                for _ in range(9):
                    _txn(session, merchant_id, at=AS_OF - timedelta(days=day))
                _txn(
                    session, merchant_id,
                    at=AS_OF - timedelta(days=day), status="DECLINED",
                )
        session.commit()

        cohorts = fit_peer_unsettled_ratio_baselines(session, AS_OF, 30)

        assert cohorts["5411"].center == pytest.approx(0.1)

    def test_scored_day_ratio_counts_every_attempt(self, session):
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        for _ in range(6):
            _txn(session, "M1", at=SCORED_DAY.replace(hour=10))
        for _ in range(4):
            _txn(
                session, "M1",
                at=SCORED_DAY.replace(hour=11), status="DECLINED",
            )
        session.commit()

        assert merchant_unsettled_ratio(session, "M1", AS_OF) == pytest.approx(0.4)

    def test_a_silent_day_has_no_ratio(self, session):
        """Nothing attempted is not a zero failure rate — it is no measurement,
        and a fabricated zero would enter the merchant into a comparison."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.commit()

        assert merchant_unsettled_ratio(session, "M1", AS_OF) is None
