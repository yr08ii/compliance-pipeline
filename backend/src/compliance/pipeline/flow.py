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
def _score(session: Session, hits: list[dict]) -> int:
    return len(stages.score_and_rank(session, hits))


@flow(name="nightly-compliance-pipeline")
def run_pipeline(session: Session, as_of: datetime | None = None) -> int:
    """Stages: pull -> profile -> route -> detect -> score/rank.

    `as_of` is the day being scored; history strictly before it forms the
    baseline. Defaults to now, but is passed explicitly by tests and backfills
    so a run is reproducible rather than dependent on wall-clock time.

    Returns the number of alerts written.
    """
    as_of = as_of or datetime.now(timezone.utc)
    _profile(session, as_of)
    lanes = _route(session)
    hits = _detect(session, lanes, as_of)
    return _score(session, hits)
