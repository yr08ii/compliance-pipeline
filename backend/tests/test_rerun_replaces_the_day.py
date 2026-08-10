"""A re-run of a scored day replaces its own queue, it does not add to it.

Every other stage already works this way — `profile` and `_persist_cohorts`
both clear prior rows first, on the stated grounds that repeated runs must not
accumulate duplicates and drift the output. `score_and_rank` was the one stage
that only ever appended, and four runs over the same scored day left 88,065
alerts in a queue whose real content was about 7,000: of the merchant and
alert-type pairs in the last run, 99.6% were already sitting there from the run
before, each one a second copy of the same finding.

`as_of` cannot be relied on to tell those runs apart — all four carried the
same one, because they scored the same trading day. What separates them is
that the later run is the current statement about that day, and the earlier
one is superseded.

A decided alert is never touched. It is an audit record of a judgement a person
made, and the run has no business deleting it.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.models import Alert, Base, Disposition, Merchant
from compliance.pipeline import stages

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


def _hit(merchant_id: str, detector: str = "amount_vs_own_baseline") -> dict:
    return {
        "merchant_id": merchant_id,
        "lane": "A",
        "detector": detector,
        "sub_score": 0.8,
        "feature": {
            "feature_name": "ticket_amount",
            "merchant_value": 9_000.0,
            "baseline_value": 120.0,
            "deviation": 30.0,
        },
    }


def _seed_previous_run(session, *, as_of, count: int, decided: int = 0) -> list[int]:
    """Alerts as an earlier run left them. Returns the decided ids."""
    decided_ids: list[int] = []
    for i in range(count):
        alert = Alert(
            merchant_id="M1",
            as_of=as_of,
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
    return decided_ids


def test_rerun_does_not_accumulate(session):
    _seed_previous_run(session, as_of=AS_OF, count=5)

    stages.score_and_rank(session, [_hit("M1"), _hit("M2")], as_of=AS_OF)
    session.commit()

    alerts = list(session.scalars(select(Alert)))
    # Two hits in, two alerts out — not seven.
    assert len(alerts) == 2
    assert {a.merchant_id for a in alerts} == {"M1", "M2"}


def test_a_decided_alert_survives_the_rerun(session):
    """The audit trail is not the run's to rewrite."""
    decided_ids = _seed_previous_run(session, as_of=AS_OF, count=5, decided=2)

    stages.score_and_rank(session, [_hit("M1")], as_of=AS_OF)
    session.commit()

    surviving = {a.id for a in session.scalars(select(Alert))}
    assert set(decided_ids) <= surviving
    # The two decided, plus the one this run raised.
    assert len(surviving) == 3


def test_another_scored_day_is_untouched(session):
    """Replacement is scoped to the day being re-scored.

    A run that scored Tuesday says nothing about Monday's queue, and must not
    clear it on its way past.
    """
    _seed_previous_run(session, as_of=OTHER_DAY, count=4)
    _seed_previous_run(session, as_of=AS_OF, count=3)

    stages.score_and_rank(session, [_hit("M1")], as_of=AS_OF)
    session.commit()

    # Keyed on the calendar date: SQLite hands `as_of` back without its
    # offset, so the aware values seeded here would not compare equal.
    by_day: dict[str, int] = {}
    for alert in session.scalars(select(Alert)):
        key = alert.as_of.date().isoformat()
        by_day[key] = by_day.get(key, 0) + 1

    assert by_day[OTHER_DAY.date().isoformat()] == 4
    assert by_day[AS_OF.date().isoformat()] == 1


def test_an_unscoped_run_clears_nothing(session):
    """Without an `as_of` there is no day to replace, so nothing is removed.

    Deleting on a guess would be the one unrecoverable way to get this wrong.
    """
    _seed_previous_run(session, as_of=AS_OF, count=3)

    stages.score_and_rank(session, [_hit("M1")], as_of=None)
    session.commit()

    assert len(list(session.scalars(select(Alert)))) == 4


def test_ranks_restart_from_one(session):
    """The queue is ordered by rank, so a re-run has to renumber from the top
    or the replacement queue sorts underneath whatever it replaced."""
    _seed_previous_run(session, as_of=AS_OF, count=5)

    stages.score_and_rank(session, [_hit("M1"), _hit("M2")], as_of=AS_OF)
    session.commit()

    ranks = sorted(a.rank for a in session.scalars(select(Alert)))
    assert ranks == [1, 2]
