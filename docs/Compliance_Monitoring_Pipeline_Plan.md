# Closed-Loop Compliance Monitoring Pipeline

## Architecture Specification & Operational Blueprint

*Adaptive merchant risk monitoring for a Hong Kong payment facilitator*

**Prepared for:** Engineering & Compliance Leadership · The Payment Cards Group / Yedpay
**Date:** July 2026 · Working Draft v2

---

## 1. Executive Summary

We operate as a payment facilitator: we sponsor merchants under a master account, we put terminals and SoftPOS apps in their hands, and we settle funds between them and cardholders' banks. That model makes us — not Visa, not Mastercard, not the issuing bank — the party held accountable when a merchant on our platform launders money, runs a bust-out, or fronts an illegal business. Section 2 sets out why that liability lands here, because the rest of this document only makes sense once that is accepted.

Today our compliance team reads transaction data by hand. That does not scale, reacts slowly, and produces different answers depending on who is reading. The obvious fix — buy a rules engine and turn it on — trades one problem for a worse one: static thresholds bury analysts in false positives, and every hour spent clearing a legitimate Lunar New Year sales surge is an hour not spent on an actual bust-out.

**This plan builds something different: a pipeline where human compliance review is not the end of the process but the training signal for it.** Every analyst decision — cleared or escalated, and *why* — is captured as a labeled example and fed back to the data science side. The system's false-positive rate is therefore designed to fall month over month as a direct function of work the compliance team is already doing. That closed loop is the core of the design and the reason this is worth building rather than buying.

Two specific problems the architecture solves head-on:

- **The cold-start problem.** A merchant onboarded yesterday has no behavioral baseline, so anomaly detection against "their normal" is meaningless. We route those merchants down a separate lane with sensitive static rules rather than pretending a model can score them.
- **The alert-fatigue problem.** Instead of one universal threshold, mature merchants are scored against their own history and their peer group, and — over time — against contextual features (festive calendar, MCC, seasonality, geography) that explain why a surge is legitimate.

### What this delivers

A four-part package, not a document:

1. **Technical architecture blueprint** — the data flow from terminal to case queue to training set.
2. **Labeled data schema** — the precise contract by which human compliance notes become ML training data. This is the piece that makes the loop real.
3. **Daily standard operating procedure** — who does what in each 24-hour cycle.
4. **A working prototype** — runnable code implementing the routing fork, both detection lanes, disposition capture, and the training handoff, exercised against synthetic transaction data before it ever touches production.

> **Feasibility verdict.** The component techniques are mature and low-risk: rules engines, unsupervised baselining, gradient-boosted classifiers, case management. Nothing here is research-grade. The hard parts are data quality, the discipline of the labeling contract, and the legal rules on holding funds. The genuine novelty is not any single algorithm — it is wiring the human review loop directly into model training and being rigorous about it.

---

## 2. Why This Liability Is Ours

This section exists because the pipeline's budget and its design constraints both follow from it. The common assumption is that if an illegal payment clears, the fault lies with Visa or the issuing bank. In a PayFac model, it does not.

| Mechanism | Why it lands on us |
|---|---|
| **The gatekeeper rule** | The networks and the regulator do not onboard every street vendor in Hong Kong; they delegate that to us. Because we onboard the merchant, we are the designated gatekeeper. A merchant laundering through a Yedpay terminal is a failure of *our* KYC/AML duty. Regulators fine the gatekeeper. |
| **We hold the financial bag** | Cardholder disputes → issuer chargeback → network claws back from the acquiring platform → that is us. If the illegal merchant has already withdrawn and vanished, the deficit sits on our balance sheet. A wave of fraud is an existential cash event, not a compliance line item. |
| **Network licenses are revocable** | Routing through Visa/Mastercard/UnionPay requires maintaining PCI-DSS and PCI-PTS standing. Persistent compliance failure gives the networks the absolute right to revoke access. Terminals that cannot accept Visa are not a business. |
| **We ship hardware and software** | Because we build terminals and SoftPOS, our integrity obligation extends past money movement to device state — firmware, cryptographic key integrity, tamper detection. Processing through a compromised terminal is a data-breach exposure, not just a bad transaction. |

The shape of the rule: *the networks and banks built the highways; we run the toll booth. If we wave a criminal through, we are the ones penalized.*

**Design consequence.** Two of these — chargeback exposure and terminal integrity — mean this pipeline cannot be scoped as pure regulatory AML. It must also serve commercial risk. See Section 3.

---

## 3. Problem Definition & Scope

The request: use the transaction data already flowing through the platform to automatically identify merchants engaged in illegal or suspicious activity, act quickly (up to holding settlement funds pending review), and be accurate enough that legitimate merchants are not disrupted.

### The reframe that matters

"Spot suspicious merchants" is two jobs wearing one coat:

1. **Regulatory AML monitoring** — legally mandated obligations under the AMLO, STR filing, recordkeeping, audit expectations. Success = we detected and reported what we were obliged to.
2. **Commercial risk management** — protecting ourselves from chargebacks, network fines, and bust-out losses. Success = money not lost.

They share the plumbing but have different success metrics, different stakeholders, and different tuning. A transaction can be commercially fine and regulatorily reportable, or vice versa. **Build the plumbing once; keep the two objectives explicitly separate so each is tuned correctly and neither silently degrades the other.** In the schema (Section 6) this appears as two distinct disposition axes rather than one "is it bad" flag.

### In scope

- **Merchant-level risk monitoring** — continuous scoring of registered merchants on transaction behavior.
- **Transaction-level detection** — velocity, structuring, refund abuse, anomaly detection, feeding the merchant score.
- **Adaptive routing** — separating merchants that have a usable baseline from those that do not (Section 5.1).
- **Case management with structured disposition capture** — the analyst queue, designed as the labeling pipeline.
- **The training feedback loop** — the packaged handoff from compliance to data science.
- **A funds-hold / reserve workflow** — controlled, auditable, human-approved, with the guardrails in Section 8.
- **Context-aware feature engineering** — the roadmap to scoring on calendar, MCC, geography and seasonality (Section 5.4).

### Out of scope

- **KYC / merchant onboarding due diligence** — a separate workstream. This pipeline *consumes* onboarding data (and depends heavily on its quality — see Section 10); it does not replace onboarding.
- **Sanctions / watchlist screening** — a distinct control that likely already exists. Note the integration point; do not rebuild.
- **Cardholder-side fraud (stolen card detection)** — technically overlapping, different objective. Keep the merchant-integrity focus.
- **Terminal firmware integrity monitoring** — real and important (Section 2), but a hardware-security workstream. This pipeline should be able to *consume* a terminal-integrity signal as a feature; it does not produce one.

---

## 4. Feasibility Review

Transaction monitoring is a standard, well-documented AML control, and the shift from pure rules to rules-plus-behavioral-scoring is established practice, with reported false-positive reductions of 50%+ when ML baselines are layered onto static thresholds. The risk is not "will it work" but "what makes it hard here."

| Dimension | Verdict | Notes |
|---|---|---|
| Technical approach | Low risk | Mature patterns. Rules + baselines + case management is a solved shape. |
| Data availability | Medium risk | We have transaction data. Quality, completeness, and merchant metadata (especially accurate MCC) drive everything. |
| The labeling contract | **Medium–high risk** | The whole closed loop depends on analysts consistently capturing structured reason codes. This is an operational-discipline risk, not a technical one — and it is the single biggest threat to the design. |
| Cold-start coverage | Medium risk | New merchants are both the highest-risk cohort and the one we can least model. The Lane B rules must carry real weight. |
| Precision | Medium risk | Achievable, but requires tuning to *our* merchant mix and continuous feedback. Make-or-break for merchant experience. |
| Regulatory / legal | Medium risk | Holding funds and filing STRs have hard legal rules under HK law. Needs compliance + legal sign-off, not engineering judgment. |
| Analyst adoption | Medium risk | The loop only closes if analysts trust the queue *and* fill in reason codes. Explainability and workflow fit matter as much as model accuracy. |

### The honest hard parts

- **Cold-start labeling.** We cannot train supervised models on day one — there is no clean history of confirmed-bad vs confirmed-good merchants. Handled by starting rules-first and treating every analyst decision as a label from the first day of Phase 2.
- **The labeling contract is a people problem.** If analysts click "cleared" without a reason code because the queue is busy, the loop silently breaks and we will not notice for months. Mitigated by making reason codes mandatory, keeping the taxonomy short, and monitoring label completeness as a first-class operational metric.
- **Precision vs. recall tension.** Loose thresholds disrupt good merchants; tight ones miss bad actors and carry liability. A tuning discipline, not a setting.
- **Concept drift.** Launderers adapt; rules decay. Requires periodic re-tuning and champion/challenger comparison.
- **Explainability is mandatory.** Regulators and analysts both need to know *why* a merchant was flagged. This rules out black-box-only approaches and favors models traceable to specific behaviors.
- **Survivorship bias in the training data.** We only ever learn the outcome of alerts we generated. Merchants the system never flagged produce no labels, so the model can become confidently blind in exactly the areas the rules already miss. Mitigated by sampling a small random control set of unflagged merchants for periodic human review — deliberately spending analyst time to buy unbiased labels.

---

## 5. The Closed-Loop Architecture

The pipeline is a four-stage cycle. Stages 1–2 run daily; Stages 3–4 run on a slower training cadence. The loop is the design — each stage exists to feed the next.

```
                        [ Raw Daily Transaction Data ]
                                      |
                        STAGE 1 — Adaptive Routing
                                      |
                      +---------------+---------------+
                      |                               |
           [ Mature Merchants ]            [ New / Low-Data Merchants ]
        (ML Baseline + Rule Engine)          (Static Rule Engine Only)
                      |                               |
                      +---------------+---------------+
                                      |
                              [ Ranked Alert Queue ]
                                      |
                    STAGE 2 — Human Triage & Labeling
                        (verdict + reason code metadata)
                                      |
                    STAGE 3 — Training Feedback Loop
                     (secondary supervised classifier)
                                      |
                    STAGE 4 — Context-Aware Scoring
                                      |
                                      +---> feeds back into Stage 1
```

### 5.1 Stage 1 — Ingestion & Adaptive Routing (the data fork)

Each night, transaction data from all active terminals is processed. The pipeline splits merchants into two parallel lanes based on whether they have enough history to support a meaningful behavioral baseline.

**Lane A — Mature merchant engine.** Merchants past the maturity threshold get unsupervised anomaly detection against their own rolling baseline *and* their peer group, running alongside the standard rule set. Flags deviation from *their specific* normal.

**Lane B — New merchant engine.** Merchants without a baseline bypass the ML layer entirely — scoring them against a baseline built from two weeks of data produces confident nonsense. They are subject instead to sensitive static rules: hard caps on velocity, single-ticket ceilings, refund-ratio limits, and tighter thresholds than Lane A would use.

**The maturity threshold is a policy decision, not a constant.** Both transaction count and elapsed time matter (a merchant with 5,000 transactions across 8 days has volume but no seasonal context). Phase 1 establishes it empirically by measuring baseline stability across our actual merchant base rather than guessing a number. The crossover must be monitored: a merchant graduating from Lane B to Lane A is a moment of elevated risk, because a bust-out operator's whole strategy is to build "normal" history precisely in order to graduate.

### 5.2 Stage 2 — Human Triage & Data Labeling (the operations loop)

Anything flagged by either lane goes to the analyst dashboard. **No alert results in an automatic account freeze.** Analysts investigate, contact the merchant if warranted, and render a verdict.

The critical shift from a conventional queue: **the analyst does not just clear the alert — they attach a structured reason code.** Not "cleared" but `CLEARED / SEASONAL_PROMOTION / merchant verified Black Friday campaign`. That metadata tag is the entire point. A cleared alert with no reason code is a wasted investigation; it tells the model nothing.

This is why case management cannot be treated as a generic bought workflow with a free-text notes field. The disposition schema (Section 6) is a product requirement.

### 5.3 Stage 3 — The Training Feedback Loop

Labeled dispositions are packaged and pushed to data science as an optimized training dataset on a regular cadence.

A **secondary supervised classifier** is trained on this data. Its job is specifically to learn the *discrepancy* between what the Stage 1 detectors thought was suspicious and what humans proved was legitimate. It sits downstream of the primary detectors, re-ranking and suppressing their output rather than replacing them. Gradient-boosted trees are the workhorse: strong on tabular data, explainable via feature attribution.

**This layer is gated on label volume and only ever promoted on measured improvement** against the Stage 1 blend, via champion-challenger. It never gets to *raise* an alert the detectors did not raise — it earns the right to deprioritize, and only that. Keeping it strictly subordinate is what makes it safe to iterate on.

### 5.4 Stage 4 — Context-Aware Feature Engineering

As the secondary model matures, it graduates from raw financial features to contextual metadata. This is the long-term differentiator — **context-aware risk scoring**:

| Feature category | Data points |
|---|---|
| **Temporal & seasonality** | Festive calendar (Lunar New Year, Mid-Autumn, Christmas, Golden Week inbound tourism), day-of-week, time-of-day anomalies, paydays. |
| **Merchant demographics** | MCC / industry, age of business, location density, business size band. |
| **Geographic routing** | Terminal IP/geolocation vs. registered physical address; card BIN geography vs. merchant location; district-level norms. |
| **Peer-relative** | Behavior vs. same-MCC, same-district, same-size cohort rather than vs. platform-wide averages. |

The point in plain terms: a jewelry shop in Mong Kok tripling volume the week before Lunar New Year is not an anomaly, and a system that cannot represent that will keep flagging it every single year. Context is what lets us protect honest merchants from festive-season lockouts while isolating actual crime.

> **Fairness caution.** Contextual features are also where discriminatory proxies enter. District and business-demographic features can encode bias against particular merchant communities, producing a system that systematically over-flags certain neighborhoods. Feature attribution review is a compliance checkpoint before promotion, not a data-science nicety.

### 5.5 Component view

| Stage | Responsibility | Build notes |
|---|---|---|
| Ingestion | Pull transaction, refund, settlement, terminal, and merchant-metadata events. | Batch to start; nightly is fine for merchant-level risk. Immutable raw store for audit. Design for streaming later. |
| Profile / feature store | Rolling merchant profiles + peer-group stats. | The heart of the system. 1/7/30/90-day windows: volume, ticket, refund ratio, geography, velocity. |
| Router | Assign each merchant to Lane A or Lane B. | Threshold is configurable policy. Log every lane assignment and crossover. |
| Detection layer | Rules + anomaly baselines in parallel. | Each detector emits a sub-score **and a reason code**. Modular — add/retire independently. |
| Scoring | Blend sub-scores into one merchant risk score + ranked alert. | Weighted blend initially, learned weights later. Every alert carries contributing reasons. |
| Case management | Review, evidence, verdict, **structured disposition**. | This IS the labeling pipeline. Schema is a requirement, not a config. |
| Action & controls | Reserve/hold workflow, STR support, offboarding. | Maker-checker, full audit trail, legal guardrails (Section 8). Never fully automated. |
| Training loop | Package dispositions → train → evaluate → champion-challenger. | Track precision/recall over time. Promote only on measured gains. |

### Design principles

- **The case system is the training set.** Disposition capture is both the analyst workflow and the source of every future label. Design it first, not last.
- **Rules and ML coexist.** Rules for known, explainable, regulator-facing patterns; ML for subtle combinations and for suppressing noise. ML never replaces the rules layer.
- **Never model what you cannot baseline.** The Lane A/B fork is the expression of this. Silence beats a confident wrong score.
- **Every alert is explainable.** No merchant is flagged without a human-readable reason — for analysts, for merchant appeals, and for regulators.
- **Human-in-the-loop for consequences.** Scoring is automated. Holding a merchant's money is not.
- **Start batch, design for streaming.** Merchant-level risk rarely needs sub-second latency. Nightly is a fine MVP; keep interfaces clean.

---

## 6. The Labeled Data Schema (Deliverable 2)

The contract between compliance and data science. If this is weak, the loop does not close and the whole design degrades into an ordinary rules engine with extra steps.

Every disposition record must carry:

| Field | Purpose |
|---|---|
| `alert_id`, `merchant_id`, `alert_timestamp` | Join keys back to the triggering features. |
| `lane` (A/B) | Which engine raised it. Lane A and Lane B alerts must be evaluated separately — mixing them corrupts both models. |
| `triggering_detectors[]` | Which rules/detectors fired, with their sub-scores, as of alert time. |
| `feature_snapshot_ref` | **Pointer to the feature values as they were at alert time.** Non-negotiable: training on features recomputed later leaks the future into the model. |
| `verdict` | `TRUE_POSITIVE` / `FALSE_POSITIVE` / `INCONCLUSIVE`. Inconclusive is a real, common outcome and must not be silently coerced into either bucket. |
| `reason_code` | Controlled vocabulary — the core signal. E.g. `SEASONAL_PROMOTION`, `VERIFIED_BUSINESS_EXPANSION`, `MCC_MISMATCH`, `STRUCTURING_CONFIRMED`, `REFUND_ABUSE`, `MERCHANT_UNREACHABLE`. Short taxonomy; extend deliberately. |
| `risk_axis` | `REGULATORY` / `COMMERCIAL` / `BOTH` — the Section 3 split made concrete. |
| `analyst_notes` | Free text. For humans and audit; never the primary training signal. |
| `action_taken` | `NONE` / `MONITOR` / `RESERVE` / `HOLD` / `OFFBOARD` / `STR_FILED`. |
| `analyst_id`, `decided_at`, `time_to_decision` | Audit trail, plus inter-analyst consistency measurement. |

**Two things this schema must protect against.**

*Label leakage.* The feature snapshot must be immutable and as-of alert time. This is the most common way pipelines of this shape quietly produce models that look excellent in evaluation and fail in production.

*Analyst disagreement.* Two analysts will label the same pattern differently. Track inter-analyst agreement from day one; a low agreement rate on a reason code means the taxonomy is ambiguous and the model is being taught noise. Fix the vocabulary, not the model.

---

## 7. Detection Typologies

The detection layer runs several complementary techniques in parallel, combining their outputs into one merchant risk score. No single technique is sufficient.

### 7.1 Rule / typology detection (both lanes)

| Typology | What it looks like | Signal |
|---|---|---|
| Transaction laundering | Registered merchant processing sales for an unregistered/illegal business behind it. | Declared MCC vs. actual transaction pattern mismatch; sudden product-mix change. |
| Bust-out merchant | Builds normal history, spikes volume, disappears with funds. | Abrupt volume/ticket increase, then refund surge or settlement pull. **Watch the Lane B→A crossover.** |
| Structuring / smurfing | Large sum deliberately split into many transactions kept under review thresholds. | Clustering just below round thresholds; velocity of near-threshold transactions. |
| Rapid movement of funds | Money lands and is instantly routed out to unrelated counterparties; no resting balance. | Settlement-out velocity; layering patterns. |
| Dormant reactivation | Long-inactive merchant suddenly processing high-value volume. | Inactivity gap followed by velocity surge. |
| Refund / credit abuse | Refunds used to move value rather than genuine returns. | High refund ratio; refunds to cards other than the original charge. |
| Velocity anomalies | Volume, count, or ticket size inconsistent with profile. | Deviation from rolling baseline (Lane A) or hard cap breach (Lane B). |
| Geographic / corridor risk | Concentration in high-risk regions, or terminal/registration mismatch. | Card BIN geography vs. merchant location; terminal geolocation vs. registered address. |

Rules are transparent, fast to ship, and easy to defend to a regulator. Their weakness: they only catch patterns already imagined, and static thresholds generate false positives. Hence the starting layer, not the whole system.

### 7.2 Behavioral baselining (Lane A only)

Rolling per-merchant and per-peer-group profiles; score deviation from *own* and *peer* normal. Unsupervised, so it works before labels exist, and it catches novel patterns rules miss. The bridge between rules MVP and supervised ML.

### 7.3 Supervised suppression model (Stage 3)

Trained on accumulated dispositions to learn which signal *combinations* actually correlate with confirmed-bad outcomes. Re-ranks and suppresses; does not originate alerts. This is where the reported 50%+ false-positive reductions come from.

### 7.4 Network / graph signals (later)

Laundering often spans merchants sharing beneficial owners, bank accounts, devices, or settlement destinations. Graph analysis surfaces these rings. High value, higher complexity — defer until the core loop is proven.

---

## 8. The "Hold Their Funds" Reality (Hong Kong)

The instinct to freeze a suspicious merchant's funds pending verification is operationally sound and legally loaded. Two things must stay separate: our ability to **hold settlement funds** (commercial/contractual) and our obligations under **anti-money-laundering law** (regulatory). Engineering builds the workflow; compliance and legal own the policy driving it.

> **Jurisdiction note.** Yedpay operates in Hong Kong. The governing framework is the **AMLO (Cap. 615)**, with suspicious transaction reports filed to the **Joint Financial Intelligence Unit (JFIU)** — *not* US FinCEN SARs. Tipping-off is an offence under **OSCO (Cap. 455) s.25A** and **DTROP (Cap. 405)**. Any prior draft citing FinCEN timelines and SAR thresholds was importing the wrong regime; the SFC/HKMA guidance applicable to our licence category governs instead. **Confirm the exact obligations with counsel — this document is general guidance, not legal advice.**

| Consideration | What it means for the build |
|---|---|
| Holding funds is contractual, not arbitrary | Our merchant agreement must already grant the right to place reserves / hold settlement on suspicion. The system enforces a policy; it does not create the legal right. **Verify the contract language exists before Phase 5.** |
| STR obligation is separate from holding funds | Reporting suspicion to the JFIU is a distinct duty from any commercial action. The pipeline must support STR workflows, not only fund holds. A merchant can be reported and left running. |
| Do not tip off | Where an STR is filed, disclosing that fact is a criminal offence. The action workflow and *all* merchant-facing messaging must respect this. Build the distinction into the tooling — an automated "your account is under review" email is a compliance incident. |
| Holding vs. closing vs. keeping open | Sometimes accounts are kept open at law-enforcement request. This is a compliance decision with its own guidance — never hard-code it. Provide options; let policy choose. |
| Precision protects us legally too | Wrongly holding a legitimate merchant's funds creates commercial and potentially legal exposure. Precision is a risk control, not a UX nicety. |

> **Guardrail.** Automate detection and prioritization freely. Gate every fund-affecting action behind human approval, a documented reason, and an audit trail. Compliance and legal are Phase 0 inputs to the architecture, not paperwork bolted on at the end.

---

## 9. Daily Standard Operating Procedure (Deliverable 3)

The 24-hour cycle, once the system is live.

| Time | Actor | Action |
|---|---|---|
| 00:00 | Pipeline | Ingest prior day's transaction, refund, settlement, terminal events into the raw store. |
| 00:30 | Pipeline | Recompute rolling merchant profiles and peer-group statistics. |
| 01:00 | Router | Assign each active merchant to Lane A or Lane B; log assignments and any crossovers. |
| 01:15 | Detection | Lane A: anomaly baselines + rules. Lane B: static rules only. Each detector emits sub-score + reason code. |
| 02:00 | Scoring | Blend into merchant risk score; **snapshot the feature vector**; generate ranked queue. |
| 02:30 | Suppression model *(post-Phase 4)* | Re-rank / deprioritize. Never originates alerts. |
| 09:00 | Analysts | Work the queue top-down. Investigate, contact merchants where warranted. |
| Per alert | Analysts | Record verdict + reason code + risk axis + action. **Mandatory — no free-text-only closure.** |
| Per fund action | Analyst + approver | Maker-checker on any reserve/hold/offboard. Documented reason, audit entry. |
| 17:00 | Compliance lead | Review the day's escalations; STR decisions; sample-check label quality. |
| 18:00 | Pipeline | Package the day's dispositions into the training store. |
| Weekly | Data science | Label-completeness and inter-analyst-agreement report. Flag taxonomy ambiguity. |
| Monthly | Data science | Retrain challenger; evaluate vs. champion; promote only on measured improvement + fairness review. |
| Quarterly | Compliance + DS | Threshold re-tuning; drift review; random control-set sampling for unbiased labels. |

---

## 10. Prototype Scope (Deliverable 4)

Build against **synthetic data first**. This is deliberate — proactive synthetic monitoring means injecting known financial-crime patterns and verifying the pipeline catches them, before production data is involved. We know the ground truth because we generated it, which is the only time we ever will.

| Component | Prototype scope |
|---|---|
| Synthetic data generator | HK merchant population across realistic MCC mix; normal trading behavior; **injected typologies with known ground truth** (structuring, bust-out, dormant reactivation, refund abuse); festive-calendar seasonality so we can prove context-awareness matters. |
| Profile store | Rolling windows per merchant + peer group. |
| Router | Lane A/B assignment; configurable maturity threshold; crossover logging. |
| Rule engine | Section 7.1 typologies, tunable thresholds, reason codes, separate Lane A/B calibration. |
| Anomaly baseline | Unsupervised deviation scoring, Lane A only. |
| Scoring & queue | Blended score, feature snapshot, ranked output with explanations. |
| Disposition capture | Section 6 schema end-to-end, including snapshot immutability. |
| Training handoff | Packaging job → training set → challenger model → champion-challenger evaluation harness. |
| Evaluation harness | Precision/recall against injected ground truth; per-lane breakdown; false-positive rate over simulated time. |

**What the prototype proves:** that the loop closes — that injected crime is caught, that legitimate seasonal surges are cleared, and that feeding dispositions back measurably reduces false positives without losing true positives. That is the claim the whole plan rests on, and it should be tested in a sandbox long before it is tested on a real merchant's livelihood.

**What the prototype cannot prove:** that any of it works on real merchants. Synthetic data contains exactly the patterns we thought to inject, which makes the evaluation results a lower bound on difficulty, not an estimate of production performance. It de-risks the engineering; it does not validate the detection. Shadow mode against production data (Phase 2) is the real test.

---

## 11. Phased Plan

Front-loaded value: a rules-based system that beats manual review ships early, and each later phase adds precision on a working base. Timeline assumes the team in Section 12.

### Phase 0 — Foundations & alignment (2–3 weeks)

- Define success metrics: target precision/recall per lane, alert volume per analyst, time-to-detection, **label completeness**.
- Compliance + legal workshop: **confirm the HK regulatory position** (AMLO/JFIU obligations for our licence category), merchant-agreement hold rights, tipping-off constraints, approval chain for fund actions.
- Data audit: inventory transaction/refund/settlement/terminal/merchant fields; assess quality and gaps — **especially MCC accuracy**, on which the peer-group and context layers depend entirely.
- **Draft the disposition taxonomy with the analysts who will use it.** Not for them — with them.
- **Milestone:** signed-off scope, metric targets, data-readiness assessment, v1 reason-code vocabulary.

### Phase 1 — Data pipeline, profile store & router (3–5 weeks)

- Ingestion into an immutable raw store; rolling windows per merchant and peer group; backfill history.
- **Establish the maturity threshold empirically** — measure where baselines actually stabilize across our merchant base.
- Build the prototype synthetic generator and evaluation harness in parallel.
- **Milestone:** queryable profiles refreshed on schedule, validated against known merchants; lane assignment working; harness running.

### Phase 2 — Rules MVP, dual lanes & analyst queue (4–6 weeks)

- Implement Section 7.1 typologies with tunable thresholds and reason codes, calibrated separately per lane.
- Stand up case management with the Section 6 schema. **Disposition capture is a launch requirement, not a follow-up.**
- **Run in shadow mode** alongside manual review; compare what it catches vs. what analysts find.
- **Milestone:** analysts working from the queue; every alert explainable; dispositions accumulating as labels; label-completeness metric reporting.

### Phase 3 — Behavioral baselining & tuning (4–6 weeks)

- Add unsupervised anomaly detection to Lane A; blend rule and anomaly sub-scores.
- Tune thresholds against accumulated dispositions; measure precision/recall shift per lane.
- Begin random control-set sampling to counter survivorship bias.
- **Milestone:** measurable false-positive reduction vs. Phase 2 with no loss in true-positive catch.

### Phase 4 — Supervised suppression model (5–7 weeks, gated on label volume)

- Train the secondary classifier on Phase 2–3 dispositions; expose per-alert feature attributions.
- Champion-challenger against the Phase 3 scorer; promote only on measured improvement plus fairness review.
- **Gate:** do not start until label volume and inter-analyst agreement clear the Phase 0 thresholds. **This phase slipping is an acceptable outcome; forcing it on thin or noisy labels is not.**
- **Milestone:** suppression model in production, outperforming the rules+anomaly blend on precision at equal recall.

### Phase 5 — Context-aware features, actions & hardening (ongoing)

- Add Section 5.4 contextual features; measure lift, review for discriminatory proxies before each promotion.
- Harden the reserve/hold workflow: maker-checker, STR support, audit trail, tipping-off safeguards.
- Add graph/network detection for merchant rings.
- Institutionalize re-tuning cadence and drift monitoring.
- **Milestone:** end-to-end auditable system with a standing improvement process.

### Timeline at a glance

| Phase | Focus | Duration | Primary output |
|---|---|---|---|
| 0 | Foundations & alignment | 2–3 wks | Scope, metrics, HK legal position, reason-code taxonomy |
| 1 | Pipeline, profiles, router | 3–5 wks | Profile store + lane routing + prototype harness |
| 2 | Rules MVP + queue | 4–6 wks | Shippable ranked queue; labels start accumulating |
| 3 | Behavioral baselining | 4–6 wks | False-positive reduction |
| 4 | Suppression model | 5–7 wks | Learned re-ranking (gated on labels) |
| 5 | Context, actions, graph | Ongoing | End-to-end auditable system |

*Rough total to a fully-featured system: 6–9 months — but production value lands at the end of Phase 2, roughly 2–3 months in. That early win matters twice over: it lifts the manual burden off compliance, and it starts generating the labels every later phase depends on.*

---

## 12. Team & Build-vs-Buy

### Core team

- **1–2 data / backend engineers** — pipeline, profile store, router, detection layer.
- **1 data scientist / ML engineer** — anomaly detection, suppression model, evaluation harness (part-time until Phase 3).
- **1 compliance SME** — typologies, thresholds, HK regulatory rules, **reason-code taxonomy ownership**. Embedded, not consulted once.
- **Fractional legal** — merchant agreement and AMLO/JFIU sign-off at Phase 0 and Phase 5.
- **Analyst representatives** — the actual users. They co-design the queue and the taxonomy, or the loop does not close.

### Build vs. buy

| Component | Recommendation | Rationale |
|---|---|---|
| Ingestion & profile store | Build | Tightly coupled to our data model; core IP. |
| Router & rules engine | Build | Our typologies and merchant mix are specific. Keep transparent and owned. |
| Case management | **Buy only if it fits the schema** | Generic workflow, mature vendors — *but* Section 6 is a hard requirement. A tool that cannot enforce structured reason codes and immutable feature snapshots breaks the entire design. Buying the shell and owning the schema is fine; buying a free-text notes field is not. |
| ML | Build on standard libs | Gradient-boosting is commodity. No heavy platform needed early. |
| Full AML suite | Evaluate, don't default | Turnkey monitoring exists, but cost, lock-in, and inability to tune to our data apply. A generic vendor cannot build the closed loop — that is the part worth owning. |

> **The pragmatic hybrid:** buy the workflow shell if and only if it enforces our schema; build the ingestion, profiles, routing, and detection that are specific to our merchants. The detection edge and the labeling loop are exactly what a generic vendor cannot tune as well as we can on our own data.

---

## 13. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Analysts skip reason codes under load** | **The loop silently breaks; design degrades to an ordinary rules engine** | Mandatory structured fields; short taxonomy; label completeness as a tracked operational metric with a threshold that triggers review. |
| Poor data quality / inaccurate MCC | Peer grouping and context features degrade | Phase 0 data audit; fix at source before building detection. |
| Too few or too noisy labels for ML | Phase 4 stalls | Rules + anomaly carry the early load. Phase 4 is explicitly gated — slipping is acceptable, forcing is not. |
| Label leakage via recomputed features | Model looks excellent, fails in production | Immutable as-of-alert feature snapshots, enforced in the schema. |
| Survivorship bias in labels | Model becomes confidently blind where rules already miss | Random control-set sampling of unflagged merchants for periodic review. |
| False positives disrupt good merchants | Merchant churn, commercial + legal exposure | Precision as a first-class metric; human approval on fund actions; appeals path; context features. |
| Contextual features encode bias | Systematic over-flagging of certain merchant communities | Feature attribution review as a compliance gate before any promotion. |
| Bust-out via Lane B→A graduation | Highest-value attack path is designed into the routing | Monitor crossovers as elevated-risk events; do not soften thresholds immediately on graduation. |
| Analysts don't adopt the tool | ROI never materializes | Co-design with analysts; explainable alerts; shadow mode to build trust. |
| Regulatory missteps (STR, tipping-off) | Legal / regulatory exposure | Compliance + legal embedded from Phase 0; workflow enforces the rules; no automated merchant messaging on flagged accounts. |
| Model / rule drift | Detection decays silently | Ongoing monitoring, scheduled re-tuning, champion-challenger before promotion. |

---

## 14. Recommendation & Next Steps

Proceed. The approach is well-established, the phasing de-risks by delivering a working rules-based system early, and the closed loop turns compliance review from a pure cost centre into the engine that improves the software. The genuine risks — data quality, the labeling discipline, and the legal guardrails around holding funds — are all manageable and all addressed above.

The framing to hold onto: **this is not primarily an algorithms problem.** The algorithms are largely solved and available off the shelf. Our edge and our risk both live in data quality, in tuning to our specific merchant base, and in the disciplined feedback loop that keeps the system honest over time. The thing a vendor cannot sell us is the loop.

The value proposition, stated plainly: *a pipeline that transforms human compliance work from a repetitive cost centre into an active engine for software optimization. By structuring a direct feedback loop where manual reviews explicitly train a multi-layered, context-aware model, false-positive rates fall systematically over time — protecting honest merchants from accidental lockouts during festive seasons while isolating true financial crime with precision.*

That claim is only true if the loop actually closes. Everything in Section 6 exists to make sure it does.

### Immediate next steps

1. **Phase 0 compliance + legal workshop** — establish the correct HK regulatory position (AMLO / JFIU / tipping-off) for our licence category, confirm merchant-agreement hold rights, and set the approval chain for fund actions.
2. **Data-readiness audit** — confirm we hold the fields the Section 7 typologies depend on, with MCC accuracy assessed specifically.
3. **Draft the reason-code taxonomy with the analysts.** This is the highest-leverage hour in the whole project and the cheapest to skip.
4. **Agree success metrics** — target precision per lane, alert volume per analyst, time-to-detection, label completeness — so later phases have a scorecard.
5. **Green-light Phase 1 + the synthetic prototype** as the first delivery increment; treat Phases 3–5 as fast-follows gated on results.

---

*Working draft for internal review. Regulatory and legal points are general guidance, not legal advice; confirm specifics with our compliance function and counsel.*

### Sources

- [AML Watcher — Complete Guide to Transaction Monitoring in 2025](https://amlwatcher.com/blog/a-complete-guide-to-transaction-monitoring-in-2025/)
- [Sumsub — Transaction Monitoring Guide](https://sumsub.com/blog/transaction-monitoring/)
- [Sumsub — AML Monitoring Rules & Scenarios](https://sumsub.com/blog/aml-transaction-monitoring-rules-scenarios/)
- [Flagright — Best AML Solutions for Payment Processors](https://www.flagright.com/post/best-aml-compliance-solutions-for-payment-processors)
- [FluxForce — How AI Cuts False Positives by 60%](https://www.fluxforce.ai/blog/aml-transaction-monitoring-how-ai-cuts-false-positives-by-60)
- [HK AMLO (Cap. 615)](https://www.elegislation.gov.hk/hk/cap615) · [JFIU — Suspicious Transaction Reporting](https://www.jfiu.gov.hk/en/str.html) *(confirm applicable guidance with counsel)*
