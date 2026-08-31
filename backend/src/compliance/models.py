from datetime import datetime, timezone
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Boolean, Text, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from compliance.glossary import alert_type_for


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"
    merchant_id: Mapped[str] = mapped_column(String, primary_key=True)
    mcc: Mapped[str] = mapped_column(String, index=True)
    mcc_description: Mapped[str | None] = mapped_column(String, default=None)
    agent_id: Mapped[str | None] = mapped_column(String, index=True, default=None)
    # Merchant-identity hashes. Used only for equality joins in ring detection,
    # never reversed, so their reversibility does not matter here.
    hashed_merchant_name: Mapped[str | None] = mapped_column(String, index=True, default=None)
    hashed_br_number: Mapped[str | None] = mapped_column(String, index=True, default=None)
    hashed_merchant_address: Mapped[str | None] = mapped_column(String, index=True, default=None)
    city: Mapped[str | None] = mapped_column(String, default=None)
    merchant_area: Mapped[str | None] = mapped_column(String, default=None)
    merchant_district: Mapped[str | None] = mapped_column(String, default=None)
    merchant_subdistrict: Mapped[str | None] = mapped_column(String, index=True, default=None)
    business_plan: Mapped[str | None] = mapped_column(String, default=None)
    business_nature: Mapped[str | None] = mapped_column(String, default=None)
    ownership_or_business_type: Mapped[str | None] = mapped_column(String, default=None)
    merchant_status: Mapped[str | None] = mapped_column(String, default=None)
    registered_address: Mapped[str | None] = mapped_column(String, default=None)
    onboarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lane: Mapped[str] = mapped_column(String, default="B")  # 'A' or 'B'
    lane_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_txn_id: Mapped[str] = mapped_column(String, unique=True, index=True)  # idempotent pull key
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.merchant_id"), index=True)
    total_amount: Mapped[float] = mapped_column(Float)  # gross value moved — the detection signal
    net_amount: Mapped[float | None] = mapped_column(Float, default=None)  # after fees
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    is_refund: Mapped[bool] = mapped_column(Boolean, default=False)
    card_bin: Mapped[str | None] = mapped_column(String, default=None)  # never full PAN
    geo: Mapped[str | None] = mapped_column(String, default=None)
    card_type: Mapped[str | None] = mapped_column(String, default=None)
    card_origin: Mapped[str | None] = mapped_column(String, default=None)
    card_issuing_country: Mapped[str | None] = mapped_column(String, index=True, default=None)
    card_issuing_bank: Mapped[str | None] = mapped_column(String, default=None)
    payment_gateway: Mapped[str | None] = mapped_column(String, default=None)
    currency: Mapped[str | None] = mapped_column(String, default=None)
    transaction_status: Mapped[str | None] = mapped_column(String, index=True, default=None)
    # Sensitive: a 1:1 hash of a PAN is brute-forceable, so this is treated as
    # cardholder data — never exposed through the API or the UI.
    hashed_pan: Mapped[str | None] = mapped_column(String, index=True, default=None)


class MerchantProfile(Base):
    __tablename__ = "merchant_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.merchant_id"), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # rolling windows


class CohortSnapshot(Base):
    """One MCC cohort's fitted amount distribution, kept whole.

    The cohort is what several detectors judge a merchant against, so the case
    page has to show it. Rebuilding it at read time meant a query per member
    over their entire history, which was slow enough that the read path had
    quietly capped itself at 200 members — an arbitrary subset of a cohort that
    runs to hundreds, presented as the distribution.

    Fitting happens once per run, where there is time to do it over every
    member, and the members themselves are stored rather than only their
    summary. Quartiles and the drawn points then come from one set of numbers,
    so the plot cannot disagree with the fence drawn across it.

    One row per cohort, not per merchant: the member list is shared by every
    merchant in the MCC, and copying it onto each profile would multiply it by
    the size of the cohort.
    """

    __tablename__ = "cohort_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mcc: Mapped[str] = mapped_column(String, index=True)
    center: Mapped[float] = mapped_column(Float)
    dispersion: Mapped[float] = mapped_column(Float)
    q1: Mapped[float] = mapped_column(Float)
    q3: Mapped[float] = mapped_column(Float)
    upper_fence: Mapped[float | None] = mapped_column(Float, default=None)
    n_merchants: Mapped[int] = mapped_column(Integer, default=0)
    usable: Mapped[bool] = mapped_column(Boolean, default=False)
    # One typical ticket per member, ascending. The distribution itself.
    members: Mapped[list] = mapped_column(JSON, default=list)
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class PipelineRun(Base):
    """One execution of the pipeline, and what it was told to do.

    A run is a statement about a scored day made under a particular set of
    parameters. Re-running the same day with a retuned threshold produces a
    different statement about it, and without a record of which parameters were
    in force there is no way to say why two runs disagreed — or even that they
    were two runs, since both carry the same `as_of`.

    The thresholds and rules are copied in rather than referenced. They live in
    tables the compliance lead can edit without a deploy, so a reference would
    resolve to whatever is current at reading time and quietly misattribute the
    settings that actually produced the alerts.

    `superseded_at` is what retires a run. Its alerts leave the queue but stay
    on record: an analyst sees one current statement per scored day, and a
    parameter change remains something you can look back at and compare rather
    than something that overwrote its predecessor.
    """

    __tablename__ = "pipeline_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The day scored. Not unique: re-scoring a day is the case this exists for.
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # NULL means this is the current statement about its scored day.
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, default=None
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    # Why this run was made — "outlier_z 3.5 -> 4.0", "post-tuning re-score".
    # Free text, because the reason for a re-run is a human one.
    label: Mapped[str | None] = mapped_column(String, default=None)
    triggered_by: Mapped[str | None] = mapped_column(String, default=None)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.merchant_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # The run's `as_of`. Distinct from created_at, which is wall-clock: a
    # backfill or a re-run scores a past day, and an auditor needs to know
    # which day was evaluated, not when the row happened to be written.
    as_of: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, default=None
    )
    # Which execution raised this. `as_of` says which day was scored; two runs
    # over one day share it, so this is the only thing that attributes an alert
    # to the parameters that produced it.
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id"), index=True, default=None
    )
    lane: Mapped[str] = mapped_column(String)
    blended_score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    # The triage badge, derived from the first triggering detector and cached
    # here so the queue can filter and count in the database. Deriving it in
    # Python meant every page view had to materialise the entire open queue —
    # tens of thousands of rows, both JSON columns included — to answer a
    # question about twenty of them. A cache, not a second source of truth:
    # `diagnostics.alert_type` still derives it wherever this is unset.
    alert_type: Mapped[str | None] = mapped_column(String, index=True, default=None)
    triggering_detectors: Mapped[list] = mapped_column(JSON, default=list)
    feature_snapshot: Mapped[list] = mapped_column(JSON, default=list)  # immutable in app logic
    disposition: Mapped["Disposition | None"] = relationship(back_populates="alert", uselist=False)


@event.listens_for(Alert, "before_insert")
def _fill_alert_type(mapper, connection, target: Alert) -> None:
    """Never let an alert reach the table without its badge.

    The queue filters and counts on this column, so a NULL is not a missing
    label — it is an alert that no filter returns and no chip counts, invisible
    to the analyst working that type. Filling it at the ORM boundary keeps the
    column authoritative (and therefore indexable) whatever wrote the row.
    """
    if not target.alert_type:
        detectors = target.triggering_detectors or []
        first = detectors[0].get("detector", "") if detectors else ""
        target.alert_type = alert_type_for(first)


class Disposition(Base):
    __tablename__ = "dispositions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), unique=True, index=True)
    verdict: Mapped[str] = mapped_column(String)  # TRUE_POSITIVE / FALSE_POSITIVE / INCONCLUSIVE
    reason_code: Mapped[str] = mapped_column(String)
    risk_axis: Mapped[str] = mapped_column(String)  # REGULATORY / COMMERCIAL / BOTH
    action_taken: Mapped[str] = mapped_column(String, default="NONE")  # recorded, never executed
    analyst_id: Mapped[str] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    signature: Mapped[str | None] = mapped_column(Text, default=None)  # non-repudiation (later)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    alert: Mapped["Alert"] = relationship(back_populates="disposition")


class CaseEvent(Base):
    __tablename__ = "case_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    disposition_id: Mapped[int] = mapped_column(ForeignKey("dispositions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    actor: Mapped[str] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DetectionSetting(Base):
    """Tunable thresholds, keyed so future setting groups can share the table.

    In the database rather than in code because the compliance lead calibrating
    against real dispositions should not need a deploy to change a number.
    """

    __tablename__ = "detection_settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String, default=None)


class TrainingBatch(Base):
    __tablename__ = "training_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(String, default=None)
