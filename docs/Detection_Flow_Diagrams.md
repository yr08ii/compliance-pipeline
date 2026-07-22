# Detection Flow — Diagram Set

*Updated flow charts for the closed-loop compliance pipeline. One master flow, then zoom-ins per stage and per detector family.*

**Date:** 2026-07-22
**Source of truth:** [Detection Layer Design](superpowers/specs/2026-07-22-detection-layer-design.md) · [Platform Design](superpowers/specs/2026-07-20-compliance-platform-design.md) · [Pipeline Plan](Compliance_Monitoring_Pipeline_Plan.md)

These replace the original hand-drawn daily-flow diagram. Section 10 lists exactly what changed and why.

---

## 1. Master flow

The whole system on one page. Automated stages run 00:00–09:00; humans work 09:00–17:00; the loops run on a slower cadence.

```mermaid
flowchart TD
    SRC[("Source database")] --> PULL["Stage 1 · Pull<br/>prior day · immutable raw store"]
    PULL --> PROF["Stage 2 · Profile<br/>rolling windows + peer cohorts"]
    PROF --> ROUTE{"Stage 3 · Route<br/>mature or new?"}

    ROUTE -->|"mature: count AND days over threshold"| LANEA["Lane A · mature merchant"]
    ROUTE -->|"new / low-data"| LANEB["Lane B · cold start"]

    LANEA --> FA["Family A<br/>Robust baselines<br/>own + peer"]
    LANEA --> FB["Family B<br/>Typology ruleset"]
    LANEB --> FBP["Ruleset-prime by MCC<br/>peer-derived caps only<br/>no anomaly model"]

    FC["Family C<br/>Ring detection<br/>portfolio-wide, not per-lane"]

    FA --> SCORE["Stage 5 · Composite score<br/>normalize, blend, carry reasons"]
    FB --> SCORE
    FBP --> SCORE
    FC --> SCORE

    SCORE --> QUEUE["Ranked alert queue"]
    RANK["Sorting model<br/>re-rank only"] -.->|"reorders, never hides"| QUEUE

    QUEUE --> HUMAN["Human triage · per alert"]
    HUMAN --> DISP["Signed disposition<br/>verdict + reason code + action tag"]

    DISP --> L1["Loop 1<br/>train the re-ranker"]
    DISP --> L2["Loop 2<br/>recalibrate detectors"]
    L1 --> RANK
    L2 -.-> FA
    L2 -.-> FB
```

**Read it as:** one fork (mature vs new), three detector families feeding one score, one human decision, two feedback loops back into the machine.

---

## 2. Zoom · Stage 3 routing (the fork)

Why the fork exists: a merchant with two weeks of data has no meaningful "normal," so scoring it against its own baseline produces confident nonsense.

```mermaid
flowchart TD
    M["Merchant with prior-day activity"] --> Q{"Enough history?<br/>transaction count AND elapsed days"}
    Q -->|"yes"| A["Lane A<br/>own baselines + peer baselines + full ruleset"]
    Q -->|"no"| B["Lane B<br/>MCC-derived static caps only"]

    B -.->|"accumulates history"| CROSS{{"Graduation event<br/>Lane B to Lane A"}}
    CROSS -->|"log it · elevated risk"| A

    A --> OUT["Sub-scores to composite"]
    B --> OUT
```

> **Graduation is an attack surface.** A bust-out operator builds "normal" history precisely in order to cross into Lane A, then spikes. Log every crossover and do not soften thresholds the moment a merchant graduates.

> **Lane B uses peer-derived *rules*, not a peer *model*.** Applying MCC-calibrated caps is fair; scoring a legitimately-unusual new merchant against a peer anomaly model over-flags it.

---

## 3. Zoom · Family A — robust baselines

Answers: *"is this number unusual for this merchant, or for its peers?"* Natively explainable — every output is a `feature_snapshot` row the divergence panel renders.

```mermaid
flowchart TD
    subgraph OWN["Own-history baselines · per merchant"]
        A1["Amount<br/>Median + MAD<br/>modified Z-score"]
        A2["Time of day<br/>circular KDE"]
        A3["Card origin<br/>categorical distribution"]
    end

    subgraph PEER["Peer-cohort baselines"]
        P1["MCC amount<br/>IQR upper fence"]
        P2["MCC time<br/>cohort active hours"]
        P3["Subdistrict amount"]
        P4["Subdistrict card origin<br/>foreign-card ratio"]
    end

    OWN --> SNAP["feature_snapshot rows<br/>feature, merchant value,<br/>baseline value, deviation"]
    PEER --> SNAP
    SNAP --> PANEL["Divergence panel<br/>what diverged from baseline"]
```

### 3a. Why robust statistics, not mean and standard deviation

Transaction amounts are heavily right-skewed. A few legitimate large sales inflate the mean and balloon the standard deviation, so a classic z-score baseline drifts and **under-flags**. The median cannot be moved by a handful of outliers.

```
MAD = median( |xᵢ − x̃| )

modified Z:   Mᵢ = 0.6745 · (xᵢ − x̃) / MAD
```

### 3b. The amount detector, including the guards

The two failure modes a naive implementation hides:

```mermaid
flowchart TD
    S["Score today's amount"] --> N{"Enough history?"}
    N -->|"no"| LB["Lane B rules instead"]
    N -->|"yes"| MZ{"Is MAD zero?"}

    MZ -->|"no"| Z["Compute modified Z"]
    MZ -->|"yes"| IQ{"Is IQR also zero?"}
    IQ -->|"no"| SC["Fallback: scaled IQR"]
    IQ -->|"yes · constant price merchant"| RU["Switch to rule:<br/>any change from the constant"]
    SC --> Z

    Z --> BAND{"Score band"}
    BAND -->|"under 2.5"| NORM["Normal"]
    BAND -->|"2.5 to 3.5"| BUMP["Moderate · score bump only"]
    BAND -->|"over 3.5"| FLAG["Outlier · contributes a flag"]
```

> **MAD = 0 is a real trap.** A merchant selling one product at one price has zero dispersion, so the modified Z divides by zero and *every* transaction reads as infinitely anomalous — that merchant floods the queue. Never divide by a zero MAD.

> **Time is circular.** 23:30 and 00:30 are 60 minutes apart, not 23 hours. A linear time baseline is wrong at the midnight boundary; use a circular kernel.

### 3c. Peer cohort fallback

Cohorts need enough members to form a distribution:

```mermaid
flowchart LR
    C1["MCC x subdistrict"] -->|"cohort too small"| C2["MCC only"]
    C2 -->|"still too small"| C3["Network-wide"]
```

---

## 4. Zoom · Family B — typology ruleset

Answers: *"does this match a known laundering pattern?"* These are **not** statistical deviations — each transaction can look unremarkable on its own. This is why the ruleset carries more weight than its box size suggests.

```mermaid
flowchart TD
    T["Prior-day activity + history"] --> R1["Structuring / smurfing<br/>amounts clustered just under a threshold"]
    T --> R2["Refund / credit abuse<br/>high refund ratio; refund to a different card"]
    T --> R3["Bust-out<br/>build-up, spike, refund surge or pull"]
    T --> R4["Dormant reactivation<br/>long silence then velocity surge"]
    T --> R5["Rapid movement<br/>in and straight out, no resting balance"]
    T --> R6["Declared vs actual mismatch<br/>MCC / business nature vs real pattern"]
    T --> R7["Decline-ratio spike<br/>high share of failed authorizations"]

    R1 --> H["Rule hit<br/>reason code + sub-score"]
    R2 --> H
    R3 --> H
    R4 --> H
    R5 --> H
    R6 --> H
    R7 --> H
    H --> SC["Composite score"]
```

R6 is the *transaction-laundering* signature — a registered business fronting a different, hidden one. R7 is the in-scope, merchant-side read of card testing (we measure the merchant's decline rate; we do not chase stolen cards).

---

## 5. Zoom · Family C — ring detection

Answers: *"are these merchants coordinating?"* Two sub-layers with very different cost.

```mermaid
flowchart TD
    subgraph P["5.1 Merchant-identity rings · BUILD FIRST"]
        M1["Shared hashed_br_number"]
        M2["Shared hashed_merchant_address"]
        M3["Shared hashed_merchant_name"]
        M4["agent_id concentration<br/>one agent's book runs hot"]
        M1 --> RING["Shell-merchant / same-owner ring"]
        M2 --> RING
        M3 --> RING
        M4 --> RING
    end

    subgraph S["5.2 Card-linkage · GATED, BUILD LAST"]
        C1["hashed_pan across merchants<br/>card swarming"]
        C2["Cross-merchant structuring"]
        C1 --> CARD["Card-linkage signal"]
        C2 --> CARD
    end

    subgraph O["5.3 Out of scope · cardholder fraud"]
        O1["Impossible geo-velocity"]
        O2["BIN / card testing"]
    end

    RING --> SCORE["Composite score"]
    CARD -.->|"only after HMAC key +<br/>PCI and PDPO sign-off"| SCORE
    O1 -.-> FRAUD["Fraud team<br/>integration point, not built here"]
    O2 -.-> FRAUD
```

**Why merchant-identity rings come first:**

| | Merchant-identity (5.1) | Card-linkage (5.2) |
|---|---|---|
| Detects | shell merchants, same beneficial owner | cards swarming across merchants |
| Method | **equality join** — never un-hashed | equality join on cardholder identity |
| Reversibility issue | **irrelevant** — we never reverse | hash is brute-forceable, must protect |
| Privacy cost | low — links merchants to each other | high — maps where each cardholder shops |
| In scope | yes, directly | yes, but gated |

> The hashed PAN is 1:1 and therefore unsalted, and an unsalted PAN hash is reversible (fix the BIN and Luhn digit → ~10⁹ candidates). Treat it as sensitive data, prefer a keyed HMAC, keep it out of the analyst UI. See open questions Q2.

---

## 6. Zoom · Stage 5 composite scoring

How three families become one ranked queue *without* losing the reasons.

```mermaid
flowchart LR
    A["Family A<br/>per-feature deviations"] --> N["Normalize each to 0-1"]
    B["Family B<br/>rule hits"] --> N
    C["Family C<br/>ring signals"] --> N

    N --> BLEND["Blend<br/>weighted, or noisy-OR so several<br/>weak signals can still rank up"]
    BLEND --> BS["blended_score<br/>sorts the queue"]

    N --> RSN["triggering_detectors<br/>+ feature_snapshot"]
    RSN --> PANEL["Divergence panel<br/>explains every alert"]

    IF["Isolation Forest<br/>deferred"] -.->|"only with per-feature attribution"| BLEND
```

> **The score sorts; the reasons explain.** A single opaque number with no decomposition fails both the analyst and the regulator. Isolation Forest stays a deferred secondary signal precisely because its raw output has no per-feature reason.

---

## 7. Zoom · human triage and further check

The 09:00–17:00 loop. Expands the "Further Check" branch of the original diagram.

```mermaid
flowchart TD
    Q["Alert from ranked queue"] --> REV["Analyst investigates<br/>divergence panel + evidence"]
    REV --> V{"Verdict"}

    V -->|"false positive"| CL1["Clear"]
    V -->|"inconclusive"| INC["Inconclusive<br/>a real outcome, not forced either way"]
    V -->|"further check"| FC["Contact merchant"]

    FC --> R1{"Merchant response"}
    R1 -->|"satisfactory"| CL2["Clear"]
    R1 -->|"unsatisfactory"| VER["Further verification<br/>interview, documents"]

    VER --> R2{"Outcome"}
    R2 -->|"satisfactory"| CL3["Clear"]
    R2 -->|"risk confirmed"| ACT["Record action tag<br/>MONITOR / RESERVE / HOLD /<br/>OFFBOARD / STR_FILED"]

    CL1 --> D["Push case info<br/>verdict + reason code + risk axis<br/>+ action tag + digital signature"]
    INC --> D
    CL2 --> D
    CL3 --> D
    ACT --> D
    ACT --> BOARD["Case follow-through board<br/>timeline + staleness flag"]

    D --> TS["Training store"]
```

> **Actions are recorded, never executed here.** The portal is the system of record; reserves, holds, offboarding and STR filing happen in the real payment and filing systems.

> **Tipping-off guardrail.** Where an STR is involved, the platform must generate no merchant-facing message. An automated "your account is under review" email is a compliance incident.

> **Every disposition is signed.** Non-repudiation is the accountability backbone in a system whose actions occur elsewhere.

---

## 8. Zoom · the two feedback loops

The original diagram drew one loop. There are two, and they do different jobs.

```mermaid
flowchart TD
    D["Signed dispositions"] --> BATCH["training_batches<br/>with immutable as-of-alert snapshots"]
    CTRL["Random control set<br/>sampled unflagged merchants"] --> BATCH

    BATCH --> L1["Loop 1 · supervised re-ranker"]
    BATCH --> L2["Loop 2 · threshold recalibration"]

    L1 --> G{"Gate:<br/>label volume AND<br/>inter-analyst agreement"}
    G -->|"not met"| WAIT["Wait · rules carry the load<br/>slipping is acceptable"]
    G -->|"met"| CC["Champion-challenger<br/>+ fairness / attribution review"]
    CC -->|"promote only on measured lift"| RR["Re-rank the queue<br/>NEVER suppress"]

    L2 --> TUNE["Retune Family A bands<br/>and Family B thresholds"]
    TUNE -.-> DET["Detectors"]
```

- **Loop 1** trains the sorting model — it reorders by likelihood of being a genuine further-check case so those are worked first. It never hides an alert.
- **Loop 2** is the arrow the original diagram was missing: dispositions should also retune the detectors themselves. A merchant repeatedly cleared as "seasonal" should have its own baseline widened.
- **The control set** exists because we only ever learn outcomes for alerts we raised. Without deliberately sampling *unflagged* merchants, both loops go confidently blind exactly where the detectors already miss.

---

## 9. Data → detector map

Which source columns feed which detector.

| Detector | Columns |
|---|---|
| Amount baseline (own + MCC/subdistrict peer) | `total_amount`, `net_amount`, `mcc`, `merchant_subdistrict` |
| Time baseline | `hkt_transaction_time` |
| Card-origin baseline | `card_issuing_country`, `card_origin`, `card_issuing_bank` |
| Peer cohorts | `mcc`, `merchant_subdistrict`, `merchant_district`, `merchant_area`, `city` |
| Structuring | `total_amount`, `hkt_transaction_time` |
| Refund abuse | `transaction_status` / `net_amount` sign *(encoding to confirm — Q1)*, `hashed_pan` |
| Declared vs actual mismatch | `mcc`, `business_nature`, `ownership_or_business_type`, `business_plan` |
| Decline-ratio spike | `transaction_status` |
| Merchant-identity rings | `hashed_br_number`, `hashed_merchant_address`, `hashed_merchant_name`, `agent_id` |
| Card-linkage rings *(gated)* | `hashed_pan` |
| Merchant state | `merchant_status`, `merchant_id` |

Not used for detection: `masked_pan` (display only — never an identifier), `payment_gateway`, `currency` (until multi-currency rules exist).

---

## 10. What changed from the original diagram

| Change | Why |
|---|---|
| Baselines split into **three named families** | The original "+ Ruleset" box was doing hidden heavy lifting. Baselines catch unusual *numbers*; rules catch *patterns* that look individually normal. Different jobs, different failure modes. |
| Added **Family C ring detection** as a portfolio-wide layer | Runs across merchants, not inside a merchant's lane — the per-merchant flow structurally cannot see coordination. |
| Ring detection leads with **merchant-identity**, not card | `hashed_br_number` / address / name give in-scope ring detection by equality join, with none of the cardholder-linkage privacy cost. Card layer demoted to last and gated. |
| Added **`agent_id`** as a risk dimension | One agent whose whole book runs hot is a gatekeeper-of-the-gatekeeper signal no per-merchant view can see. |
| Added the **second feedback loop** | The original drew labels → re-ranker only. Labels should also recalibrate detector thresholds. |
| Made the sorting model explicitly **re-rank only** | Confirmed decision. It reorders; it never hides an alert from a human. |
| Added **guards** (MAD = 0, min-history, circular time, cohort fallback) | Each is a silent failure that would either flood the queue or corrupt a baseline. |
| Added **inconclusive** as a first-class verdict | Forcing every case into true/false positive corrupts the training labels. |
| Marked **cardholder-fraud checks out of scope** | Impossible geo-velocity and BIN testing detect stolen *cards*, not bad *merchants* — different objective, different owner. |
| Dropped `terminal_id` | Not in the real schema. Cross-terminal checks are really cross-*merchant* or cross-*agent*. |
