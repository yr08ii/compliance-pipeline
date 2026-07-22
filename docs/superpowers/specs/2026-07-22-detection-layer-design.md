# Detection Layer — Design Specification

*The statistics and detectors behind the alerts. Refines the flat stub into the real Lane A/B engine.*

**Date:** 2026-07-22 · Draft v1
**Status:** Design — pending review
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

**Technical care:** time-of-day is *circular* — 23:30 and 00:30 are 60 minutes apart, not 23 hours. Use a circular kernel (map time onto the unit circle) or the density is wrong at the midnight boundary. Flag when the density at the observed time falls below a low percentile of the merchant's own density (calibrated), not an absolute probability.

`feature_snapshot` entry: `{feature_name: "txn_hour", merchant_value: hour, baseline_value: <modal/active window>, deviation: <how far into the tail>}` — rendered as "3:30am, outside the 9am–9pm active window."

### 3.3 Card-origin baseline — categorical

Maintain the merchant's historical distribution of card *issuer countries* (derived from BIN → issuer-country lookup; **no PAN needed** for this one). A sudden surge of a rare-for-this-merchant origin is the signal. Score by how improbable the observed mix is under the historical categorical distribution.

`feature_snapshot`: `{feature_name: "foreign_card_share", merchant_value: 0.72, baseline_value: 0.05, deviation: <ratio or surprisal>}`.

### 3.4 Peer baselines — MCC and subdistrict

For merchants (mature or new), compare against the cohort, not just their own history:

- **MCC amount** — IQR upper fence across all same-MCC merchants: flag `x > Q₃ + k · IQR` (k = 1.5 mild, 3.0 extreme). Answers "high even for a jewelry shop."
- **MCC time** — the cohort's active-hours density.
- **Subdistrict amount / card-origin** — the district's norms (Mong Kok ≠ airport for foreign-card share); flag deviation from the district's foreign-card ratio.

Peer baselines require a **minimum cohort size**; a 3-merchant MCC cohort is not a distribution. Fall back MCC×subdistrict → MCC-only → network-wide as cohorts thin out.

`peer_groups` stores `{mcc, subdistrict, n_merchants, amount_q1, amount_q3, active_hours_kde_ref, foreign_card_ratio, ...}`, refreshed nightly.

### 3.5 The cold-start (new / Lane B) path

New merchants have no own baseline, so — exactly as the diagram shows — they get `Ruleset′ (mcc)`: **peer-derived rules and thresholds**, not a peer anomaly *model*. The distinction matters for fairness: scoring a legitimately-unusual new merchant against a peer *model* over-flags it; applying sensible MCC-calibrated *caps* (velocity, single-ticket, refund ratio) does not pretend to know its normal. Lane B stays rule-based until it graduates.

> **Graduation is a risk moment.** A bust-out operator builds "normal" history precisely to cross Lane B → A. Log every crossover; do not soften thresholds the instant a merchant graduates.

---

## 4. Family B — the typology ruleset (the `+ Ruleset` box)

Statistical baselines do not catch patterns that are individually unremarkable. These are rules, per-lane calibrated, each emitting a reason code:

| Typology | Signal (not a per-feature deviation) |
|---|---|
| Structuring / smurfing | clustering of amounts just under a reporting/review threshold; velocity of near-threshold txns |
| Refund / credit abuse | high refund ratio; refunds to a *different* card than the original charge |
| Bust-out | build-up then abrupt volume/ticket spike then refund surge / settlement pull |
| Dormant reactivation | long inactivity → sudden high-value velocity |
| Rapid movement | funds land and are routed out with no resting balance |

These populate `triggering_detectors` with a rule name and a sub-score. Where a rule has a natural numeric basis (refund ratio), also emit a `feature_snapshot` row so the panel can show it.

---

## 5. Family C — cross-merchant / ring detection (hashed-PAN layer)

Enabled by the **1:1 hashed PAN** available in the source data — a stable per-card identifier that lets us trace one card across terminals *without storing the raw PAN*. This unlocks the merchant-integrity checks the per-merchant flow structurally cannot see:

- **Card swarming** — one card across ≥ N terminals in a short window (ring moving through a district's merchants).
- **Terminal hopping / collusion** — clusters of cards following the same terminal→terminal path (colluding merchants distributing synthetic sales to stay under each single-merchant radar).
- **Cross-terminal structuring** — one card/account's aggregate split across many merchants inside 24h to dodge thresholds.

These are **in scope** — they detect *merchant* coordination, our objective.

### 5.1 Security & privacy treatment (mandatory — this changes our data posture)

A hash that is 1:1 and joinable is deterministic and unsalted **by construction**, and an unsalted hash of a PAN is reversible: fixing the BIN and the Luhn digit leaves only ~10⁹ candidates, so a plain SHA-256 is brute-forced back to the card number in seconds (PCI SSC warns of exactly this). Therefore:

- The hashed PAN is **sensitive data**, not a safe token. It must be encrypted at rest, access-controlled, and excluded from the analyst-facing UI and from `feature_snapshot`. Our earlier "nothing sensitive lives here" posture must be updated to acknowledge this identifier.
- **Prefer a keyed hash (HMAC with a secret key held separately from the data).** Still deterministic and joinable for detection; not brute-forceable without the key. The key becomes the protected secret. If the source hands us a plain hash, treat it as PAN-equivalent.
- Building a cross-merchant map of where each cardholder transacts is **cardholder linkage** — a PDPO consideration justified by the AML purpose, but requiring access control and a documented basis. Flag for compliance/legal, like the fund-hold rules.

### 5.2 What stays out

**Impossible geo-velocity** and **BIN / card-testing attacks** are now *technically* feasible with the hashed PAN, but they detect **cardholder / stolen-card fraud** — a different objective with a different owner (issuer / real-time fraud), explicitly out of scope in the platform spec. Keep them out of this pipeline; note the integration point so a fraud team can consume the same identifier. Revisit only as a deliberate scope decision.

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

## 8. Data gaps to close before build

| Need | Status | Action |
|---|---|---|
| Hashed PAN in the store | not ingested (only `card_bin` today) | add ingestion; HMAC-key treatment (§5.1); encrypt + access-control |
| BIN → issuer-country table | not present | source a BIN reference table (for card-origin baseline) — no PAN needed |
| Subdistrict geo on merchant/terminal | only `geo="HK"` today | enrich merchant/terminal with district/subdistrict |
| Festive calendar | not present | HK calendar (Lunar New Year, Mid-Autumn, Golden Week) — feeds the time/seasonality context so surges are understood, not flagged |
| `peer_groups` table | deferred in skeleton | build (MCC × subdistrict cohort stats), min-cohort fallback logic |

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

1. **Robust amount baseline** (Median/MAD + modified z, with the MAD=0 and min-n guards) replacing the stub — Lane A, fully explainable, drops into the existing divergence panel.
2. **Peer baselines** (`peer_groups` + MCC IQR) and the Lane B `Ruleset′(mcc)` cold-start path.
3. **Time (circular KDE) and card-origin** baselines; festive-calendar context.
4. **Typology ruleset** (structuring, refund abuse, bust-out, dormant, rapid movement).
5. **Cross-merchant / ring layer** (hashed PAN) — *after* the §5.1 security treatment (HMAC key, encryption, access control, compliance sign-off) is in place.
6. **Composite scoring + reasons**, then the **two feedback loops** once labels accumulate.

Each is its own spec → plan → build increment. Isolation Forest and the cardholder-fraud checks are explicitly deferred / out.
