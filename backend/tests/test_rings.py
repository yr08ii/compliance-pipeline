"""Family C ring rules, at their boundaries."""

from datetime import datetime, timedelta, timezone

from compliance.detection.rings import (
    CardEvent,
    MerchantNode,
    RingInput,
    evaluate,
    related_merchants,
)
from compliance.detection.ruleset import Family, RuleInstance, default_instances

HKT = timezone(timedelta(hours=8))
NOON = datetime(2026, 4, 30, 12, 0, tzinfo=HKT)


def rules(template: str):
    return [i for i in default_instances(Family.C) if i.template == template]


def chain(prefix: str, count: int, *, br: str, place=("Mong kok", "Yau tsim mong")):
    return [
        MerchantNode(
            merchant_id=f"{prefix}{i}",
            hashed_br_number=br,
            subdistrict=place[0],
            district=place[1],
            agent_id="AGT1",
        )
        for i in range(count)
    ]


def fired(hits, merchant_id: str) -> bool:
    return any(h.merchant_id == merchant_id for h in hits)


class TestIdentityRings:
    def test_shared_registration_groups_the_members(self):
        merchants = chain("S", 3, br="same")
        hits = evaluate(
            RingInput(merchants=merchants, flagged=frozenset({"S0"})),
            rules("shared_identity_ring"),
        )
        assert {h.merchant_id for h in hits} == {"S0", "S1", "S2"}

    def test_distinct_registrations_are_not_a_ring(self):
        merchants = [
            MerchantNode(merchant_id=f"D{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        assert not evaluate(
            RingInput(merchants=merchants, flagged=frozenset({"D0"})),
            rules("shared_identity_ring"),
        )

    def test_a_null_hash_never_links_merchants(self):
        """Merchants with no registration on file would otherwise all group
        together under the shared value None — the largest false ring
        possible."""
        merchants = [MerchantNode(merchant_id=f"N{i}") for i in range(6)]
        assert not evaluate(
            RingInput(merchants=merchants, flagged=frozenset({"N0"})),
            rules("shared_identity_ring"),
        )

    def test_group_below_the_minimum_size_is_a_landlord_not_a_structure(self):
        merchants = chain("P", 2, br="same")
        assert not evaluate(
            RingInput(merchants=merchants, flagged=frozenset({"P0"})),
            rules("shared_identity_ring"),
        )

    def test_related_merchants_is_symmetric(self):
        linked = related_merchants(chain("S", 3, br="same"))
        assert linked["S0"] == {"S1", "S2"}
        assert linked["S2"] == {"S0", "S1"}


class TestBranchStructuring:
    def _events(self, count: int):
        return [
            CardEvent("pan1", f"S{i}", f"T{i}", NOON + timedelta(minutes=20 * i))
            for i in range(count)
        ]

    def test_four_branches_in_a_day_exceeds_the_limit(self):
        hits = evaluate(
            RingInput(merchants=chain("S", 4, br="same"), card_events=self._events(4)),
            rules("card_across_related_merchants"),
        )
        assert {h.merchant_id for h in hits} == {"S0", "S1", "S2", "S3"}

    def test_three_branches_is_allowed(self):
        """The stated rule: up to three branches of one chain in a day is
        plausible; a fourth owes an explanation."""
        hits = evaluate(
            RingInput(merchants=chain("S", 4, br="same"), card_events=self._events(3)),
            rules("card_across_related_merchants"),
        )
        assert hits == []

    def test_unrelated_merchants_are_not_branches(self):
        merchants = [
            MerchantNode(merchant_id=f"S{i}", hashed_br_number=f"br{i}")
            for i in range(4)
        ]
        assert not evaluate(
            RingInput(merchants=merchants, card_events=self._events(4)),
            rules("card_across_related_merchants"),
        )

    def test_visits_on_different_days_do_not_add_up(self):
        events = [
            CardEvent("pan1", f"S{i}", f"T{i}", NOON + timedelta(days=i))
            for i in range(4)
        ]
        assert not evaluate(
            RingInput(merchants=chain("S", 4, br="same"), card_events=events),
            rules("card_across_related_merchants"),
        )

    def test_evidence_never_carries_the_card_identifier(self):
        hits = evaluate(
            RingInput(merchants=chain("S", 4, br="same"), card_events=self._events(4)),
            rules("card_across_related_merchants"),
        )
        for hit in hits:
            for contribution in hit.hit.contributions:
                assert "pan" not in contribution.value.lower()
                assert contribution.field == "merchant_id"


class TestGeoVelocity:
    def _pair(self, minutes: float, a="Tung chung", b="Sai kung"):
        merchants = [
            MerchantNode(merchant_id="A", subdistrict=a, hashed_br_number="br1"),
            MerchantNode(merchant_id="B", subdistrict=b, hashed_br_number="br2"),
        ]
        events = [
            CardEvent("pan1", "A", "T1", NOON),
            CardEvent("pan1", "B", "T2", NOON + timedelta(minutes=minutes)),
        ]
        return RingInput(merchants=merchants, card_events=events)

    def test_forty_kilometres_in_twenty_minutes_is_impossible(self):
        hits = evaluate(self._pair(20), rules("impossible_geo_velocity"))
        assert {h.merchant_id for h in hits} == {"A", "B"}

    def test_the_same_journey_over_two_hours_is_ordinary(self):
        assert not evaluate(self._pair(120), rules("impossible_geo_velocity"))

    def test_neighbouring_subdistricts_never_fire(self):
        """Centroid distance is meaningless at short range, so the rule holds
        a minimum distance rather than trusting a sub-kilometre reading."""
        assert not evaluate(
            self._pair(1, a="Mong kok", b="Yau ma tei"),
            rules("impossible_geo_velocity"),
        )

    def test_simultaneous_transactions_do_not_fire(self):
        """A zero-second gap is clock resolution or a batch import, not
        supersonic travel."""
        assert not evaluate(self._pair(0), rules("impossible_geo_velocity"))

    def test_unmapped_place_is_skipped_not_guessed(self):
        assert not evaluate(
            self._pair(20, b="Atlantis"), rules("impossible_geo_velocity")
        )

    def test_the_limit_is_tunable(self):
        """A walking-pace limit would flag every card used in two districts,
        which is exactly why the shipped default is 60 km/h."""
        strict = RuleInstance(
            instance_id="strict",
            template="impossible_geo_velocity",
            params={"max_kmh": 5.0},
        )
        assert evaluate(self._pair(120), [strict])


class TestCardSwarm:
    def test_one_card_across_five_unrelated_merchants_in_the_window(self):
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(minutes=10 * i))
            for i in range(5)
        ]
        hits = evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )
        assert {h.merchant_id for h in hits} == {f"U{i}" for i in range(5)}

    def test_the_same_visits_spread_over_a_week_are_just_shopping(self):
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(days=i))
            for i in range(5)
        ]
        assert not evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )


class TestAgentConcentration:
    def test_an_agent_whose_book_runs_hot_is_surfaced(self):
        hot = [
            MerchantNode(merchant_id=f"H{i}", agent_id="BAD") for i in range(6)
        ]
        cold = [
            MerchantNode(merchant_id=f"C{i}", agent_id="OK") for i in range(60)
        ]
        # Every one of the hot agent's merchants is flagged; two of sixty
        # elsewhere.
        flagged = frozenset({f"H{i}" for i in range(6)} | {"C0", "C1"})

        hits = evaluate(
            RingInput(merchants=hot + cold, flagged=flagged),
            rules("agent_alert_concentration"),
        )
        assert {h.merchant_id for h in hits} == {f"H{i}" for i in range(6)}

    def test_a_small_book_is_not_a_rate(self):
        """One bad merchant out of two is 50% and means nothing."""
        small = [MerchantNode(merchant_id=f"S{i}", agent_id="TINY") for i in range(2)]
        rest = [MerchantNode(merchant_id=f"R{i}", agent_id="OK") for i in range(60)]

        assert not evaluate(
            RingInput(merchants=small + rest, flagged=frozenset({"S0", "R0"})),
            rules("agent_alert_concentration"),
        )
