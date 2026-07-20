from prefect import flow, task
from sqlalchemy.orm import Session
from compliance.pipeline import stages


@task
def _profile(session: Session) -> None:
    stages.profile(session)


@task
def _route(session: Session) -> dict[str, str]:
    return stages.route(session)


@task
def _detect(session: Session, lanes: dict[str, str]) -> list[dict]:
    return stages.detect(session, lanes)


@task
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
