from datetime import datetime, timezone
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"
    merchant_id: Mapped[str] = mapped_column(String, primary_key=True)
    mcc: Mapped[str] = mapped_column(String, index=True)
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
    terminal_id: Mapped[str | None] = mapped_column(String, default=None)
    card_bin: Mapped[str | None] = mapped_column(String, default=None)  # never full PAN
    geo: Mapped[str | None] = mapped_column(String, default=None)


class MerchantProfile(Base):
    __tablename__ = "merchant_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.merchant_id"), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # rolling windows


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.merchant_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lane: Mapped[str] = mapped_column(String)
    blended_score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    triggering_detectors: Mapped[list] = mapped_column(JSON, default=list)
    feature_snapshot: Mapped[list] = mapped_column(JSON, default=list)  # immutable in app logic
    disposition: Mapped["Disposition | None"] = relationship(back_populates="alert", uselist=False)


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


class TrainingBatch(Base):
    __tablename__ = "training_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(String, default=None)
