from datetime import datetime, timezone

from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from sqlalchemy.orm import Session

from compliance.pipeline import stages


@task(cache_policy=NO_CACHE)
def _profile(session: Session, as_of: datetime) -> None:
    stages.profile(session, as_of=as_of)


@task(cache_policy=NO_CACHE)
def _route(session: Session) -> dict[str, str]:
    return stages.route(session)


@task(cache_policy=NO_CACHE)
def _detect(session: Session, lanes: dict[str, str], as_of: datetime) -> list[dict]:
    return stages.detect(session, lanes, as_of=as_of)


@task(cache_policy=NO_CACHE)
def _typologies(session: Session, as_of: datetime) -> list[dict]:
    return stages.typologies(session, as_of=as_of)


@task(cache_policy=NO_CACHE)
def _rings(
    session: Session, as_of: datetime, flagged_now: frozenset[str]
) -> list[dict]:
    return stages.rings(session, as_of=as_of, flagged_now=flagged_now)


@task(cache_policy=NO_CACHE)
def _score(session: Session, hits: list[dict], as_of: datetime, run) -> int:
    return len(stages.score_and_rank(session, hits, as_of=as_of, run=run))


@flow(name="nightly-compliance-pipeline")
def run_pipeline(
    session: Session,
    as_of: datetime | None = None,
    *,
    label: str | None = None,
    triggered_by: str | None = None,
) -> int:
    """Stages: pull -> profile -> route -> detect (A, B, C) -> score/rank.

    `as_of` is the day being scored; history strictly before it forms the
    baseline. Defaults to now, but is passed explicitly by tests and backfills
    so a run is reproducible rather than dependent on wall-clock time.

    `label` says why this run was made — "outlier_z 3.5 -> 4.0" — which is the
    only thing distinguishing two runs over one day once both are recorded.

    Returns the number of alerts written.
    """
    as_of = as_of or datetime.now(timezone.utc)
    # Opened before any stage runs, so the thresholds recorded against the run
    # are the ones the run actually fitted and scored under — not whatever the
    # store holds by the time it finishes.
    run = stages.open_run(
        session, as_of=as_of, label=label, triggered_by=triggered_by
    )
    _profile(session, as_of)
    lanes = _route(session)
    hits = _detect(session, lanes, as_of) + _typologies(session, as_of)
    # Family C runs last and reads what A and B just found: a ring's severity
    # depends on how many of its members are already flagged, and nothing is
    # written to the alert table until the final stage.
    hits = hits + _rings(session, as_of, frozenset(h["merchant_id"] for h in hits))
    return _score(session, hits, as_of, run)


def run_pipeline_direct(
    session: Session,
    as_of: datetime | None = None,
    *,
    label: str | None = None,
    triggered_by: str | None = None,
) -> int:
    """Run the pipeline stages directly without Prefect overhead.

    Same logic as ``run_pipeline`` but avoids spinning up a temporary
    Prefect server — use this for CLI runs, tests, and backfills.
    """
    as_of = as_of or datetime.now(timezone.utc)
    run = stages.open_run(
        session, as_of=as_of, label=label, triggered_by=triggered_by
    )
    stages.profile(session, as_of=as_of)
    lanes = stages.route(session)
    hits = stages.detect(session, lanes, as_of=as_of)
    # Family B runs per merchant like Family A; Family C runs across the
    # portfolio, and last, because it reads what the other two just found.
    hits += stages.typologies(session, as_of=as_of)
    hits += stages.rings(
        session,
        as_of=as_of,
        flagged_now=frozenset(h["merchant_id"] for h in hits),
    )
    return len(stages.score_and_rank(session, hits, as_of=as_of, run=run))
