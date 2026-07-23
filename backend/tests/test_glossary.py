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
    jargon = ("mcc", "baseline", "z-score", "subdistrict", "peer", "vs_")
    # "sale"/"takings" are vaguer than the source system's own noun. Using a
    # synonym for transaction adds a translation step instead of removing one.
    vague = ("sale", "takings", "level ", "run level")
    for term in glossary.DETECTORS + glossary.FEATURES:
        lowered = term.label.lower()
        assert not any(word in lowered for word in jargon), term.label
        assert not any(word in lowered for word in vague), term.label


def test_endpoint_returns_all_four_vocabularies():
    resp = TestClient(create_app()).get("/api/glossary")

    assert resp.status_code == 200
    body = resp.json()
    assert {t["key"] for t in body["detectors"]} == {t.key for t in glossary.DETECTORS}
    assert {t["key"] for t in body["lanes"]} == {"A", "B"}
    assert body["features"] and body["baseline_methods"]


def test_every_feature_name_emitted_by_a_detector_has_a_label():
    """Feature names appear in the divergence panel, so they need translating
    too — not just the detector that produced them."""
    import re
    from pathlib import Path

    source = Path(stages.__file__).read_text()
    emitted = set(re.findall(r'"feature_name":\s*"([a-z_0-9]+)"', source))
    labelled = {t.key for t in glossary.FEATURES}

    assert emitted - labelled == set(), f"feature names with no label: {emitted - labelled}"
