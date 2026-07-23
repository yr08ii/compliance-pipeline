"""Family A's remaining two detectors: when a merchant trades, and whose
cards it accepts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.detection.profiles import (
    ActiveHours,
    OriginMix,
    fit_active_hours,
    fit_origin_mix,
    hour_is_unusual,
    origin_surprisal,
)
from compliance.models import Base, Merchant, Transaction

AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)
_seq = [0]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _txn(session, merchant_id, hour, days_before=1, country="HK"):
    _seq[0] += 1
    session.add(Transaction(
        source_txn_id=f"TO{_seq[0]:06d}", merchant_id=merchant_id, total_amount=100.0,
        occurred_at=(AS_OF - timedelta(days=days_before)).replace(hour=hour),
        is_refund=False, card_issuing_country=country,
    ))


class TestActiveHours:
    def test_learns_a_daytime_merchants_window(self, session):
        session.add(Merchant(merchant_id="DAY", mcc="5411"))
        for day in range(1, 20):
            for hour in (9, 11, 13, 15, 17):
                _txn(session, "DAY", hour, day)
        session.flush()

        hours = fit_active_hours(session, "DAY", AS_OF, 30)

        assert hours.usable
        assert hour_is_unusual(3, hours) is True
        assert hour_is_unusual(13, hours) is False

    def test_handles_a_bar_trading_across_midnight(self, session):
        """Time is circular: 23:00 and 01:00 are two hours apart, not 22.
        A linear model would treat this merchant as having two separate
        clusters and call its quietest hour normal."""
        session.add(Merchant(merchant_id="BAR", mcc="5813"))
        for day in range(1, 20):
            for hour in (21, 22, 23, 0, 1, 2):
                _txn(session, "BAR", hour, day)
        session.flush()

        hours = fit_active_hours(session, "BAR", AS_OF, 30)

        assert hours.usable
        assert hour_is_unusual(0, hours) is False, "midnight is this bar's core trade"
        assert hour_is_unusual(23, hours) is False
        assert hour_is_unusual(12, hours) is True, "noon is when it is shut"

    def test_thin_history_is_unusable(self, session):
        session.add(Merchant(merchant_id="THIN", mcc="5411"))
        _txn(session, "THIN", 10)
        session.flush()

        assert fit_active_hours(session, "THIN", AS_OF, 30).usable is False


class TestOriginMix:
    def test_learns_the_usual_card_origins(self, session):
        session.add(Merchant(merchant_id="LOCAL", mcc="5411"))
        for day in range(1, 20):
            for _ in range(5):
                _txn(session, "LOCAL", 10, day, country="HK")
        session.flush()

        mix = fit_origin_mix(session, "LOCAL", AS_OF, 30)

        assert mix.usable
        assert mix.share("HK") > 0.9
        assert origin_surprisal("HK", mix) < origin_surprisal("RU", mix)

    def test_a_never_seen_origin_is_maximally_surprising(self, session):
        session.add(Merchant(merchant_id="LOCAL", mcc="5411"))
        for day in range(1, 20):
            for _ in range(5):
                _txn(session, "LOCAL", 10, day, country="HK")
        session.flush()

        mix = fit_origin_mix(session, "LOCAL", AS_OF, 30)

        assert origin_surprisal("KP", mix) > 3.0

    def test_a_merchant_that_always_sees_tourists_is_not_flagged(self, session):
        """An airport shop's foreign cards are its normal."""
        session.add(Merchant(merchant_id="AIRPORT", mcc="5309"))
        for day in range(1, 20):
            for country in ("US", "GB", "JP", "CN", "HK"):
                _txn(session, "AIRPORT", 10, day, country=country)
        session.flush()

        mix = fit_origin_mix(session, "AIRPORT", AS_OF, 30)

        assert origin_surprisal("US", mix) < 3.0
