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
