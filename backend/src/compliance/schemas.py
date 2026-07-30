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
    """An alert with enough merchant context to build the review header.

    The metadata is joined at read time rather than copied into the alert row:
    a merchant's MCC description or district can be corrected later, and the
    header should show what is true now. The `feature_snapshot` stays frozen,
    because that is what the detector actually judged."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    merchant_id: str
    lane: str
    blended_score: float
    rank: int
    created_at: datetime
    triggering_detectors: list[DetectorHit]
    feature_snapshot: list[FeatureDivergence]
    # Joined merchant metadata.
    mcc: str | None = None
    mcc_description: str | None = None
    merchant_district: str | None = None
    merchant_subdistrict: str | None = None
    business_nature: str | None = None
    merchant_status: str | None = None
    # The day evaluated — as_of minus one, not the row's write time.
    scored_date: str | None = None
    alert_type: str | None = None


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


class LedgerRow(BaseModel):
    """One transaction on the scored day. No PAN hash — that is cardholder
    data and must not cross the API boundary."""

    source_txn_id: str
    occurred_at: datetime
    total_amount: float
    net_amount: float | None
    is_refund: bool
    card_type: str | None
    card_issuing_country: str | None
    card_issuing_bank: str | None
    transaction_status: str | None
    currency: str | None
    # Whether this row breached the merchant's own amount baseline, so the
    # analyst can see which checkout drove the alert rather than scanning.
    is_outlier: bool


class Ledger(BaseModel):
    merchant_id: str
    date: str
    count: int
    total_amount: float
    outlier_count: int
    transactions: list[LedgerRow]


class DetectorVerdict(BaseModel):
    """One detector's result, whether it fired or not. Showing the passes
    matters as much as the failures: it tells the analyst what was ruled out."""

    rank: int
    detector: str
    label: str
    alert_type: str
    compared_against: str
    status: str
    merchant_value: float | str | None
    baseline_value: float | str | None
    deviation: float | None
    band: str | None
    message: str


class StatBlock(BaseModel):
    mean: float | None
    median: float | None
    mad: float | None
    n: int


class Statistics(BaseModel):
    merchant: StatBlock
    peer_mcc: StatBlock
    peer_subdistrict: StatBlock
    modified_z: float | None
    method: str


class HourDensity(BaseModel):
    """96 bins of 15 minutes, for the KDE plot."""

    merchant: list[float]
    cohort: list[float]
    threshold: float
    cohort_threshold: float
    scored_day_hours: list[float]


class PeerDistribution(BaseModel):
    """Where this merchant sits against its cohort, for the box plot."""

    merchant_value: float | None
    peer_median: float | None
    peer_dispersion: float | None
    peer_q1: float | None
    peer_q3: float | None
    peer_upper_fence: float | None
    n_merchants: int
    peer_values: list[float]


class Diagnostics(BaseModel):
    alert_id: int
    merchant_id: str
    scored_date: str
    root_cause: str | None
    detectors: list[DetectorVerdict]
    statistics: Statistics
    hour_density: HourDensity
    peer_distribution: PeerDistribution
    window: dict
