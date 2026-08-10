"""The plotted peer cohort is the cohort the detector judged against.

The box plot behind "merchant level vs MCC peers" used to be rebuilt at read
time from an arbitrary 200 members of the cohort, over all-time history, with
no quarantine exclusion — while the median and fence drawn on top of it came
from the fitted baseline. The picture and its own reference lines disagreed,
and nothing on the screen said so.

The cohort is now fitted once per run and persisted whole. These tests pin the
two properties that matter: every member is present, and what is plotted is
identical to what was fitted.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.api import create_app
from compliance.db import get_session
from compliance.detection.windows import fit_peer_baselines
from compliance.models import (
    Alert,
    Base,
    CohortSnapshot,
    Disposition,
    Merchant,
    Transaction,
)
from compliance.pipeline import stages

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)
SCORED_DAY = AS_OF - timedelta(days=1)


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _seed_cohort(session, *, members: int, mcc: str = "5411") -> None:
    """A cohort whose members each have a distinct, stable typical ticket."""
    for i in range(members):
        merchant_id = f"M{i:04d}"
        session.add(Merchant(merchant_id=merchant_id, mcc=mcc, lane="A"))
        session.flush()
        for day in range(1, 15):
            session.add(
                Transaction(
                    source_txn_id=f"T-{merchant_id}-{day}",
                    merchant_id=merchant_id,
                    total_amount=100.0 + i,
                    occurred_at=AS_OF - timedelta(days=day),
                    is_refund=False,
                )
            )


def test_snapshot_holds_every_member_of_the_cohort():
    engine = _engine()
    S = sessionmaker(bind=engine)
    with S() as s:
        _seed_cohort(s, members=6)
        s.commit()

        stages.profile(s, as_of=AS_OF)
        s.commit()

        fitted = fit_peer_baselines(s, AS_OF, stages.WINDOW_DAYS, lag_days=stages.LAG_DAYS)
        snapshot = s.scalars(
            select(CohortSnapshot).where(CohortSnapshot.mcc == "5411")
        ).one()

        # The persisted cohort is the fitted one, member for member — not a
        # summary of it and not a re-derivation.
        assert snapshot.n_merchants == 6
        assert snapshot.members == sorted(fitted["5411"].members)
        assert snapshot.center == pytest.approx(fitted["5411"].center)
        assert snapshot.q1 == pytest.approx(fitted["5411"].q1)
        assert snapshot.q3 == pytest.approx(fitted["5411"].q3)


def test_snapshot_excludes_a_quarantined_day():
    """A confirmed bad day never shapes the cohort — plotted or fitted.

    The read path used to plot all-time history, so a day the team had already
    confirmed as fraud stayed visible in the distribution it was excluded from.
    """
    engine = _engine()
    S = sessionmaker(bind=engine)
    with S() as s:
        _seed_cohort(s, members=6)
        # M0000 has one enormous day, confirmed as a true positive.
        bad_day = AS_OF - timedelta(days=3)
        s.add(
            Transaction(
                source_txn_id="T-M0000-BAD",
                merchant_id="M0000",
                total_amount=500_000.0,
                occurred_at=bad_day,
                is_refund=False,
            )
        )
        alert = Alert(
            merchant_id="M0000",
            lane="A",
            blended_score=0.99,
            rank=1,
            triggering_detectors=[{"detector": "amount_vs_own_baseline"}],
            feature_snapshot=[],
            created_at=bad_day,
            as_of=AS_OF,
        )
        s.add(alert)
        s.flush()
        s.add(
            Disposition(
                alert_id=alert.id,
                verdict="TRUE_POSITIVE",
                reason_code="CONFIRMED",
                risk_axis="REGULATORY",
                analyst_id="a.chan",
            )
        )
        s.commit()

        stages.profile(s, as_of=AS_OF)
        s.commit()

        snapshot = s.scalars(
            select(CohortSnapshot).where(CohortSnapshot.mcc == "5411")
        ).one()
        assert max(snapshot.members) < 1_000.0


def test_diagnostics_plots_the_whole_cohort_uncapped():
    """No cap. A 250-member cohort is plotted with 250 members.

    The previous read path truncated to an arbitrary 200 with no marker on the
    plot, so an analyst read quartiles from a sample they could not see the
    edge of.
    """
    engine = _engine()
    S = sessionmaker(bind=engine)
    members = 250
    with S() as s:
        _seed_cohort(s, members=members)
        s.add(
            Transaction(
                source_txn_id="T-M0000-SCORED",
                merchant_id="M0000",
                total_amount=9_000.0,
                occurred_at=SCORED_DAY.replace(hour=11),
                is_refund=False,
            )
        )
        s.commit()

        stages.profile(s, as_of=AS_OF)
        s.commit()

        alert = Alert(
            merchant_id="M0000",
            lane="A",
            blended_score=0.9,
            rank=1,
            triggering_detectors=[{"detector": "merchant_level_vs_mcc_peers"}],
            feature_snapshot=[{"feature_name": "ticket", "deviation": 6.0}],
            as_of=AS_OF,
            alert_type="mcc_peer_discrepancy",
        )
        s.add(alert)
        s.commit()
        alert_id = alert.id

    app = create_app()

    def _override():
        yield S()

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    body = client.get(f"/api/alerts/{alert_id}/diagnostics").json()
    peer = body["peer_distribution"]

    assert len(peer["peer_values"]) == members
    assert peer["n_merchants"] == members
    # The count the screen reports and the points it draws are the same set.
    assert len(peer["peer_values"]) == peer["n_merchants"]


def test_diagnostics_quartiles_come_from_the_fit():
    """Q1 and Q3 are read back, not recomputed from a different sample."""
    engine = _engine()
    S = sessionmaker(bind=engine)
    with S() as s:
        _seed_cohort(s, members=40)
        s.commit()
        stages.profile(s, as_of=AS_OF)
        s.commit()

        fitted = fit_peer_baselines(s, AS_OF, stages.WINDOW_DAYS, lag_days=stages.LAG_DAYS)
        alert = Alert(
            merchant_id="M0000",
            lane="A",
            blended_score=0.9,
            rank=1,
            triggering_detectors=[{"detector": "merchant_level_vs_mcc_peers"}],
            feature_snapshot=[],
            as_of=AS_OF,
            alert_type="mcc_peer_discrepancy",
        )
        s.add(alert)
        s.commit()
        alert_id = alert.id

    app = create_app()

    def _override():
        yield S()

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    peer = client.get(f"/api/alerts/{alert_id}/diagnostics").json()["peer_distribution"]
    assert peer["peer_q1"] == pytest.approx(fitted["5411"].q1)
    assert peer["peer_q3"] == pytest.approx(fitted["5411"].q3)


def _diagnostics_statements(members: int) -> list[str]:
    """The SQL one case page costs against a cohort of `members`."""
    engine = _engine()
    S = sessionmaker(bind=engine)
    with S() as s:
        _seed_cohort(s, members=members)
        s.commit()
        stages.profile(s, as_of=AS_OF)
        s.commit()
        alert = Alert(
            merchant_id="M0000",
            lane="A",
            blended_score=0.9,
            rank=1,
            triggering_detectors=[{"detector": "merchant_level_vs_mcc_peers"}],
            feature_snapshot=[],
            as_of=AS_OF,
            alert_type="mcc_peer_discrepancy",
        )
        s.add(alert)
        s.commit()
        alert_id = alert.id

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    app = create_app()

    def _override():
        yield S()

    app.dependency_overrides[get_session] = _override
    TestClient(app).get(f"/api/alerts/{alert_id}/diagnostics")
    return statements


def test_case_page_cost_is_independent_of_cohort_size():
    """Opening a case costs the same in a cohort of 10 as in one of 120.

    The read path used to issue a query per cohort member, each pulling that
    member's entire history. On the real portfolio — a cohort of 938 against
    1.5M transactions — that was forty seconds on a page an analyst opens all
    day. The cap that hid it is gone, so this is what stops it returning.
    """
    small = _diagnostics_statements(10)
    large = _diagnostics_statements(120)

    assert len(small) == len(large)
    assert len([s for s in large if "FROM cohort_snapshots" in s]) == 1
