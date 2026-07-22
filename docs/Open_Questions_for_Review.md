# Open Questions for Review

*Decisions that need a supervisor, compliance, legal, or source-system answer before the affected work can proceed. Living document — add as they arise, mark resolved with the decision and date.*

**Last updated:** 2026-07-22

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

## Resolved

*(none yet — move items here with the decision and date as they're answered)*
