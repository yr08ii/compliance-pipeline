"""A re-run of a scored day replaces its queue without erasing its predecessor.

`score_and_rank` was the one stage that only ever appended — `profile` and
`_persist_cohorts` both clear prior rows first, on the stated grounds that
repeated runs must not accumulate duplicates and drift the output. Four runs
over the same scored day left 88,065 alerts in a queue whose real content was
about 7,000: of the merchant and alert-type pairs in the last run, 99.6% were
already sitting there from the run before, each one a second copy of the same
finding in front of an analyst with no way to tell which copy was current.

`as_of` cannot tell those runs apart — all four carried the same one, because
they scored the same trading day. Only a run identifier can.

So a run is retired rather than deleted. Its alerts leave the queue and stay on
record, which is what makes a parameter change reviewable: the reason to
re-score a day is usually that a threshold moved, and the question immediately
after is what moved with it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.models import Alert, Base, Disposition, Merchant, PipelineRun
from compliance.pipeline import stages
from compliance.settings_store import DetectionSettings, save_settings

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)
OTHER_DAY = datetime(2026, 4, 30, tzinfo=HKT)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        s.add(Merchant(merchant_id="M1", mcc="5411", lane="A"))
        s.add(Merchant(merchant_id="M2", mcc="5411", lane="A"))
        s.commit()
        yield s


def _hit(merchant_id: str) -> dict:
    return {
        "merchant_id": merchant_id,
        "lane": "A",
        "detector": "amount_vs_own_baseline",
        "sub_score": 0.8,
        "feature": {
            "feature_name": "ticket_amount",
            "merchant_value": 9_000.0,
            "baseline_value": 120.0,
            "deviation": 30.0,
        },
    }


def _earlier_run(session, *, as_of, count: int, decided: int = 0, label="first pass"):
    """A completed run and the alerts it left. Returns (run, decided ids)."""
    run = stages.open_run(session, as_of=as_of, label=label)
    decided_ids: list[int] = []
    for i in range(count):
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
            decided_ids.append(alert.id)
    session.commit()
    return run, decided_ids


def _queue(session) -> list[Alert]:
    """What the portal shows: alerts of runs nobody has superseded."""
    live = select(PipelineRun.id).where(PipelineRun.superseded_at.is_(None))
    return list(
        session.scalars(
            select(Alert).where((Alert.run_id.is_(None)) | Alert.run_id.in_(live))
        )
    )


class TestSupersession:
    def test_the_queue_holds_one_run_not_their_sum(self, session):
        _earlier_run(session, as_of=AS_OF, count=5)

        stages.score_and_rank(session, [_hit("M1"), _hit("M2")], as_of=AS_OF)
        session.commit()

        current = _queue(session)
        assert len(current) == 2
        assert {a.merchant_id for a in current} == {"M1", "M2"}

    def test_the_superseded_run_keeps_its_alerts(self, session):
        """Retired, not deleted — otherwise the identifier labels nothing."""
        previous, _ = _earlier_run(session, as_of=AS_OF, count=5)

        stages.score_and_rank(session, [_hit("M1")], as_of=AS_OF)
        session.commit()

        held = list(
            session.scalars(select(Alert).where(Alert.run_id == previous.id))
        )
        assert len(held) == 5
        assert session.get(PipelineRun, previous.id).superseded_at is not None

    def test_a_decided_alert_is_never_disturbed(self, session):
        """The audit trail is not the run's to rewrite."""
        _, decided_ids = _earlier_run(session, as_of=AS_OF, count=5, decided=2)

        stages.score_and_rank(session, [_hit("M1")], as_of=AS_OF)
        session.commit()

        for alert_id in decided_ids:
            alert = session.get(Alert, alert_id)
            assert alert is not None
            assert alert.disposition is not None

    def test_another_scored_day_is_untouched(self, session):
        """A run that scored Tuesday says nothing about Monday's queue."""
        monday, _ = _earlier_run(session, as_of=OTHER_DAY, count=4)
        _earlier_run(session, as_of=AS_OF, count=3)

        stages.score_and_rank(session, [_hit("M1")], as_of=AS_OF)
        session.commit()

        assert session.get(PipelineRun, monday.id).superseded_at is None
        assert len(_queue(session)) == 4 + 1

    def test_an_unscoped_run_supersedes_nothing(self, session):
        """With no day named there is nothing to be the current statement about.

        Retiring on a guess would take a queue away from the analyst working it.
        """
        previous, _ = _earlier_run(session, as_of=AS_OF, count=3)

        stages.score_and_rank(session, [_hit("M1")], as_of=None)
        session.commit()

        assert session.get(PipelineRun, previous.id).superseded_at is None

    def test_ranks_restart_from_one(self, session):
        """The queue is ordered by rank, so a re-run renumbers from the top or
        its alerts sort underneath the run they replaced."""
        _earlier_run(session, as_of=AS_OF, count=5)

        stages.score_and_rank(session, [_hit("M1"), _hit("M2")], as_of=AS_OF)
        session.commit()

        assert sorted(a.rank for a in _queue(session)) == [1, 2]


class TestRunRecord:
    def test_a_run_records_the_thresholds_it_scored_under(self, session):
        """The reason the record exists.

        Thresholds live in a table the compliance lead edits without a deploy,
        so reading them back later would report whatever is current rather than
        what produced these alerts.
        """
        save_settings(session, DetectionSettings(outlier_z=3.5))
        session.commit()

        run = stages.open_run(session, as_of=AS_OF, label="baseline tuning")
        session.commit()

        assert run.settings["outlier_z"] == 3.5
        assert run.label == "baseline tuning"

        # Retuned afterwards: the recorded run must not follow.
        save_settings(session, DetectionSettings(outlier_z=4.0))
        session.commit()
        session.refresh(run)

        assert run.settings["outlier_z"] == 3.5

    def test_two_runs_over_one_day_are_distinguishable(self, session):
        """Which is the whole point — `as_of` is identical for both."""
        first = stages.open_run(session, as_of=AS_OF, label="outlier_z 3.5")
        stages.score_and_rank(session, [_hit("M1")], as_of=AS_OF, run=first)
        session.commit()

        second = stages.open_run(session, as_of=AS_OF, label="outlier_z 4.0")
        stages.score_and_rank(session, [_hit("M2")], as_of=AS_OF, run=second)
        session.commit()

        assert first.as_of == second.as_of
        assert first.id != second.id
        assert session.get(PipelineRun, first.id).superseded_at is not None
        assert session.get(PipelineRun, second.id).superseded_at is None

        by_run = {
            run_id: merchant_id
            for run_id, merchant_id in session.execute(
                select(Alert.run_id, Alert.merchant_id)
            )
        }
        assert by_run[first.id] == "M1"
        assert by_run[second.id] == "M2"

    def test_a_run_counts_what_it_raised(self, session):
        run = stages.open_run(session, as_of=AS_OF)
        stages.score_and_rank(
            session, [_hit("M1"), _hit("M2")], as_of=AS_OF, run=run
        )
        session.commit()

        assert run.alert_count == 2
        assert run.finished_at is not None
