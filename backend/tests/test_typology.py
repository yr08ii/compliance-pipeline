"""Family B rules, at their boundaries.

Each rule is a pure function of pre-fetched data, so these build the exact
shape and assert the rule fires — and, just as importantly, that the
neighbouring shape does not.
"""

from datetime import date, datetime, timedelta, timezone

from compliance.detection.ruleset import Family, default_instances
from compliance.detection.typology import Txn, TypologyInput, evaluate

HKT = timezone(timedelta(hours=8))
DAY = date(2026, 4, 30)


def rules(template: str):
    return [i for i in default_instances(Family.B) if i.template == template]


def txn(n: int, amount: float, *, hour: int = 12, refund: bool = False,
        status: str = "SUCCESS") -> Txn:
    when = datetime(2026, 4, 30, hour, n % 60, tzinfo=HKT)
    return Txn(
        source_txn_id=f"T{n}",
        amount=amount,
        occurred_at=when,
        hour=hour + (n % 60) / 60,
        is_refund=refund,
        status=status,
        card_type="VISA",
    )


def make(day, **kw) -> TypologyInput:
    return TypologyInput(
        merchant_id="M1",
        mcc="5732",
        day=day,
        daily_value=kw.pop("daily_value", []),
        scored_day=DAY,
        **kw,
    )


class TestStructuring:
    def test_fires_on_a_cluster_just_under_the_threshold(self):
        day = [txn(i, 7_800.0) for i in range(3)]
        hits = evaluate(make(day, baseline_center=600.0),
                        rules("structuring_below_threshold"))

        assert len(hits) == 1
        assert len(hits[0].contributions) == 3
        assert {c["field"] if isinstance(c, dict) else c.field
                for c in hits[0].contributions} == {"total_amount"}

    def test_silent_below_the_minimum_count(self):
        day = [txn(i, 7_800.0) for i in range(2)]
        assert not evaluate(make(day, baseline_center=600.0),
                            rules("structuring_below_threshold"))

    def test_silent_when_the_merchant_normally_trades_near_the_line(self):
        """A jeweller whose every sale is near the threshold would otherwise
        fire every single day."""
        day = [txn(i, 7_800.0) for i in range(5)]
        assert not evaluate(make(day, baseline_center=7_000.0),
                            rules("structuring_below_threshold"))

    def test_silent_when_amounts_are_over_the_threshold(self):
        """Above the line is not structuring — it is a reportable transaction
        that the regime handles on its own."""
        day = [txn(i, 8_400.0) for i in range(5)]
        assert not evaluate(make(day, baseline_center=600.0),
                            rules("structuring_below_threshold"))

    def test_silent_when_amounts_are_far_below_the_band(self):
        day = [txn(i, 400.0) for i in range(9)]
        assert not evaluate(make(day, baseline_center=600.0),
                            rules("structuring_below_threshold"))


class TestRefundAbuse:
    def test_fires_when_most_of_the_day_is_returned(self):
        day = [txn(0, 1_000.0), txn(1, 1_000.0)] + [
            txn(i, 600.0, refund=True) for i in range(2, 5)
        ]
        hits = evaluate(make(day), rules("refund_ratio_spike"))

        assert len(hits) == 1
        assert hits[0].feature["merchant_value"] > 0.3

    def test_a_single_refund_is_ordinary_retail(self):
        day = [txn(0, 1_000.0), txn(1, 900.0, refund=True)]
        assert not evaluate(make(day), rules("refund_ratio_spike"))


class TestDeclineSpike:
    def test_fires_on_a_run_of_refusals(self):
        day = [txn(i, 100.0, status="DECLINED") for i in range(10)] + [
            txn(i, 100.0) for i in range(10, 30)
        ]
        hits = evaluate(make(day), rules("decline_ratio_spike"))

        assert len(hits) == 1
        assert all(c.field == "transaction_status" for c in hits[0].contributions)

    def test_silent_below_the_minimum_attempts(self):
        """Three declines out of four is a bad afternoon, not a pattern."""
        day = [txn(i, 100.0, status="DECLINED") for i in range(3)] + [txn(9, 100.0)]
        assert not evaluate(make(day), rules("decline_ratio_spike"))

    def test_refunds_are_not_authorisation_attempts(self):
        """Counting them would let a heavy refund day dilute the ratio and
        hide a card-testing run."""
        day = (
            [txn(i, 100.0, status="DECLINED") for i in range(8)]
            + [txn(i, 100.0) for i in range(8, 20)]
            + [txn(i, 100.0, refund=True, status="REFUNDED") for i in range(20, 60)]
        )
        hits = evaluate(make(day), rules("decline_ratio_spike"))

        assert hits, "refunds diluted the decline ratio"
        assert hits[0].feature["merchant_value"] == 8 / 20


class TestDormantReactivation:
    def test_fires_after_a_long_silence(self):
        day = [txn(i, 500.0) for i in range(12)]
        hits = evaluate(
            make(day, last_active=DAY - timedelta(days=90)),
            rules("dormant_reactivation"),
        )
        assert len(hits) == 1

    def test_silent_when_the_merchant_eases_back_in(self):
        """Genuine reactivation ramps; a takeover starts at full speed."""
        day = [txn(0, 500.0), txn(1, 400.0)]
        assert not evaluate(
            make(day, last_active=DAY - timedelta(days=90)),
            rules("dormant_reactivation"),
        )

    def test_silent_for_a_short_break(self):
        day = [txn(i, 500.0) for i in range(12)]
        assert not evaluate(
            make(day, last_active=DAY - timedelta(days=3)),
            rules("dormant_reactivation"),
        )


class TestRapidMovement:
    def test_fires_when_value_in_matches_value_out(self):
        day = [txn(0, 30_000.0), txn(1, 29_000.0, refund=True)]
        assert evaluate(make(day), rules("rapid_movement"))

    def test_silent_when_a_balance_is_left_behind(self):
        day = [txn(0, 30_000.0), txn(1, 5_000.0, refund=True)]
        assert not evaluate(make(day), rules("rapid_movement"))

    def test_silent_on_a_small_day(self):
        day = [txn(0, 500.0), txn(1, 490.0, refund=True)]
        assert not evaluate(make(day), rules("rapid_movement"))


class TestBustOut:
    def _history(self, early: float, late: float, days: int = 30):
        return [
            (DAY - timedelta(days=days - i), early if i < days - 7 else late)
            for i in range(days)
        ]

    def test_fires_on_a_climb_followed_by_refunds(self):
        day = [txn(0, 10_000.0), txn(1, 2_000.0, refund=True)]
        hits = evaluate(
            make(day, daily_value=self._history(1_000.0, 9_000.0)),
            rules("bust_out"),
        )
        assert len(hits) == 1

    def test_silent_on_steady_growth_without_refunds(self):
        day = [txn(0, 10_000.0)]
        assert not evaluate(
            make(day, daily_value=self._history(1_000.0, 9_000.0)),
            rules("bust_out"),
        )

    def test_silent_without_enough_history_to_have_an_earlier_level(self):
        day = [txn(0, 10_000.0), txn(1, 2_000.0, refund=True)]
        assert not evaluate(
            make(day, daily_value=self._history(1_000.0, 9_000.0, days=8)),
            rules("bust_out"),
        )


class TestScoping:
    def test_an_instance_scoped_to_another_mcc_does_not_run(self):
        from compliance.detection.ruleset import RuleInstance

        scoped = RuleInstance(
            instance_id="jewellers_only",
            template="structuring_below_threshold",
            mcc_scope=("5944",),
        )
        day = [txn(i, 7_800.0) for i in range(5)]
        assert not evaluate(make(day, baseline_center=600.0), [scoped])

    def test_reason_code_carries_the_parameters_in_force(self):
        """A bare rule name stops meaning anything once the rule is retuned."""
        day = [txn(i, 7_800.0) for i in range(3)]
        hit = evaluate(make(day, baseline_center=600.0),
                       rules("structuring_below_threshold"))[0]

        assert "threshold=8000" in hit.reason_code
        assert "min_count=3" in hit.reason_code
