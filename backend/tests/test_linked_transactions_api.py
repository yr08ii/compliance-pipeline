"""The cross-merchant view behind a ring alert.

A card-linkage finding is a claim about several merchants at once. The day
ledger shows one merchant's day, so half the evidence for "this card was in
two places twenty minutes apart" sat behind a different merchant's page and
the analyst had to line up the timestamps by hand.

This endpoint returns the card's transactions across every merchant it
touched, in time order with the gaps between them, plus — for impossible
travel — the distance, time and speed calculation that produced the verdict.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance import synthetic
from compliance.api import create_app
from compliance.db import get_session
from compliance.models import Alert, Base

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)


@pytest.fixture(scope="module")
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    from compliance.pipeline.flow import run_pipeline_direct

    with S() as s:
        synthetic.generate_history(s, as_of=AS_OF)
        s.commit()
        run_pipeline_direct(s, as_of=AS_OF)
        s.commit()

    app = create_app()

    def override():
        with S() as s:
            yield s

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        c.session_factory = S
        yield c


def geo_alert(client) -> int:
    with client.session_factory() as s:
        for alert in s.scalars(select(Alert)):
            if any(
                h["detector"] == "impossible_geo_velocity"
                for h in alert.triggering_detectors
            ):
                return alert.id
    raise AssertionError("the generator plants an impossible journey")


class TestTheJourneyIsShown:
    def test_both_merchants_appear(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        link = body["links"][0]
        assert {t["merchant_id"] for t in link["transactions"]} == {
            synthetic.FAR_A,
            synthetic.FAR_B,
        }

    def test_the_alert_merchant_is_distinguishable_from_the_counterparty(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        flags = [t["is_alert_merchant"] for t in body["links"][0]["transactions"]]
        assert True in flags and False in flags

    def test_transactions_are_in_time_order_with_the_gaps(self, client):
        """The gap between consecutive transactions is the column the whole
        impossible-travel claim rests on, so it is a column."""
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        rows = body["links"][0]["transactions"]
        assert rows[0]["minutes_since_previous"] is None
        assert rows[1]["minutes_since_previous"] > 0
        assert [r["occurred_at"] for r in rows] == sorted(
            r["occurred_at"] for r in rows
        )

    def test_the_places_are_named(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        assert all(
            t["merchant_subdistrict"] for t in body["links"][0]["transactions"]
        )

    def test_the_speed_calculation_is_returned_in_full(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        leg = body["links"][0]["legs"][0]
        assert leg["distance_km"] > 0
        assert leg["minutes"] > 0
        # The three numbers an analyst checks the verdict against.
        assert leg["kmh"] > leg["limit_kmh"]
        assert leg["over_limit_multiple"] == round(leg["kmh"] / leg["limit_kmh"], 2)
        assert leg["from_place"] != leg["to_place"]


class TestTheCaseIsAboutTheCard:
    """A card-linkage alert is one investigation into one card, so the page
    has to be able to describe the card rather than one of the merchants it
    happened to be attributed to."""

    def test_the_trail_rolls_up_per_merchant(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        trail = body["links"][0]["trail"]
        assert {m["merchant_id"] for m in trail} == {
            synthetic.FAR_A,
            synthetic.FAR_B,
        }
        assert all(m["transactions"] >= 1 for m in trail)
        assert all(m["subdistrict"] for m in trail)

    def test_the_span_and_value_of_the_whole_trail_are_given(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        link = body["links"][0]
        assert link["first_seen"] <= link["last_seen"]
        assert link["total_amount"] > 0
        assert link["rails"] == ["VISA"]

    def test_the_triggering_transactions_are_distinguished(self, client):
        """Widening the evidence to the card's whole day must not blur what
        the accusation rests on."""
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        rows = body["links"][0]["transactions"]
        assert any(r["is_focus"] for r in rows)

    def test_the_arrival_row_carries_the_speed_that_condemns_it(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).json()
        rows = body["links"][0]["transactions"]
        arrivals = [r for r in rows if r["arrived_at_kmh"] is not None]
        assert arrivals, "the arrival end of an impossible leg"
        assert arrivals[0]["arrived_at_kmh"] > 60
        assert arrivals[0]["arrived_from_km"] > 0


class TestCommonOwnershipIsMarked:
    def test_branches_of_one_chain_share_a_group_label(self, client):
        """The difference between this card visiting four branches of one
        owner and four unrelated shops is the whole finding, so it is on the
        row rather than left to be inferred."""
        with client.session_factory() as s:
            alert = next(
                a for a in s.scalars(select(Alert))
                if any(
                    h["detector"] == "card_across_related_merchants"
                    for h in a.triggering_detectors
                )
            )
            alert_id = alert.id

        body = client.get(f"/api/alerts/{alert_id}/linked-transactions").json()
        link = body["links"][0]
        assert link["related"] is True
        groups = {t["owner_group"] for t in link["transactions"]}
        assert groups == {"Chain A"}, "all four branches are one owner"


class TestBoundaries:
    def test_the_card_hash_never_reaches_the_response(self, client):
        body = client.get(
            f"/api/alerts/{geo_alert(client)}/linked-transactions"
        ).text
        assert "panhash" not in body
        assert "hashed_pan" not in body

    def test_an_alert_with_no_card_linkage_returns_nothing(self, client):
        with client.session_factory() as s:
            alert = next(
                a for a in s.scalars(select(Alert))
                if not any(h.get("linkage") for h in a.triggering_detectors)
            )
            alert_id = alert.id
        body = client.get(f"/api/alerts/{alert_id}/linked-transactions").json()
        assert body["links"] == []

    def test_an_unknown_alert_is_a_404(self, client):
        assert client.get("/api/alerts/999999/linked-transactions").status_code == 404
