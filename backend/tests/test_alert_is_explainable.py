"""Every alert in the queue must survive being opened.

The pipeline decides whether to raise an alert; the case page re-runs the same
detectors to explain it. When those two disagree, the analyst gets an alert
headed "Merchant level vs MCC baseline" whose own detail panel reports
0 fired, 2 passed, 10 skipped, and the reason given is "Skipped (MCC peer
merchant baseline not usable)" — the case page saying the check could not run
at all, underneath a queue entry claiming it did.

That happened because the two sides carried separate copies of the gate: the
pipeline fired on `peer_usable and baseline_center`, while the case page also
required `baseline_usable`. A merchant with two transactions in its whole
window has a `baseline_center` — the median of two numbers — and no baseline.

The tests here assert the invariant rather than either copy of the condition:
an alert that fires must have something to show, and the two sides must not be
able to drift apart again.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from compliance import diagnostics as diag
from compliance.models import Alert, Base, Merchant, MerchantProfile, Transaction
from compliance.pipeline import stages
from compliance.pipeline.merchant_study import merchant_level_is_comparable
from compliance.synthetic import generate_history

HKT = timezone(timedelta(hours=8))
AS_OF = datetime(2026, 5, 1, tzinfo=HKT)
SCORED_DAY = datetime(2026, 4, 30, tzinfo=HKT)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestTheGateIsShared:
    """One predicate, consulted by both sides. A second copy is the bug."""

    def test_a_merchant_with_no_usable_baseline_is_not_comparable(self):
        metrics = {
            "peer_usable": True,
            # The median of two transactions. A number, not a level.
            "baseline_center": 16_280.0,
            "baseline_usable": False,
        }
        assert merchant_level_is_comparable(metrics, day_count=3) is not None

    def test_the_reason_is_returned_not_just_a_boolean(self):
        """The case page has to print why the check did not run, and it must
        be the same sentence the pipeline acted on."""
        metrics = {"peer_usable": False, "baseline_center": 1.0,
                   "baseline_usable": True}
        reason = merchant_level_is_comparable(metrics, day_count=3)
        assert isinstance(reason, str) and reason

    def test_a_silent_day_is_not_comparable(self):
        """A merchant that did not trade has nothing new to judge. The
        baseline window is lagged and unchanged from yesterday, so re-raising
        this every night until somebody dispositions it adds no information —
        it just refills the queue."""
        metrics = {"peer_usable": True, "baseline_center": 500.0,
                   "baseline_usable": True}
        assert merchant_level_is_comparable(metrics, day_count=0) is not None

    def test_a_mature_merchant_that_traded_is_comparable(self):
        metrics = {"peer_usable": True, "baseline_center": 500.0,
                   "baseline_usable": True}
        assert merchant_level_is_comparable(metrics, day_count=3) is None


class TestLimitedHistoryRaisesNothing:
    def _thin_merchant(self, session):
        """Exactly the shape from the report: two large transactions in the
        whole window, no trade on the scored day, and a cohort whose median is
        far below. The old gate fired; the case page then said it had skipped
        the check."""
        session.add(Merchant(merchant_id="THIN", mcc="5719", lane="B"))
        for i in range(30):
            session.add(Merchant(merchant_id=f"PEER{i}", mcc="5719", lane="A"))
        session.flush()

        # Peers trade normally, so the cohort baseline is real.
        for i in range(30):
            for d in range(20):
                session.add(
                    Transaction(
                        source_txn_id=f"P{i}-{d}",
                        merchant_id=f"PEER{i}",
                        total_amount=324.0 + i,
                        occurred_at=SCORED_DAY - timedelta(days=d + 8, hours=-12),
                        is_refund=False,
                    )
                )
        # THIN has two transactions, both long before the scored day.
        for i in range(2):
            session.add(
                Transaction(
                    source_txn_id=f"THIN{i}",
                    merchant_id="THIN",
                    total_amount=16_280.0,
                    occurred_at=SCORED_DAY - timedelta(days=15 + i, hours=-12),
                    is_refund=False,
                )
            )
        session.flush()

    def test_no_merchant_level_alert_is_raised(self, session):
        self._thin_merchant(session)
        stages.profile(session, as_of=AS_OF)
        lanes = stages.route(session)
        hits = stages.detect(session, lanes, as_of=AS_OF)

        offenders = [
            h for h in hits
            if h["merchant_id"] == "THIN"
            and h["detector"] == "merchant_level_vs_mcc_peers"
        ]
        assert offenders == []


class TestEveryAlertExplainsItself:
    def test_no_alert_opens_to_zero_fired(self, session):
        """The invariant the report caught. An alert whose case page shows
        nothing fired is an alert nobody can act on, whatever raised it."""
        generate_history(session, as_of=AS_OF)
        session.flush()
        stages.profile(session, as_of=AS_OF)
        lanes = stages.route(session)
        hits = stages.detect(session, lanes, as_of=AS_OF)
        hits += stages.typologies(session, as_of=AS_OF)
        stages.score_and_rank(session, hits, as_of=AS_OF)
        session.flush()

        empty = []
        for alert in session.scalars(select(Alert)):
            payload = diag.diagnostics(session, alert)
            if not any(d["status"] == "FAIL" for d in payload["detectors"]):
                empty.append(
                    (alert.merchant_id, alert.triggering_detectors[0]["detector"])
                )
        assert empty == []
