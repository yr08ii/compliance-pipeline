"""Family A's remaining two detectors: when a merchant trades, and whose
cards it accepts."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.detection.profiles import fit_origin_mix, origin_surprisal
from compliance.detection.timedensity import fit_time_density, time_is_unusual
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


class TestTradingHours:
    def test_learns_a_daytime_merchants_window(self, session):
        session.add(Merchant(merchant_id="DAY", mcc="5411"))
        for day in range(1, 20):
            for hour in (9, 11, 13, 15, 17):
                _txn(session, "DAY", hour, day)
        session.flush()

        hours = fit_time_density(session, "DAY", AS_OF, 30)

        assert hours.usable
        assert time_is_unusual(3.0, hours) is True
        assert time_is_unusual(13.0, hours) is False

    def test_handles_a_bar_trading_across_midnight(self, session):
        """Time is circular: 23:00 and 01:00 are two hours apart, not 22."""
        session.add(Merchant(merchant_id="BAR", mcc="5813"))
        for day in range(1, 20):
            for hour in (21, 22, 23, 0, 1, 2):
                _txn(session, "BAR", hour, day)
        session.flush()

        hours = fit_time_density(session, "BAR", AS_OF, 30)

        assert hours.usable
        assert time_is_unusual(0.0, hours) is False, "midnight is this bar's core trade"
        assert time_is_unusual(23.0, hours) is False
        assert time_is_unusual(12.0, hours) is True, "noon is when it is shut"

    def test_thin_history_is_unusable(self, session):
        session.add(Merchant(merchant_id="THIN", mcc="5411"))
        _txn(session, "THIN", 10)
        session.flush()

        assert fit_time_density(session, "THIN", AS_OF, 30).usable is False


class TestOriginMix:
    def test_learns_the_usual_overseas_card_origins(self, session):
        """The mix is over foreign issuers; home cards are not in the sample."""
        session.add(Merchant(merchant_id="MIXED", mcc="5411"))
        for day in range(1, 20):
            for _ in range(5):
                _txn(session, "MIXED", 10, day, country="HK")
            for country in ("CN", "TW"):
                _txn(session, "MIXED", 10, day, country=country)
        session.flush()

        mix = fit_origin_mix(session, "MIXED", AS_OF, 30)

        assert mix.usable
        assert set(mix.counts) == {"CN", "TW"}
        assert origin_surprisal("CN", mix) < origin_surprisal("RU", mix)

    def test_a_home_only_merchant_has_no_overseas_pattern(self, session):
        """Nothing to compare against, so nothing is scored.

        A merchant that has only ever taken home cards has no overseas history,
        and the detector must not manufacture one — under the old mix its first
        tourist was maximally surprising by construction.
        """
        session.add(Merchant(merchant_id="LOCAL", mcc="5411"))
        for day in range(1, 20):
            for _ in range(5):
                _txn(session, "LOCAL", 10, day, country="HK")
        session.flush()

        mix = fit_origin_mix(session, "LOCAL", AS_OF, 30)

        assert mix.n == 0
        assert not mix.usable
        assert origin_surprisal("KP", mix) == 0.0

    def test_a_never_seen_origin_is_maximally_surprising(self, session):
        """Against a real overseas history, an unfamiliar country still is."""
        session.add(Merchant(merchant_id="REGULAR", mcc="5411"))
        for day in range(1, 20):
            for _ in range(3):
                _txn(session, "REGULAR", 10, day, country="CN")
        session.flush()

        mix = fit_origin_mix(session, "REGULAR", AS_OF, 30)

        assert mix.usable
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
