"""Deciding an alert, and what that decision does to the data.

The decision is the product. Everything upstream exists to put a merchant in
front of an analyst; everything downstream depends on what they concluded:

* **False alert** — the behaviour was legitimate, so it belongs in the baseline.
  Suppressing it would teach the system that normal trading is abnormal.
* **True alert** — confirmed bad, so it must be quarantined out of every future
  baseline, or the system absorbs its own findings as normal.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.api import create_app
from compliance.db import get_session
from compliance.models import Alert, Base, CaseEvent, Disposition, Merchant

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)


@pytest.fixture()
def ctx():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        s.add(Merchant(merchant_id="M1", mcc="5411", lane="A"))
        for i in (1, 2):
            s.add(
                Alert(
                    id=i,
                    merchant_id="M1",
                    as_of=AS_OF,
                    lane="A",
                    blended_score=0.9,
                    rank=i,
                    triggering_detectors=[
                        {"detector": "amount_vs_own_baseline", "sub_score": 0.9}
                    ],
                    feature_snapshot=[],
                )
            )
        s.commit()

    app = create_app()

    def override():
        db = S()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override
    return TestClient(app), S


class TestDeciding:
    def test_recording_a_true_alert(self, ctx):
        client, _ = ctx
        resp = client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "TRUE_POSITIVE",
                "reason_code": "STRUCTURING_CONFIRMED",
                "risk_axis": "REGULATORY",
                "notes": "Merchant could not explain the transaction.",
                "analyst_id": "analyst-1",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["verdict"] == "TRUE_POSITIVE"

    def test_a_decision_requires_reasoning(self, ctx):
        """A verdict with no reason code teaches the model nothing and leaves
        an auditor with an unexplained decision."""
        client, _ = ctx
        resp = client.post(
            "/api/alerts/1/disposition",
            json={"verdict": "TRUE_POSITIVE", "risk_axis": "REGULATORY",
                  "analyst_id": "a"},
        )

        assert resp.status_code == 422

    def test_deciding_twice_is_rejected(self, ctx):
        """An alert already ruled on must not be silently overwritten — the
        first decision is part of the audit trail."""
        client, _ = ctx
        body = {
            "verdict": "FALSE_POSITIVE",
            "reason_code": "SEASONAL_PROMOTION",
            "risk_axis": "COMMERCIAL",
            "analyst_id": "analyst-1",
        }
        assert client.post("/api/alerts/1/disposition", json=body).status_code == 200

        again = client.post("/api/alerts/1/disposition", json=body)

        assert again.status_code == 409

    def test_a_true_alert_opens_a_case(self, ctx):
        client, S = ctx
        client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "TRUE_POSITIVE",
                "reason_code": "STRUCTURING_CONFIRMED",
                "risk_axis": "REGULATORY",
                "analyst_id": "analyst-1",
            },
        )

        with S() as s:
            events = list(s.scalars(select(CaseEvent)))
        assert len(events) == 1
        assert events[0].event_type == "OPENED"

    def test_a_false_alert_opens_no_case(self, ctx):
        """Nothing to follow through on — it was legitimate trading."""
        client, S = ctx
        client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "FALSE_POSITIVE",
                "reason_code": "SEASONAL_PROMOTION",
                "risk_axis": "COMMERCIAL",
                "analyst_id": "analyst-1",
            },
        )

        with S() as s:
            assert list(s.scalars(select(CaseEvent))) == []


class TestQueueFiltering:
    def test_a_decided_alert_leaves_the_open_queue(self, ctx):
        client, _ = ctx
        assert len(client.get("/api/alerts").json()["items"]) == 2

        client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "FALSE_POSITIVE",
                "reason_code": "SEASONAL_PROMOTION",
                "risk_axis": "COMMERCIAL",
                "analyst_id": "analyst-1",
            },
        )

        remaining = client.get("/api/alerts").json()
        assert remaining["total"] == 1
        assert [a["id"] for a in remaining["items"]] == [2]

    def test_the_case_still_opens_from_its_alert(self, ctx):
        """Leaving the queue must not mean becoming unreachable."""
        client, _ = ctx
        client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "TRUE_POSITIVE",
                "reason_code": "STRUCTURING_CONFIRMED",
                "risk_axis": "REGULATORY",
                "analyst_id": "analyst-1",
            },
        )

        assert client.get("/api/alerts/1").status_code == 200


class TestBaselineFeedback:
    def test_a_true_alert_quarantines_its_day(self, ctx):
        """Confirmed-bad days must never shape a future baseline."""
        from compliance.detection.windows import quarantined_days

        client, S = ctx
        client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "TRUE_POSITIVE",
                "reason_code": "STRUCTURING_CONFIRMED",
                "risk_axis": "REGULATORY",
                "analyst_id": "analyst-1",
            },
        )

        with S() as s:
            assert any(m == "M1" for m, _ in quarantined_days(s))

    def test_a_false_alert_leaves_the_data_in(self, ctx):
        """Cleared means legitimate, so it stays in the training data —
        excluding it would teach the system that normal trading is abnormal."""
        from compliance.detection.windows import quarantined_days

        client, S = ctx
        client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "FALSE_POSITIVE",
                "reason_code": "SEASONAL_PROMOTION",
                "risk_axis": "COMMERCIAL",
                "analyst_id": "analyst-1",
            },
        )

        with S() as s:
            assert quarantined_days(s) == set()


class TestCaseStages:
    def _open_case(self, client):
        client.post(
            "/api/alerts/1/disposition",
            json={
                "verdict": "TRUE_POSITIVE",
                "reason_code": "STRUCTURING_CONFIRMED",
                "risk_axis": "REGULATORY",
                "analyst_id": "analyst-1",
            },
        )
        return client.get("/api/cases").json()["items"][0]["disposition_id"]

    def test_a_case_appears_with_its_opening_stage(self, ctx):
        client, _ = ctx
        self._open_case(client)

        cases = client.get("/api/cases").json()

        assert cases["total"] == 1
        assert cases["items"][0]["stage"] == "OPENED"
        assert cases["items"][0]["merchant_id"] == "M1"

    def test_stages_append_rather_than_replace(self, ctx):
        """The timeline is the record; overwriting a status would erase it."""
        client, _ = ctx
        case_id = self._open_case(client)

        for stage, note in [
            ("MERCHANT_CONTACTED", "Called the listed number."),
            ("DOCUMENTS_RECEIVED", "Invoices for the day supplied."),
            ("DOCUMENTS_VERIFIED", "Invoices match the transactions."),
        ]:
            resp = client.post(
                f"/api/cases/{case_id}/events",
                json={"event_type": stage, "note": note, "actor": "analyst-1"},
            )
            assert resp.status_code == 200

        case = client.get(f"/api/cases/{case_id}").json()
        assert [e["event_type"] for e in case["events"]] == [
            "OPENED",
            "MERCHANT_CONTACTED",
            "DOCUMENTS_RECEIVED",
            "DOCUMENTS_VERIFIED",
        ]
        assert case["stage"] == "DOCUMENTS_VERIFIED"

    def test_a_case_can_be_resolved_either_way(self, ctx):
        client, _ = ctx
        case_id = self._open_case(client)

        client.post(
            f"/api/cases/{case_id}/events",
            json={
                "event_type": "CLEARED",
                "note": "Merchant verified as legitimate.",
                "actor": "analyst-1",
            },
        )

        case = client.get(f"/api/cases/{case_id}").json()
        assert case["stage"] == "CLEARED"
        assert case["is_resolved"] is True

    def test_an_unknown_stage_is_rejected(self, ctx):
        """Free-text stages would make the follow-through board unsortable
        and the timeline unauditable."""
        client, _ = ctx
        case_id = self._open_case(client)

        resp = client.post(
            f"/api/cases/{case_id}/events",
            json={"event_type": "did some stuff", "actor": "analyst-1"},
        )

        assert resp.status_code == 422
