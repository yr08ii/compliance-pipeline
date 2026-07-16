# Compliance Transaction-Monitoring Pipeline
## Feasibility Review & Phased Project Plan

*Automated detection of high-risk merchants from card-transaction data*

**Prepared for:** Engineering Lead · Regulated Payment Facilitator
**Date:** July 2026 · Working Draft v1

---

## 1. Executive Summary

You process card transactions as a payment facilitator, sponsoring merchants under a master account with Visa and Mastercard and settling funds between those merchants and cardholders' banks. That model makes you the party the card networks and sponsor bank hold accountable when a merchant on your platform turns out to be laundering money, running a fraud scheme, or selling illegal goods. Today your compliance team reads through pages of transaction data by hand to find those merchants. That approach does not scale, is slow to react, and is inconsistent from analyst to analyst.

This project builds a transaction-monitoring pipeline that ingests the transaction data you already have, scores every merchant continuously against known money-laundering and fraud typologies, and surfaces a ranked, explained queue of the merchants most likely to be a problem — before the sponsor bank or a network calls you about them. The explicit design goal is precision as much as recall: catch the bad actors while keeping good merchants out of the alert queue, so you are not freezing legitimate businesses' funds or drowning analysts in false positives.

> **Bottom line on feasibility.** Highly feasible and largely a known problem. This is an established discipline (AML transaction monitoring) with mature patterns — rules engines plus behavioral/ML scoring plus a case-management workflow. The build is real engineering work, but nothing here is research-grade risk. The hard parts are not the algorithms; they are data quality, labeling, tuning thresholds to your merchant base, and the legal/operational rules around when you may hold funds.

### What this plan delivers

- A **feasibility verdict** with the honest risks and constraints, including the legal reality of "freezing funds."
- A **reference architecture** for the pipeline: ingestion → feature/profile store → detection (rules + ML) → alert scoring → case management → feedback loop.
- A **phased plan** (five phases over roughly 6–9 months) that delivers value early with a rules-based MVP and layers ML on top once you have labeled outcomes.
- **Roles, effort, and milestones** per phase, plus a build-vs-buy view so you can decide where to write code and where to license a vendor.

---

## 2. Problem Definition & Scope

Stripped to essentials, the request is: use the transaction data flowing through the platform to automatically identify merchants engaged in illegal or suspicious activity, act on them quickly (up to and including holding their settlement funds pending review), and do it accurately enough that legitimate merchants are not disrupted.

### In scope

- **Merchant-level risk monitoring** — scoring registered merchants on an ongoing basis using their transaction behavior.
- **Transaction-level monitoring** — velocity, structuring, refund abuse, and anomaly detection feeding the merchant score.
- **Alerting and case management** — a ranked queue with explanations, so an analyst reviews the strongest signals first instead of reading raw pages.
- **A funds-hold / reserve workflow** — a controlled, auditable process to place a reserve or hold on a merchant pending physical review, with the legal guardrails in Section 6.
- **A feedback loop** — analyst decisions (true/false positive, SAR filed, merchant offboarded) feed back to tune rules and train models.

### Out of scope (for this project)

- **KYC / merchant onboarding due diligence** — related and important, but a separate workstream. This pipeline consumes onboarding data; it does not replace onboarding.
- **Sanctions / watchlist screening** — likely already exists or is a distinct control; note the integration point but do not rebuild it here.
- **Cardholder-side fraud (stolen card detection)** — overlaps technically but is a different objective; keep the merchant-integrity focus.

> **Reframe worth making.** "Spot suspicious merchants" is really two different jobs wearing one coat: (1) regulatory AML monitoring, which has legally mandated obligations (SARs, recordkeeping) and audit expectations, and (2) commercial risk management, protecting yourself from chargebacks and network fines. They share plumbing but have different success metrics and different stakeholders. Build the plumbing once; keep the two objectives explicit so you tune each correctly.

---

## 3. Feasibility Review

The core question — can transaction data reliably flag suspicious merchants — is answered yes across the industry. Transaction monitoring is a standard AML control, and the shift from pure rules to rules-plus-behavioral-scoring is well documented, with reported false-positive reductions of 50% or more when ML baselines are layered on top of static thresholds. The feasibility risks are therefore not "will it work" but "what will make it hard."

| Dimension | Verdict | Notes |
|---|---|---|
| Technical approach | Low risk | Mature patterns exist. Rules + behavioral baselines + case management is a solved shape. |
| Data availability | Medium risk | You have transaction data — but quality, completeness, and merchant metadata drive everything. Garbage in, garbage out. |
| Labeled outcomes for ML | Medium–high risk | Supervised ML needs confirmed good/bad labels. Early on you have few. Start with rules + anomaly detection; ML follows once cases accumulate. |
| Precision (avoiding false positives) | Medium risk | Achievable but requires tuning to YOUR merchant mix and continuous feedback. This is the make-or-break for merchant experience. |
| Regulatory / legal | Medium risk | Freezing funds and filing SARs have hard legal rules. Needs compliance + legal sign-off, not just engineering. |
| Operational adoption | Medium risk | The tool only helps if analysts trust and use the queue. Explainability and workflow fit matter as much as model accuracy. |

### The honest hard parts

- **Cold-start labeling.** You cannot train a supervised model on day one because you do not yet have a clean history of confirmed-bad vs confirmed-good merchants. The plan handles this by starting rules-first and treating every analyst decision as a label from day one.
- **Precision vs. recall tension.** Loosen thresholds and you disrupt good merchants; tighten them and you miss bad actors and carry liability. This is a tuning discipline, not a one-time setting — hence the feedback loop is a first-class part of the architecture, not an afterthought.
- **Concept drift.** Launderers adapt. A model or rule set that works today degrades. Plan for periodic re-tuning and champion/challenger model comparison.
- **Explainability is mandatory, not optional.** Both regulators and your own analysts need to know *why* a merchant was flagged. This rules out black-box-only approaches and favors models whose output can be traced to specific behaviors.

---

## 4. Transaction-Monitoring Types & Typologies

The detection layer combines several complementary techniques. No single one is sufficient; the pipeline runs them in parallel and combines their outputs into one merchant risk score. Below are the categories most relevant to a payment facilitator's merchant base.

### 4.1 Rule / typology detection (start here)

| Typology | What it looks like | Signal |
|---|---|---|
| Transaction laundering | A registered merchant processing sales for an unregistered / illegal business hidden behind it. | Mismatch between declared MCC and actual transaction pattern; sudden new product mix. |
| Bust-out merchant | Merchant builds normal history, then spikes volume and disappears with the funds. | Abrupt volume/ticket increase, then refund surge or settlement pull. |
| Structuring | Many transactions kept just under reporting or review thresholds. | Clustering of amounts just below round thresholds; velocity of near-threshold txns. |
| Refund / credit abuse | Refunds used to move value or launder rather than genuine returns. | High refund ratio, refunds to different cards than the original charge. |
| Velocity anomalies | Volume, count, or ticket size inconsistent with the merchant profile. | Deviation from the merchant's own rolling baseline. |
| Geographic / corridor risk | Activity concentrated in high-risk regions or corridors. | Card BIN geography vs. merchant location mismatch. |

Rules are transparent, fast to ship, and easy to explain to a regulator. Their weakness is that they only catch patterns you have already thought of, and static thresholds generate false positives. That is why they are the starting layer, not the whole system.

### 4.2 Behavioral baselining (anomaly detection)

Instead of one universal threshold, build a rolling profile per merchant (and per peer group — merchants of the same size, MCC, and geography) and score how far each merchant deviates from its own and its peers' normal behavior. This is unsupervised, so it works before you have labels, and it catches novel patterns rules would miss. It is the bridge between the rules MVP and full supervised ML.

### 4.3 Supervised ML risk scoring (layer on later)

Once analyst decisions have accumulated into a labeled dataset, train a model (gradient-boosted trees are the workhorse here — strong on tabular data and explainable via feature attribution) to output a continuous merchant risk score. This is where the reported 50%+ false-positive reductions come from: the model learns which combinations of signals actually correlate with confirmed-bad outcomes, rather than firing on any single threshold breach.

### 4.4 Network / graph signals (advanced, optional)

Money laundering often spans multiple merchants and shared attributes — same beneficial owner, bank account, device, or settlement destination across supposedly independent merchants. Graph analysis surfaces these rings. High value but higher complexity; defer to a later phase once the core pipeline is proven.

---

## 5. Reference Architecture

The pipeline is a linear flow with one feedback loop. Each stage is independently testable and can be built and improved on its own timeline.

| Stage | Responsibility | Notes for build |
|---|---|---|
| 1. Ingestion | Pull transaction, refund, settlement, and merchant-metadata events from the app. | Batch to start (nightly is fine for merchant-level risk); design so it can move to streaming later. Immutable raw store for audit. |
| 2. Feature / profile store | Compute and hold rolling merchant profiles and peer-group stats. | This is the heart of the system. Rolling windows (1/7/30/90 day) for volume, ticket, refund ratio, geography, velocity. |
| 3. Detection layer | Run rules + anomaly baselines (+ ML later) in parallel. | Each detector emits a sub-score and a reason code. Keep detectors modular so you can add/retire them independently. |
| 4. Scoring & prioritization | Combine sub-scores into one merchant risk score + ranked alert. | Weighted blend initially; learned weights later. Every alert carries its contributing reasons (explainability). |
| 5. Case management | Analyst queue: review, evidence, decision, disposition. | Replaces reading raw pages. Captures the decision — this IS your labeling pipeline. Consider buying this layer. |
| 6. Action & controls | Reserve/hold workflow, SAR filing support, offboarding. | Maker-checker approval, full audit trail, legal guardrails (Section 6). Never fully automated for fund holds. |
| 7. Feedback loop | Feed dispositions back to tune rules and train models. | Closes the loop. Track precision/recall over time; champion-challenger for model changes. |

### Design principles

- **Rules and ML coexist.** Rules for known, explainable, regulator-facing patterns; ML for the subtle combinations. Do not treat ML as a replacement for rules.
- **Every alert is explainable.** No merchant is flagged without a human-readable reason. This serves analysts, merchants (appeals), and regulators.
- **Human-in-the-loop for consequences.** Scoring can be automated; holding a merchant's money is not. A person approves fund actions.
- **The case system is the training set.** Design disposition capture carefully — it is both the analyst workflow and the source of your future ML labels.
- **Start batch, design for streaming.** Merchant-level risk rarely needs sub-second latency. Nightly scoring is a fine MVP; keep interfaces clean so you can tighten latency if a use case demands it.

---

## 6. The "Freeze Their Funds" Reality

The instinct to freeze a suspicious merchant's funds until you physically verify them is operationally sound but legally loaded. Two things must be separated: your ability to hold settlement funds (a commercial/contractual matter) and your obligations under anti-money-laundering law (a regulatory matter). Engineering must build the workflow; compliance and legal must own the policy that drives it.

| Consideration | What it means for the build |
|---|---|
| Holding funds is contractual, not arbitrary | Your merchant agreement must already grant the right to place reserves / hold settlement on suspicion. The system enforces a policy; it does not create the legal right. Confirm the contract language exists. |
| SAR obligation is separate from holding funds | As a regulated entity you likely must file a Suspicious Activity Report on knowledge/suspicion of illegal activity (generally within 30 days of detection, for amounts at/above the threshold). The pipeline should support SAR workflows, not just fund holds. |
| Do not "tip off" | If a SAR is filed, you generally must not tell the merchant a SAR exists. The action workflow and any merchant-facing messaging must respect this. Build the distinction into the tooling. |
| Closing vs. holding an account | Whether to hold, keep open (sometimes at law-enforcement request), or offboard is a compliance decision with its own guidance — not something to hard-code. Provide options; let policy choose. |
| Precision protects you legally too | Wrongly holding a legitimate merchant's funds creates commercial and potentially legal exposure. The precision goal is a risk control, not just a UX nicety. |

> **Guardrail for the design.** Automate detection and prioritization freely. Gate every fund-affecting action behind human approval, a documented reason, and an audit trail. Bring compliance and legal in at Phase 0, not at the end — the workflow rules they set are inputs to the architecture, not paperwork to bolt on afterward. This is general guidance, not legal advice; your compliance and legal counsel own the final policy.

---

## 7. Phased Project Plan

The plan front-loads value: a rules-based system that already beats manual review ships in the first couple of months, and each later phase adds precision and automation on top of a working base. Timeline assumes a small dedicated team (see Section 8); compress or extend with staffing.

### Phase 0 — Foundations & alignment (2–3 weeks)

- **Goal:** agree scope, success metrics, and legal guardrails before writing detection logic.
- Define success metrics: target precision/recall, alert volume per analyst, time-to-detection.
- Compliance + legal workshop: confirm SAR process, merchant-agreement hold rights, tipping-off rules, approval chain for fund actions.
- Data audit: inventory transaction/refund/settlement/merchant fields, assess quality and gaps.
- **Milestone:** signed-off scope doc, metric targets, and a data-readiness assessment.

### Phase 1 — Data pipeline & profile store (3–5 weeks)

- **Goal:** reliable ingestion and the rolling merchant-profile store — the foundation everything else sits on.
- Build ingestion into an immutable raw store; compute rolling windows (volume, ticket, refund ratio, velocity, geography) per merchant and peer group.
- Backfill history so profiles have context from day one.
- **Milestone:** queryable merchant profiles refreshed on schedule, validated against known merchants.

### Phase 2 — Rules-based MVP + analyst queue (4–6 weeks)

- **Goal:** replace manual page-reading with a ranked, explained alert queue. This is the first shippable win.
- Implement the core typology rules from Section 4.1 with tunable thresholds and reason codes.
- Stand up (buy or build) a case-management queue: review, evidence, disposition capture.
- Run in shadow mode alongside manual review; compare what it catches vs. what analysts find.
- **Milestone:** analysts working from the queue; every alert explainable; dispositions being captured as labels.

### Phase 3 — Behavioral baselining & tuning (4–6 weeks)

- **Goal:** cut false positives by scoring deviation from each merchant's own and peer baseline rather than static thresholds.
- Add unsupervised anomaly detection; blend rule sub-scores and anomaly scores into one merchant risk score.
- Tune thresholds against accumulated dispositions; measure precision/recall shift.
- **Milestone:** measurable false-positive reduction vs. Phase 2 with no loss in true-positive catch.

### Phase 4 — Supervised ML scoring (5–7 weeks, gated on label volume)

- **Goal:** learn which signal combinations actually predict confirmed-bad merchants.
- Train a gradient-boosted model on the labeled dispositions from Phases 2–3; expose per-alert feature attributions for explainability.
- Deploy champion-challenger against the Phase 3 scorer; promote only on measured improvement.
- **Milestone:** ML score in production, outperforming the rules+anomaly blend on precision at equal recall.

### Phase 5 — Action workflow, graph signals & hardening (ongoing)

- **Goal:** close the loop with controlled actions and advanced detection.
- Harden the reserve/hold workflow: maker-checker approval, SAR support, audit trail, tipping-off safeguards.
- Add graph/network detection for merchant rings sharing owners, accounts, or devices.
- Institutionalize re-tuning cadence and model monitoring for drift.
- **Milestone:** end-to-end system from detection to auditable action, with a standing improvement process.

### Timeline at a glance

| Phase | Focus | Duration | Primary output |
|---|---|---|---|
| 0 | Foundations & alignment | 2–3 wks | Scope, metrics, legal guardrails |
| 1 | Data pipeline & profiles | 3–5 wks | Merchant profile store |
| 2 | Rules MVP + queue | 4–6 wks | Shippable ranked alert queue |
| 3 | Behavioral baselining | 4–6 wks | False-positive reduction |
| 4 | Supervised ML scoring | 5–7 wks | Learned risk score |
| 5 | Actions, graph, hardening | Ongoing | End-to-end auditable system |

*Rough total to a fully-featured system: 6–9 months, but you have production value at the end of Phase 2 (roughly 2–3 months in). That early win matters — it takes the manual burden off the compliance team and starts generating the labels the later phases depend on.*

---

## 8. Team, Effort & Build-vs-Buy

### Suggested core team

- **1–2 data / backend engineers** — pipeline, profile store, detection layer.
- **1 data scientist / ML engineer** — anomaly detection and supervised scoring (part-time until Phase 3–4).
- **1 compliance SME** — typologies, thresholds, SAR/legal rules, disposition definitions (embedded, not consulted once).
- **Fractional legal** — merchant-agreement and regulatory sign-off at Phase 0 and Phase 5.
- **Analyst representative(s)** — the actual users; involve them in queue design so they adopt it.

### Build vs. buy

| Component | Recommendation | Rationale |
|---|---|---|
| Ingestion & profile store | Build | Tightly coupled to your data model; the core IP and the thing you most need to control. |
| Rules engine | Build (or light OSS) | Your typologies are specific to your merchant base; keep them transparent and owned. |
| Case management | Consider buy | Generic workflow; mature vendor tools exist. Buying frees the team to focus on detection. |
| ML platform | Build on standard libs | Gradient-boosting libraries are commodity; no need for a heavy platform early. |
| Full AML suite (vendor) | Evaluate, don't default | Vendors offer turnkey monitoring but cost, lock-in, and tuning-to-your-data limits apply. Reasonable to buy the workflow and build the detection edge. |

> **A pragmatic hybrid.** You do not have to choose purely build or buy. A common and sensible path: buy the case-management/workflow layer, build the ingestion, profiles, and detection logic that are specific to your merchants — because that detection edge is exactly what a generic vendor cannot tune as well as you can on your own data.

---

## 9. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Poor data quality / missing fields | Everything downstream degrades | Phase 0 data audit; fix at source before building detection. |
| Too few labels for ML | Phase 4 stalls | Rules + anomaly detection carry the early load; capture every disposition as a label from Phase 2. |
| False positives disrupt good merchants | Merchant churn, commercial + legal exposure | Precision as a first-class metric; human approval on fund actions; appeals path. |
| Analysts don't adopt the tool | ROI never materializes | Involve analysts in design; explainable alerts; run in shadow mode to build trust. |
| Regulatory missteps (SAR, tipping-off) | Legal / regulatory exposure | Compliance + legal embedded from Phase 0; workflow enforces the rules. |
| Model / rule drift over time | Detection quality decays silently | Ongoing monitoring, scheduled re-tuning, champion-challenger before any promotion. |

---

## 10. Recommendation & Next Steps

Proceed. The project is feasible, the approach is well-established, and the phasing lets you de-risk by delivering a working rules-based system early and layering intelligence on top of proven foundations. The genuine risks are data quality, precision tuning, and the legal guardrails around holding funds — all manageable, none research-grade, and all addressed head-on in the plan above.

The single most important framing to hold onto: this is not primarily an algorithms problem. The algorithms are largely solved. Your edge and your risk both live in data quality, in tuning to your specific merchant base, and in the disciplined feedback loop that keeps the system honest over time.

### Immediate next steps

1. Run the Phase 0 compliance + legal workshop and lock down SAR process, hold rights, and the approval chain for fund actions.
2. Complete the data-readiness audit — confirm you actually have the fields the typologies in Section 4 depend on.
3. Agree the success metrics (target precision, alert volume per analyst, time-to-detection) so later phases have a scorecard.
4. Green-light Phases 1–2 as the first delivery increment; treat Phases 3–5 as fast-follows gated on results.

---

*Prepared as a working draft for internal review. Regulatory and legal points are general guidance, not legal advice; confirm specifics with your compliance function and counsel.*

### Sources

- [Sumsub — Transaction Monitoring Guide](https://sumsub.com/blog/transaction-monitoring/)
- [Sumsub — AML Monitoring Rules & Scenarios](https://sumsub.com/blog/aml-transaction-monitoring-rules-scenarios/)
- [Flagright — Best AML Solutions for Payment Processors](https://www.flagright.com/post/best-aml-compliance-solutions-for-payment-processors)
- [FluxForce — How AI Cuts False Positives by 60%](https://www.fluxforce.ai/blog/aml-transaction-monitoring-how-ai-cuts-false-positives-by-60)
- [FFIEC BSA/AML — SAR Regulatory Requirements](https://bsaaml.ffiec.gov/manual/AssessingComplianceWithBSARegulatoryRequirements/04)
- [FinCEN — SAR FAQs (October 2025)](https://www.fincen.gov/system/files/2025-10/SAR-FAQs-October-2025.pdf)
