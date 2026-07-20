# Compliance Monitoring Platform — Design Specification

*The internal web platform the compliance team lives inside.*

**Date:** 2026-07-20 · Draft v1
**Status:** Design — pending review
**Related:** [Closed-Loop Compliance Monitoring Pipeline](../../Compliance_Monitoring_Pipeline_Plan.md)

---

## 1. What this is

A single, self-contained web platform, running locally on the organisation's own hardware, that operates the closed-loop compliance pipeline end to end. It does two things:

1. **Runs the automated pipeline** every night — pull → profile → route → detect → rank — producing a queue of merchants that need a human to look at them, all inside a 9-hour window (00:00–09:00).
2. **Gives the human compliance team the place they work** from 09:00 — a dashboard, a ranked queue, a case-review screen that shows *why* each merchant was flagged, a disposition form that captures their verdict as training data, and a follow-through board that tracks confirmed cases to resolution.

Every disposition the team records is packaged and fed back to train a secondary model that learns to suppress false positives over time. That feedback loop is the point of the system; the platform is the machine that makes the loop turn every day.

### Non-negotiable properties

- **Fully local, fully private.** Data is pulled from local sources, stored locally, and never leaves the building. Models run locally. No external APIs, no cloud services, no subscriptions.
- **Deterministic pipeline.** The router, rule engine, and baseline scorer are scheduled batch jobs, not LLM agents. The same input produces the same output, every run, and every decision is traceable to specific rules and feature values. This is an audit requirement, not a preference.
- **Human-in-the-loop for consequences.** The pipeline scores and ranks. It never freezes a merchant's funds. A person decides, and their decision is digitally signed and audit-logged.
- **System of record, not execution.** No real-world action (reserve, hold, offboard, STR filing) is *executed* through this portal. Those happen in the actual payment and filing systems. The portal *records the decision* — every action tag is captured as signed metadata for accountability and for training. This deliberately keeps the platform out of the path where it could trigger a real fund movement.
- **Shadow mode first.** The system runs alongside manual review and is measured against it before it drives any real decision about a merchant's money.

### Explicitly not in this platform

- KYC / merchant onboarding (separate workstream; this consumes onboarding data).
- Sanctions / watchlist screening (a distinct existing control; integration point only).
- Cardholder-side fraud / stolen-card detection (different objective).
- Terminal firmware integrity (a hardware-security workstream; consumed as a feature signal at most).

---

## 2. Architecture at a glance

One repository, one deployment, one box.

```
┌─────────────────────────────────────────────────────────────┐
│                     Local server (on-prem)                    │
│                                                               │
│  ┌────────────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │  Prefect   │   │   FastAPI     │   │   React frontend    │  │
│  │ (nightly   │──▶│  (API +       │◀──│  (shadcn/ui,        │  │
│  │  pipeline) │   │  serves the   │   │   served as static  │  │
│  │            │   │  built UI)    │   │   bundle by FastAPI) │  │
│  └─────┬──────┘   └───────┬───────┘   └────────────────────┘  │
│        │                  │                                    │
│        ▼                  ▼                                    │
│  ┌──────────────────────────────────────┐   ┌─────────────┐  │
│  │            PostgreSQL                  │   │  Local ML    │  │
│  │  merchants · transactions · profiles   │   │  models on   │  │
│  │  peer_groups · alerts · dispositions   │   │  disk        │  │
│  │  case_events · training_batches        │   │  (pickled)   │  │
│  └──────────────────────────────────────┘   └─────────────┘  │
│                                                               │
│  Local transaction source ──(scheduled pull)──▶ pipeline      │
└─────────────────────────────────────────────────────────────┘
```

**Operationally it is a monolith.** `docker compose up` brings up Postgres, the FastAPI app (which serves both the API and the pre-built React bundle), and the Prefect server + worker. Nothing else. It runs on Docker Engine / Podman on Linux — not Docker Desktop, which is licensed.

### Why this shape

- **Python owns everything that thinks.** The pull job, router, rule engine, baselines, training loop, and models are all Python, sharing one set of types. A feature vector computed by the router is the same object the case UI later renders.
- **React owns everything that renders.** The case-review screen needs to be genuinely good — dense tables, instant filtering, a clear divergence panel — because if analysts find the queue unpleasant they will skip the reason codes, and skipped reason codes silently kill the training loop. UI quality is load-bearing here, not cosmetic. React + shadcn/ui + Tailwind reaches that bar.
- **The type boundary is generated, not hand-maintained.** FastAPI emits an OpenAPI schema; the TypeScript client is generated from it. Change a Pydantic model, regenerate, and the frontend breaks at compile time instead of at runtime. One source of truth across two languages.

### Stack (all open-source, no subscriptions)

| Layer | Choice | License |
|---|---|---|
| Backend, jobs, models | Python · FastAPI · Pydantic | MIT / BSD |
| Database | PostgreSQL | PostgreSQL |
| Orchestration | Prefect (self-hosted) | Apache 2 |
| ML | scikit-learn · XGBoost/LightGBM | BSD / Apache 2 |
| Frontend | React · Vite · Tailwind · shadcn/ui | MIT |
| Container runtime | Docker Engine / Podman (Linux) | Apache 2 |
| Local LLM (optional, later) | Ollama + open-weight model | MIT + model licence |

At hundreds of thousands of transactions a day, PostgreSQL alone carries the load — provided rolling-window aggregates are materialised as tables rather than recomputed from raw rows each run. No separate analytics engine (DuckDB) is needed at this volume. One less moving part.

---

## 3. The nightly pipeline (00:00–09:00)

A sequential Prefect flow. Each stage consumes the previous stage's output; each is an individually retryable task with run history visible in the Prefect UI, so "why didn't Tuesday's queue populate" has a real answer rather than a log grep.

| # | Stage | Does | Writes |
|---|---|---|---|
| 1 | **Pull** | Query the local transaction source for the prior day. Pull only what detection needs — amount, timestamp, refund flag, merchant_id, terminal_id, card BIN, geography. No PAN. | `transactions` (append, immutable) |
| 2 | **Profile** | Recompute rolling 1/7/30/90-day aggregates per merchant; refresh peer-group stats. | `merchant_profiles`, `peer_groups` |
| 3 | **Route** | Assign each active merchant to Lane A (mature, has baseline) or Lane B (new/low-data). Log assignments and Lane B→A crossovers. | `merchants.lane`, lane history |
| 4 | **Detect** | Lane A: anomaly baseline + rule engine. Lane B: rule engine only. Each detector emits a sub-score and a reason code. | detector outputs (in memory → alerts) |
| 5 | **Score & rank** | Blend sub-scores into one merchant risk score. Snapshot the exact feature vector used. Produce the ranked queue. | `alerts` (incl. immutable `feature_snapshot`) |
| 6 | **Suppress** *(post-Phase 4)* | Secondary model re-ranks / deprioritises. Never originates an alert. | updates alert ranking |

**Determinism and audit.** Every run records what data range it consumed and which model/rule versions were active. Re-running stage 5 must reproduce the same scores given the same stage-4 output. Prefect's run history is the "when did each stage run, on what data" audit trail the disposition schema assumes exists.

**The 9-hour window is comfortable, not tight,** at this volume — but the pipeline is built to fail loudly and retry per-stage, so a slow or failed stage at 03:00 is visible and recoverable before analysts arrive at 09:00.

---

## 4. Data model

PostgreSQL. Tables in pipeline order.

- **`merchants`** — `merchant_id`, `mcc`, registered address, onboarding date, current `lane`, `lane_changed_at`. Lane history in a child table so Lane B→A crossovers are a queryable timeline.
- **`transactions`** — raw pulled rows. `merchant_id`, `amount`, `occurred_at`, `is_refund`, `terminal_id`, `card_bin`, `geo`. Append-only. **No PAN** — data you never copied is data you cannot leak.
- **`merchant_profiles`** — one row per merchant per day. Rolling 1/7/30/90-day volume, ticket size, refund ratio, velocity, geography spread. The materialised aggregate that keeps queries fast.
- **`peer_groups`** — precomputed stats per (MCC × geography × size-band) cohort, refreshed nightly, so Lane A scores "vs. peers" without a live cross-merchant join.
- **`alerts`** — one row per flagged merchant per run. `lane`, `triggering_detectors` (which fired + sub-scores), `blended_score`, `rank`, and **`feature_snapshot` (JSONB, immutable)** — the exact feature values as of alert time. This is what the case-review divergence panel renders and what the training loop trains on. Immutability is non-negotiable: training on recomputed features leaks the future into the model.
- **`dispositions`** — one per triaged alert. `verdict` (`TRUE_POSITIVE` / `FALSE_POSITIVE` / `INCONCLUSIVE`), `reason_code` (controlled vocabulary), `risk_axis` (`REGULATORY` / `COMMERCIAL` / `BOTH`), `action_taken` (`NONE` / `MONITOR` / `RESERVE` / `HOLD` / `OFFBOARD` / `STR_FILED` — recorded, never executed here), `analyst_id`, `decided_at`, `time_to_decision`, free-text notes, and `signature` (the analyst's digital signature over the decision + feature snapshot, for non-repudiation).
- **`case_events`** — append-only follow-through timeline. One row per update on a confirmed case (`action_taken ≠ NONE`): `event_type`, `note`, `actor`, `occurred_at`. Renders the case history and drives staleness detection.
- **`training_batches`** — a record of each export of dispositions to the training loop: date range, row count, which model version consumed it. The "what data trained the live model" audit trail.

**Why `feature_snapshot` is JSONB, not columns.** The feature set grows — context features (calendar, MCC-relative, geography) arrive in a later phase. A rigid column schema means a migration per feature. Each entry is `{feature_name, merchant_value, baseline_value, deviation}`; the frontend turns it into highlighted rows with no backend logic beyond returning the JSON.

---

## 5. The screens

Five screens. This is where the compliance team lives.

### 5.1 Operations dashboard (landing)

The morning glance. Answers "is the system healthy and what's my day."

- Last night's run: status per stage, when it finished, row counts, any failures (pulled from Prefect).
- Queue depth: how many alerts waiting, split by lane and by score band.
- SLA / accountability: oldest untriaged alert, cases overdue for a follow-through update.
- A small trend strip: false-positive rate and label-completeness over recent weeks (the health of the loop itself).

### 5.2 Alert queue (the worklist)

The ranked list of flagged merchants. The analyst's inbox.

- Sorted by blended risk score, filterable by lane, score band, MCC, date.
- Each row: merchant, score, top contributing reason codes, age. Dense, keyboard-navigable, instant filtering — the Supabase-grade table.
- Click a row → case-review screen.
- Lane A and Lane B alerts are visually distinguished and separately filterable — they are evaluated differently and must never be silently mixed.

### 5.3 Case-review screen (the core)

One merchant, everything needed to render a verdict. The screen the whole platform is built around.

- **Header:** merchant identity, MCC, lane, age of business, current score.
- **Divergence panel:** the heart of it. Renders `feature_snapshot` as a set of rows — for each feature, the merchant's value vs. its baseline (own history + peer group), with the deviation highlighted. This is the "highlighted areas that diverge from baseline that assisted the model's decision to flag" — made concrete. Lane A shows deviation-from-baseline; Lane B shows which static rule thresholds were breached.
- **Evidence in context:** recent transaction history, refund pattern, velocity chart, geography — the raw material behind the flag, not just the summary.
- **Disposition form:** verdict · reason code (dropdown, controlled vocabulary — mandatory, no free-text-only closure) · risk axis · action taken · notes. Submitting writes the `disposition` row and, if `action_taken ≠ NONE`, opens a case on the follow-through board.
- **Digital signature:** submitting a disposition is a signed act. The analyst's decision is signed with their per-analyst signing key, producing a non-repudiable record — they cannot later deny having made this call. The signature covers the verdict, the reason code, the action tag, and the feature snapshot it was based on. This is the accountability backbone in a system where the *action itself* happens elsewhere.

> The disposition form is deliberately not a free-text box. A cleared alert with no structured reason code is a wasted investigation — it teaches the model nothing. Mandatory reason codes are what keep the loop alive.
>
> The action tag records *what the human decided to do* (or that a real-world action was taken externally). The portal never performs it. Every possible tag is captured — including `NONE` and `MONITOR` — because the full distribution of human decisions is exactly what the secondary model learns from.

### 5.4 Case follow-through board (accountability)

Confirmed cases (`action_taken ≠ NONE`) tracked to resolution. This is the accountability mechanism.

- Board / list of open cases, each with its status and last-update time.
- Click a case → its `case_events` timeline: every update, who made it, when ("Jul 20: escalated to legal · Jul 22: merchant responded, evidence attached · Jul 25: STR filed externally").
- **Staleness flags:** a case with no update for N days is surfaced — this is what keeps the compliance team accountable for following through, exactly as requested.
- Each `case_event` is attributable to a named actor and timestamped. Because real actions occur outside the portal, an event records *that* an external action was taken (e.g. "reserve placed in payment system") — the portal is the accountability log, not the execution point. Case events may also be digitally signed where non-repudiation matters.

> **Tipping-off guardrail.** Where an STR path is involved, the platform must not generate any merchant-facing message. This is a criminal-law constraint (HK OSCO s.25A), enforced in the tooling, not left to analyst memory.

### 5.5 Model & pipeline health

The view that proves the loop is working.

- False-positive rate over time (the headline metric — is it falling?).
- Label completeness and inter-analyst agreement (is the training data trustworthy?).
- Training-batch history: what trained the live model, when, champion-challenger results.
- Feature-attribution review surface: which features drive the model, checked for discriminatory proxies before any promotion.

---

## 6. The feedback loop (Stages 3–4)

On a slower cadence than the nightly pipeline:

1. **Package** (nightly/weekly): export new dispositions + their immutable feature snapshots into a `training_batch`.
2. **Train** (monthly): fit a secondary supervised classifier (gradient-boosted trees) on accumulated labelled dispositions. Its job is to learn the *discrepancy* between what the Stage-1 detectors flagged and what humans confirmed — it re-ranks and suppresses; it never raises an alert the detectors didn't.
3. **Evaluate:** champion-challenger against the current scorer. Promote only on measured improvement in precision at equal recall, and only after a fairness/feature-attribution review.
4. **Context features** (later phase): as the model matures, add calendar, MCC-relative, and geography features so a Lunar New Year sales surge is understood as seasonal, not flagged as anomalous.

**Guardrails baked in:**
- Phase 4 (supervised model) is *gated* on label volume and inter-analyst agreement. Slipping is acceptable; forcing a model onto thin or noisy labels is not.
- A small random control set of *unflagged* merchants is periodically sent to human review, to counter survivorship bias — otherwise the model goes confidently blind exactly where the rules already miss.

---

## 7. Error handling & operational concerns

- **Per-stage retry + visibility.** Prefect retries transient failures and shows exactly where a run died. A failed stage at 03:00 is recoverable before 09:00.
- **Idempotent pull.** Re-running the pull for a date must not double-count. The pull is keyed on source transaction identity; re-runs upsert, not append-duplicate.
- **Immutability where it matters.** `transactions`, `alerts.feature_snapshot`, and `case_events` are append-only / write-once. This is both an audit requirement and the guard against label leakage.
- **Auth & audit.** Named analyst logins (local auth — no external IdP). Every analyst holds a per-analyst signing key; every disposition is digitally signed, giving non-repudiation on top of the who + when. Every case event carries a named actor and timestamp. This is the audit trail a regulator asks for — and the signature is what makes it stand up.
- **Backups.** Local, encrypted, tested-restore. Local does not mean un-backed-up.
- **Shadow mode switch.** A config flag runs the pipeline in shadow (scores and queues, but flagged as non-authoritative) vs. live. Ship in shadow; flip to live only after measuring against manual review.

---

## 8. Testing strategy

- **Synthetic data generator** (build early, alongside the pull job): a realistic HK merchant population with normal trading behaviour *and injected typologies with known ground truth* (structuring, bust-out, dormant reactivation, refund abuse), plus festive-calendar seasonality. Because we generated it, we know the right answer — the only time we ever will.
- **Detection unit tests:** each rule and the baseline scorer tested against synthetic cases with known labels.
- **Evaluation harness:** precision/recall against injected ground truth, broken down per lane, false-positive rate over simulated time. This is how we prove the loop measurably reduces false positives without losing true positives.
- **What synthetic data cannot prove:** that any of it works on real merchants — synthetic data contains exactly the patterns we thought to inject. It de-risks the engineering; shadow mode against real data is the real test of detection.
- **End-to-end walking-skeleton test:** a thin run through every stage and every screen with a tiny synthetic dataset, wired first, before any stage is deepened.

---

## 9. Build order

The strategy is a **walking skeleton** — thin end-to-end first, then deepen each stage in the pipeline's phase order. Laying out the platform first is right; but the "gaps" (routing threshold, detection logic, reason-code taxonomy) are the *hard* parts, and the skeleton is where we put them and how we measure them — not a shortcut past them.

1. **Skeleton:** repo, `docker compose`, Postgres schema, FastAPI + generated TS client, React shell with the five screens rendering stub data, one trivial Prefect flow that moves a tiny synthetic dataset through all six stages into one stub alert. Proves the whole thing connects.
2. **Ingestion + profiles + router** (plan Phase 1): real pull, materialised profiles, empirical maturity threshold, synthetic generator + eval harness.
3. **Rules MVP + queue + disposition capture** (plan Phase 2): real typology rules, the case-review screen with the divergence panel, mandatory reason codes. Runs in shadow mode. Labels start accumulating.
4. **Behavioural baselining** (plan Phase 3): Lane A anomaly detection, tuning, control-set sampling.
5. **Secondary model** (plan Phase 4, gated on labels): the suppression classifier, champion-challenger.
6. **Context features + follow-through hardening + graph signals** (plan Phase 5).

Each numbered step after the skeleton is its own spec → plan → build cycle.

---

## 10. Decisions & open questions

**Resolved:**

- **Auth model — decided.** Local authentication (named logins, no external IdP), plus a per-analyst signing key so every case review is digitally signed. Non-repudiation is a first-class requirement, not just an audit log.
- **Action execution — decided.** No real-world action is executed through the portal. Every action tag (`NONE / MONITOR / RESERVE / HOLD / OFFBOARD / STR_FILED`) is *recorded* as signed metadata for accountability and training; the action itself happens in external systems. This removes maker-checker execution gates — there is no execution here to gate — and replaces them with signed decisions and an attributable case-event log.

**Still open (kept in mind, not blocking the build):**

- **Reason-code taxonomy v1:** must be drafted *with* the analysts before Phase 2. Placeholder vocabulary in the plan doc; needs their sign-off.
- **Retention:** how long are raw transactions kept locally? A compliance/legal answer, not an engineering one.
- **Staleness threshold N:** how many days without a case update before it's flagged? A policy input.

These three are carried as configurable values, not hard-coded — so the build proceeds with sensible defaults and the policy answers slot in without a code change.
