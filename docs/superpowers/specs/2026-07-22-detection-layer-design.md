# Detection Layer — Design Specification

*The statistics and detectors behind the alerts. Refines the flat stub into the real Lane A/B engine.*

**Date:** 2026-07-22 · Draft v1 · revised 2026-08-05
**Status:** Families A, B and C implemented. §5.2 card-linkage built but **gated** — the security treatment it requires is not yet in place. One scope reversal recorded in §5.3.
**Related:** [Closed-Loop Pipeline](../../Compliance_Monitoring_Pipeline_Plan.md) · [Platform Design](2026-07-20-compliance-platform-design.md)

---

## 0. Where this fits

The walking skeleton ships one stub detector: `daily_volume > 8000`. This spec replaces that with the real detection layer — the "baselines + ruleset" tier of the daily-flow diagram — while keeping everything mapped onto the data model and UI we already built.

Anchor to what exists:
- `MerchantProfile.metrics` (JSON) holds each merchant's rolling baseline parameters.
- `peer_groups` (deferred table) holds MCC × subdistrict cohort statistics.
- `Alert.feature_snapshot` (`{feature_name, merchant_value, baseline_value, deviation}`) is what the divergence panel renders. **Every explainable detector must emit this shape.**
- `Alert.blended_score` + `triggering_detectors` carry the composite score and its reasons.

Two design rules inherited and reaffirmed:
- **Explainability is mandatory.** A detector that cannot say *why* in the `feature_snapshot` shape is a secondary signal, never a primary flag.
- **The secondary model re-ranks only.** It reorders the queue; it never hides an alert. (Confirmed decision.)

### 0.1 Source schema (actual columns)

The pull reads these columns. Detectors reference these names, not invented ones.

**Transaction-specific:** `payment_id`, `card_type`, `card_origin`, `card_issuing_country`, `card_issuing_bank`, `payment_gateway`, `currency`, `total_amount`, `net_amount`, `hkt_transaction_time`, `transaction_status`, `hashed_pan`, `masked_pan`.
**Merchant / peer identifiers:** `merchant_id`, `agent_id`, `mcc`, `mcc_description`, `business_plan`, `business_nature`, `ownership_or_business_type`, `merchant_status`.
**Merchant linkage (ring detection):** `hashed_merchant_name`, `hashed_br_number`, `hashed_merchant_address`.
**Geography (peer grouping):** `city`, `merchant_area`, `merchant_district`, `merchant_subdistrict`.

Two consequences for earlier assumptions:
- **No `terminal_id`.** The skeleton's `terminal_id` column is unsupported by the source. Cross-terminal checks are really cross-*merchant* (`merchant_id`) or cross-*agent* (`agent_id`).
- **No explicit refund flag.** Refunds are represented via `transaction_status` and/or the sign of `net_amount`/`total_amount` — confirm the exact encoding before building refund-ratio rules. The skeleton's `is_refund` boolean is a placeholder for whatever that resolves to.

---

## 1. The three detector families

The daily flow runs three complementary families in parallel and combines them into one merchant risk score. They answer different questions:

| Family | Question | Shape | Explainable? |
|---|---|---|---|
| **A. Robust baselines** | "Is this number unusual *for this merchant / its peers*?" | Per-feature deviation | Yes — native `feature_snapshot` |
| **B. Typology rules** | "Does this match a known laundering *pattern*?" | Rule hit + reason code | Yes — rule name is the reason |
| **C. Cross-merchant / ring** | "Are these merchants or cards *coordinating*?" | Graph/velocity signal | Partially — needs a rendered explanation |

The diagram's baseline boxes are Family A. The `+ Ruleset` box is Family B — and it carries more weight than its size suggests, because the worst typologies are patterns, not deviations. Family C is the cross-terminal layer the diagram deliberately leaves out; it is enabled by the hashed-PAN identifier (Section 5) and gated on the security treatment there.

---

## 2. Why robust statistics (not mean / standard deviation)

Transaction amounts are heavily right-skewed with heavy tails. A few legitimate high-value sales inflate the mean and balloon the standard deviation, so a classic z-score baseline drifts and under-flags (false negatives). The fix is **robust, non-parametric statistics** built on the median, which a handful of large values cannot move.

This is the single biggest upgrade over the stub, and it maps cleanly onto our existing UI because the median *is* a `baseline_value` and the robust deviation *is* a `deviation`.

---

## 3. Family A — the per-merchant and peer baselines

### 3.1 Amount baseline — Median & MAD → Modified Z-score

Over a rolling window (start 30 days; tune in calibration), compute the median `x̃` and the Median Absolute Deviation:

```
MAD = median( |xᵢ − x̃| )
```

Score an incoming aggregate (e.g. the day's ticket sizes, or daily volume) with the **modified z-score**:

```
Mᵢ = 0.6745 · (xᵢ − x̃) / MAD
```

The `0.6745` is the constant that makes MAD comparable to a standard deviation under normality. Flagging bands (starting points, not law — calibrated in Phase 1/3 against dispositions):

| \|Mᵢ\| | Meaning |
|---|---|
| ≤ 2.5 | normal |
| 2.5 – 3.5 | moderate — score bump, not a standalone flag |
| > 3.5 | outlier — contributes a flag |

`feature_snapshot` entry: `{feature_name: "daily_amount", merchant_value: xᵢ, baseline_value: x̃, deviation: Mᵢ}`.

**Two failure modes that must be guarded (the stub hides these):**

- **MAD = 0.** A merchant selling one product at a fixed price has zero dispersion, so `Mᵢ` divides by zero and *every* deviation reads as infinite — the whole merchant floods the queue. Guard: if `MAD == 0`, fall back to a scaled IQR; if that is also 0 (truly constant history), switch this merchant's amount check to a rule ("any change from the constant price") rather than a z-score. Never divide by a zero MAD.
- **Too little history.** Median/MAD need enough points to be stable. Below a minimum count *and* a minimum span of days, the merchant is not "mature" — this is precisely the **Lane B** boundary. The maturity threshold (count + days) is set empirically in Phase 1, not guessed.

### 3.2 Time baseline — KDE over time-of-day

Build a smooth probability density of *when* the merchant normally transacts (a bar clusters at 18:00–02:00; a bakery at 06:00–14:00) via Kernel Density Estimation. A transaction lands in a low-density region → anomalous timing.

**Implemented as a von Mises kernel density on the 24-hour circle**, estimated on a 15-minute grid:

- **Circular** — 23:30 and 00:30 are 60 minutes apart, not 23 hours, so a bar trading across midnight is one pattern rather than two clusters.
- **Sub-hour resolution** — 03:15 and 03:45 are different distances from a 04:00 cluster. Hour buckets make them identical.
- **Adaptive bandwidth** — Silverman-style `h ∝ n^(-1/5)`, clamped. A fixed width under-smooths a sparse merchant into noise and over-smooths a sharply-scheduled one until its boundary blurs.
- **Per-merchant threshold** — a low percentile of *that merchant's own* density, never an absolute share. A fixed "below 1% of trade" cutoff flags constantly for any merchant whose trade spreads across many hours: a round-the-clock forecourt has no hour above ~4%.
- **Cohort version uses a stricter percentile** — a cohort pools members keeping genuinely different hours, so its tails are legitimately wider; the same cutoff would flag a merchant for closing an hour later than most of its trade.

Estimation is binned and convolved once, so cost does not scale with transaction count. Evaluating a naive KDE at every observation would be quadratic across a whole merchant base.

**Timezone is load-bearing here.** "3am" means 3am where the merchant trades, so hour-of-day is read in Hong Kong time and the pipeline's scored day is the Hong Kong business day. Anchoring to UTC would cut the day at 08:00 local and split a merchant's trading in two.

`feature_snapshot` entry: `{feature_name: "txn_hour", merchant_value: hour, baseline_value: <modal/active window>, deviation: <how far into the tail>}` — rendered as "3:30am, outside the 9am–9pm active window."

### 3.3 Card-origin baseline — categorical

Maintain the merchant's historical distribution of card *issuer countries* — directly from `card_issuing_country` / `card_origin` (**already in the schema; no BIN lookup needed**). `card_issuing_bank` gives a finer cut if wanted. A sudden surge of a rare-for-this-merchant origin is the signal. Score by how improbable the observed mix is under the historical categorical distribution.

`feature_snapshot`: `{feature_name: "foreign_card_share", merchant_value: 0.72, baseline_value: 0.05, deviation: <ratio or surprisal>}`.

### 3.4 Peer baselines — MCC and subdistrict

For merchants (mature or new), compare against the cohort, not just their own history:

There are **two distinct peer questions**, and both are needed:

- **Is this merchant's *transaction* unusual for its trade?** Ticket against the cohort's transaction distribution. This is the one that covers cold start: a median does not move for one large sale, so neither the merchant's own baseline (which it may not have) nor the merchant-level test can see it. Each member's contribution to the cohort is **capped** so a high-volume merchant cannot drag the distribution to cover itself.
- **Is this *merchant* unusual for its cohort?** The merchant's own median against the cohort's distribution of medians — one vote per merchant. Catches the systematic case the first misses: every ticket sits inside the cohort's range, but the merchant's whole level is shifted.

Both score with the **same modified z-score** as the self baseline. An earlier draft used a Tukey IQR fence here; that was wrong twice over. The IQR has a 25% breakdown point, so in a small cohort a single deviant member lands inside the upper quartile and drags Q₃ up far enough to cover itself — the exact contamination the detector exists to catch. And reporting a ratio while the self detector reported a z-score mixed units in the divergence panel. Cohort distributions are heavy-tailed like a merchant's own, so they take the same robust test.

- **MCC time** — the cohort's active-hours density.
- **Subdistrict amount / card-origin** — the district's norms (Mong Kok ≠ airport for foreign-card share); flag deviation from the district's foreign-card ratio.

Peer baselines require a **minimum cohort size**; a 3-merchant MCC cohort is not a distribution. Fall back MCC×subdistrict → MCC-only → network-wide as cohorts thin out.

`peer_groups` stores `{mcc, subdistrict, n_merchants, amount_q1, amount_q3, active_hours_kde_ref, foreign_card_ratio, ...}`, refreshed nightly.

### 3.3a Volume and speed — the other two dimensions

Amount alone is not enough, and the gap is not merely coverage: it is what makes cohort manipulation viable. To drag a cohort's amount distribution a merchant must transact far more than its peers, so if nothing measures volume, the evasion is free.

| Quantity | Detector | Catches |
|---|---|---|
| **Amount** | ticket vs own / cohort | one large sale; wrong price level for the trade |
| **Volume** | daily count vs own / cohort | many ordinary tickets; the manipulation attempt itself |
| **Speed** | peak transactions per rolling hour vs own | a burst inside an ordinary daily total |

All three use the same median/MAD/modified-z machinery, so every one reports comparable units into the divergence panel.

**Counts need a dispersion floor of one transaction.** A merchant that trades exactly five times a day has zero spread — ordinary, not degenerate. Without a floor it falls to the constant fallback and a jump from five to sixty is invisible. The same floor applies to cohort counts, or a cold-start merchant flooding transactions has nothing watching it. Amounts keep the strict fallback, where zero spread genuinely does mean "fixed price, use a rule".

**Speed is distinct from volume.** A merchant can put its entire ordinary daily count through in minutes; the day's total looks normal and only the within-day rate exposes the shape. Measured as the busiest rolling hour, so a burst is caught wherever it falls rather than against clock boundaries.

**Together they close the loop.** Amount, volume, speed and peer comparison each cover what the others miss, so no single evasion route is silent: moving one signal shows up in another, and the lag means none of it can affect the baseline currently doing the judging.

### 3.4a Baseline integrity — self-contamination and backfill

A baseline fitted from a merchant's own history has a structural weakness: **the baseline can learn the crime.** This section states the exposure honestly and the mitigations.

**What is already handled.** The scored period is excluded from its own baseline — the window is `[as_of − N, as_of)`, `as_of` exclusive. We never compute a median that includes the value being judged.

**What robust statistics buy.** The median has a **50% breakdown point**: contamination below half the window does not move the center. This is the concrete payoff of median/MAD over mean/σ, where a single fraudulent transaction shifts the baseline immediately and begins masking the next one. A merchant abusing on a minority of days does not poison its own baseline.

**What remains exposed.**

| Exposure | Why it bites |
|---|---|
| **Sustained abuse** | Past ~50% of the window, the median follows the crime and "normal" becomes the abuse. |
| **Slow ramps** | A merchant growing a few percent a day is never an outlier against its own trailing window, yet reaches 10× in two months. **No trailing self-baseline can see this** — it is the natural evasion against this design. |
| **Contaminated backfill** | Initial baselines fitted on months of unaudited history encode undetected crime as normal. No statistic fixes this; the ground truth does not exist yet. |

**Mitigations, in priority order.**

1. **Peer baselines (§3.4) are the structural answer.** A merchant's own baseline can be poisoned by its own behaviour; the MCC/subdistrict peer baseline cannot be, unless the whole cohort is criminal. Peer comparison is immune to per-merchant self-contamination — which raises the priority of §3.4 from "more coverage" to "the antidote."
2. **Quarantine confirmed-bad periods.** Transactions belonging to an alert dispositioned `TRUE_POSITIVE` are excluded from all future baseline fitting. Without this, the system's own confirmed findings are absorbed into normal. This is a second, and arguably better, use of disposition data than model training.
3. **Trend / level-shift detector.** Compare a short-window median (e.g. 7d) against a long-window one (e.g. 90d). A ramp appears as a level shift even when no single day breaches. This is the only detector that catches slow growth.
4. **Optional window lag.** End the baseline window several days before the scored day so very recent activity is not instantly normalised. Costs adaptation speed — hold unless 1–3 prove insufficient.

**Launch backfill.** Fitting baselines over historical data is a one-time **backfill job** (a mode of Stage 2), not a training run — it exists so merchants start in Lane A rather than every merchant sitting in Lane B for the first month. The resulting baselines are provisional by definition. They are corrected by: running in **shadow mode** first, leaning on **peer** rather than **self** comparison in the early period, and **re-fitting once the first dispositions land** (feedback Loop 2). This is why shadow mode is a requirement and not a nicety — it is the only way to validate baselines fitted on unaudited history.

### 3.5 The cold-start (new / Lane B) path

New merchants have no own baseline, so — exactly as the diagram shows — they get `Ruleset′ (mcc)`: **peer-derived rules and thresholds**, not a peer anomaly *model*. The distinction matters for fairness: scoring a legitimately-unusual new merchant against a peer *model* over-flags it; applying sensible MCC-calibrated *caps* (velocity, single-ticket, refund ratio) does not pretend to know its normal. Lane B stays rule-based until it graduates.

> **Graduation is a risk moment.** A bust-out operator builds "normal" history precisely to cross Lane B → A. Log every crossover; do not soften thresholds the instant a merchant graduates.

---

## 4. Family B — the typology ruleset (the `+ Ruleset` box)

Statistical baselines do not catch patterns that are individually unremarkable. These are rules, per-lane calibrated, each emitting a reason code:

| Typology | Signal (not a per-feature deviation) | Columns |
|---|---|---|
| Structuring / smurfing | clustering of amounts just under a reporting/review threshold; velocity of near-threshold txns | `total_amount`, `hkt_transaction_time` |
| Refund / credit abuse | high refund ratio; refunds to a *different* `hashed_pan` than the original charge | `transaction_status`/`net_amount`, `hashed_pan` |
| Bust-out | build-up then abrupt volume/ticket spike then refund surge / settlement pull | `total_amount`, time |
| Dormant reactivation | long inactivity → sudden high-value velocity | time gaps |
| Rapid movement | funds land and are routed out with no resting balance | `net_amount`, time |
| **Declared-vs-actual mismatch** | `mcc` / `business_nature` inconsistent with the actual transaction pattern (ticket sizes, timing, card mix) — the *transaction-laundering* signature | `mcc`, `business_nature`, `ownership_or_business_type` |
| **Decline-ratio spike** | high share of failed/declined authorizations at a merchant — the in-scope, merchant-side read of card-testing | `transaction_status` |

These populate `triggering_detectors` with a rule name and a sub-score. Where a rule has a natural numeric basis (refund ratio, decline ratio), also emit a `feature_snapshot` row so the panel can show it.

### 4.1 Implemented — the exact tests

**Built 2026-08-05.** The table above describes the *signals*; the precise conditions, parameters and shipped defaults are specified in [Detection Flow Diagrams §4a](../../Detection_Flow_Diagrams.md). Three design decisions taken during implementation:

- **Every rule carries a second condition.** A single-condition rule floods the queue: structuring without a check on the merchant's own level fires daily on any jeweller; bust-out without the refund leg fires on every merchant that grows. The guard is the difference between a usable rule and a queue-flooder, and it is documented per rule.
- **Rules are parameterised templates stored as data**, not constants in code, so a compliance officer can retune them and add their own MCC-scoped instances without a deploy. The reason code on an alert carries the parameters in force, so it still explains itself after a retune.
- **No free-text expression language.** Analyst-authored predicates evaluated at runtime would be an arbitrary code path into the detection engine, and an AML rule nobody can statically review is not auditable.

**Refund encoding (Q1) is resolved** against the real extract: `REFUNDED`/`REVERSED`/`CHARGEBACK` are value moving out; `DECLINED` is an attempted-and-refused authorisation that moves no money; `CANCELLED`/`VOIDED` are incomplete attempts. Refunds are excluded from the decline ratio's denominator, or a heavy refund day would dilute it and hide a card-testing run.

---

## 5. Family C — cross-merchant / ring detection

Two sub-layers, in priority order. Lead with merchant-identity linkage (cheap, in-scope, low privacy cost); the card-linkage layer is a higher-cost secondary.

### 5.1 Merchant-identity rings (primary — build first)

The schema carries `hashed_merchant_name`, `hashed_br_number`, and `hashed_merchant_address`. Multiple distinct `merchant_id`s that **share** one of these are the classic shell-merchant / same-beneficial-owner ring — several "independent" storefronts behind one owner, address, or business registration, used to spread synthetic sales under each single-merchant radar.

- **Detection is an equality join**, not a reversal: "do these two merchants share a `hashed_br_number`?" We never un-hash anything, so the hash being reversible is irrelevant here — a decisive advantage over the card layer.
- **Directly in scope** (merchant integrity) and **low privacy cost** — it links *merchants* to each other, which is our job, with none of the cardholder-linkage burden of §5.2.
- **`agent_id` is the same idea one level up.** Aggregate alert rates per onboarding agent: one agent whose whole book runs hot is a gatekeeper-of-the-gatekeeper risk — a signal no per-merchant view can see.

Signals: shared `hashed_br_number` / `hashed_merchant_address` / `hashed_merchant_name` across merchant_ids; abnormal alert/True-Positive rate concentrated under one `agent_id`; a cluster of merchants sharing identity attributes that spike volume together.

These are graph signals; render the explanation as "shares registration with 4 other merchants, 2 already flagged" rather than a bare score. Standard data-protection hygiene still applies to the merchant hashes (equality-join only, access-controlled), but the stakes are far lower than cardholder data.

### 5.2 Card-linkage layer (secondary — higher privacy cost)

The **1:1 `hashed_pan`** lets us trace one card across merchants (there is no terminal id):

- **Card swarming** — one `hashed_pan` across ≥ N merchants in a short window (a ring moving through a district).
- **Cross-merchant structuring** — one card's aggregate split across many merchants inside 24h to dodge thresholds.

Still merchant-integrity relevant, but it carries a real cost that §5.1 does not:

**Security & privacy treatment (mandatory — this changes our data posture).** A hash that is 1:1 and joinable is deterministic and unsalted by construction, and an unsalted hash of a PAN is reversible: fix the BIN and Luhn digit and only ~10⁹ candidates remain, so a plain SHA-256 is brute-forced back to the card number in seconds (PCI SSC warns of exactly this). Therefore:

- The `hashed_pan` is **sensitive data**, not a safe token: encrypt at rest, access-control, and keep it out of the analyst UI and `feature_snapshot`. Our "nothing sensitive lives here" posture must be updated to acknowledge it. (`masked_pan` is display-only and must never be treated as an identifier.)
- **Prefer a keyed hash (HMAC, key held separately)** — still joinable for detection, not brute-forceable without the key. Open question: do we control the hashing (can switch to HMAC) or does the source hand us the hash as-is (then treat as PAN-equivalent)?
- Building a map of where each cardholder transacts is **cardholder linkage** — a PDPO consideration justified by AML purpose but requiring access control and a documented basis. Flag for compliance/legal.

### 5.3 What stays out

**BIN / card-testing attacks** are technically feasible with `hashed_pan`, but they detect **cardholder / stolen-card fraud** — a different objective and owner (issuer / real-time fraud), out of scope here. Keep them out; note the integration point so a fraud team can consume the same identifier. The merchant-side decline-ratio rule (§4) is the in-scope substitute.

> **~~Impossible geo-velocity~~ — scope reversed, 2026-08-05.** This section originally excluded geo-velocity on the same grounds. That exclusion is withdrawn, as a deliberate scope decision requested in feedback03 and recorded here rather than left as a silent contradiction between the spec and the build.
>
> **The argument for reversing it:** a card physically impossible to have been present at both merchants means at least one of those merchants accepted a card that was not there. That is a *merchant-acceptance* question, not a cardholder one, and it is squarely our objective. The output is a ring signal joining two merchants, never a fraud verdict on the cardholder.
>
> **The argument that kept it out still partly holds** and is why the signal is scored as a ring contributor rather than a standalone flag: the same evidence is also consistent with ordinary card compromise, which is the fraud team's problem, not ours. The integration point in the paragraph above is unchanged.
>
> Implemented per [Detection Flow Diagrams §5.6](../../Detection_Flow_Diagrams.md). Distances come from a committed HK subdistrict coordinate table, never a maps API — a detector whose answer depends on an external service is not reproducible for audit and cannot run air-gapped. The threshold is 60 km/h; a walking-pace limit would flag essentially every card used in two districts on the same day, because that is slower than the journey actually takes. Centroid distance understates the real journey, so the rule under-flags by construction.

---

## 6. Composite scoring — "sort alerts by risk"

Each family emits sub-scores; the merchant risk score combines them:

1. **Normalize** each sub-score to [0,1] (squash the modified z-score, map IQR exceedance, etc.) so families are comparable.
2. **Combine** — start with a transparent weighted blend (or noisy-OR so multiple independent weak signals can still rank a merchant up), learned weights later. Avoid a single opaque number with no decomposition.
3. **Carry the reasons** — `triggering_detectors` lists every contributing detector + sub-score; `feature_snapshot` carries the explainable per-feature rows. The blended score sorts the queue; the reasons explain each alert. This is what the divergence panel already renders.

Isolation Forest (Gemini Method C) is a legitimate *secondary* multivariate signal — it catches rare *combinations* (normal amount, but 3:30am at a store that never trades past 9pm). But its raw output is one opaque score, which fights explainability and does not fit `feature_snapshot`. If added, it is a score contributor with per-feature attribution surfaced (e.g. depth/SHAP-style) — **not** an MVP primary detector. Defer.

---

## 7. The two feedback paths (the diagram shows one)

The daily-flow diagram loops dispositions → **train the sorting model** → re-rank. That is correct and confirmed **re-rank only**: the model reorders by likelihood of being a real further-check case; humans still see every alert. But there is a *second* loop the diagram omits:

1. **Train the re-ranker** (shown) — supervised, gated on label volume + inter-analyst agreement, promoted only on measured lift. Re-rank only.
2. **Recalibrate the detectors** (missing from the diagram) — dispositions also tune the Family A thresholds and Family B rules. A merchant repeatedly cleared as "seasonal" should have its own baseline widened; a rule with a high false-positive rate should have its threshold revisited. Draw this arrow back onto the baselines/ruleset, not just the sorter.

Both depend on the random-control-set sampling from the plan (label a few *unflagged* merchants) to counter survivorship bias — otherwise both loops go confidently blind where the detectors already miss.

---

## 8. Data status (most gaps already closed by the schema)

**Already present in the source — no work needed:** card origin (`card_issuing_country`/`card_origin`, plus `card_issuing_bank`), subdistrict geography (`city`/`merchant_area`/`merchant_district`/`merchant_subdistrict`), transaction time (`hkt_transaction_time`), merchant-linkage hashes (`hashed_br_number`/`hashed_merchant_address`/`hashed_merchant_name`), `agent_id`, `transaction_status`, `mcc`/`business_nature`. The earlier "BIN lookup" and "geo enrichment" gaps are void.

**Still to do:**

| Need | Status | Action |
|---|---|---|
| Ingest the real schema | skeleton uses a 7-column subset with an invented `terminal_id` | widen the `transactions`/`merchants` model to §0.1; drop `terminal_id`; resolve refund encoding (`transaction_status`/`net_amount`) |
| `hashed_pan` handling | present in source, not yet ingested | §5.2 treatment — HMAC (if we control hashing) or PAN-equivalent protection; encrypt + access-control; keep out of UI |
| Festive calendar | not in schema | add HK calendar (Lunar New Year, Mid-Autumn, Golden Week) so seasonal surges are understood, not flagged |
| `peer_groups` table | deferred in skeleton | build (MCC × subdistrict cohort stats) from existing columns; min-cohort fallback |
| Merchant-identity ring index | not built | equality-join index on the merchant hashes + `agent_id` aggregation (§5.1) |

---

## 9. Starting thresholds (calibrate, do not enshrine)

Every number here is a *starting point* set against synthetic data and re-tuned against real dispositions in Phase 1/3:

| Detector | Start | Calibrated against |
|---|---|---|
| Amount modified-z | flag > 3.5, bump 2.5–3.5 | disposition precision/recall per lane |
| MCC IQR fence | k = 3.0 (extreme), 1.5 (mild) | cohort false-positive rate |
| Time density | below merchant's ~1st percentile | seasonal confounds (festive calendar) |
| Card swarming | ≥ 3 terminals / 15 min | ring investigations |
| Maturity (Lane A/B) | count + days, empirical | baseline stability sweep (Phase 1) |

---

## 10. Build order (feeds the next implementation plan)

0. ~~**Ingest the real schema**~~ — **done.** Model widened to §0.1, `terminal_id` dropped, JSON parser behind a format-agnostic boundary, idempotent on `payment_id`. Refunds honoured from either a status or a negative amount pending the confirmed encoding (open question Q1).
1. ~~**Robust amount baseline**~~ — **done.** Median/MAD + modified z, with the MAD=0 / min-observations / min-span guards, wired through stages 2–4.
2. ~~**Peer baselines**~~ — **done.** Both peer questions (§3.4), scored with the same modified z.
2a. ~~**Baseline integrity**~~ — **done.** Lag, `TRUE_POSITIVE` quarantine, trend detector, peer cohorts (§3.4a), and the backfill command (once-off, no UI, prints its own provisional caveat).
2b. ~~**Volume and speed**~~ — **done.** Daily count (self and peer) and peak hourly rate (§3.3a).
2c. ~~**Baseline provenance UI**~~ — **done.** Window bounds, next inclusion date, coverage, withheld days.
2d. ~~**Time and card-origin baselines**~~ — **done** (§3.2, §3.3). Hour-of-day smooths circularly; origin uses Laplace-smoothed surprisal so an unseen country is improbable rather than impossible, and a merchant that always sees foreign cards is not flagged for foreignness.

**Family A is complete.**
2e. ~~**Payment-method baselines**~~ — **done.** One amount baseline per merchant per `card_type`: a pooled baseline's spread is set by the widest rail and swallows the narrow one.
2f. ~~**Materiality floor and tunable thresholds**~~ — **done.** Statistical significance is not practical significance; thresholds live in the database with per-MCC overrides.

3. ~~**Time (circular KDE) and card-origin** baselines~~ — **done.** Festive-calendar context still outstanding.
4. ~~**Typology ruleset**~~ — **done, 2026-08-05.** All seven rules, as parameterised templates with a tuning surface (§4.1).
5. ~~**Merchant-identity rings** (§5.1)~~ — **done, 2026-08-05.** Equality-join on `hashed_br_number`/`address`/`name` + `agent_id` aggregation.
6. **Composite scoring + reasons** — *partial.* Every family carries its reasons and per-transaction evidence, and the queue ranks on sub-score. The **blend** is still one-alert-per-hit rather than a normalized multi-family composite, and the **two feedback loops** wait on label volume.
7. ~~**Card-linkage layer** (§5.2, `hashed_pan`)~~ — **built, 2026-08-05, but gated.** Card swarming, branch structuring and geo-velocity are implemented and covered by tests. The `hashed_pan` never leaves the ring module and never reaches an alert, a contribution, or the UI — enforced by test. **The security treatment named below is still outstanding**, so these three rules should run in shadow until it lands.

**Still outstanding**

| Item | Why it matters |
|---|---|
| HMAC re-hashing of `hashed_pan`, or a decision that the source hash is PAN-equivalent | Open question Q2. Until resolved, treat the column as cardholder data at rest. |
| Encryption at rest + access control on `hashed_pan` | The card-linkage rules are built against a column that is not yet protected to the standard §5.2 requires. |
| PDPO / compliance sign-off on cardholder linkage | Building a map of where each cardholder transacts needs a documented basis. |
| Festive calendar (Lunar New Year, Mid-Autumn, Golden Week) | Seasonal surges are currently flagged rather than understood. |
| Normalized multi-family composite score | Sub-scores are comparable within a family, only roughly across families. |
| The two feedback loops | Gated on label volume and inter-analyst agreement. |

Isolation Forest and the BIN / card-testing checks remain explicitly deferred / out.
