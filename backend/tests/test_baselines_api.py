from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from compliance.api import create_app
from compliance.db import get_session
from compliance.models import Base, Merchant, MerchantProfile

AS_OF = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        s.add(Merchant(merchant_id="M1", mcc="5411", lane="A"))
        s.add(MerchantProfile(
            merchant_id="M1", as_of=AS_OF,
            metrics={
                "baseline_center": 120.0, "baseline_dispersion": 25.0,
                "baseline_method": "mad", "baseline_n": 180, "baseline_usable": True,
                "window_start": "2026-06-13T00:00:00+00:00",
                "window_end": "2026-07-13T00:00:00+00:00",
                "window_days": 30, "lag_days": 7, "quarantined_days": 2,
                "peer_mcc": "5411", "peer_merchants": 5, "peer_usable": True,
                "volume_usable": True, "velocity_usable": True,
                "trend_is_ramp": False,
            },
        ))
        s.commit()

    app = create_app()

    def override():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def test_lists_baseline_provenance_per_merchant():
    resp = _client().get("/api/baselines")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 30
    assert body["lag_days"] == 7
    row = body["merchants"][0]
    assert row["merchant_id"] == "M1"
    assert row["method"] == "mad"
    assert row["observations"] == 180
    assert row["quarantined_days"] == 2


def test_reports_the_day_that_enters_the_baseline_next():
    """The lag means a known day rolls in on the next run — analysts should be
    able to see which, and that it is pending review until then."""
    body = _client().get("/api/baselines").json()
    assert body["next_inclusion_date"] == "2026-07-13"


def test_summarises_coverage():
    body = _client().get("/api/baselines").json()
    assert body["usable_count"] == 1
    assert body["total_count"] == 1
