# Open Questions for Review

*Decisions that need a supervisor, compliance, legal, or source-system answer before the affected work can proceed. Living document — add as they arise, mark resolved with the decision and date.*

**Last updated:** 2026-08-10

Each item: what we need to know · why it matters · what each answer implies · who owns it · what it blocks.

---

## A. Raised this session — needed to turn the detection design into a build plan

### Q1. How do refunds appear in the transaction schema?

- **The question.** The source schema has no explicit refund flag. Refunds are presumably encoded either in `transaction_status` or as the sign of `net_amount` / `total_amount`. Which is it, and what exactly does a refund row look like?
- **Why it matters.** Refund-ratio rules, refund-abuse detection (refunds to a different card than the original charge), and the bust-out typology (build-up → spike → refund surge) all depend on identifying refunds precisely. Guessing wrong means either missing refund-based laundering or miscounting legitimate returns.
- **Implications.** If it's a `transaction_status` value, the rule reads that field. If it's a sign on `net_amount`, we detect on the sign and must make sure aggregates (daily volume, ticket median) handle negatives correctly.
- **Owner:** data / source-system team.
- **Blocks:** refund-ratio rule, refund-abuse typology, bust-out typology, and correct amount baselines.

### Q2. Do we control the `hashed_pan` hashing, or does the source hand it to us finished?

- **The question.** The `hashed_pan` is a 1:1 (deterministic, unsalted) hash so it can be joined across merchants. An unsalted PAN hash is reversible by brute force (fix the BIN + Luhn digit → ~10⁹ candidates → recoverable in seconds). Do we perform the hashing ourselves, or receive it already hashed?
- **Why it matters.** It decides whether we can protect the identifier properly.
  - If **we control the hashing:** switch to a **keyed hash (HMAC, key stored separately)** — still joinable for detection, but not brute-forceable without the key.
  - If **the source hands it to us finished (plain hash):** we must treat it as **PAN-equivalent sensitive data** — encrypt at rest, strict access control, never in the analyst UI.
- **Also a privacy question.** Tracing one card across merchants builds a map of where each cardholder shops → a PDPO consideration (cross-merchant cardholder linkage), justified by AML purpose but needing a documented basis and access control.
- **Owner:** data / source-system team + compliance/legal.
- **Blocks:** the card-linkage ring layer (last build step). Does **not** block the merchant-identity ring layer (which only equality-joins merchant hashes, never cardholder data), nor any per-merchant baseline. Time to resolve, but worth starting early.

---

## B. Standing compliance / legal items (Phase 0 workshop)

### Q3. Hong Kong regulatory position for our licence category

- Confirm the exact AML obligations under **AMLO (Cap. 615)**: suspicious-transaction reporting to the **JFIU**, recordkeeping, and any SFC/HKMA guidance applicable to our licence. (The earlier draft mistakenly cited US FinCEN/SAR rules — corrected, but the real HK obligations need confirming with counsel.)
- **Owner:** compliance + legal. **Blocks:** the action/STR workflow, any go-live.

### Q4. Merchant-agreement hold rights, tipping-off, approval chain

- Confirm the merchant agreement already grants the right to place reserves / hold settlement on suspicion (the system enforces a policy; it does not create the legal right).
- Confirm tipping-off constraints (OSCO s.25A) — the platform must never send a merchant-facing message on a flagged/STR account.
- Confirm the maker-checker approval chain for any fund-affecting decision (recorded here, executed externally).
- **Owner:** compliance + legal. **Blocks:** follow-through board action semantics, go-live.

### Q5. Cross-merchant cardholder linkage (PDPO basis)

- Document the lawful basis and access-control regime for building cross-merchant card-tracking from `hashed_pan` (see Q2). Related but distinct from the hashing-control question.
- **Owner:** compliance/legal. **Blocks:** card-linkage ring layer.

### Q6. Data retention

- How long are raw transactions retained locally? A compliance/legal answer, not an engineering one. Carried as a configurable value in the meantime.
- **Owner:** compliance/legal. **Blocks:** nothing immediately; needed before go-live.

---

## C. Scope confirmations

### Q7. Cardholder-fraud checks — confirm they stay out

- Impossible geo-velocity and BIN/card-testing detection are now *technically* feasible with `hashed_pan`, but they catch **stolen-card / cardholder fraud** — a different objective with a different owner (issuer / real-time fraud team). The plan scopes them out; the merchant-side decline-ratio rule is the in-scope substitute.
- Confirm: do these stay out of this pipeline (recommended), and is there a fraud team that should consume the same identifier via a noted integration point?
- **Owner:** supervisor / product. **Blocks:** nothing; a scope confirmation.

---

## D. Product / operational items

### Q8. Reason-code taxonomy v1

- The controlled vocabulary analysts pick from when closing an alert (the labels that train the model) must be drafted **with** the analysts before Phase 2. A placeholder set exists in the plan; it needs their sign-off. This is the highest-leverage hour in the project and the cheapest to skip.
- **Owner:** compliance SME + analysts. **Blocks:** the training loop (labels are only as good as the taxonomy).

### Q9. Case-staleness threshold

- How many days without an update before a confirmed case is flagged as stale on the follow-through board? A policy input; carried as a configurable value.
- **Owner:** compliance lead. **Blocks:** nothing; a setting.

### Q10. Frontend component approach (bespoke vs shadcn/ui)

- The platform spec named `shadcn/ui`; the current build is bespoke Tailwind + custom CSS (polished, but not shadcn). A conscious call before the UI grows: keep bespoke, or adopt shadcn now while the surface is small.
- **Owner:** engineering / whoever maintains the UI. **Blocks:** nothing; a direction decision.

---

## C. Raised 2026-08-10 by the read-path and correctness work (PR #9)

*Four decisions the PR deliberately did not make, because each one is a calibration, policy or deployment call rather than an engineering one. None blocks the merge; all four should be answered before the next tuning cycle.*

### Q11. `MIN_OBSERVATIONS = 20` on the now foreign-only card-origin mix

- **The question.** The origin mix is now built over overseas issuers only — home cards are excluded, by instruction, because Hong Kong is the overwhelming majority for nearly every merchant and it set the scale for what counted as surprising. Measured on overseas cards alone, far fewer merchants have enough history: merchants clearing the threshold fall from **3,400 to 664** on the current extract. Is 20 still the right bar, and what should it be?
- **Why it matters.** The threshold now means something different from what it meant when it was set. Twenty observations of a mix dominated by home cards is a low bar; twenty *foreign* cards is a high one. The detector is not weaker — a merchant with 1,000 home cards and 4 foreign ones previously had a "usable" mix in which its first tourist was maximally surprising by construction, and is now correctly skipped — but coverage dropped by 80% and that is a conscious trade, not an accident.
- **Implications.** Lowering it buys coverage at the cost of scoring merchants against a handful of observations, which is the failure mode the threshold exists to prevent. Leaving it at 20 means roughly four in five merchants are never checked on this signal. Either is defensible; neither should be chosen by default. The right input is dispositions — how many of the alerts this detector has raised were confirmed — not a number picked in advance.
- **Owner:** compliance lead, with the analysts' disposition history.
- **Blocks:** nothing. It is a one-line constant in `detection/profiles.py`. Deliberately left alone rather than tuned to taste.

### Q12. How should AUTHORIZED and PENDING transactions be treated?

- **The question.** `settled_sale()` now excludes DECLINED, CANCELLED, REVERSED and VOIDED from every baseline, and the new failed-transaction detectors take those four as their subject. The extract also carries **AUTHORIZED (26 rows, average HKD 14,135)** and **PENDING (9 rows)**, which are neither settled nor failed — they are in flight. They currently count as settled. Is that right, and does the source ever update these rows to a terminal status?
- **Why it matters.** The row counts are negligible, but the AUTHORIZED average is fifty times a successful transaction's, so a handful of them can move a small merchant's ticket baseline. More importantly, if the source *does* later update these rows, then a baseline fitted tonight and the same baseline refitted next week disagree about the same historical day — which breaks the reproducibility the pipeline is built on.
- **Implications.** If they settle later, they should be excluded until they do. If they are terminal states that simply never resolve, treating them as settled is defensible for the 26 rows but should be a recorded decision rather than a default. The same question applies to the **8,078 rows carrying no status at all**, which are currently kept everywhere on the reasoning that unknown is not failed.
- **Owner:** data / source-system team, for the factual half; compliance lead for the treatment.
- **Blocks:** nothing today. Would block any claim that a refitted baseline reproduces an earlier one exactly.

### Q13. Historic pipeline runs carry no record of the parameters they used

- **The question.** `pipeline_runs` records the thresholds and rules in force when each run started, which is what makes a parameter change reviewable. Runs that predate the table were reconstructed by the migration from the dates their alerts were written, and carry **no settings snapshot** — the screen says so explicitly rather than showing an empty table. Does anything need to be reconstructed for those runs, and is the reconstruction itself acceptable for audit?
- **Why it matters.** Two limits are worth stating plainly. The reconstruction groups by the calendar day alerts were written, so **two runs on one day would collapse into one**, and the parameters those runs used were never recorded anywhere — they cannot be recovered, only guessed at. Writing today's thresholds into a historic run would have asserted something nobody verified, so the migration deliberately did not.
- **Implications.** If an auditor needs to know what the 6 August run scored under, the honest answer is that it was not recorded. If that is unacceptable, the gap needs documenting as a known limitation with a start date, rather than being papered over. Everything from PR #9 onward captures it properly.
- **Owner:** compliance lead, with whoever owns the audit position.
- **Blocks:** nothing. Affects what can be claimed about runs before 2026-08-10.

### Q14. Migration backfills are Postgres-only

- **The question.** The `alert_type` and `pipeline_runs` backfills use Postgres-specific SQL — `->` / `->>` JSON operators and `RETURNING`. The test suite runs against SQLite in memory, so the backfills are not exercised there. Is Postgres the only target that will ever run migrations?
- **Why it matters.** Today the answer is plainly yes and the code is correct. It is written down because the divergence is invisible: the schema is defined once in SQLAlchemy and works on both, so a green test suite says nothing about whether the migrations run. Anyone who later points migrations at another engine will find out at deploy time.
- **Implications.** If Postgres-only is a standing commitment, this is a note and nothing more. If not, the backfills need portable equivalents and coverage that actually runs them.
- **Owner:** engineering.
- **Blocks:** nothing. A constraint to be aware of, not a defect.

### Also noted, lower priority

Observations from the same work that need a decision eventually but are not urgent:

- **`MIN_RATIO_DISPERSION = 0.02` is now load-bearing.** The failed-transaction detectors have a median baseline of zero across the portfolio, so the dispersion floor is the only thing setting their threshold — it puts it near 10% of attempts rather than at the first decline. Changing that constant now moves a detector, which was not true when it was set.
- **The pooled amount baseline still mixes wallet rails with cards.** Per-rail baselines exist and mitigate it, but `amount_vs_own_baseline` fires on the pooled, bimodal distribution. Related: MCC peer medians are confounded by wallet mix — two merchants in one MCC with different rail splits have different median tickets for reasons unrelated to risk.
- **The three dispositions in the store are on merchant `PEEROUT`, decided by an analyst named `analyst`.** If they are test data, they are currently quarantining real days out of real baselines. Worth confirming before the next run rather than after.
- **The case page recomputes Family A and Family B verdicts on every open** (~0.08s, so not hurting). It is the same compute-on-read shape the rest of this work moved into the nightly run, and worth revisiting if the detector suite grows.

---

## Resolved

*(none yet — move items here with the decision and date as they're answered)*
