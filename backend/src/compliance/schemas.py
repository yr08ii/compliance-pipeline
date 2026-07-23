from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DetectorHit(BaseModel):
    detector: str
    sub_score: float


class FeatureDivergence(BaseModel):
    feature_name: str
    merchant_value: float
    baseline_value: float
    deviation: float


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    merchant_id: str
    lane: str
    blended_score: float
    rank: int
    created_at: datetime
    triggering_detectors: list[DetectorHit]
    feature_snapshot: list[FeatureDivergence]


class BaselineRow(BaseModel):
    """One merchant's baseline provenance."""

    merchant_id: str
    mcc: str | None
    lane: str
    method: str
    usable: bool
    center: float | None
    observations: int
    quarantined_days: int
    peer_merchants: int
    peer_usable: bool
    volume_usable: bool
    velocity_usable: bool
    is_ramp: bool


class BaselineOverview(BaseModel):
    """What the baselines are built from, and what changes next run."""

    window_start: str | None
    window_end: str | None
    window_days: int
    lag_days: int
    next_inclusion_date: str | None
    total_count: int
    usable_count: int
    quarantined_total: int
    merchants: list[BaselineRow]


class GlossaryTerm(BaseModel):
    """One internal identifier and its plain-English rendering."""

    key: str
    label: str
    meaning: str
    compared_against: str


class Glossary(BaseModel):
    """Every identifier that can reach a person, translated."""

    detectors: list[GlossaryTerm]
    features: list[GlossaryTerm]
    lanes: list[GlossaryTerm]
    baseline_methods: list[GlossaryTerm]
