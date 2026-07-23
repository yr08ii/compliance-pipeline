"""The peer baselines keyed on something other than MCC amount:
cohort active hours, subdistrict amount, and subdistrict foreign-card ratio."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.detection.baselines import score_value
from compliance.detection.profiles import (
    fit_cohort_hours,
    hour_is_unusual_for_cohort,
)
from compliance.detection.windows import (
    fit_peer_foreign_ratio_baselines,
    fit_peer_transaction_baselines,
)
from compliance.models import Base, Merchant, Transaction

AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)
WINDOW = 30
_seq = [0]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _txn(session, mid, amount, days_before, hour=10, country="HK"):
    _seq[0] += 1
    session.add(Transaction(
        source_txn_id=f"PD{_seq[0]:06d}", merchant_id=mid, total_amount=amount,
        occurred_at=(AS_OF - timedelta(days=days_before)).replace(hour=hour),
        is_refund=False, card_issuing_country=country,
    ))


class TestCohortHours:
    def test_learns_when_a_trade_normally_operates(self, session):
        for i in range(4):
            session.add(Merchant(merchant_id=f"B{i}", mcc="5813"))
            for day in range(1, 20):
                for hour in (21, 22, 23, 0, 1):
                    _txn(session, f"B{i}", 200.0, day, hour)
        session.flush()

        cohorts = fit_cohort_hours(session, AS_OF, WINDOW)

        assert cohorts["5813"].usable
        assert hour_is_unusual_for_cohort(1, cohorts["5813"]) is False
        assert hour_is_unusual_for_cohort(11, cohorts["5813"]) is True

    def test_covers_a_merchant_with_no_hours_pattern_of_its_own(self, session):
        """A cold-start merchant has no own hour density. The cohort's is the
        only thing that can say 3am is odd for a bar district."""
        for i in range(4):
            session.add(Merchant(merchant_id=f"D{i}", mcc="5411"))
            for day in range(1, 20):
                for hour in (9, 12, 15, 18):
                    _txn(session, f"D{i}", 100.0, day, hour)
        session.add(Merchant(merchant_id="NEW", mcc="5411"))
        _txn(session, "NEW", 100.0, 1, hour=3)
        session.flush()

        cohorts = fit_cohort_hours(session, AS_OF, WINDOW)

        assert hour_is_unusual_for_cohort(3, cohorts["5411"]) is True

    def test_each_member_counts_once(self, session):
        """A high-volume member must not define the cohort's hours."""
        for i in range(4):
            session.add(Merchant(merchant_id=f"E{i}", mcc="5411"))
            for day in range(1, 20):
                _txn(session, f"E{i}", 100.0, day, hour=10)
        session.add(Merchant(merchant_id="LOUD", mcc="5411"))
        for day in range(1, 20):
            for _ in range(200):
                _txn(session, "LOUD", 100.0, day, hour=3)
        session.flush()

        cohorts = fit_cohort_hours(session, AS_OF, WINDOW)

        # Without equal weighting LOUD would be 98% of the pooled transactions
        # and 3am would look like this trade's core hour.
        assert cohorts["5411"].share(10) > cohorts["5411"].share(3) * 3


class TestSubdistrictAmount:
    def test_cohorts_by_district_not_only_by_trade(self, session):
        """Price levels differ by district; a Central ticket can be ordinary
        for Central and high for Sham Shui Po."""
        for i in range(4):
            session.add(Merchant(merchant_id=f"C{i}", mcc="5812",
                                 merchant_subdistrict="Central"))
            session.add(Merchant(merchant_id=f"S{i}", mcc="5812",
                                 merchant_subdistrict="Sham Shui Po"))
            for day in range(1, 20):
                _txn(session, f"C{i}", 800.0 + i * 40, day)
                _txn(session, f"S{i}", 60.0 + i * 5, day)
        session.flush()

        cohorts = fit_peer_transaction_baselines(
            session, AS_OF, WINDOW, dimension="subdistrict"
        )

        assert cohorts["Central"].center > cohorts["Sham Shui Po"].center * 5
        assert score_value(800.0, cohorts["Sham Shui Po"]).is_outlier is True
        assert score_value(800.0, cohorts["Central"]).is_outlier is False


class TestSubdistrictForeignRatio:
    def test_learns_the_districts_normal_foreign_share(self, session):
        for i in range(4):
            session.add(Merchant(merchant_id=f"T{i}", mcc="5309",
                                 merchant_subdistrict="Airport"))
            session.add(Merchant(merchant_id=f"R{i}", mcc="5411",
                                 merchant_subdistrict="Tin Shui Wai"))
            for day in range(1, 20):
                for country in ("US", "GB", "JP", "HK"):
                    _txn(session, f"T{i}", 500.0, day, country=country)
                _txn(session, f"R{i}", 80.0, day, country="HK")
        session.flush()

        cohorts = fit_peer_foreign_ratio_baselines(session, AS_OF, WINDOW)

        # A tourist district is mostly foreign cards; a residential one is not.
        assert cohorts["Airport"].center > 0.6
        assert cohorts["Tin Shui Wai"].center < 0.1

    def test_a_residential_merchant_suddenly_all_foreign_is_an_outlier(self, session):
        for i in range(5):
            session.add(Merchant(merchant_id=f"R{i}", mcc="5411",
                                 merchant_subdistrict="Tin Shui Wai"))
            for day in range(1, 20):
                for _ in range(9):
                    _txn(session, f"R{i}", 80.0, day, country="HK")
                if i:  # a little natural variation between members
                    _txn(session, f"R{i}", 80.0, day, country="CN")
        session.flush()

        cohorts = fit_peer_foreign_ratio_baselines(session, AS_OF, WINDOW)

        assert cohorts["Tin Shui Wai"].usable
        assert score_value(0.95, cohorts["Tin Shui Wai"]).is_outlier is True
