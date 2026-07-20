from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from compliance.models import Base, Alert
from compliance.seed import seed
from compliance.pipeline.flow import run_pipeline


def test_pipeline_produces_ranked_alert_with_snapshot():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        seed(s)
        s.commit()
        n = run_pipeline(s)
        s.commit()
        assert n >= 1
        alerts = list(s.scalars(select(Alert).order_by(Alert.rank)))
        assert alerts[0].rank == 1
        assert alerts[0].lane in ("A", "B")
        assert len(alerts[0].feature_snapshot) >= 1
        assert "deviation" in alerts[0].feature_snapshot[0]


def test_pipeline_is_deterministic():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        seed(s)
        s.commit()
        first = run_pipeline(s)
        s.query(Alert).delete()
        s.commit()
        second = run_pipeline(s)
        assert first == second
