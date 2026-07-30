# Explainable Workbench + Scored-Day Fix — Changelog

**Date:** 2026-07-29
**Responding to:** `docs/feedback/feedback01.md`

---

## The finding that mattered most

The feedback reported a UI symptom: *"0 single-transaction anomalies flagged;
the queue is entirely systemic merchant-level discrepancies"*, and asked for a
message explaining it.

That was not a display gap. The scored window was `occurred_at >= as_of`,
open-ended and off by one:

* `--as-of 2026-05-01` against data ending 2026-04-30 selected **nothing**.
  Only the detectors that ignore scored-day data (`merchant_level_vs_mcc_peers`,
  `level_shift_ramp`) could fire. The queue looked systemic because nothing
  else had been examined.
* Unbounded, a re-run over history also swept in every later transaction,
  mixing unrelated days into one verdict.

The requested message would have papered over a detection gap.

**Fixed.** `scored_day_bounds()` defines it once: the half-open local day
before `as_of`, anchored to Hong Kong midnight. The same run now fires ten
detector families, including the single-transaction ones.

| | Before | After |
|---|---|---|
| Detector families firing | 2 | 10 |
| `amount_vs_own_baseline` | 0 | 4 |
| `burst_rate_vs_own_baseline` | 0 | 1 |
| `count_vs_own_baseline` | 0 | 1 |

---

## Data loss during this work — read this

Reproducing the reported run with `compliance-run --as-of 2026-05-01`
**destroyed the loaded dataset**: 3.7M transactions across 5,490 merchants.
`compliance-run` calls `_reset_demo_data()` — truncating every table — before
generating synthetic demo data. The name suggests "run the pipeline"; it does
not warn.

Now guarded:

* `compliance-run` refuses over a non-empty store, reports what it found, and
  names the safe command. `--force` overrides.
* **`compliance-pipeline`** added: scores what is already loaded, deletes
  nothing. This is what a nightly run, a backfill, or a re-score should use.

Re-ingest with `compliance-upload <file.csv>` then
`compliance-pipeline --as-of YYYY-MM-DD`.

---

## Changes by feedback section

### A. Ambiguous terminology

"Category" is gone. Labels now name the baseline compared against:

| Before | After |
|---|---|
| Large transaction for this category | Amount anomaly vs MCC baseline |
| Typical amount high for this category | Merchant level vs MCC baseline |
| Unusually large transaction | Amount anomaly vs own baseline |
| Transaction amounts trending up | Sustained level shift (7d vs 90d) |

MCC code and description appear together everywhere. Descriptions resolve
portfolio-wide — the description belongs to the code, not the merchant row, so
a merchant missing one borrows what its peers carry.

The jargon test was inverted: it previously rejected "baseline" and "MCC" as
jargon. Those are the analyst's vocabulary and the feedback asked for them. It
now rejects vague words instead — "category", "sale", "too high".

### B. Alert type badges

Four badges, every detector mapped, enforced both ways by tests so a new
detector cannot ship unbadged:

| Badge | Detectors |
|---|---|
| Single txn spike | amount / count / burst vs own baseline |
| MCC peer discrepancy | ticket, merchant level, count vs MCC; level shift |
| Subdistrict anomaly | ticket vs subdistrict; foreign card ratio; card origin |
| Temporal anomaly | hour vs own pattern; hour vs MCC hours |

Filterable in the queue with counts. Empty categories show disabled, not
hidden.

### C. Merchant header

Merchant ID, MCC code + description, district and subdistrict, group, scored
date, business nature, status. Merchant name renders as "Withheld (hashed at
source)" rather than being omitted.

### D. Diagnostic tabs

* **Why it fired** — all 12 checks with verdict, merchant value, baseline,
  deviation and a sentence each. Passes shown too: they say what was ruled out.
* **Transactions** — the scored day's ledger, rows breaching the merchant's own
  threshold marked. `hashed_pan` never crosses the API boundary.
* **Statistical proof** — mean/median/MAD/N for merchant and both peer cohorts,
  modified z-score, baseline window provenance, plus a log-scale peer box plot
  and a trading-hours density plot with the scored day as rug marks.

Plots are inline SVG (the platform runs air-gapped), each with an aria-label
summary, marking the merchant by shape and label rather than colour alone.

Mean sits beside median deliberately: the gap is the right-skew that makes
mean-based baselines unusable, worth showing rather than asserting.

### E. Date handling

`Alert.as_of` added — `created_at` is wall-clock, so a backfill mislabelled
which day it evaluated. The queue banner and review header both state the
scored day.

---

## New endpoints

```
GET /api/alerts[/{id}]                        + merchant identity, scored date, alert type
GET /api/alerts/{id}/diagnostics              12 verdicts, statistics, KDE curves, peer distribution
GET /api/merchants/{id}/transactions?date=    the day's ledger, outliers flagged
```

Diagnostics reuse `pipeline.merchant_study` — the same pure function
`compliance-study` calls, so the screen and the terminal cannot disagree.

## Tests

128 passing (was 102). New: scored-day semantics, destructive-command guard,
diagnostics payload, alert-type mapping completeness.

## Still open

* Thresholds remain uncalibrated starting points.
* Families B and C unbuilt; Lane B merchants are covered only by cohort tests.
* Q1 (refund encoding) and Q2 (`hashed_pan` hashing control) still open.
