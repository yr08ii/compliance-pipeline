"""Every internal identifier that can reach a person must have a label."""

from fastapi.testclient import TestClient

from compliance import glossary
from compliance.api import create_app
from compliance.pipeline import stages


def test_every_live_detector_has_a_label():
    """A detector shipping without a label would surface its raw identifier in
    the queue, which is the thing this exists to prevent."""
    live = {
        value for name, value in vars(stages).items()
        if name.endswith("_DETECTOR") and isinstance(value, str)
    }
    labelled = {t.key for t in glossary.DETECTORS}

    assert live - labelled == set(), f"detectors with no plain-English label: {live - labelled}"


def test_no_labels_for_detectors_that_no_longer_exist():
    live = {
        value for name, value in vars(stages).items()
        if name.endswith("_DETECTOR") and isinstance(value, str)
    }
    labelled = {t.key for t in glossary.DETECTORS}

    assert labelled - live == set(), f"labels for removed detectors: {labelled - live}"


def test_labels_avoid_the_internal_vocabulary():
    """The point is a sentence an analyst can act on, not a restatement of the
    identifier."""
    # Compliance analysts work with MCC codes and baselines daily — those are
    # their vocabulary, not jargon, and the feedback asked for them explicitly.
    # What must not appear is anything vague: "category" and "sale" hide which
    # grouping or quantity is meant, which is the friction being removed.
    vague = ("category", "sale", "takings", "run level", "too high")
    for term in glossary.DETECTORS + glossary.FEATURES:
        lowered = term.label.lower()
        assert not any(word in lowered for word in vague), term.label


def test_every_detector_maps_to_an_alert_type():
    """An unmapped detector would render an unlabelled badge in the queue."""
    live = {t.key for t in glossary.DETECTORS}
    mapped = set(glossary.DETECTOR_ALERT_TYPE)
    assert live - mapped == set(), f"detectors with no alert type: {live - mapped}"
    assert mapped - live == set(), f"alert types for removed detectors: {mapped - live}"


def test_alert_types_are_all_declared():
    declared = {t.key for t in glossary.ALERT_TYPES}
    used = set(glossary.DETECTOR_ALERT_TYPE.values())
    assert used <= declared, f"undeclared alert types: {used - declared}"


def test_endpoint_returns_all_four_vocabularies():
    resp = TestClient(create_app()).get("/api/glossary")

    assert resp.status_code == 200
    body = resp.json()
    assert {t["key"] for t in body["detectors"]} == {t.key for t in glossary.DETECTORS}
    assert {t["key"] for t in body["lanes"]} == {"A", "B"}
    assert body["features"] and body["baseline_methods"]
    assert {t["key"] for t in body["alert_types"]} == {
        t.key for t in glossary.ALERT_TYPES
    }


def test_every_feature_name_emitted_by_a_detector_has_a_label():
    """Feature names appear in the divergence panel, so they need translating
    too — not just the detector that produced them."""
    import re
    from pathlib import Path

    source = Path(stages.__file__).read_text()
    emitted = set(re.findall(r'"feature_name":\s*"([a-z_0-9]+)"', source))
    labelled = {t.key for t in glossary.FEATURES}
    # Per-rail feature names are generated (amount_on_visa, amount_on_octopus,
    # ...) and the frontend formats them from the prefix, so a literal label
    # for every rail the world might add is neither possible nor useful.
    emitted = {e for e in emitted if not e.startswith("amount_on_")}

    assert emitted - labelled == set(), f"feature names with no label: {emitted - labelled}"
