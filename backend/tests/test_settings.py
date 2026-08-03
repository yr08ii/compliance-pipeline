"""Thresholds an analyst can tune, and the materiality floor.

Two separate ideas, easily confused:

* **Statistical significance** — is this unusual for this merchant? The
  modified z-score answers that.
* **Practical significance** — is it large enough to matter for AML? A HKD 150
  transaction at a convenience store can be a genuine 4-sigma outlier and still
  be worthless to a launderer.

Firing on the first alone is what produced thousands of unactionable alerts.
Both must hold.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from compliance.models import Base
from compliance.settings_store import (
    DEFAULTS,
    DetectionSettings,
    effective_settings,
    load_settings,
    save_settings,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestDefaults:
    def test_an_empty_store_returns_the_shipped_defaults(self, session):
        s = load_settings(session)

        assert s.outlier_z == DEFAULTS.outlier_z
        assert s.materiality_floor > 0

    def test_defaults_are_sane(self):
        assert 2.0 <= DEFAULTS.outlier_z <= 6.0
        assert DEFAULTS.min_observations >= 1
        assert DEFAULTS.min_span_days >= 1
        assert DEFAULTS.lag_days >= 1


class TestPersistence:
    def test_saved_settings_come_back(self, session):
        save_settings(session, DetectionSettings(outlier_z=4.5, materiality_floor=2000.0))
        session.flush()

        s = load_settings(session)

        assert s.outlier_z == 4.5
        assert s.materiality_floor == 2000.0

    def test_saving_twice_updates_rather_than_accumulates(self, session):
        save_settings(session, DetectionSettings(outlier_z=4.0))
        session.flush()
        save_settings(session, DetectionSettings(outlier_z=5.0))
        session.flush()

        assert load_settings(session).outlier_z == 5.0


class TestMccOverrides:
    def test_an_mcc_can_tolerate_more_volatility(self, session):
        """Jewellers have genuinely lumpy tickets; a grocer does not. One
        global threshold either floods on jewellers or goes blind on grocers."""
        save_settings(
            session,
            DetectionSettings(outlier_z=3.5, mcc_overrides={"5944": {"outlier_z": 6.0}}),
        )
        session.flush()

        assert effective_settings(session, mcc="5944").outlier_z == 6.0
        assert effective_settings(session, mcc="5411").outlier_z == 3.5

    def test_an_override_can_set_only_some_fields(self, session):
        """A partial override must inherit the rest, not reset it to defaults."""
        save_settings(
            session,
            DetectionSettings(
                outlier_z=3.5,
                materiality_floor=500.0,
                mcc_overrides={"5944": {"materiality_floor": 5000.0}},
            ),
        )
        session.flush()

        s = effective_settings(session, mcc="5944")

        assert s.materiality_floor == 5000.0
        assert s.outlier_z == 3.5, "unspecified fields must inherit the global value"

    def test_unknown_mcc_falls_back_to_global(self, session):
        save_settings(session, DetectionSettings(outlier_z=3.5))
        session.flush()

        assert effective_settings(session, mcc="9999").outlier_z == 3.5


class TestMateriality:
    def test_a_small_transaction_is_not_material_however_odd(self):
        """The convenience-store case: statistically a clear outlier, and
        still not worth an analyst's time."""
        s = DetectionSettings(outlier_z=3.5, materiality_floor=1000.0)

        assert s.is_material(150.0) is False
        assert s.fires(deviation=6.0, amount=150.0) is False

    def test_a_large_transaction_still_needs_to_be_unusual(self):
        """Materiality is a floor, not a trigger. A big transaction at a
        merchant that always makes big transactions is just business."""
        s = DetectionSettings(outlier_z=3.5, materiality_floor=1000.0)

        assert s.is_material(50_000.0) is True
        assert s.fires(deviation=1.2, amount=50_000.0) is False

    def test_both_conditions_together_fire(self):
        s = DetectionSettings(outlier_z=3.5, materiality_floor=1000.0)

        assert s.fires(deviation=6.0, amount=50_000.0) is True

    def test_the_floor_can_be_disabled(self, session):
        """Set to zero, behaviour is exactly as before — so the floor can be
        turned off without touching code if it proves wrong."""
        s = DetectionSettings(outlier_z=3.5, materiality_floor=0.0)

        assert s.fires(deviation=6.0, amount=1.0) is True

    def test_materiality_does_not_apply_to_non_amount_signals(self):
        """A burst of small transactions, or trading at 3am, is not made
        harmless by the amounts being small — that is the shape of
        structuring. The floor guards amount comparisons only."""
        s = DetectionSettings(outlier_z=3.5, materiality_floor=1000.0)

        assert s.fires(deviation=6.0, amount=None) is True
