from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from compliance.models import Alert, Base
from compliance.pipeline.flow import run_pipeline
from compliance.synthetic import generate_history

AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _prepared_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    generate_history(session, as_of=AS_OF)
    session.commit()
    return session


def test_pipeline_produces_ranked_alert_with_snapshot():
    with _prepared_session() as s:
        written = run_pipeline(s, as_of=AS_OF)
        s.commit()

        assert written >= 1
        alerts = list(s.scalars(select(Alert).order_by(Alert.rank)))
        assert alerts[0].rank == 1
        assert alerts[0].lane in ("A", "B")
        snapshot = alerts[0].feature_snapshot
        assert len(snapshot) >= 1
        assert "deviation" in snapshot[0]
        assert snapshot[0]["baseline_value"] > 0


def test_pipeline_is_deterministic():
    """Same data in, same alerts out — an audit requirement, so it is pinned."""
    with _prepared_session() as s:
        first = run_pipeline(s, as_of=AS_OF)
        first_scores = [a.blended_score for a in s.scalars(select(Alert).order_by(Alert.rank))]
        s.query(Alert).delete()
        s.commit()

        second = run_pipeline(s, as_of=AS_OF)
        second_scores = [a.blended_score for a in s.scalars(select(Alert).order_by(Alert.rank))]

        assert first == second
        assert first_scores == second_scores
