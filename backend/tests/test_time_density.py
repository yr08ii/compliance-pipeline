"""Circular KDE over time-of-day: von Mises kernel, adaptive bandwidth, and a
threshold calibrated per merchant rather than a share fixed for everyone."""

import math
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.detection.timedensity import (
    BINS,
    TimeDensity,
    bandwidth_hours,
    fit_time_density,
    time_is_unusual,
)
from compliance.models import Base, Merchant, Transaction

AS_OF = datetime(2026, 7, 20, tzinfo=timezone(timedelta(hours=8)))
WINDOW = 30
_seq = [0]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _txn(session, mid, days_before, hour, minute=0):
    _seq[0] += 1
    session.add(Transaction(
        source_txn_id=f"KD{_seq[0]:06d}", merchant_id=mid, total_amount=100.0,
        occurred_at=(AS_OF - timedelta(days=days_before)).replace(hour=hour, minute=minute),
        is_refund=False,
    ))


class TestBandwidth:
    def test_narrows_as_observations_grow(self):
        """More data supports a finer estimate; a sparse merchant needs more
        smoothing or its density is just noise."""
        assert bandwidth_hours(30) > bandwidth_hours(1000)

    def test_is_clamped_to_a_sane_range(self):
        assert 0.25 <= bandwidth_hours(5) <= 4.0
        assert 0.25 <= bandwidth_hours(1_000_000) <= 4.0


class TestContinuity:
    def test_resolves_below_the_hour(self, session):
        """The whole point over hour-bucketing: 03:15 and 03:45 are different
        distances from a 04:00 cluster."""
        session.add(Merchant(merchant_id="M", mcc="5411"))
        for day in range(1, 25):
            _txn(session, "M", day, 4, 0)
        session.flush()

        d = fit_time_density(session, "M", AS_OF, WINDOW)

        assert d.density_at(3.75) > d.density_at(3.25)


class TestCircularity:
    def test_a_bar_across_midnight_is_one_pattern(self, session):
        session.add(Merchant(merchant_id="BAR", mcc="5813"))
        for day in range(1, 25):
            for hour in (22, 23, 0, 1):
                _txn(session, "BAR", day, hour)
        session.flush()

        d = fit_time_density(session, "BAR", AS_OF, WINDOW)

        assert time_is_unusual(0.5, d) is False, "midnight is this bar's core trade"
        assert time_is_unusual(23.5, d) is False
        assert time_is_unusual(12.0, d) is True, "noon is when it is shut"

    def test_density_wraps_at_the_boundary(self, session):
        session.add(Merchant(merchant_id="W", mcc="5411"))
        for day in range(1, 25):
            _txn(session, "W", day, 0, 0)
        session.flush()

        d = fit_time_density(session, "W", AS_OF, WINDOW)

        # 23:30 is 30 minutes from the 00:00 cluster, so it must be denser
        # than 12:00, which is twelve hours away.
        assert d.density_at(23.5) > d.density_at(12.0)


class TestMultimodal:
    def test_keeps_lunch_and_dinner_as_separate_peaks(self, session):
        session.add(Merchant(merchant_id="REST", mcc="5812"))
        for day in range(1, 25):
            for hour in (12, 13, 19, 20):
                _txn(session, "REST", day, hour)
        session.flush()

        d = fit_time_density(session, "REST", AS_OF, WINDOW)

        # Both services are dense; the afternoon lull between them is not.
        assert d.density_at(12.5) > d.density_at(16.0)
        assert d.density_at(19.5) > d.density_at(16.0)


class TestRelativeThreshold:
    def test_a_round_the_clock_merchant_is_not_flagged_everywhere(self, session):
        """A fixed share threshold punishes merchants whose trade is spread
        thin across many hours. The threshold must be relative to the
        merchant's own density."""
        session.add(Merchant(merchant_id="ALLDAY", mcc="5541"))
        for day in range(1, 25):
            for hour in range(24):
                _txn(session, "ALLDAY", day, hour)
        session.flush()

        d = fit_time_density(session, "ALLDAY", AS_OF, WINDOW)

        assert all(time_is_unusual(float(h), d) is False for h in range(24))

    def test_a_narrow_window_merchant_still_flags_outside_it(self, session):
        session.add(Merchant(merchant_id="BANK", mcc="6011"))
        for day in range(1, 25):
            for hour in (9, 10, 11, 12, 13, 14, 15, 16):
                _txn(session, "BANK", day, hour)
        session.flush()

        d = fit_time_density(session, "BANK", AS_OF, WINDOW)

        assert time_is_unusual(3.0, d) is True
        assert time_is_unusual(11.0, d) is False


class TestUnusable:
    def test_thin_history_is_not_scored(self, session):
        session.add(Merchant(merchant_id="THIN", mcc="5411"))
        _txn(session, "THIN", 1, 10)
        session.flush()

        d = fit_time_density(session, "THIN", AS_OF, WINDOW)

        assert d.usable is False
        assert time_is_unusual(3.0, d) is False


class TestDeterminism:
    def test_same_history_yields_the_same_density(self, session):
        session.add(Merchant(merchant_id="D", mcc="5411"))
        for day in range(1, 25):
            for hour in (9, 14, 20):
                _txn(session, "D", day, hour)
        session.flush()

        first = fit_time_density(session, "D", AS_OF, WINDOW)
        second = fit_time_density(session, "D", AS_OF, WINDOW)

        assert first.density == second.density
        assert first.threshold == second.threshold

    def test_density_is_a_normalised_distribution(self, session):
        session.add(Merchant(merchant_id="N", mcc="5411"))
        for day in range(1, 25):
            for hour in (8, 9, 10):
                _txn(session, "N", day, hour)
        session.flush()

        d = fit_time_density(session, "N", AS_OF, WINDOW)

        # Integrates to 1 over the 24-hour circle.
        assert math.isclose(sum(d.density) * (24.0 / BINS), 1.0, rel_tol=1e-9)
