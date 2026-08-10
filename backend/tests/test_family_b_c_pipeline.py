"""Family B and C, end to end against the synthetic ground truth.

The generator plants each typology and each ring deliberately, so these tests
assert what the detectors are *for*: the planted merchant is found, and the
well-behaved ones are not. A detector that fires on everything passes a
"did it fire" test and is useless.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance import synthetic
from compliance.models import Alert, Base
from compliance.pipeline.flow import run_pipeline_direct

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)


@pytest.fixture(scope="module")
def alerts():
    """One full pipeline run over the generated history."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as session:
        synthetic.generate_history(session, as_of=AS_OF)
        session.commit()
        run_pipeline_direct(session, as_of=AS_OF)
        session.commit()
        yield list(session.scalars(__import__("sqlalchemy").select(Alert)))


def raised_for(alerts, detector: str) -> list:
    """The alerts a given detector raised.

    The card-linkage rules raise one alert per card rather than one per
    participating merchant, so the count itself is part of the contract.
    """
    return [
        a for a in alerts
        if any(h["detector"] == detector for h in a.triggering_detectors)
    ]


def linkage(alert) -> dict:
    """The cross-merchant picture a Family C card rule recorded."""
    return alert.triggering_detectors[0]["linkage"]


def fired(alerts, detector: str) -> set[str]:
    """Merchants for which a given detector raised an alert."""
    return {
        a.merchant_id
        for a in alerts
        if any(d["detector"] == detector for d in a.triggering_detectors)
    }


class TestFamilyB:
    def test_structuring_finds_the_planted_merchant(self, alerts):
        assert synthetic.STRUCT in fired(alerts, "structuring_below_threshold")

    def test_structuring_spares_its_ordinary_peers(self, alerts):
        """The two other electronics shops trade the same way minus the
        cluster. If they fire too, the rule is detecting the trade rather than
        the pattern."""
        hit = fired(alerts, "structuring_below_threshold")
        assert "ELEC2" not in hit and "ELEC3" not in hit

    def test_structuring_names_the_transactions_in_the_band(self, alerts):
        alert = next(
            a for a in alerts
            if a.merchant_id == synthetic.STRUCT
            and a.triggering_detectors[0]["detector"] == "structuring_below_threshold"
        )
        contributions = alert.triggering_detectors[0]["contributions"]

        assert len(contributions) == 4
        assert {c["field"] for c in contributions} == {"total_amount"}

    def test_refund_abuse_finds_the_refunder(self, alerts):
        assert synthetic.REFUNDER in fired(alerts, "refund_ratio_spike")

    def test_dormant_reactivation_finds_the_returning_merchant(self, alerts):
        assert synthetic.DORMANT in fired(alerts, "dormant_reactivation")

    def test_decline_spike_finds_the_card_testing_terminal(self, alerts):
        assert synthetic.DECLINER in fired(alerts, "decline_ratio_spike")

    def test_steady_merchant_matches_no_typology(self, alerts):
        """The control. STEADY is generated to be unremarkable, so any Family
        B rule firing on it is a false positive by construction."""
        from compliance.detection.ruleset import Family, default_instances

        typologies = {i.template for i in default_instances(Family.B)}
        for alert in alerts:
            if alert.merchant_id != synthetic.STEADY:
                continue
            for hit in alert.triggering_detectors:
                assert hit["detector"] not in typologies, hit


class TestFamilyC:
    def test_shared_registration_ring_is_found(self, alerts):
        """All three storefronts share one registration hash, so all three
        should be surfaced — a ring is not a property of one member."""
        assert fired(alerts, "shared_identity_ring") >= synthetic.RING_MEMBERS

    def test_one_card_across_the_ring_branches_is_flagged(self, alerts):
        """Three related merchants is the shipped limit, and the generator
        puts one card at all three, so the limit is exceeded.

        One card is one investigation, so it is one alert — attributed to a
        member of the ring and naming the rest. Raising it once per branch
        made the same finding arrive three times."""
        raised = raised_for(alerts, "card_across_related_merchants")
        assert len(raised) == 1
        assert raised[0].merchant_id in synthetic.RING_MEMBERS
        assert linkage(raised[0])["merchants"] == sorted(synthetic.RING_MEMBERS)

    def test_the_branch_finding_names_common_ownership(self, alerts):
        """What distinguishes this rule from card swarming, and the thing the
        analyst is asked to notice."""
        raised = raised_for(alerts, "card_across_related_merchants")
        assert linkage(raised[0])["related"] is True

    def test_impossible_travel_between_far_merchants(self, alerts):
        raised = raised_for(alerts, "impossible_geo_velocity")
        assert len(raised) == 1
        assert {synthetic.FAR_A, synthetic.FAR_B} == set(
            linkage(raised[0])["merchants"]
        )

    def test_impossible_travel_shows_the_arithmetic(self, alerts):
        """An analyst told a journey was impossible is owed the sum that says
        so: the two places, the distance, the elapsed time, the implied speed,
        and how far over the limit it lands."""
        raised = raised_for(alerts, "impossible_geo_velocity")
        leg = linkage(raised[0])["legs"][0]
        assert {leg["from_merchant"], leg["to_merchant"]} == {
            synthetic.FAR_A,
            synthetic.FAR_B,
        }
        assert leg["distance_km"] > 0
        assert leg["minutes"] > 0
        assert leg["kmh"] > leg["limit_kmh"]
        assert leg["over_limit_multiple"] > 1

    def test_the_linked_evidence_spans_both_merchants(self, alerts):
        """"Show me this card's transactions at all of them" is the analyst's
        actual question, so the evidence does not stop at the merchant
        carrying the alert."""
        raised = raised_for(alerts, "impossible_geo_velocity")
        contributions = raised[0].triggering_detectors[0]["contributions"]
        assert {c["value"] for c in contributions} == {
            synthetic.FAR_A,
            synthetic.FAR_B,
        }

    def test_wallet_rails_never_raise_a_card_linkage_alert(self, alerts):
        """Alipay, Octopus, WeChat Pay and PayMe carry no card number. The
        source writes the column blank, so every wallet row compares equal to
        every other — which read as one card at thousands of merchants and
        produced tens of thousands of ring alerts against the real extract."""
        card_rules = {
            "card_across_related_merchants",
            "card_swarm",
            "impossible_geo_velocity",
        }
        for alert in alerts:
            for hit in alert.triggering_detectors:
                if hit["detector"] not in card_rules:
                    continue
                assert alert.merchant_id not in synthetic.WALLET_ONLY_MERCHANTS

    def test_ring_alerts_never_carry_the_card_hash(self, alerts):
        """The PAN hash is brute-forceable, so it is cardholder data and must
        not reach an alert — however useful it was for detection."""
        import json

        for alert in alerts:
            blob = json.dumps(alert.triggering_detectors) + json.dumps(
                alert.feature_snapshot
            )
            assert "panhash" not in blob
            assert "hashed_pan" not in blob

    def test_unrelated_merchants_are_not_called_a_ring(self, alerts):
        """Every non-ring merchant has its own registration hash, so the
        equality join must not group them."""
        assert synthetic.STEADY not in fired(alerts, "shared_identity_ring")


class TestEvidence:
    def test_every_alert_is_explainable(self, alerts):
        """An alert with neither a feature row nor a message cannot be
        explained to an analyst, let alone a regulator."""
        for alert in alerts:
            hit = alert.triggering_detectors[0]
            assert alert.feature_snapshot or hit.get("message"), alert.merchant_id

    def test_rule_alerts_record_the_parameters_that_fired_them(self, alerts):
        """A bare rule name stops meaning anything once the rule is retuned."""
        for alert in alerts:
            for hit in alert.triggering_detectors:
                if hit.get("rule_id"):
                    assert "(" in hit["reason_code"], hit

    def test_ranks_are_unique_and_dense(self, alerts):
        ranks = sorted(a.rank for a in alerts)
        assert ranks == list(range(1, len(alerts) + 1))
