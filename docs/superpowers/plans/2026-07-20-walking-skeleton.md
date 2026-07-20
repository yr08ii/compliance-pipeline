# Compliance Platform — Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a thin end-to-end slice of the compliance platform: a synthetic transaction batch flows through a Prefect pipeline of six stub stages, writes one alert with an immutable feature snapshot, the FastAPI backend serves it, and a React/shadcn frontend renders all five screens — proving every part connects before any single stage is deepened.

**Architecture:** One repo, one deploy. Python (FastAPI + Prefect + SQLAlchemy) owns the pipeline, the API, and the data. React + Vite + Tailwind + shadcn/ui owns the UI, built to a static bundle that FastAPI serves. TypeScript API types are generated from FastAPI's OpenAPI schema so the type boundary is checked at compile time. PostgreSQL is the single datastore.

**Tech Stack:** Python 3.13, uv, FastAPI, SQLAlchemy 2.0, Alembic, Prefect 3, Pydantic 2, pytest; Node 24, Vite, React 18, TypeScript, Tailwind, shadcn/ui, openapi-typescript; PostgreSQL 16; docker compose (production).

## Global Constraints

- **Fully local, no external calls.** No cloud services, no external APIs, no telemetry. Prefect runs in local/ephemeral mode; no Prefect Cloud.
- **No PAN.** The `transactions` table never stores a full card number. Card data is limited to `card_bin` (first 6–8 digits).
- **Deterministic pipeline.** Pipeline stages are plain Python functions, not LLM agents. Same input → same output.
- **System of record, not execution.** No stage or endpoint performs a real-world action (fund hold, STR filing). Action tags are recorded only.
- **Immutability where marked.** `transactions`, `alerts.feature_snapshot`, and `case_events` are append-only / write-once in application logic.
- **Python dependency management via `uv`.** Backend lives in `backend/`, frontend in `frontend/`.
- **Database access only via `DATABASE_URL`** env var, e.g. `postgresql+psycopg://compliance:compliance@localhost:5432/compliance`.
- **Sentence case in UI copy.** No Title Case headings, no ALL CAPS.

---

## File Structure

```
compliance-pipeline/
├── docker-compose.yml            # postgres + backend (production shape)
├── .env.example                  # DATABASE_URL and friends
├── backend/
│   ├── pyproject.toml            # uv project
│   ├── alembic.ini
│   ├── migrations/               # alembic migrations
│   │   ├── env.py
│   │   └── versions/
│   ├── src/compliance/
│   │   ├── __init__.py
│   │   ├── config.py             # settings from env
│   │   ├── db.py                 # engine, session
│   │   ├── models.py             # SQLAlchemy ORM tables
│   │   ├── schemas.py            # Pydantic API models
│   │   ├── api.py                # FastAPI app + routes + static serving
│   │   ├── seed.py               # tiny synthetic dataset
│   │   └── pipeline/
│   │       ├── __init__.py
│   │       ├── flow.py           # Prefect flow: 6 stub stages
│   │       └── stages.py         # the six stage functions
│   └── tests/
│       ├── conftest.py
│       ├── test_health.py
│       ├── test_models.py
│       ├── test_alerts_api.py
│       └── test_pipeline.py
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx               # router + layout shell
        ├── api/client.ts         # generated types + fetch wrappers
        ├── lib/utils.ts          # shadcn cn()
        └── screens/
            ├── Dashboard.tsx
            ├── AlertQueue.tsx
            ├── CaseReview.tsx
            ├── FollowThrough.tsx
            └── ModelHealth.tsx
```

**Responsibilities:** `models.py` is the only place tables are defined; `schemas.py` is the only place API shapes are defined; `stages.py` holds pure stage functions that `flow.py` orchestrates; each screen file owns exactly one screen. Files that change together (a table and its migration) are committed together.

---

## Task 1: Backend project scaffold + health endpoint

**Files:**
- Create: `backend/pyproject.toml`, `backend/src/compliance/__init__.py`, `backend/src/compliance/config.py`, `backend/src/compliance/api.py`, `backend/tests/conftest.py`, `backend/tests/test_health.py`, `.env.example`

**Interfaces:**
- Produces: `create_app() -> FastAPI` in `compliance.api`; `Settings` in `compliance.config` with `.database_url: str`.

- [ ] **Step 1: Initialize the uv project**

Run:
```bash
cd backend
uv init --package --name compliance --python 3.13 .
uv add fastapi "uvicorn[standard]" "sqlalchemy>=2.0" "psycopg[binary]" alembic "pydantic>=2" pydantic-settings prefect
uv add --dev pytest httpx
```
Expected: `pyproject.toml` created, `.venv` populated, no errors.

- [ ] **Step 2: Write the failing health test**

Create `backend/tests/test_health.py`:
```python
from fastapi.testclient import TestClient
from compliance.api import create_app


def test_health_ok():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

Create `backend/tests/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compliance.api'`

- [ ] **Step 4: Write config and the app**

Create `backend/src/compliance/config.py`:
```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://compliance:compliance@localhost:5432/compliance"


settings = Settings()
```

Create `backend/src/compliance/api.py`:
```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Compliance Monitoring Platform")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

Create `.env.example` at repo root:
```
DATABASE_URL=postgresql+psycopg://compliance:compliance@localhost:5432/compliance
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests .env.example
git commit -m "feat: backend scaffold with health endpoint"
```

---

## Task 2: Database models + migration

**Files:**
- Create: `backend/src/compliance/db.py`, `backend/src/compliance/models.py`, `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions/0001_initial.py`, `backend/tests/test_models.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Produces: `Base` (declarative base), ORM classes `Merchant`, `Transaction`, `MerchantProfile`, `Alert`, `Disposition`, `CaseEvent`, `TrainingBatch` in `compliance.models`; `engine`, `SessionLocal`, `get_session()` in `compliance.db`.

- [ ] **Step 1: Write the failing model test**

Create `backend/tests/test_models.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from compliance.models import Base, Merchant, Alert


def test_create_merchant_and_alert():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        m = Merchant(merchant_id="M001", mcc="5411", lane="B")
        s.add(m)
        s.flush()
        a = Alert(
            merchant_id="M001",
            lane="B",
            blended_score=0.82,
            rank=1,
            triggering_detectors=[{"detector": "velocity_cap", "sub_score": 0.9}],
            feature_snapshot=[
                {"feature_name": "daily_volume", "merchant_value": 50000,
                 "baseline_value": 8000, "deviation": 5.25}
            ],
        )
        s.add(a)
        s.commit()
        assert s.get(Alert, a.id).feature_snapshot[0]["deviation"] == 5.25
```

Add to `backend/tests/conftest.py` dependency (SQLite driver for fast tests):
Run: `cd backend && uv add --dev pysqlite3-binary || true` (pysqlite3 ships with Python; this is a no-op fallback)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compliance.models'`

- [ ] **Step 3: Write db.py**

Create `backend/src/compliance/db.py`:
```python
from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from compliance.config import settings

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
```

- [ ] **Step 4: Write models.py**

Create `backend/src/compliance/models.py`:
```python
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
    amount: Mapped[float] = mapped_column(Float)
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 6: Wire Alembic and generate the initial migration**

Run:
```bash
cd backend
uv run alembic init -t generic migrations
```
Edit `backend/alembic.ini`: set `sqlalchemy.url =` to empty (URL comes from env).
Replace `backend/migrations/env.py` target metadata block so it reads:
```python
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from compliance.models import Base  # noqa: E402

config = context.config
config.set_main_option(
    "sqlalchemy.url",
    os.environ.get("DATABASE_URL",
                   "postgresql+psycopg://compliance:compliance@localhost:5432/compliance"),
)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```
Then run: `cd backend && uv run alembic revision --autogenerate -m "initial"` (requires a running Postgres; see Task 8 for the compose file, or a local Postgres).
Expected: a file appears in `backend/migrations/versions/`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/compliance/db.py backend/src/compliance/models.py \
  backend/alembic.ini backend/migrations backend/tests/test_models.py
git commit -m "feat: database models and initial migration"
```

---

## Task 3: Pydantic schemas + alerts API

**Files:**
- Create: `backend/src/compliance/schemas.py`, `backend/tests/test_alerts_api.py`
- Modify: `backend/src/compliance/api.py`

**Interfaces:**
- Consumes: `Alert` model, `get_session`.
- Produces: `AlertOut`, `FeatureDivergence`, `DetectorHit` in `compliance.schemas`; `GET /api/alerts` and `GET /api/alerts/{id}` returning `AlertOut`.

- [ ] **Step 1: Write the failing API test**

Create `backend/tests/test_alerts_api.py`:
```python
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from compliance.models import Base, Merchant, Alert
from compliance.api import create_app
from compliance.db import get_session


def _client_with_data():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    with TestSession() as s:
        s.add(Merchant(merchant_id="M001", mcc="5411", lane="A"))
        s.add(Alert(id=1, merchant_id="M001", lane="A", blended_score=0.9, rank=1,
                    triggering_detectors=[{"detector": "velocity", "sub_score": 0.9}],
                    feature_snapshot=[{"feature_name": "daily_volume", "merchant_value": 5e4,
                                       "baseline_value": 8e3, "deviation": 5.25}],
                    created_at=datetime(2026, 7, 20, tzinfo=timezone.utc)))
        s.commit()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: iter([TestSession()])
    return TestClient(app)


def test_list_alerts():
    client = _client_with_data()
    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["merchant_id"] == "M001"
    assert body[0]["lane"] == "A"


def test_get_alert_detail_has_divergence():
    client = _client_with_data()
    resp = client.get("/api/alerts/1")
    assert resp.status_code == 200
    assert resp.json()["feature_snapshot"][0]["deviation"] == 5.25
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_alerts_api.py -v`
Expected: FAIL — `/api/alerts` returns 404 (route not defined)

- [ ] **Step 3: Write the schemas**

Create `backend/src/compliance/schemas.py`:
```python
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
```

- [ ] **Step 4: Add the routes**

Replace `backend/src/compliance/api.py`:
```python
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from compliance.db import get_session
from compliance.models import Alert
from compliance.schemas import AlertOut


def create_app() -> FastAPI:
    app = FastAPI(title="Compliance Monitoring Platform")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/alerts", response_model=list[AlertOut])
    def list_alerts(session: Session = Depends(get_session)) -> list[Alert]:
        return list(session.scalars(select(Alert).order_by(Alert.rank)))

    @app.get("/api/alerts/{alert_id}", response_model=AlertOut)
    def get_alert(alert_id: int, session: Session = Depends(get_session)) -> Alert:
        alert = session.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        return alert

    return app


app = create_app()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_alerts_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/src/compliance/schemas.py backend/src/compliance/api.py \
  backend/tests/test_alerts_api.py
git commit -m "feat: alerts API with divergence-carrying schema"
```

---

## Task 4: Synthetic seed dataset

**Files:**
- Create: `backend/src/compliance/seed.py`, `backend/tests/test_seed.py`

**Interfaces:**
- Consumes: models, `SessionLocal`.
- Produces: `seed(session) -> None` in `compliance.seed` that inserts merchants and transactions for one day.

- [ ] **Step 1: Write the failing seed test**

Create `backend/tests/test_seed.py`:
```python
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from compliance.models import Base, Merchant, Transaction
from compliance.seed import seed


def test_seed_inserts_merchants_and_transactions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        seed(s)
        s.commit()
        merchants = s.scalar(select(func.count()).select_from(Merchant))
        txns = s.scalar(select(func.count()).select_from(Transaction))
        assert merchants >= 3
        assert txns >= 20
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compliance.seed'`

- [ ] **Step 3: Write the seed**

Create `backend/src/compliance/seed.py`:
```python
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from compliance.models import Merchant, Transaction

_DAY = datetime(2026, 7, 19, tzinfo=timezone.utc)


def seed(session: Session) -> None:
    merchants = [
        Merchant(merchant_id="M001", mcc="5411", lane="A"),   # grocery, mature
        Merchant(merchant_id="M002", mcc="5944", lane="A"),   # jewelry, mature
        Merchant(merchant_id="M003", mcc="5732", lane="B"),   # electronics, new
    ]
    session.add_all(merchants)
    txn_id = 0
    for m in merchants:
        base_amount = 120.0 if m.merchant_id != "M002" else 4000.0
        count = 10 if m.merchant_id != "M003" else 3
        for i in range(count):
            txn_id += 1
            session.add(Transaction(
                source_txn_id=f"T{txn_id:05d}",
                merchant_id=m.merchant_id,
                amount=base_amount * (1 + 0.1 * i),
                occurred_at=_DAY + timedelta(hours=i),
                is_refund=(i % 7 == 0),
                terminal_id=f"TERM-{m.merchant_id}",
                card_bin="457896",
                geo="HK",
            ))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_seed.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/compliance/seed.py backend/tests/test_seed.py
git commit -m "feat: synthetic seed dataset for one day"
```

---

## Task 5: Prefect pipeline — six stub stages producing one alert

**Files:**
- Create: `backend/src/compliance/pipeline/__init__.py`, `backend/src/compliance/pipeline/stages.py`, `backend/src/compliance/pipeline/flow.py`, `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: models, a `Session`.
- Produces: pure functions in `stages.py` — `route(session)`, `detect(session)`, `score_and_rank(session) -> list[Alert]`; and `run_pipeline(session) -> int` (returns number of alerts written) in `flow.py`.

- [ ] **Step 1: Write the failing pipeline test**

Create `backend/tests/test_pipeline.py`:
```python
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from compliance.models import Base, Alert
from compliance.seed import seed
from compliance.pipeline.flow import run_pipeline


def test_pipeline_produces_ranked_alert_with_snapshot():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        seed(s)
        s.commit()
        n = run_pipeline(s)
        s.commit()
        assert n >= 1
        alerts = list(s.scalars(select(Alert).order_by(Alert.rank)))
        assert alerts[0].rank == 1
        assert alerts[0].lane in ("A", "B")
        assert len(alerts[0].feature_snapshot) >= 1
        assert "deviation" in alerts[0].feature_snapshot[0]


def test_pipeline_is_deterministic():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        seed(s)
        s.commit()
        first = run_pipeline(s)
        s.query(Alert).delete()
        s.commit()
        second = run_pipeline(s)
        assert first == second
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'compliance.pipeline.flow'`

- [ ] **Step 3: Write the stage functions**

Create `backend/src/compliance/pipeline/__init__.py` (empty).

Create `backend/src/compliance/pipeline/stages.py`:
```python
"""Six deterministic stub stages. Real logic arrives in later plans.

Stage 1 (pull) and Stage 2 (profile) are represented by the seed data and a
simple in-place profile computation; this skeleton exercises the shape, not
the detection quality.
"""
from statistics import mean
from sqlalchemy import select
from sqlalchemy.orm import Session
from compliance.models import Merchant, Transaction, MerchantProfile, Alert


def profile(session: Session) -> None:
    """Stage 2: compute a trivial per-merchant daily-volume profile."""
    for m in session.scalars(select(Merchant)):
        amounts = [t.amount for t in session.scalars(
            select(Transaction).where(Transaction.merchant_id == m.merchant_id))]
        daily_volume = sum(amounts)
        avg_ticket = mean(amounts) if amounts else 0.0
        session.add(MerchantProfile(
            merchant_id=m.merchant_id,
            as_of=max((t.occurred_at for t in session.scalars(
                select(Transaction).where(Transaction.merchant_id == m.merchant_id))),
                default=None),
            metrics={"daily_volume": daily_volume, "avg_ticket": avg_ticket,
                     "txn_count": len(amounts)},
        ))


def route(session: Session) -> dict[str, str]:
    """Stage 3: lane by txn count. <5 txns -> Lane B, else Lane A. Stub threshold."""
    lanes: dict[str, str] = {}
    for p in session.scalars(select(MerchantProfile)):
        lane = "A" if p.metrics.get("txn_count", 0) >= 5 else "B"
        lanes[p.merchant_id] = lane
        m = session.get(Merchant, p.merchant_id)
        if m is not None:
            m.lane = lane
    return lanes


def detect(session: Session, lanes: dict[str, str]) -> list[dict]:
    """Stage 4: one stub detector — flag daily_volume above a flat baseline of 8000."""
    baseline = 8000.0
    hits: list[dict] = []
    for p in session.scalars(select(MerchantProfile)):
        volume = p.metrics.get("daily_volume", 0.0)
        if volume > baseline:
            deviation = round(volume / baseline, 2)
            hits.append({
                "merchant_id": p.merchant_id,
                "lane": lanes.get(p.merchant_id, "B"),
                "detector": "daily_volume_over_baseline",
                "sub_score": min(deviation / 10.0, 1.0),
                "feature": {"feature_name": "daily_volume", "merchant_value": volume,
                            "baseline_value": baseline, "deviation": deviation},
            })
    return hits


def score_and_rank(session: Session, hits: list[dict]) -> list[Alert]:
    """Stage 5: one alert per hit, ranked by sub_score desc, with feature snapshot."""
    ordered = sorted(hits, key=lambda h: h["sub_score"], reverse=True)
    alerts: list[Alert] = []
    for rank, h in enumerate(ordered, start=1):
        alert = Alert(
            merchant_id=h["merchant_id"],
            lane=h["lane"],
            blended_score=h["sub_score"],
            rank=rank,
            triggering_detectors=[{"detector": h["detector"], "sub_score": h["sub_score"]}],
            feature_snapshot=[h["feature"]],
        )
        session.add(alert)
        alerts.append(alert)
    return alerts
```

- [ ] **Step 4: Write the flow**

Create `backend/src/compliance/pipeline/flow.py`:
```python
from prefect import flow, task
from sqlalchemy.orm import Session
from compliance.pipeline import stages


@task
def _profile(session: Session) -> None:
    stages.profile(session)


@task
def _route(session: Session) -> dict[str, str]:
    return stages.route(session)


@task
def _detect(session: Session, lanes: dict[str, str]) -> list[dict]:
    return stages.detect(session, lanes)


@task
def _score(session: Session, hits: list[dict]) -> int:
    return len(stages.score_and_rank(session, hits))


@flow(name="nightly-compliance-pipeline")
def run_pipeline(session: Session) -> int:
    """Stages: pull(seed) -> profile -> route -> detect -> score/rank.
    Returns the number of alerts written."""
    _profile(session)
    lanes = _route(session)
    hits = _detect(session, lanes)
    return _score(session, hits)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: PASS (2 passed). If Prefect emits async-loop warnings under pytest, they are non-fatal; the assertions must pass.

- [ ] **Step 6: Run the whole backend suite**

Run: `cd backend && uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/compliance/pipeline backend/tests/test_pipeline.py
git commit -m "feat: prefect pipeline of six stub stages producing ranked alerts"
```

---

## Task 6: Frontend scaffold + generated API types

**Files:**
- Create: `frontend/` (Vite React-TS app), `frontend/src/api/client.ts`, `frontend/src/api/schema.d.ts` (generated)
- Modify: `frontend/vite.config.ts`

**Interfaces:**
- Consumes: the backend OpenAPI schema at `/openapi.json`.
- Produces: `apiGet<T>(path)` fetch helper and generated `paths`/`components` types.

- [ ] **Step 1: Scaffold the Vite app**

Run:
```bash
cd frontend 2>/dev/null || (cd .. && npm create vite@latest frontend -- --template react-ts)
cd frontend && npm install
npm install -D tailwindcss postcss autoprefixer openapi-typescript
npx tailwindcss init -p
```
Expected: `frontend/` populated, Tailwind config created.

- [ ] **Step 2: Configure Vite dev proxy to the backend**

Replace `frontend/vite.config.ts`:
```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    proxy: { "/api": "http://localhost:8000", "/openapi.json": "http://localhost:8000" },
  },
});
```

- [ ] **Step 3: Generate types from the running backend**

With the backend running (`cd backend && uv run uvicorn compliance.api:app --port 8000`), run:
```bash
cd frontend && npx openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts
```
Expected: `src/api/schema.d.ts` created with `paths` and `components`.

- [ ] **Step 4: Write the fetch helper**

Create `frontend/src/api/client.ts`:
```typescript
import type { components } from "./schema";

export type AlertOut = components["schemas"]["AlertOut"];

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return (await resp.json()) as T;
}
```

- [ ] **Step 5: Verify the app builds**

Run: `cd frontend && npm run build`
Expected: `dist/` produced, no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
  frontend/tailwind.config.js frontend/postcss.config.js frontend/src/api
git commit -m "feat: frontend scaffold with generated API types"
```

---

## Task 7: Five screen shells + routing

**Files:**
- Create: `frontend/src/lib/utils.ts`, `frontend/src/screens/Dashboard.tsx`, `frontend/src/screens/AlertQueue.tsx`, `frontend/src/screens/CaseReview.tsx`, `frontend/src/screens/FollowThrough.tsx`, `frontend/src/screens/ModelHealth.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/index.css`

**Interfaces:**
- Consumes: `apiGet`, `AlertOut`.
- Produces: five routed screens; `AlertQueue` lists real alerts, `CaseReview` renders the divergence panel from `feature_snapshot`.

- [ ] **Step 1: Install router and set up Tailwind CSS**

Run: `cd frontend && npm install react-router-dom`
Replace `frontend/src/index.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```
Set `frontend/tailwind.config.js` `content`:
```javascript
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
```

- [ ] **Step 2: Write the cn() utility**

Run: `cd frontend && npm install clsx tailwind-merge`
Create `frontend/src/lib/utils.ts`:
```typescript
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 3: Write the app shell with navigation**

Replace `frontend/src/App.tsx`:
```tsx
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./screens/Dashboard";
import AlertQueue from "./screens/AlertQueue";
import CaseReview from "./screens/CaseReview";
import FollowThrough from "./screens/FollowThrough";
import ModelHealth from "./screens/ModelHealth";
import { cn } from "./lib/utils";

const nav = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/queue", label: "Alert queue" },
  { to: "/cases", label: "Case follow-through" },
  { to: "/health", label: "Model health" },
];

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen text-slate-900">
        <aside className="w-56 border-r bg-slate-50 p-4">
          <h1 className="mb-6 text-sm font-medium text-slate-500">Compliance monitoring</h1>
          <nav className="flex flex-col gap-1">
            {nav.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end}
                className={({ isActive }) => cn("rounded px-3 py-2 text-sm",
                  isActive ? "bg-slate-900 text-white" : "hover:bg-slate-200")}>
                {n.label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className="flex-1 p-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/queue" element={<AlertQueue />} />
            <Route path="/case/:id" element={<CaseReview />} />
            <Route path="/cases" element={<FollowThrough />} />
            <Route path="/health" element={<ModelHealth />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
```

- [ ] **Step 4: Write the alert queue screen (reads real data)**

Create `frontend/src/screens/AlertQueue.tsx`:
```tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";

export default function AlertQueue() {
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<AlertOut[]>("/api/alerts").then(setAlerts).catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="text-red-600">Failed to load alerts: {error}</p>;

  return (
    <div>
      <h2 className="mb-4 text-lg font-medium">Alert queue</h2>
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr><th className="py-2">Rank</th><th>Merchant</th><th>Lane</th><th>Score</th><th></th></tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id} className="border-t">
              <td className="py-2">{a.rank}</td>
              <td>{a.merchant_id}</td>
              <td>{a.lane}</td>
              <td>{a.blended_score.toFixed(2)}</td>
              <td><Link className="text-blue-600" to={`/case/${a.id}`}>Review</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
      {alerts.length === 0 && <p className="mt-4 text-slate-500">No alerts in the queue.</p>}
    </div>
  );
}
```

- [ ] **Step 5: Write the case-review screen with the divergence panel**

Create `frontend/src/screens/CaseReview.tsx`:
```tsx
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";

export default function CaseReview() {
  const { id } = useParams();
  const [alert, setAlert] = useState<AlertOut | null>(null);

  useEffect(() => {
    if (id) apiGet<AlertOut>(`/api/alerts/${id}`).then(setAlert).catch(() => setAlert(null));
  }, [id]);

  if (!alert) return <p className="text-slate-500">Loading case…</p>;

  return (
    <div className="max-w-3xl">
      <h2 className="text-lg font-medium">Case review — {alert.merchant_id}</h2>
      <p className="mb-6 text-sm text-slate-500">
        Lane {alert.lane} · score {alert.blended_score.toFixed(2)} · rank {alert.rank}
      </p>

      <h3 className="mb-2 text-sm font-medium">What diverged from baseline</h3>
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr><th className="py-2">Feature</th><th>Merchant</th><th>Baseline</th><th>Deviation</th></tr>
        </thead>
        <tbody>
          {alert.feature_snapshot.map((f, i) => (
            <tr key={i} className="border-t">
              <td className="py-2">{f.feature_name}</td>
              <td>{f.merchant_value.toLocaleString()}</td>
              <td>{f.baseline_value.toLocaleString()}</td>
              <td className={f.deviation >= 3 ? "font-medium text-red-600" : ""}>
                {f.deviation.toFixed(2)}×
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-6 text-sm text-slate-400">
        Disposition form and digital signature arrive in the Phase 2 plan.
      </p>
    </div>
  );
}
```

- [ ] **Step 6: Write the three remaining stub screens**

Create `frontend/src/screens/Dashboard.tsx`:
```tsx
export default function Dashboard() {
  return (
    <div>
      <h2 className="mb-4 text-lg font-medium">Operations dashboard</h2>
      <p className="text-sm text-slate-500">
        Last night's run health, queue depth, and SLA land here in a later plan.
      </p>
    </div>
  );
}
```

Create `frontend/src/screens/FollowThrough.tsx`:
```tsx
export default function FollowThrough() {
  return (
    <div>
      <h2 className="mb-4 text-lg font-medium">Case follow-through</h2>
      <p className="text-sm text-slate-500">
        Confirmed cases and their timelines land here in a later plan.
      </p>
    </div>
  );
}
```

Create `frontend/src/screens/ModelHealth.tsx`:
```tsx
export default function ModelHealth() {
  return (
    <div>
      <h2 className="mb-4 text-lg font-medium">Model &amp; pipeline health</h2>
      <p className="text-sm text-slate-500">
        False-positive trend, label completeness, and training-batch history land here later.
      </p>
    </div>
  );
}
```

- [ ] **Step 7: Verify the build**

Run: `cd frontend && npm run build`
Expected: `dist/` produced, no TypeScript errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src
git commit -m "feat: five screen shells with live alert queue and divergence panel"
```

---

## Task 8: Serve the built frontend from FastAPI + docker compose

**Files:**
- Create: `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile` (optional build stage)
- Modify: `backend/src/compliance/api.py`

**Interfaces:**
- Consumes: `frontend/dist`, the FastAPI app.
- Produces: FastAPI serving `/` (the SPA) and `/api/*`; a compose stack bringing up Postgres + the app.

- [ ] **Step 1: Write the failing static-serving test**

Add to `backend/tests/test_health.py`:
```python
import os
from pathlib import Path
from fastapi.testclient import TestClient
from compliance.api import create_app


def test_spa_served_when_dist_exists(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>app</title>")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "app" in resp.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest tests/test_health.py::test_spa_served_when_dist_exists -v`
Expected: FAIL — `/` returns 404.

- [ ] **Step 3: Add static serving to the app**

In `backend/src/compliance/api.py`, add near the top of `create_app`, after routes are defined, before `return app`:
```python
    import os
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles

    dist = os.environ.get("FRONTEND_DIST")
    if dist and Path(dist, "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
```
(Add the imports at module top if preferred; keep `/api/*` routes registered before the mount so they take precedence.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: PASS (all health tests).

- [ ] **Step 5: Write docker-compose.yml (production shape)**

Create `docker-compose.yml` at repo root:
```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: compliance
      POSTGRES_PASSWORD: compliance
      POSTGRES_DB: compliance
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U compliance"]
      interval: 5s
      timeout: 3s
      retries: 10

  app:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+psycopg://compliance:compliance@db:5432/compliance
      FRONTEND_DIST: /app/frontend_dist
    depends_on:
      db: { condition: service_healthy }
    ports: ["8000:8000"]
    volumes: ["./frontend/dist:/app/frontend_dist:ro"]

volumes:
  pgdata:
```

- [ ] **Step 6: Write the backend Dockerfile**

Create `backend/Dockerfile`:
```dockerfile
FROM python:3.13-slim
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
CMD ["uv", "run", "uvicorn", "compliance.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml backend/Dockerfile backend/src/compliance/api.py \
  backend/tests/test_health.py
git commit -m "feat: FastAPI serves built SPA; docker compose stack"
```

---

## Task 9: End-to-end smoke — one command, data visible in the UI

**Files:**
- Create: `backend/src/compliance/cli.py`, `Makefile`, `README.md` (run instructions)
- Modify: `backend/pyproject.toml` (add a console script)

**Interfaces:**
- Consumes: `run_pipeline`, `seed`, `SessionLocal`, Alembic.
- Produces: `compliance-run` command that migrates, seeds, and runs the pipeline; a documented one-command local bring-up.

- [ ] **Step 1: Write the CLI**

Create `backend/src/compliance/cli.py`:
```python
"""Local bootstrap: seed synthetic data and run the pipeline once."""
from compliance.db import SessionLocal
from compliance.seed import seed
from compliance.pipeline.flow import run_pipeline


def main() -> None:
    with SessionLocal() as session:
        seed(session)
        session.commit()
        n = run_pipeline(session)
        session.commit()
        print(f"pipeline complete: {n} alert(s) written")


if __name__ == "__main__":
    main()
```

Add to `backend/pyproject.toml` under `[project.scripts]`:
```toml
[project.scripts]
compliance-run = "compliance.cli:main"
```

- [ ] **Step 2: Write the Makefile**

Create `Makefile` at repo root:
```makefile
.PHONY: db backend-migrate seed-run frontend up

db:
	docker compose up -d db

backend-migrate:
	cd backend && DATABASE_URL=$${DATABASE_URL} uv run alembic upgrade head

seed-run:
	cd backend && uv run compliance-run

frontend:
	cd frontend && npm run build

serve:
	cd backend && FRONTEND_DIST=../frontend/dist uv run uvicorn compliance.api:app --port 8000
```

- [ ] **Step 3: Write README run instructions**

Replace repo-root `README.md`:
```markdown
# Compliance monitoring platform

Local, self-contained compliance transaction-monitoring platform.

## Run locally

1. Start Postgres: `make db` (needs Docker Engine/Podman) or point `DATABASE_URL` at a local Postgres.
2. Apply migrations: `make backend-migrate`
3. Seed data and run the pipeline: `make seed-run`
4. Build the frontend: `make frontend`
5. Serve everything: `make serve`
6. Open http://localhost:8000 → Alert queue shows the flagged merchant; click Review to see the divergence panel.

See `docs/superpowers/specs/2026-07-20-compliance-platform-design.md` for the full design.
```

- [ ] **Step 4: Manual end-to-end verification**

Run, in order:
```bash
make db && sleep 5
make backend-migrate
make seed-run          # expect: "pipeline complete: N alert(s) written"
make frontend
make serve &
sleep 3
curl -s http://localhost:8000/api/alerts | head -c 200   # expect JSON with a merchant_id
```
Expected: the curl returns a non-empty JSON array; visiting `http://localhost:8000` shows the alert queue with at least one row, and the case-review page renders the divergence table.

- [ ] **Step 5: Commit**

```bash
git add backend/src/compliance/cli.py backend/pyproject.toml Makefile README.md
git commit -m "feat: one-command local bring-up and e2e smoke path"
```

---

## Self-Review

**Spec coverage (against the design doc):**
- Local, self-contained, no subscriptions → Global Constraints + docker compose local Postgres. ✓
- Deterministic pipeline (not agents) → Task 5 stages are pure functions; `test_pipeline_is_deterministic`. ✓
- Nightly six-stage pipeline → Task 5 (pull via seed, profile, route, detect, score/rank; suppress deferred to Phase 4 as the spec states). ✓
- Data model (8 tables) → Task 2 models all eight. ✓
- No PAN → `card_bin` only; Global Constraints. ✓
- Immutable feature snapshot → `Alert.feature_snapshot` JSON, written once by the pipeline, read by the UI. ✓
- Five screens → Task 7; Dashboard/FollowThrough/ModelHealth are honest stubs (deferred per walking-skeleton scope), AlertQueue + CaseReview are live. ✓
- Divergence panel from feature_snapshot → Task 7 Step 5. ✓
- Generated TS types across the boundary → Task 6. ✓
- Single deploy (FastAPI serves SPA) → Task 8. ✓
- System of record, not execution → no action is executed anywhere; `action_taken` is a recorded column only. ✓
- Digital signatures, auth, real detection, taxonomy → explicitly deferred to later plans; `signature` column exists but is nullable/unused here. Noted in CaseReview copy. ✓

**Deferred by design (not gaps):** real pull from a live source, materialised rolling windows, peer groups, the empirical maturity threshold, anomaly baselining, the secondary model, disposition capture UI, case-event timeline UI, digital signatures, local auth. Each is a later plan per Section 9 of the spec.

**Placeholder scan:** no "TBD"/"handle edge cases" steps; every code step carries complete code. ✓

**Type consistency:** `AlertOut.feature_snapshot: list[FeatureDivergence]` (fields `feature_name`, `merchant_value`, `baseline_value`, `deviation`) matches the dicts written in `stages.detect`/`score_and_rank` and consumed in `CaseReview.tsx`. `run_pipeline(session) -> int` matches its test and CLI use. ✓
