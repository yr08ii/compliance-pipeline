"""The queue pages in the database, not in Python.

At portfolio scale the open queue holds tens of thousands of alerts. Slicing a
fully materialised list cost the whole table on every page view — including the
two JSON columns on every row — which is what put seconds between an analyst
clicking a page and seeing it. These tests pin the shape of the work, not just
the answer: a correct queue built the wrong way reads identically.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance import diagnostics as diag
from compliance.api import create_app
from compliance.db import get_session
from compliance.glossary import DETECTOR_ALERT_TYPE
from compliance.models import Alert, Base, Disposition, Merchant

# One detector per alert type, so the seeded queue exercises every badge.
SAMPLE_DETECTORS = [
    "amount_vs_own_baseline",  # single_txn_spike
    "ticket_vs_mcc_peers",  # mcc_peer_discrepancy
    "hour_vs_own_pattern",  # temporal_anomaly
    "ticket_vs_subdistrict_peers",  # subdistrict_anomaly
    "structuring_below_threshold",  # typology_match
    "card_swarm",  # ring_signal
]


@pytest.fixture()
def queue():
    """A 240-alert open queue: deep enough that a full scan is visible."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    with S() as s:
        s.add(Merchant(merchant_id="M001", mcc="5411", lane="A"))
        s.flush()
        for i in range(240):
            detector = SAMPLE_DETECTORS[i % len(SAMPLE_DETECTORS)]
            s.add(
                Alert(
                    id=i + 1,
                    merchant_id="M001",
                    lane="A",
                    blended_score=1.0 - i / 1000,
                    rank=i + 1,
                    triggering_detectors=[{"detector": detector, "sub_score": 0.9}],
                    feature_snapshot=[
                        {
                            "feature_name": detector,
                            "merchant_value": 9_000.0,
                            "baseline_value": 120.0,
                            "deviation": 4.0,
                        }
                    ],
                    alert_type=DETECTOR_ALERT_TYPE[detector],
                    created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    as_of=datetime(2026, 5, 1, tzinfo=timezone.utc),
                )
            )
        # One decided alert, which the queue must drop.
        s.add(
            Disposition(
                alert_id=1,
                verdict="FALSE_POSITIVE",
                reason_code="EXPLAINED",
                risk_axis="COMMERCIAL",
                analyst_id="a.chan",
            )
        )
        s.commit()

    app = create_app()

    def _override():
        yield S()

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)
    statements.clear()
    return client, statements


def _alert_selects(statements: list[str]) -> list[str]:
    """Statements that read alert rows, excluding aggregates."""
    return [
        s
        for s in statements
        if "FROM alerts" in s and "count(" not in s.lower()
    ]


def test_page_is_bounded_in_sql(queue):
    client, statements = queue
    resp = client.get("/api/alerts?page=1&page_size=20")

    assert resp.status_code == 200
    reads = _alert_selects(statements)
    assert reads, "expected the queue to read alerts"
    # Every read of alert rows carries a LIMIT. Without this the endpoint can
    # return a correct page while still paying for the whole table.
    for statement in reads:
        assert "LIMIT" in statement, f"unbounded read of alerts: {statement}"


def test_deep_page_costs_the_same_as_the_first(queue):
    client, statements = queue

    client.get("/api/alerts?page=1&page_size=20")
    first = len(statements)
    statements.clear()

    client.get("/api/alerts?page=11&page_size=20")
    last = len(statements)

    # Page 11 of 12 must not cost more than page 1. A Python-side slice makes
    # both pages cost the whole queue, which is equal but for the wrong reason
    # — the LIMIT assertion above is what separates the two.
    assert last == first


def test_pagination_returns_the_right_slice(queue):
    client, _ = queue
    body = client.get("/api/alerts?page=2&page_size=20").json()

    # 240 seeded, one decided and dropped.
    assert body["total"] == 239
    assert body["pages"] == 12
    assert len(body["items"]) == 20
    # Ordered by the rank the pipeline assigned; alert 1 is decided, so the
    # second page starts at rank 22.
    assert [i["rank"] for i in body["items"]] == list(range(22, 42))


def test_last_page_is_partial_not_empty(queue):
    client, _ = queue
    body = client.get("/api/alerts?page=12&page_size=20").json()
    assert len(body["items"]) == 19


def test_type_filter_matches_python_derivation(queue):
    """The SQL filter and the badge on the row must agree.

    The badge is derived from the detector; the filter is now a column. If the
    two ever disagree, the queue silently hides alerts from the analyst who
    filtered for exactly them.
    """
    client, _ = queue
    for alert_type in sorted(set(DETECTOR_ALERT_TYPE.values())):
        body = client.get(
            f"/api/alerts?alert_type={alert_type}&page_size=200"
        ).json()
        for item in body["items"]:
            assert item["alert_type"] == alert_type
        expected = 40 if alert_type in {DETECTOR_ALERT_TYPE[d] for d in SAMPLE_DETECTORS} else 0
        if alert_type == "single_txn_spike":
            expected -= 1  # the decided one
        assert body["total"] == expected, alert_type


def test_counts_cover_the_whole_queue_not_the_page(queue):
    client, _ = queue
    counts = client.get("/api/alerts/counts").json()

    assert counts["total"] == 239
    assert counts["by_type"]["single_txn_spike"] == 39
    assert counts["by_type"]["ring_signal"] == 40
    assert sum(counts["by_type"].values()) == 239
    assert counts["scored_date"] == "2026-04-30"


def test_counts_are_aggregated_in_sql(queue):
    client, statements = queue
    client.get("/api/alerts/counts")

    assert any(
        "GROUP BY" in s and "count(" in s.lower() for s in statements
    ), "counts must be aggregated by the database"
    # The scored date needs one alert, not the queue.
    for statement in _alert_selects(statements):
        assert "LIMIT" in statement, f"unbounded read of alerts: {statement}"


def test_alert_type_falls_back_when_the_column_is_empty(queue):
    """Rows written before the column existed still carry a badge.

    The column is a cached derivation, so the derivation stays authoritative
    for anything the backfill has not reached.
    """
    client, _ = queue
    alert = Alert(
        merchant_id="M001",
        lane="A",
        blended_score=0.5,
        rank=999,
        triggering_detectors=[{"detector": "card_swarm", "sub_score": 0.5}],
        feature_snapshot=[],
        alert_type=None,
    )
    assert diag.alert_type(alert) == "ring_signal"
