"""Detection thresholds the compliance lead can tune, without a deploy.

Two distinct questions decide whether an alert is worth raising:

* **Is it unusual?** — the modified z-score against the merchant's baseline.
* **Is it worth acting on?** — the amount, against an absolute floor.

Only the first was asked. A convenience store's HKD 150 transaction can be a
genuine four-sigma outlier and still be useless to a launderer, so thousands of
those crowded out the alerts that mattered. Statistical significance is not
practical significance, and an AML queue needs both.

Thresholds live in the database rather than in code because the people who
should own them — the compliance lead calibrating against real dispositions —
should not need an engineer to change a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from compliance.models import DetectionSetting

SETTINGS_KEY = "detection"


@dataclass(frozen=True)
class DetectionSettings:
    """One set of detection thresholds.

    Defaults match what the code shipped with, so adopting this store changes
    no behaviour until somebody deliberately tunes something.
    """

    # Modified z above which a value is an outlier.
    outlier_z: float = 3.5
    # Modified z above which a value is notable but not alone worth an alert.
    moderate_z: float = 2.5
    # Amounts below this never raise an amount alert, however unusual. Zero
    # disables the floor entirely.
    materiality_floor: float = 1000.0
    # Maturity: a merchant needs both enough transactions and enough elapsed
    # days before it can be judged against its own past.
    min_observations: int = 12
    min_span_days: int = 14
    # Days the baseline window is held back, giving analysts time to rule on
    # activity before it becomes part of "normal".
    lag_days: int = 7
    window_days: int = 30
    # Per-MCC overrides: {"5944": {"outlier_z": 6.0}}. Partial — unspecified
    # fields inherit the global value.
    mcc_overrides: dict[str, dict] = field(default_factory=dict)

    def is_material(self, amount: float | None) -> bool:
        """Whether an amount is large enough to be worth an analyst's time."""
        if amount is None:
            return True
        return amount >= self.materiality_floor

    def fires(self, *, deviation: float, amount: float | None = None) -> bool:
        """Whether a signal should raise an alert.

        `amount=None` means the signal is not an amount comparison — a burst of
        small transactions or trading at 3am. Those are not made harmless by
        being small; small and frequent is the shape of structuring. The floor
        applies to amount comparisons only.
        """
        return deviation > self.outlier_z and self.is_material(amount)

    def for_mcc(self, mcc: str | None) -> "DetectionSettings":
        """This merchant category's effective thresholds.

        Some trades are legitimately volatile — a jeweller's tickets are lumpy
        where a grocer's are not. One global threshold either floods on the
        volatile trades or goes blind on the steady ones.
        """
        override = self.mcc_overrides.get(mcc or "")
        if not override:
            return self
        allowed = {
            k: v
            for k, v in override.items()
            if k in {"outlier_z", "moderate_z", "materiality_floor"}
        }
        return replace(self, **allowed)

    def as_dict(self) -> dict:
        return {
            "outlier_z": self.outlier_z,
            "moderate_z": self.moderate_z,
            "materiality_floor": self.materiality_floor,
            "min_observations": self.min_observations,
            "min_span_days": self.min_span_days,
            "lag_days": self.lag_days,
            "window_days": self.window_days,
            "mcc_overrides": self.mcc_overrides,
        }


DEFAULTS = DetectionSettings()


def load_settings(session: Session) -> DetectionSettings:
    """Current thresholds, or the shipped defaults if none were ever saved."""
    row = session.scalars(
        select(DetectionSetting).where(DetectionSetting.key == SETTINGS_KEY)
    ).first()
    if row is None:
        return DEFAULTS
    stored = dict(row.value or {})
    known = {f for f in DEFAULTS.as_dict()}
    return DetectionSettings(**{k: v for k, v in stored.items() if k in known})


def save_settings(session: Session, settings: DetectionSettings) -> None:
    """Write thresholds, replacing any previous set."""
    row = session.scalars(
        select(DetectionSetting).where(DetectionSetting.key == SETTINGS_KEY)
    ).first()
    if row is None:
        session.add(DetectionSetting(key=SETTINGS_KEY, value=settings.as_dict()))
    else:
        row.value = settings.as_dict()


def effective_settings(session: Session, mcc: str | None = None) -> DetectionSettings:
    """Thresholds in force for one merchant category."""
    return load_settings(session).for_mcc(mcc)
