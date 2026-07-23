"""The four defences against a baseline learning the crime (spec §3.4a)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.detection.baselines import (
    DispersionMethod,
    fit_peer_baseline,
    score_value,
)
from compliance.detection.windows import (
    fit_all_baselines,
    fit_peer_baselines,
    fit_peer_transaction_baselines,
    fit_peer_volume_baselines,
    fit_trend,
    fit_velocity_baselines,
    fit_volume_baselines,
    daily_peak_rate,
    daily_counts_in_window,
    quarantined_days,
)
from compliance.models import Alert, Base, Disposition, Merchant, Transaction

AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)
WINDOW = 30
_seq = [0]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _txn(session, merchant_id, amount, days_before):
    _seq[0] += 1
    session.add(
        Transaction(
            source_txn_id=f"BI{_seq[0]:07d}",
            merchant_id=merchant_id,
            total_amount=amount,
            occurred_at=AS_OF - timedelta(days=days_before),
            is_refund=False,
        )
    )


def _txn_at(session, merchant_id, amount, days_before, hour, minute=0):
    _seq[0] += 1
    session.add(
        Transaction(
            source_txn_id=f"BV{_seq[0]:07d}",
            merchant_id=merchant_id,
            total_amount=amount,
            occurred_at=(AS_OF - timedelta(days=days_before)).replace(
                hour=hour, minute=minute
            ),
            is_refund=False,
        )
    )


def _confirm_true_positive(session, merchant_id, days_before):
    """An alert on that day, reviewed and confirmed as real."""
    alert = Alert(
        merchant_id=merchant_id,
        created_at=AS_OF - timedelta(days=days_before),
        lane="A",
        blended_score=0.9,
        rank=1,
        triggering_detectors=[],
        feature_snapshot=[],
    )
    session.add(alert)
    session.flush()
    session.add(
        Disposition(
            alert_id=alert.id,
            verdict="TRUE_POSITIVE",
            reason_code="STRUCTURING_CONFIRMED",
            risk_axis="REGULATORY",
            action_taken="STR_FILED",
            analyst_id="analyst-1",
        )
    )
    session.flush()


class TestLag:
    def test_lag_excludes_recent_days_from_the_baseline(self, session):
        """Recent data must not enter the baseline before analysts have had
        time to rule on it — a disposition takes days to arrive."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        for day in range(8, 30):
            _txn(session, "M1", 90.0, day)
            _txn(session, "M1", 110.0, day)
        # A heavy burst inside the lag period, large enough to move an
        # unlagged median. It must not shape the baseline until analysts
        # have had the chance to rule on it.
        for day in range(1, 7):
            for _ in range(20):
                _txn(session, "M1", 9_000.0, day)
        session.flush()

        lagged = fit_all_baselines(
            session, AS_OF, WINDOW, min_observations=12, lag_days=7
        )["M1"]
        unlagged = fit_all_baselines(
            session, AS_OF, WINDOW, min_observations=12, lag_days=0
        )["M1"]

        assert lagged.center == pytest.approx(100.0)
        assert unlagged.center == pytest.approx(9_000.0)

    def test_lagged_window_still_reaches_back_a_full_window(self, session):
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        for day in range(8, 34):
            _txn(session, "M1", 45.0, day)
            _txn(session, "M1", 55.0, day)
        session.flush()

        b = fit_all_baselines(
            session, AS_OF, WINDOW, min_observations=12, lag_days=7
        )["M1"]

        assert b.usable is True
        assert b.center == pytest.approx(50.0)


class TestQuarantine:
    def test_confirmed_true_positive_days_are_quarantined(self, session):
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        session.flush()
        _confirm_true_positive(session, "M1", days_before=10)

        assert ("M1", (AS_OF - timedelta(days=10)).date()) in quarantined_days(session)

    def test_false_positive_days_are_not_quarantined(self, session):
        """A cleared alert means the behaviour was legitimate — that data
        belongs in the baseline."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        alert = Alert(
            merchant_id="M1", created_at=AS_OF - timedelta(days=10), lane="A",
            blended_score=0.5, rank=1, triggering_detectors=[], feature_snapshot=[],
        )
        session.add(alert)
        session.flush()
        session.add(Disposition(
            alert_id=alert.id, verdict="FALSE_POSITIVE", reason_code="SEASONAL_PROMOTION",
            risk_axis="COMMERCIAL", action_taken="NONE", analyst_id="analyst-1",
        ))
        session.flush()

        assert quarantined_days(session) == set()

    def test_quarantined_transactions_do_not_shape_the_baseline(self, session):
        """Otherwise the system absorbs its own confirmed findings as normal."""
        session.add(Merchant(merchant_id="M1", mcc="5411"))
        for day in range(10, 30):
            _txn(session, "M1", 100.0, day)
        for _ in range(30):
            _txn(session, "M1", 8_000.0, 9)
        session.flush()
        _confirm_true_positive(session, "M1", days_before=9)

        clean = fit_all_baselines(session, AS_OF, WINDOW, min_observations=12)["M1"]

        assert clean.center == pytest.approx(100.0)


class TestTrend:
    def test_detects_a_slow_ramp_no_single_day_would_flag(self, session):
        """The boiling-frog evasion: never an outlier against its own trailing
        window, but 10x over two months."""
        session.add(Merchant(merchant_id="RAMP", mcc="5411"))
        for day in range(0, 60):
            amount = 100.0 * (1.04 ** (60 - day))
            for _ in range(3):
                _txn(session, "RAMP", round(amount, 2), day)
        session.flush()

        trend = fit_trend(session, "RAMP", AS_OF, short_days=7, long_days=60, lag_days=0)

        assert trend.is_ramp is True
        assert trend.ratio > 2.0

    def test_steady_merchant_is_not_a_ramp(self, session):
        session.add(Merchant(merchant_id="FLAT", mcc="5411"))
        for day in range(0, 60):
            for amount in (95.0, 105.0):
                _txn(session, "FLAT", amount, day)
        session.flush()

        trend = fit_trend(session, "FLAT", AS_OF, short_days=7, long_days=60, lag_days=0)

        assert trend.is_ramp is False

    def test_thin_history_is_not_reported_as_a_ramp(self, session):
        session.add(Merchant(merchant_id="THIN", mcc="5411"))
        _txn(session, "THIN", 100.0, 1)
        session.flush()

        assert fit_trend(session, "THIN", AS_OF, short_days=7, long_days=60,
                         lag_days=0).is_ramp is False


class TestPeerBaseline:
    def test_upper_fence_sits_above_the_cohort_centre(self):
        peer = fit_peer_baseline([10.0, 20.0, 30.0, 40.0, 50.0], n_merchants=5)
        assert peer.usable
        assert peer.upper_fence() > peer.center

    def test_cohort_too_small_is_unusable(self):
        assert fit_peer_baseline([10.0, 20.0], n_merchants=1).usable is False

    def test_fits_a_baseline_per_mcc(self, session):
        session.add_all([
            Merchant(merchant_id="G1", mcc="5411"),
            Merchant(merchant_id="G2", mcc="5411"),
            Merchant(merchant_id="G3", mcc="5411"),
            Merchant(merchant_id="G4", mcc="5411"),
            Merchant(merchant_id="J1", mcc="5944"),
            Merchant(merchant_id="J2", mcc="5944"),
            Merchant(merchant_id="J3", mcc="5944"),
            Merchant(merchant_id="J4", mcc="5944"),
        ])
        # Members differ from one another, as a real cohort does.
        for m, typical in (("G1", 80.0), ("G2", 100.0), ("G3", 120.0), ("G4", 140.0)):
            for day in range(1, 20):
                _txn(session, m, typical, day)
        for m, typical in (("J1", 2600.0), ("J2", 3000.0), ("J3", 3400.0), ("J4", 3800.0)):
            for day in range(1, 20):
                _txn(session, m, typical, day)
        session.flush()

        peers = fit_peer_baselines(session, AS_OF, WINDOW, min_cohort_merchants=3)

        assert peers["5411"].usable and peers["5944"].usable
        assert peers["5944"].center > peers["5411"].center * 10

    def test_peer_baseline_is_unpoisoned_by_one_high_volume_bad_merchant(self, session):
        """The structural point: a merchant can corrupt its own baseline but
        not its cohort's — even when it out-transacts every honest member.

        Cohorts are built one-vote-per-merchant for exactly this reason. Pooled
        raw transactions would let this merchant's volume drag the fence up to
        cover itself.
        """
        session.add_all([Merchant(merchant_id=f"P{i}", mcc="5411") for i in range(6)])
        for i, typical in enumerate([90.0, 100.0, 110.0, 120.0, 130.0]):
            for day in range(1, 20):
                _txn(session, f"P{i}", typical, day)
        # Five times the transaction volume of any honest member.
        for day in range(1, 20):
            for _ in range(5):
                _txn(session, "P5", 50_000.0, day)
        session.flush()

        peers = fit_peer_baselines(session, AS_OF, WINDOW, min_cohort_merchants=3)

        assert peers["5411"].usable
        assert peers["5411"].upper_fence() < 50_000.0


class TestPeerTransactionBaseline:
    """Transaction-level peer test: is this merchant's TRANSACTION unusual for
    its cohort? The merchant-median test cannot answer this — a median does
    not move for one large ticket, which is exactly the point of a median."""

    def _cohort(self, session):
        session.add_all([Merchant(merchant_id=f"C{i}", mcc="5814") for i in range(5)])
        for i in range(5):
            for day in range(1, 25):
                _txn(session, f"C{i}", 40.0 + i * 5, day)
                _txn(session, f"C{i}", 60.0 + i * 5, day)

    def test_catches_a_single_outrageous_ticket(self, session):
        self._cohort(session)
        session.add(Merchant(merchant_id="ODD", mcc="5814"))
        for day in range(1, 25):
            _txn(session, "ODD", 50.0, day)
        _txn(session, "ODD", 50_000.0, 1)
        session.flush()

        cohort = fit_peer_transaction_baselines(session, AS_OF, WINDOW)["5814"]

        assert cohort.usable
        assert score_value(50_000.0, cohort).is_outlier is True

    def test_a_normal_ticket_for_the_cohort_is_not_flagged(self, session):
        self._cohort(session)
        session.flush()

        cohort = fit_peer_transaction_baselines(session, AS_OF, WINDOW)["5814"]

        assert score_value(55.0, cohort).is_outlier is False

    def test_one_high_volume_merchant_cannot_dominate_the_cohort(self, session):
        """Per-merchant capping: pooling raw transactions would let a member
        with far more volume drag the cohort distribution to cover itself."""
        self._cohort(session)
        session.add(Merchant(merchant_id="LOUD", mcc="5814"))
        for day in range(1, 25):
            for _ in range(40):
                _txn(session, "LOUD", 9_000.0, day)
        session.flush()

        cohort = fit_peer_transaction_baselines(session, AS_OF, WINDOW)["5814"]

        assert cohort.center < 500.0, "a single loud merchant captured the cohort"
        assert score_value(9_000.0, cohort).is_outlier is True


class TestVolumeBaseline:
    """Transacting far more than you normally do, or far more than your peers,
    is its own signal — and the one that makes cohort manipulation visible.
    You cannot pump enough volume to drag a cohort median without the volume
    itself becoming an outlier."""

    def test_daily_counts_are_measured_per_active_day(self, session):
        session.add(Merchant(merchant_id="V1", mcc="5411"))
        for day in range(1, 11):
            for _ in range(4):
                _txn(session, "V1", 100.0, day)
        session.flush()

        counts = daily_counts_in_window(session, "V1", AS_OF, WINDOW)

        assert counts == [4] * 10

    def test_flags_a_merchant_transacting_far_more_than_usual(self, session):
        session.add(Merchant(merchant_id="V1", mcc="5411"))
        for day in range(2, 30):
            for _ in range(4):
                _txn(session, "V1", 100.0, day)
        session.flush()

        baseline = fit_volume_baselines(
            session, AS_OF, WINDOW, min_observations=12
        )["V1"]

        assert baseline.usable
        assert score_value(60, baseline).is_outlier is True
        assert score_value(5, baseline).is_outlier is False

    def test_volume_cohort_flags_a_merchant_swamping_its_peers(self, session):
        """The merchant attempting to dominate its cohort's amount
        distribution is caught here instead."""
        for i in range(5):
            session.add(Merchant(merchant_id=f"Q{i}", mcc="5814"))
            for day in range(1, 25):
                for _ in range(3 + i):
                    _txn(session, f"Q{i}", 50.0, day)
        session.add(Merchant(merchant_id="FLOOD", mcc="5814"))
        for day in range(1, 25):
            for _ in range(200):
                _txn(session, "FLOOD", 50.0, day)
        session.flush()

        cohort = fit_peer_volume_baselines(session, AS_OF, WINDOW)["5814"]

        assert cohort.usable
        assert score_value(200, cohort.as_baseline()).is_outlier is True
        assert score_value(5, cohort.as_baseline()).is_outlier is False


class TestVelocity:
    """Speed: how tightly transactions cluster in time. A structuring burst
    puts many transactions through in minutes — a daily count smooths that
    away, because the day's total can look ordinary."""

    def test_measures_the_busiest_hour_of_each_day(self, session):
        session.add(Merchant(merchant_id="S1", mcc="5411"))
        # Six transactions, but spread one per hour: no burst.
        for day in (1, 2):
            for hour in range(6):
                _txn_at(session, "S1", 100.0, day, hour)
        session.flush()

        assert daily_peak_rate(session, "S1", AS_OF, WINDOW) == [1, 1]

    def test_a_burst_registers_as_peak_rate(self, session):
        session.add(Merchant(merchant_id="S1", mcc="5411"))
        for i in range(8):
            _txn_at(session, "S1", 100.0, 1, 10, minute=i * 5)
        session.flush()

        assert daily_peak_rate(session, "S1", AS_OF, WINDOW) == [8]

    def test_flags_a_burst_against_a_merchants_usual_pace(self, session):
        session.add(Merchant(merchant_id="S1", mcc="5411"))
        for day in range(2, 30):
            for hour in range(4):
                _txn_at(session, "S1", 100.0, day, hour * 4)
        session.flush()

        baseline = fit_velocity_baselines(
            session, AS_OF, WINDOW, min_observations=12
        )["S1"]

        assert baseline.usable
        assert score_value(20, baseline).is_outlier is True
        assert score_value(2, baseline).is_outlier is False
