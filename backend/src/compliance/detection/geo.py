"""Distance between Hong Kong places, for the geo-velocity rule.

Ships a static coordinate table (`data/hk_geo.json`) and computes great-circle
distance on demand. Two properties matter more than precision:

* **No network call.** A detector that asks a maps API for a distance is not
  reproducible — the same run on the same data can produce a different answer
  next year, which fails audit — and it cannot run air-gapped. Every number
  here is a pure function of the committed table.
* **Under-, never over-statement.** Centroid-to-centroid distance ignores
  terrain, harbour crossings and road routing, so it is a lower bound on the
  real journey. A lower bound on distance is a lower bound on the implied
  speed, which makes the rule under-flag rather than over-flag. That is the
  right direction for a rule that accuses a cardholder of being in two places
  at once.

Two places inside one subdistrict are 0 km apart here, so same-subdistrict
pairs can never trip the rule at all. That is intentional, not a gap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

EARTH_RADIUS_KM = 6371.0088

_DATA = Path(__file__).resolve().parent.parent / "data" / "hk_geo.json"


def _normalise(name: str | None) -> str:
    """Fold the spelling variants the source actually contains.

    The real extract carries `'Lamma island '` with a trailing space and both
    `'Kwai fong'` and `'Kuai fong'`. Matching on the raw string silently drops
    those rows out of every geographic check, which is worse than a wrong
    answer because nothing reports it.
    """
    if not name:
        return ""
    return " ".join(name.split()).strip().casefold()


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
    raw = json.loads(_DATA.read_text())
    subs = {
        _normalise(k): (float(v[0]), float(v[1]))
        for k, v in raw["subdistricts"].items()
    }
    districts = {
        _normalise(k): (float(v[0]), float(v[1]))
        for k, v in raw["districts"].items()
    }
    return subs, districts


def coordinates(
    subdistrict: str | None, district: str | None = None
) -> tuple[float, float] | None:
    """Centroid for a place, falling back subdistrict -> district -> unknown.

    Returns None rather than a default point when neither is known. A default
    would silently place every unmapped merchant at one spot, manufacturing
    impossible velocities between merchants whose only shared property is that
    we do not know where they are.
    """
    subs, districts = _tables()
    hit = subs.get(_normalise(subdistrict))
    if hit is not None:
        return hit
    return districts.get(_normalise(district))


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in kilometres."""
    lat1, lon1 = radians(a[0]), radians(a[1])
    lat2, lon2 = radians(b[0]), radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(h))


def distance_km(
    place_a: tuple[str | None, str | None], place_b: tuple[str | None, str | None]
) -> float | None:
    """Distance between two (subdistrict, district) pairs, or None if unmapped."""
    a = coordinates(*place_a)
    b = coordinates(*place_b)
    if a is None or b is None:
        return None
    return haversine_km(a, b)


@dataclass(frozen=True)
class Leg:
    """One card's hop between two merchants, with the speed it implies."""

    from_merchant: str
    to_merchant: str
    from_place: str
    to_place: str
    distance_km: float
    minutes: float
    kmh: float


def implied_speed(distance: float, minutes: float) -> float | None:
    """Average speed in km/h for a journey, or None if it is not measurable.

    Two transactions at the same recorded minute give no time to divide by. We
    return None rather than infinity: a zero-second gap at two different
    merchants is a clock-resolution artefact or a batch import, not evidence
    of supersonic travel, and treating it as an infinite speed makes the rule
    fire on the data's timestamp granularity instead of on behaviour.
    """
    if minutes <= 0:
        return None
    return distance / (minutes / 60.0)
