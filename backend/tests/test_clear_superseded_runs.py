"""Clearing a scored day's retired runs.

Supersession keeps earlier runs readable so a threshold change can be compared
against what it replaced. Once that comparison is done they are history, and
this is how the team discards it — from the screen, per day, rather than by
someone opening a psql session.

Two things are never in scope: the run currently speaking for the day, which is
the queue the analysts are working, and any alert somebody has ruled on.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.api import create_app
from compliance.db import get_session
from compliance.models import Alert, Base, Disposition, Merchant, PipelineRun
from compliance.pipeline import stages

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)
OTHER_DAY = datetime(2026, 4, 28, tzinfo=HKT)


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)

    with S() as s:
        s.add(Merchant(merchant_id="M1", mcc="5411", lane="A"))
        s.commit()

    app = create_app()

    def _override():
        yield S()

    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    c.session_factory = S
    return c


def _run(session, *, as_of, alerts: int, decided: int = 0, label=None):
    run = stages.open_run(session, as_of=as_of, label=label)
    for i in range(alerts):
        alert = Alert(
            merchant_id="M1",
            as_of=as_of,
            run_id=run.id,
            lane="A",
            blended_score=0.5,
            rank=i + 1,
            triggering_detectors=[{"detector": "amount_vs_own_baseline"}],
            feature_snapshot=[],
        )
        session.add(alert)
        session.flush()
        if i < decided:
            session.add(
                Disposition(
                    alert_id=alert.id,
                    verdict="TRUE_POSITIVE",
                    reason_code="CONFIRMED",
                    risk_axis="REGULATORY",
                    analyst_id="a.chan",
                )
            )
    session.commit()
    return run


def test_clears_the_retired_runs_of_that_day(client):
    with client.session_factory() as s:
        first = _run(s, as_of=AS_OF, alerts=5, label="run one")
        second = _run(s, as_of=AS_OF, alerts=4, label="run two")
        current = _run(s, as_of=AS_OF, alerts=3, label="run three")
        stages.supersede_previous_runs(s, as_of=AS_OF, keep=current.id)
        s.commit()
        first_id, second_id, current_id = first.id, second.id, current.id

    body = client.delete("/api/runs/superseded?as_of=2026-05-01").json()

    assert body == {"runs_cleared": 2, "alerts_removed": 9, "alerts_kept": 0}

    with client.session_factory() as s:
        assert s.get(PipelineRun, first_id) is None
        assert s.get(PipelineRun, second_id) is None
        # The run the analysts are working is untouched.
        assert s.get(PipelineRun, current_id) is not None
        assert len(list(s.scalars(select(Alert)))) == 3


def test_a_decided_alert_and_its_run_survive(client):
    """A disposition is a person's judgement, and it also quarantines that
    merchant's day from future baselines — discarding it would readmit
    confirmed-bad trade as normal."""
    with client.session_factory() as s:
        old = _run(s, as_of=AS_OF, alerts=5, decided=2)
        current = _run(s, as_of=AS_OF, alerts=1)
        stages.supersede_previous_runs(s, as_of=AS_OF, keep=current.id)
        s.commit()
        old_id = old.id

    body = client.delete("/api/runs/superseded?as_of=2026-05-01").json()

    assert body["alerts_removed"] == 3
    assert body["alerts_kept"] == 2
    # The run stays because its decided alerts still point at it.
    assert body["runs_cleared"] == 0

    with client.session_factory() as s:
        assert s.get(PipelineRun, old_id) is not None
        surviving = list(s.scalars(select(Alert).where(Alert.run_id == old_id)))
        assert len(surviving) == 2
        assert all(a.disposition is not None for a in surviving)


def test_another_day_is_not_touched(client):
    with client.session_factory() as s:
        other_old = _run(s, as_of=OTHER_DAY, alerts=4)
        other_current = _run(s, as_of=OTHER_DAY, alerts=2)
        stages.supersede_previous_runs(s, as_of=OTHER_DAY, keep=other_current.id)
        old = _run(s, as_of=AS_OF, alerts=3)
        current = _run(s, as_of=AS_OF, alerts=1)
        stages.supersede_previous_runs(s, as_of=AS_OF, keep=current.id)
        s.commit()
        other_old_id = other_old.id

    body = client.delete("/api/runs/superseded?as_of=2026-05-01").json()

    assert body["alerts_removed"] == 3
    with client.session_factory() as s:
        assert s.get(PipelineRun, other_old_id) is not None


def test_a_day_with_only_one_run_clears_nothing(client):
    with client.session_factory() as s:
        _run(s, as_of=AS_OF, alerts=6)
        s.commit()

    body = client.delete("/api/runs/superseded?as_of=2026-05-01").json()

    assert body == {"runs_cleared": 0, "alerts_removed": 0, "alerts_kept": 0}
    with client.session_factory() as s:
        assert len(list(s.scalars(select(Alert)))) == 6


def test_a_malformed_date_is_refused(client):
    assert client.delete("/api/runs/superseded?as_of=01-05-2026").status_code == 422
