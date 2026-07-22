from prefect import flow, task
from prefect.cache_policies import NO_CACHE
from sqlalchemy.orm import Session
from compliance.pipeline import stages


@task(cache_policy=NO_CACHE)
def _profile(session: Session) -> None:
    stages.profile(session)


@task(cache_policy=NO_CACHE)
def _route(session: Session) -> dict[str, str]:
    return stages.route(session)


@task(cache_policy=NO_CACHE)
def _detect(session: Session, lanes: dict[str, str]) -> list[dict]:
    return stages.detect(session, lanes)


@task(cache_policy=NO_CACHE)
def _score(session: Session, hits: list[dict]) -> int:
    return len(stages.score_and_rank(session, hits))


@flow(name="nightly-compliance-pipeline")
def run_pipeline(session: Session) -> int:
    """Stages: pull(seed) -> profile -> route -> detect -> score/rank.
    Returns the number of alerts written."""
    _profile(session)
    lanes = _route(session)
    hits = _detect(session, lanes)
    return _score(session, hits)
