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


class TestWalletRails:
    """Wallets carry no card number, so no card-linkage rule may read them.

    Alipay, Octopus, WeChat Pay and PayMe settle from a stored balance. The
    source has nothing to hash and writes `hashed_pan` blank, which is over
    half the real extract. Every rule in this file follows one card across
    merchants; a rail with no card cannot participate, and treating a shared
    blank as a shared identity is what produced 65,000 ring alerts.
    """

    def _wallet_events(self, rail: str, count: int):
        # The failure mode being guarded: every row carrying the *same*
        # placeholder, so the rules read them as one card.
        return [
            CardEvent("", f"U{i}", f"T{i}", NOON + timedelta(minutes=10 * i),
                      card_type=rail)
            for i in range(count)
        ]

    def _unrelated(self, count: int):
        return [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(count)
        ]

    def test_a_blank_card_key_never_links_merchants(self):
        assert not evaluate(
            RingInput(
                merchants=self._unrelated(6),
                card_events=self._wallet_events("OCTOPUS", 6),
            ),
            rules("card_swarm"),
        )

    def test_a_wallet_rail_is_excluded_even_when_it_carries_a_token(self):
        """Defence in depth: if the source ever starts emitting a per-wallet
        token, it is still not a card, and one Octopus account must not become
        a ring."""
        events = [
            CardEvent("wallet-token-1", f"U{i}", f"T{i}",
                      NOON + timedelta(minutes=10 * i), card_type="ALIPAY")
            for i in range(6)
        ]
        assert not evaluate(
            RingInput(merchants=self._unrelated(6), card_events=events),
            rules("card_swarm"),
        )

    def test_wallet_rails_are_matched_case_and_space_insensitively(self):
        events = [
            CardEvent("wallet-token-1", f"U{i}", f"T{i}",
                      NOON + timedelta(minutes=10 * i), card_type=" wechat ")
            for i in range(6)
        ]
        assert not evaluate(
            RingInput(merchants=self._unrelated(6), card_events=events),
            rules("card_swarm"),
        )

    def test_a_real_card_rail_still_fires(self):
        events = [
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(minutes=10 * i),
                      card_type="VISA")
            for i in range(6)
        ]
        assert evaluate(
            RingInput(merchants=self._unrelated(6), card_events=events),
            rules("card_swarm"),
        )


class TestOneAlertPerCard:
    """A card is one investigation, so it is one alert.

    Attributing a finding to every participating merchant multiplied one
    card's journey into an alert per merchant per leg. The queue counts
    investigations, and "this card was in four places" is one of them.
    """

    def test_a_swarm_produces_a_single_finding(self):
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(minutes=10 * i),
                      card_type="VISA")
            for i in range(5)
        ]
        hits = evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )
        assert len(hits) == 1

    def test_two_cards_are_two_findings(self):
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent(pan, f"U{i}", f"{pan}-T{i}",
                      NOON + timedelta(minutes=10 * i), card_type="VISA")
            for pan in ("pan1", "pan2")
            for i in range(5)
        ]
        hits = evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )
        assert len(hits) == 2

    def test_the_finding_names_every_merchant_the_card_touched(self):
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(minutes=10 * i),
                      card_type="VISA")
            for i in range(5)
        ]
        hit = evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )[0]
        assert set(hit.hit.linkage["merchants"]) == {f"U{i}" for i in range(5)}

    def test_the_finding_carries_every_linked_transaction(self):
        """The analyst's question is "show me this card's transactions across
        all of them", so the evidence spans merchants rather than stopping at
        the one carrying the alert."""
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(minutes=10 * i),
                      card_type="VISA")
            for i in range(5)
        ]
        hit = evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )[0]
        assert {c.source_txn_id for c in hit.hit.contributions} == {
            f"T{i}" for i in range(5)
        }

    def test_the_card_reference_is_not_the_card_identifier(self):
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent("panhash-secret", f"U{i}", f"T{i}",
                      NOON + timedelta(minutes=10 * i), card_type="VISA")
            for i in range(5)
        ]
        hit = evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )[0]
        blob = repr(hit.hit)
        assert "panhash-secret" not in blob
        assert "secret" not in hit.hit.linkage["card_ref"]


class TestBranchStructuring:
    def _events(self, count: int):
        return [
            CardEvent("pan1", f"S{i}", f"T{i}", NOON + timedelta(minutes=20 * i),
                      card_type="VISA")
            for i in range(count)
        ]

    def test_four_branches_in_a_day_exceeds_the_limit(self):
        hits = evaluate(
            RingInput(merchants=chain("S", 4, br="same"), card_events=self._events(4)),
            rules("card_across_related_merchants"),
        )
        assert len(hits) == 1
        assert set(hits[0].hit.linkage["merchants"]) == {"S0", "S1", "S2", "S3"}

    def test_the_alert_is_attributed_to_a_merchant_in_the_group(self):
        """It still has to join a merchant's queue — an alert floating free of
        the portfolio is one nobody is accountable for."""
        hits = evaluate(
            RingInput(merchants=chain("S", 4, br="same"), card_events=self._events(4)),
            rules("card_across_related_merchants"),
        )
        assert hits[0].merchant_id in {"S0", "S1", "S2", "S3"}

    def test_common_ownership_is_stated_in_the_finding(self):
        """Feedback: when the merchants are branches of one chain, say so —
        that is the difference between this rule and card swarming."""
        hits = evaluate(
            RingInput(merchants=chain("S", 4, br="same"), card_events=self._events(4)),
            rules("card_across_related_merchants"),
        )
        assert hits[0].hit.linkage["related"] is True

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
            CardEvent("pan1", f"S{i}", f"T{i}", NOON + timedelta(days=i),
                      card_type="VISA")
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
            CardEvent("pan1", "A", "T1", NOON, card_type="VISA"),
            CardEvent("pan1", "B", "T2", NOON + timedelta(minutes=minutes),
                      card_type="MASTER"),
        ]
        return RingInput(merchants=merchants, card_events=events)

    def test_forty_kilometres_in_twenty_minutes_is_impossible(self):
        hits = evaluate(self._pair(20), rules("impossible_geo_velocity"))
        assert len(hits) == 1
        assert set(hits[0].hit.linkage["merchants"]) == {"A", "B"}

    def test_the_journey_is_shown_leg_by_leg(self):
        """Feedback: the analyst wants to see the distance, the time delta, the
        implied speed, and by how much it exceeds the limit — not a verdict."""
        hit = evaluate(self._pair(20), rules("impossible_geo_velocity"))[0]
        leg = hit.hit.linkage["legs"][0]
        assert leg["from_merchant"] == "A"
        assert leg["to_merchant"] == "B"
        assert leg["from_place"] == "Tung chung"
        assert leg["to_place"] == "Sai kung"
        assert leg["minutes"] == 20
        assert leg["distance_km"] > 3
        assert leg["kmh"] > 60
        assert leg["limit_kmh"] == 60
        # How far over the line, which is the number that ranks the alert.
        assert leg["over_limit_multiple"] == round(leg["kmh"] / 60, 2)

    def test_both_ends_of_the_journey_are_in_the_evidence(self):
        hit = evaluate(self._pair(20), rules("impossible_geo_velocity"))[0]
        assert {c.source_txn_id for c in hit.hit.contributions} == {"T1", "T2"}

    def test_a_faster_journey_scores_higher(self):
        # Both breach the 60 km/h limit over the same ~40 km; only the time
        # differs, so the ranking must follow the implied speed.
        slow = evaluate(self._pair(30), rules("impossible_geo_velocity"))
        fast = evaluate(self._pair(10), rules("impossible_geo_velocity"))
        assert fast[0].hit.sub_score > slow[0].hit.sub_score

    def test_one_card_hopping_repeatedly_is_still_one_finding(self):
        merchants = [
            MerchantNode(merchant_id="A", subdistrict="Tung chung",
                         hashed_br_number="br1"),
            MerchantNode(merchant_id="B", subdistrict="Sai kung",
                         hashed_br_number="br2"),
        ]
        events = [
            CardEvent("pan1", "A" if i % 2 == 0 else "B", f"T{i}",
                      NOON + timedelta(minutes=20 * i), card_type="VISA")
            for i in range(6)
        ]
        hits = evaluate(
            RingInput(merchants=merchants, card_events=events),
            rules("impossible_geo_velocity"),
        )
        assert len(hits) == 1
        assert len(hits[0].hit.linkage["legs"]) == 5

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
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(minutes=10 * i),
                      card_type="VISA")
            for i in range(5)
        ]
        hits = evaluate(
            RingInput(merchants=merchants, card_events=events), rules("card_swarm")
        )
        assert len(hits) == 1
        assert set(hits[0].hit.linkage["merchants"]) == {f"U{i}" for i in range(5)}

    def test_the_same_visits_spread_over_a_week_are_just_shopping(self):
        merchants = [
            MerchantNode(merchant_id=f"U{i}", hashed_br_number=f"br{i}")
            for i in range(5)
        ]
        events = [
            CardEvent("pan1", f"U{i}", f"T{i}", NOON + timedelta(days=i),
                      card_type="VISA")
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
