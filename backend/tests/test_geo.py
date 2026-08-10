"""The Hong Kong distance table behind the geo-velocity rule."""

import csv
from pathlib import Path

import pytest

from compliance.detection import geo


class TestLookup:
    def test_known_subdistrict_resolves(self):
        assert geo.coordinates("Mong kok") is not None

    def test_lookup_is_case_and_whitespace_insensitive(self):
        """The real extract carries 'Lamma island ' with a trailing space and
        both 'Kwai fong' and 'Kuai fong'. Matching raw strings would drop
        those rows out of every geographic check without reporting it."""
        assert geo.coordinates("MONG KOK") == geo.coordinates("Mong kok")
        assert geo.coordinates("  Lamma island  ") == geo.coordinates("Lamma island")
        assert geo.coordinates("Kuai fong") == geo.coordinates("Kwai fong")

    def test_falls_back_to_the_district(self):
        assert geo.coordinates("Nowhere at all", "Sha tin") is not None

    def test_unknown_place_is_none_not_a_default_point(self):
        """A default would put every unmapped merchant on one spot and
        manufacture impossible velocities between merchants whose only shared
        property is that we do not know where they are."""
        assert geo.coordinates("Nowhere", "Nowhere either") is None


class TestDistance:
    def test_same_place_is_zero(self):
        assert geo.distance_km(("Central", None), ("Central", None)) == 0.0

    def test_cross_territory_distance_is_plausible(self):
        """Tung Chung to Sai Kung spans most of the territory — roughly 40 km
        as the crow flies."""
        km = geo.distance_km(("Tung chung", None), ("Sai kung", None))
        assert 30 < km < 50

    def test_neighbouring_subdistricts_are_close(self):
        km = geo.distance_km(("Mong kok", None), ("Yau ma tei", None))
        assert 0 < km < 2

    def test_unknown_endpoint_gives_no_distance(self):
        assert geo.distance_km(("Central", None), ("Atlantis", None)) is None


class TestSpeed:
    def test_speed_is_distance_over_time(self):
        assert geo.implied_speed(30.0, 30.0) == pytest.approx(60.0)

    def test_simultaneous_transactions_have_no_measurable_speed(self):
        """Two transactions at the same recorded minute are a clock-resolution
        artefact or a batch import, not evidence of supersonic travel. Treating
        it as infinite speed makes the rule fire on timestamp granularity."""
        assert geo.implied_speed(30.0, 0.0) is None
        assert geo.implied_speed(30.0, -5.0) is None


class TestCoverage:
    def test_every_place_in_the_real_extract_is_mapped(self):
        """A subdistrict missing from the table silently disables geo checks
        for every merchant in it, so the gap has to be visible here."""
        source = (
            Path(__file__).resolve().parents[2]
            / "real_data"
            / "600ca455-cab7-42e7-961e-0f2f239fab21.csv"
        )
        if not source.exists():
            pytest.skip("real extract not present in this checkout")

        seen: set[tuple[str, str]] = set()
        with source.open() as handle:
            for i, row in enumerate(csv.DictReader(handle)):
                seen.add((row["merchant_subdistrict"], row["merchant_district"]))
                if i > 400_000:
                    break

        unmapped = {
            pair for pair in seen if geo.coordinates(pair[0], pair[1]) is None
        }
        assert unmapped == set(), f"places with no coordinate: {sorted(unmapped)}"
