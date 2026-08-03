from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from compliance.models import Base, Merchant, Alert
from compliance.api import create_app
from compliance.db import get_session


def _client_with_data():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    with TestSession() as s:
        s.add(Merchant(merchant_id="M001", mcc="5411", lane="A"))
        s.add(Alert(id=1, merchant_id="M001", lane="A", blended_score=0.9, rank=1,
                    triggering_detectors=[{"detector": "velocity", "sub_score": 0.9}],
                    feature_snapshot=[{"feature_name": "daily_volume", "merchant_value": 5e4,
                                       "baseline_value": 8e3, "deviation": 5.25}],
                    created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)))
        s.commit()
    app = create_app()

    def _override_get_session():
        yield TestSession()

    app.dependency_overrides[get_session] = _override_get_session
    return TestClient(app)


def test_list_alerts():
    client = _client_with_data()
    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    body = resp.json()
    # The queue is paginated: a page of items plus the totals the pager needs.
    assert body["total"] == 1
    assert body["pages"] == 1
    assert body["items"][0]["merchant_id"] == "M001"
    assert body["items"][0]["lane"] == "A"


def test_get_alert_detail_has_divergence():
    client = _client_with_data()
    resp = client.get("/api/alerts/1")
    assert resp.status_code == 200
    assert resp.json()["feature_snapshot"][0]["deviation"] == 5.25
