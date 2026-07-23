"""Integration: does Family A actually catch what it should, end to end?

Uses generated history with known ground truth — we know which merchants were
given an anomaly, so we can assert the pipeline finds those and leaves the
well-behaved ones alone.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from compliance.models import Alert, Base, Merchant, MerchantProfile
from compliance.pipeline import stages
from compliance.synthetic import generate_history

AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _run_family_a(session):
    stages.profile(session, as_of=AS_OF)
    lanes = stages.route(session)
    return stages.detect(session, lanes, as_of=AS_OF)


class TestGeneratedHistory:
    def test_is_deterministic(self, session):
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()
        first = [(m.merchant_id, m.mcc) for m in session.scalars(select(Merchant))]

        engine2 = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine2)
        with Session(engine2) as s2:
            generate_history(s2, as_of=AS_OF, seed=7)
            s2.flush()
            second = [(m.merchant_id, m.mcc) for m in s2.scalars(select(Merchant))]

        assert first == second

    def test_produces_both_mature_and_new_merchants(self, session):
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()
        stages.profile(session, as_of=AS_OF)
        lanes = stages.route(session)

        assert "A" in lanes.values(), "no merchant accumulated a usable baseline"
        assert "B" in lanes.values(), "no cold-start merchant to exercise Lane B"


class TestFamilyADetection:
    def test_flags_the_merchant_given_a_spike(self, session):
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()

        hits = _run_family_a(session)

        flagged = {h["merchant_id"] for h in hits}
        assert "SPIKE" in flagged, "the injected amount spike was not caught"

    def test_leaves_the_steady_merchant_alone(self, session):
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()

        hits = _run_family_a(session)

        flagged = {h["merchant_id"] for h in hits}
        assert "STEADY" not in flagged, "a well-behaved merchant was flagged"

    def test_new_merchant_is_not_scored_by_a_baseline(self, session):
        """Lane B merchants have no baseline; scoring one would be inventing a
        normal it does not have."""
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()

        hits = _run_family_a(session)

        baseline_hits = [
            h for h in hits
            if h["merchant_id"] == "NEWBIE" and h["detector"] == "amount_vs_own_baseline"
        ]
        assert baseline_hits == []

    def test_snapshot_carries_the_real_median_as_the_baseline(self, session):
        """The divergence panel must show the merchant's own fitted median, not
        a hardcoded constant."""
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()

        hits = _run_family_a(session)
        spike = next(
            h for h in hits
            if h["merchant_id"] == "SPIKE" and h["detector"] == "amount_vs_own_baseline"
        )
        feature = spike["feature"]

        assert feature["feature_name"] == "ticket_amount"
        assert feature["baseline_value"] > 0
        assert feature["merchant_value"] > feature["baseline_value"]
        assert feature["deviation"] > 3.5


class TestProfilePersistsBaseline:
    def test_stores_the_fitted_baseline_for_audit(self, session):
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()
        stages.profile(session, as_of=AS_OF)

        profile = session.scalars(
            select(MerchantProfile).where(MerchantProfile.merchant_id == "STEADY")
        ).one()

        assert profile.metrics["baseline_method"] == "mad"
        assert profile.metrics["baseline_center"] > 0
        assert profile.metrics["baseline_dispersion"] > 0
        assert profile.metrics["baseline_n"] >= 12


class TestEndToEnd:
    def test_alert_is_written_with_a_real_baseline(self, session):
        generate_history(session, as_of=AS_OF, seed=7)
        session.flush()
        hits = _run_family_a(session)
        stages.score_and_rank(session, hits)
        session.flush()

        alerts = list(session.scalars(select(Alert).order_by(Alert.rank)))
        assert alerts, "no alert written"
        assert alerts[0].rank == 1
        assert all(a.feature_snapshot[0]["baseline_value"] > 0 for a in alerts)
        assert "SPIKE" in {a.merchant_id for a in alerts}
