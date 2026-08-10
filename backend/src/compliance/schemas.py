from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class TxnContribution(BaseModel):
    """One transaction's part in one detector firing.

    `field` names the source column that carries the cause, so the ledger can
    highlight that cell rather than leaving the analyst to guess which
    property of a highlighted row was the problem."""

    source_txn_id: str
    field: str
    value: str
    reason: str


class DetectorHit(BaseModel):
    detector: str
    sub_score: float
    # Set for Family B and C, whose findings are rule matches. `reason_code`
    # carries the parameters in force when it fired, so it still explains
    # itself after the rule is retuned.
    rule_id: str | None = None
    reason_code: str | None = None
    message: str | None = None
    contributions: list[TxnContribution] = []
    # Family C only: which merchants one card connected, and the journey
    # between them. A ring finding is about several merchants at once, and
    # neither the feature row nor the contribution list can say that.
    linkage: dict | None = None


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
    alert_types: list[GlossaryTerm]


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


class GeoLeg(BaseModel):
    """One hop a card made, and the arithmetic that calls it impossible.

    Every term is shown rather than a verdict. An analyst asked to accept that
    a cardholder could not have made a journey is owed the distance, the
    elapsed time, the speed those imply, and the limit being compared against.
    """

    from_merchant: str
    from_place: str
    from_txn_id: str
    from_time: str
    to_merchant: str
    to_place: str
    to_txn_id: str
    to_time: str
    # Between subdistrict centroids, so a lower bound on the real journey —
    # which makes the implied speed a lower bound too.
    distance_km: float
    minutes: float
    kmh: float
    limit_kmh: float
    over_limit_multiple: float


class LinkedRow(BaseModel):
    """One transaction of a linked card, at whichever merchant took it.

    No PAN hash, here least of all: this view exists precisely because several
    merchants are involved, and the card that connects them is the one thing
    that must not travel with the evidence."""

    source_txn_id: str
    merchant_id: str
    occurred_at: datetime
    total_amount: float
    card_type: str | None
    mcc: str | None
    mcc_description: str | None
    merchant_district: str | None
    merchant_subdistrict: str | None
    # Whether this row is at the merchant whose queue the alert sits in.
    is_alert_merchant: bool
    # Set when this merchant shares a registration, address or trading name
    # with another in the list — branches of one chain rather than separate
    # shops, which is what the finding turns on.
    owner_group: str | None
    # Gap from the previous transaction of the same card. The column the
    # impossible-travel claim actually rests on.
    minutes_since_previous: float | None
    # Whether the rule fired on this transaction, as against it being the rest
    # of the card's day shown around it for context.
    is_focus: bool = False
    # Set where this transaction is the arrival end of an impossible leg, so
    # the speed sits on the row it condemns.
    arrived_at_kmh: float | None = None
    arrived_from_km: float | None = None


class TrailMerchant(BaseModel):
    """One merchant on a card's trail, summarised.

    The header of a card-linkage case is a list of merchants, not one
    merchant — so the merchants need enough with them to be read at a glance
    without opening each."""

    merchant_id: str
    mcc: str | None
    mcc_description: str | None
    district: str | None
    subdistrict: str | None
    owner_group: str | None
    is_alert_merchant: bool
    transactions: int
    total_amount: float
    first_seen: datetime
    last_seen: datetime


class CardLink(BaseModel):
    """One card-linkage finding, across every merchant it touched."""

    detector: str
    label: str
    # A per-run display label ("card 1"). Not derived from the card hash — a
    # position in a sort order carries no information about the card.
    card_ref: str
    related: bool
    merchants: list[str]
    legs: list[GeoLeg]
    transactions: list[LinkedRow]
    # The trail rolled up per merchant, for the case header. A card-linkage
    # case is about the card, so its header has to describe the card: where it
    # went, when, and for how much.
    trail: list[TrailMerchant] = []
    # Span and totals of the whole trail.
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    total_amount: float = 0.0
    rails: list[str] = []


class LinkedTransactions(BaseModel):
    alert_id: int
    merchant_id: str
    links: list[CardLink]


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
    # "A" robust baselines, "B" typology rules, "C" ring detection. The panel
    # groups by this because the three call for different follow-up.
    family: str = "A"
    contributions: list[TxnContribution] = []


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


class BaselineWindow(BaseModel):
    """Which data formed the baseline this alert was judged against."""

    window_start: str | None
    window_end: str | None
    window_days: int | None
    lag_days: int | None
    quarantined_days: int | None


class Diagnostics(BaseModel):
    alert_id: int
    merchant_id: str
    scored_date: str
    root_cause: str | None
    detectors: list[DetectorVerdict]
    statistics: Statistics
    hour_density: HourDensity
    peer_distribution: PeerDistribution
    window: BaselineWindow


class DispositionIn(BaseModel):
    """An analyst's decision on an alert.

    `reason_code` is required, not optional: a verdict without one teaches the
    model nothing and leaves an auditor with an unexplained decision.
    """

    verdict: str = Field(pattern="^(TRUE_POSITIVE|FALSE_POSITIVE|INCONCLUSIVE)$")
    reason_code: str = Field(min_length=2)
    risk_axis: str = Field(pattern="^(REGULATORY|COMMERCIAL|BOTH)$")
    action_taken: str = "NONE"
    analyst_id: str = Field(min_length=1)
    notes: str | None = None


class DispositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    alert_id: int
    verdict: str
    reason_code: str
    risk_axis: str
    action_taken: str
    analyst_id: str
    notes: str | None
    decided_at: datetime


class CaseEventIn(BaseModel):
    event_type: str
    note: str | None = None
    actor: str = Field(min_length=1)


class CaseEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    label: str
    note: str | None
    actor: str
    occurred_at: datetime


class CaseSummary(BaseModel):
    disposition_id: int
    alert_id: int
    merchant_id: str
    mcc: str | None
    mcc_description: str | None
    reason_code: str
    risk_axis: str
    stage: str
    stage_label: str
    is_resolved: bool
    opened_at: datetime
    last_update: datetime
    days_since_update: int
    is_stale: bool


class CaseDetail(CaseSummary):
    notes: str | None
    analyst_id: str
    scored_date: str | None
    events: list[CaseEventOut]


class RuleParam(BaseModel):
    """One tunable number on a rule, with the bounds it must stay inside."""

    key: str
    label: str
    kind: str
    default: float
    minimum: float
    maximum: float
    step: float
    hint: str


class RuleTemplateOut(BaseModel):
    """A rule the engine can evaluate, and everything the tuning screen needs
    to render controls for it without hard-coding anything."""

    key: str
    family: str
    label: str
    description: str
    rationale: str
    scopable: bool
    params: list[RuleParam]


class RuleInstanceIn(BaseModel):
    instance_id: str = Field(min_length=1)
    template: str = Field(min_length=1)
    enabled: bool = True
    params: dict[str, float] = {}
    mcc_scope: list[str] = []
    custom: bool = False
    label: str | None = None


class RuleSet(BaseModel):
    """The configured rules, alongside the catalogue they instantiate."""

    templates: list[RuleTemplateOut]
    instances: list[RuleInstanceIn]


class Page(BaseModel):
    """A slice of a list, so a queue of thousands does not arrive at once."""

    total: int
    page: int
    page_size: int
    pages: int


class AlertPage(Page):
    items: list[AlertOut]


class CasePage(Page):
    items: list[CaseSummary]
