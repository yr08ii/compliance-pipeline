"""The payload an analyst needs to answer "why did this fire?" without
leaving the screen: merchant identity, the day's transactions, and the
statistics behind each detector."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance.api import create_app
from compliance.db import get_session
from compliance.models import (
    Alert,
    Base,
    CohortSnapshot,
    Merchant,
    MerchantProfile,
    Transaction,
)

AS_OF = datetime(2026, 5, 1, tzinfo=timezone(timedelta(hours=8)))
SCORED_DAY = AS_OF - timedelta(days=1)  # 2026-04-30


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)

    with S() as s:
        s.add(
            Merchant(
                merchant_id="MID-889124",
                mcc="5411",
                mcc_description="Grocery Stores & Supermarkets",
                merchant_district="Yau tsim mong",
                merchant_subdistrict="Tsim sha tsui",
                city="Hong Kong",
                lane="A",
                business_nature="Retail",
                merchant_status="ACTIVE",
            )
        )
        # Peers, so the MCC cohort is a real distribution.
        for i in range(4):
            s.add(Merchant(merchant_id=f"PEER{i}", mcc="5411", lane="A"))
        s.flush()

        # Scored-day transactions, one of them the outlier.
        for i in range(5):
            s.add(
                Transaction(
                    source_txn_id=f"T{i}",
                    merchant_id="MID-889124",
                    total_amount=120.0 if i else 9_000.0,
                    occurred_at=SCORED_DAY.replace(hour=10 + i),
                    is_refund=False,
                    card_issuing_country="HK" if i else "US",
                    card_type="VISA",
                    transaction_status="SUCCESS",
                )
            )
        # A transaction on a different day, which the ledger must exclude.
        s.add(
            Transaction(
                source_txn_id="OTHER",
                merchant_id="MID-889124",
                total_amount=100.0,
                occurred_at=SCORED_DAY - timedelta(days=3),
                is_refund=False,
            )
        )

        s.add(
            MerchantProfile(
                merchant_id="MID-889124",
                as_of=AS_OF,
                metrics={
                    "baseline_center": 120.0,
                    "baseline_dispersion": 20.0,
                    "baseline_method": "mad",
                    "baseline_n": 180,
                    "baseline_usable": True,
                    "window_start": "2026-03-25T00:00:00+08:00",
                    "window_end": "2026-04-24T00:00:00+08:00",
                    "window_days": 30,
                    "lag_days": 7,
                    "quarantined_days": 0,
                    "peer_mcc": "5411",
                    "peer_center": 115.0,
                    "peer_dispersion": 18.0,
                    "peer_merchants": 5,
                    "peer_usable": True,
                    "peer_txn_center": 118.0,
                    "peer_txn_dispersion": 22.0,
                    "peer_txn_usable": True,
                    "volume_center": 5.0,
                    "volume_dispersion": 1.0,
                    "volume_usable": True,
                    "velocity_center": 1.0,
                    "velocity_dispersion": 1.0,
                    "velocity_usable": True,
                    "peer_volume_center": 5.0,
                    "peer_volume_dispersion": 1.0,
                    "peer_volume_usable": True,
                    "trend_ratio": 1.0,
                    "trend_is_ramp": False,
                    "hours_density": [0.04] * 96,
                    "hours_threshold": 0.01,
                    "hours_bandwidth": 0.9,
                    "hours_n": 180,
                    "hours_usable": True,
                    "origin_counts": {"HK": 180},
                    "origin_n": 180,
                    "origin_usable": True,
                    "cohort_hours_density": [0.04] * 96,
                    "cohort_hours_threshold": 0.005,
                    "cohort_hours_n": 900,
                    "cohort_hours_usable": True,
                    "district_txn_center": 116.0,
                    "district_txn_dispersion": 19.0,
                    "district_txn_usable": True,
                    "foreign_center": 0.02,
                    "foreign_dispersion": 0.02,
                    "foreign_usable": True,
                    "subdistrict": "Tsim sha tsui",
                },
            )
        )

        # The cohort as the run fitted it. The case page plots this rather than
        # rebuilding it, so the fixture has to carry it like a real run would.
        s.add(
            CohortSnapshot(
                as_of=AS_OF,
                mcc="5411",
                center=115.0,
                dispersion=18.0,
                q1=104.0,
                q3=128.0,
                upper_fence=155.0,
                n_merchants=5,
                usable=True,
                members=[98.0, 104.0, 115.0, 128.0, 141.0],
            )
        )
        s.add(
            Alert(
                id=1,
                merchant_id="MID-889124",
                as_of=AS_OF,
                lane="A",
                blended_score=0.9,
                rank=1,
                triggering_detectors=[
                    {"detector": "amount_vs_own_baseline", "sub_score": 0.9}
                ],
                feature_snapshot=[
                    {
                        "feature_name": "ticket_amount",
                        "merchant_value": 9000.0,
                        "baseline_value": 120.0,
                        "deviation": 300.0,
                    }
                ],
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
    return TestClient(app)


class TestAlertDetail:
    def test_carries_merchant_identity(self, client):
        """The review header cannot be built from the alert row alone."""
        body = client.get("/api/alerts/1").json()

        assert body["mcc"] == "5411"
        assert body["mcc_description"] == "Grocery Stores & Supermarkets"
        assert body["merchant_district"] == "Yau tsim mong"
        assert body["merchant_subdistrict"] == "Tsim sha tsui"
        assert body["lane"] == "A"

    def test_reports_the_scored_day_not_the_write_time(self, client):
        """as_of 2026-05-01 evaluates 2026-04-30. Showing the row's write
        timestamp instead would mislabel every backfilled alert."""
        body = client.get("/api/alerts/1").json()

        assert body["scored_date"] == "2026-04-30"

    def test_carries_its_alert_type(self, client):
        body = client.get("/api/alerts/1").json()

        assert body["alert_type"] == "single_txn_spike"

    def test_queue_rows_carry_the_same_metadata(self, client):
        row = client.get("/api/alerts").json()["items"][0]

        assert row["mcc_description"] == "Grocery Stores & Supermarkets"
        assert row["alert_type"] == "single_txn_spike"
        assert row["scored_date"] == "2026-04-30"


class TestLedger:
    def test_lists_the_scored_day_transactions(self, client):
        body = client.get(
            "/api/merchants/MID-889124/transactions?date=2026-04-30"
        ).json()

        assert body["count"] == 5
        assert len(body["transactions"]) == 5
        assert body["total_amount"] == pytest.approx(9000.0 + 120.0 * 4)

    def test_excludes_other_days(self, client):
        body = client.get(
            "/api/merchants/MID-889124/transactions?date=2026-04-27"
        ).json()

        assert body["count"] == 1

    def test_never_exposes_the_pan_hash(self, client):
        """A 1:1 PAN hash is brute-forceable, so it is cardholder data and
        must not cross the API boundary."""
        raw = client.get(
            "/api/merchants/MID-889124/transactions?date=2026-04-30"
        ).text

        assert "hashed_pan" not in raw

    def test_flags_which_rows_are_outliers(self, client):
        """The ledger is only useful if the analyst can see which checkout
        drove the alert."""
        body = client.get(
            "/api/merchants/MID-889124/transactions?date=2026-04-30"
        ).json()

        outliers = [t for t in body["transactions"] if t["is_outlier"]]
        assert len(outliers) == 1
        assert outliers[0]["total_amount"] == 9000.0


class TestDiagnostics:
    def test_returns_every_detector_with_a_verdict(self, client):
        """Every Family A baseline and every enabled Family B rule reports,
        whether it fired or not. A rule that stayed silent is information: it
        says structuring was checked for and not found, which is not the same
        as nobody having looked."""
        from compliance.detection.ruleset import Family, default_instances

        body = client.get("/api/alerts/1/diagnostics").json()
        families = [d["family"] for d in body["detectors"]]

        assert families.count("A") == 12
        assert families.count("B") == len(default_instances(Family.B))
        assert {d["status"] for d in body["detectors"]} <= {"OK", "FAIL", "SKIP"}
        assert any(d["status"] == "FAIL" for d in body["detectors"])

    def test_detectors_carry_the_transactions_that_drove_them(self, client):
        """The feedback's core ask: an amount alert must name the transaction,
        not just report that the day was anomalous."""
        body = client.get("/api/alerts/1/diagnostics").json()
        amount = next(
            d for d in body["detectors"] if d["detector"] == "amount_vs_own_baseline"
        )

        assert amount["status"] == "FAIL"
        assert amount["contributions"], "amount alert named no transaction"
        # And it must say which column carried the cause, so the ledger can
        # highlight that cell rather than the whole row.
        assert {c["field"] for c in amount["contributions"]} == {"total_amount"}
        assert all(c["source_txn_id"] for c in amount["contributions"])
        assert all(c["reason"] for c in amount["contributions"])

    def test_each_detector_explains_itself(self, client):
        body = client.get("/api/alerts/1/diagnostics").json()
        failed = next(d for d in body["detectors"] if d["status"] == "FAIL")

        assert failed["label"]
        assert failed["message"]
        assert failed["alert_type"]

    def test_carries_the_statistics_the_feedback_asked_for(self, client):
        """Mean, median, MAD, modified z and N — merchant against peers."""
        body = client.get("/api/alerts/1/diagnostics").json()
        stats = body["statistics"]

        assert stats["merchant"]["median"] == pytest.approx(120.0)
        assert stats["merchant"]["mad"] == pytest.approx(20.0)
        assert stats["merchant"]["n"] == 180
        assert stats["merchant"]["mean"] is not None
        assert stats["peer_mcc"]["median"] == pytest.approx(115.0)
        assert stats["peer_mcc"]["n"] == 5
        assert stats["modified_z"] is not None

    def test_carries_the_hour_density_curve_for_plotting(self, client):
        body = client.get("/api/alerts/1/diagnostics").json()

        assert len(body["hour_density"]["merchant"]) == 96
        assert len(body["hour_density"]["cohort"]) == 96
        assert body["hour_density"]["threshold"] > 0

    def test_carries_the_peer_distribution_for_the_box_plot(self, client):
        body = client.get("/api/alerts/1/diagnostics").json()
        dist = body["peer_distribution"]

        assert dist["merchant_value"] is not None
        assert dist["peer_median"] is not None
        assert dist["n_merchants"] == 5

    def test_404_for_an_unknown_alert(self, client):
        assert client.get("/api/alerts/999/diagnostics").status_code == 404
