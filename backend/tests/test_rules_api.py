"""The tuning surface for Family B and C.

The feedback asked for an interface where compliance can manipulate the rules
and add their own. These tests hold that surface to the properties that make
it safe: bounded parameters, no unknown templates, and a rule set that still
loads after a template is retired.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from compliance import rules_store
from compliance.api import create_app
from compliance.db import get_session
from compliance.detection import ruleset
from compliance.detection.ruleset import Family, RuleInstance
from compliance.models import Base


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture()
def client(session_factory):
    app = create_app()

    def _session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    return TestClient(app)


class TestCatalogue:
    def test_serves_templates_alongside_instances(self, client):
        """The screen renders controls from the backend's declaration, so a
        rule cannot gain a parameter the UI has no way to reach."""
        body = client.get("/api/rules").json()

        assert {t["key"] for t in body["templates"]} == {
            t.key for t in ruleset.TEMPLATES
        }
        assert {i["template"] for i in body["instances"]} == {
            t.key for t in ruleset.TEMPLATES
        }

    def test_every_template_declares_bounded_parameters(self, client):
        body = client.get("/api/rules").json()
        for template in body["templates"]:
            assert template["params"], template["key"]
            for param in template["params"]:
                assert param["minimum"] < param["maximum"]
                assert param["minimum"] <= param["default"] <= param["maximum"]
                # A threshold with no stated trade-off invites turning
                # sensitivity down until the queue is empty.
                assert param["hint"], (template["key"], param["key"])

    def test_every_template_carries_a_rationale(self, client):
        """An AML rule that cannot say why it is built that way is not
        defensible to a regulator."""
        for template in client.get("/api/rules").json()["templates"]:
            assert len(template["rationale"]) > 80, template["key"]


class TestEditing:
    def test_retuning_a_parameter_persists(self, client):
        body = client.get("/api/rules").json()
        instances = body["instances"]
        target = next(
            i for i in instances if i["template"] == "structuring_below_threshold"
        )
        target["params"]["threshold"] = 50_000.0

        resp = client.put("/api/rules", json={"instances": instances})
        assert resp.status_code == 200

        saved = next(
            i for i in client.get("/api/rules").json()["instances"]
            if i["template"] == "structuring_below_threshold"
        )
        assert saved["params"]["threshold"] == 50_000.0

    def test_an_officer_can_add_their_own_rule_instance(self, client):
        """Two instances of one template with different parameters and scopes
        — structuring portfolio-wide, and a stricter one for jewellers."""
        instances = client.get("/api/rules").json()["instances"]
        instances.append({
            "instance_id": "structuring_jewellers",
            "template": "structuring_below_threshold",
            "enabled": True,
            "params": {"threshold": 100_000.0},
            "mcc_scope": ["5944"],
            "custom": True,
            "label": "Structuring — jewellers",
        })

        resp = client.put("/api/rules", json={"instances": instances})
        assert resp.status_code == 200

        saved = client.get("/api/rules").json()["instances"]
        added = next(i for i in saved if i["instance_id"] == "structuring_jewellers")
        assert added["mcc_scope"] == ["5944"]
        assert added["custom"] is True
        assert added["label"] == "Structuring — jewellers"

    def test_a_rule_can_be_switched_off(self, client):
        instances = client.get("/api/rules").json()["instances"]
        for i in instances:
            if i["template"] == "card_swarm":
                i["enabled"] = False

        client.put("/api/rules", json={"instances": instances})

        with_off = next(
            i for i in client.get("/api/rules").json()["instances"]
            if i["template"] == "card_swarm"
        )
        assert with_off["enabled"] is False


class TestValidation:
    def test_out_of_range_parameter_is_rejected(self, client):
        """A geo-velocity rule at 0 km/h would flag the whole portfolio. A
        tuning screen must not be able to do that by typo."""
        instances = client.get("/api/rules").json()["instances"]
        for i in instances:
            if i["template"] == "impossible_geo_velocity":
                i["params"]["max_kmh"] = 0.0

        resp = client.put("/api/rules", json={"instances": instances})
        assert resp.status_code == 422
        assert "Maximum plausible speed" in resp.json()["detail"]

    def test_unknown_template_is_rejected(self, client):
        resp = client.put("/api/rules", json={
            "instances": [{"instance_id": "x", "template": "not_a_rule"}]
        })
        assert resp.status_code == 422

    def test_duplicate_instance_ids_are_rejected(self, client):
        resp = client.put("/api/rules", json={"instances": [
            {"instance_id": "dup", "template": "card_swarm"},
            {"instance_id": "dup", "template": "bust_out"},
        ]})
        assert resp.status_code == 422
        assert "duplicate" in resp.json()["detail"]

    def test_portfolio_wide_rules_cannot_be_scoped_to_an_mcc(self, client):
        """A ring spans merchants of different categories by nature, so an MCC
        scope on one would quietly exclude half the ring."""
        resp = client.put("/api/rules", json={"instances": [
            {
                "instance_id": "ring",
                "template": "shared_identity_ring",
                "mcc_scope": ["5411"],
            }
        ]})
        assert resp.status_code == 422

    def test_a_rejected_save_changes_nothing(self, client):
        """Whole-set replacement: a partial save would leave the officer
        believing they configured something they had not."""
        before = client.get("/api/rules").json()["instances"]
        client.put("/api/rules", json={"instances": [
            {"instance_id": "x", "template": "not_a_rule"}
        ]})
        assert client.get("/api/rules").json()["instances"] == before


class TestStore:
    def test_defaults_are_used_when_nothing_was_ever_saved(self, session_factory):
        with session_factory() as s:
            assert len(rules_store.load_rules(s)) == len(ruleset.TEMPLATES)

    def test_a_retired_template_does_not_stop_the_nightly_run(self, session_factory):
        """The alternative is a pipeline that cannot start until somebody
        edits a JSON blob in the database."""
        from compliance.models import DetectionSetting

        with session_factory() as s:
            s.add(DetectionSetting(key=rules_store.RULES_KEY, value={
                "instances": [
                    {"instance_id": "gone", "template": "retired_rule"},
                    {"instance_id": "card_swarm", "template": "card_swarm"},
                ]
            }))
            s.commit()

            live = rules_store.load_rules(s)
            assert [i.template for i in live] == ["card_swarm"]

    def test_disabled_rules_are_excluded_from_the_active_set(self, session_factory):
        with session_factory() as s:
            rules_store.save_rules(s, [
                RuleInstance("a", "card_swarm", enabled=False),
                RuleInstance("b", "bust_out", enabled=True),
            ])
            s.commit()

            assert [i.template for i in rules_store.active_rules(s)] == ["bust_out"]

    def test_family_filter_splits_the_two_rulesets(self, session_factory):
        with session_factory() as s:
            assert all(
                i.spec().family is Family.B
                for i in rules_store.load_rules(s, Family.B)
            )
            assert all(
                i.spec().family is Family.C
                for i in rules_store.load_rules(s, Family.C)
            )

    def test_a_parameter_removed_from_a_template_is_dropped(self, session_factory):
        """Adding or retiring a parameter must not invalidate every instance
        already stored."""
        inst = RuleInstance.from_dict({
            "instance_id": "x",
            "template": "card_swarm",
            "params": {"min_merchants": 4, "retired_knob": 99},
        })
        assert inst.params == {"min_merchants": 4.0}
        # And an unset parameter still resolves, via the template default.
        assert inst.value("window_minutes") == 120
