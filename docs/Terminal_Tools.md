# Terminal Tools — Data Analysis Guide

All analysis can be done from the terminal without opening a browser. These
tools inspect the pipeline's data, baselines, detection results, and support
CSV data upload.

---

## Setup

**Prerequisite:** the database must have data. Either generate synthetic data:

```bash
cd backend
compliance                        # generates synthetic merchants + runs pipeline
compliance --as-of 2026-05-01     # score a specific date instead of today
```

Or upload your own CSV (see section 4):

```bash
compliance-upload your_data.csv --run
```

---

## 1. CSV Upload — `compliance-upload`

Upload a CSV file of transactions, ingest them into the database, and
optionally run the full pipeline.

```bash
compliance-upload <file.csv>                     # ingest only
compliance-upload <file.csv> --run               # ingest + run pipeline
compliance-upload <file.csv> --dry-run           # parse and validate only
compliance-upload <file.csv> --run --as-of 2026-07-25  # specific score date
```

**Flags:**

| Flag | Effect |
|------|--------|
| `--run` | Run the full pipeline after ingestion (profile → route → detect → score) |
| `--dry-run` | Parse and validate the CSV without writing to the database |
| `--as-of` | Score date for the pipeline (YYYY-MM-DD, default: today in HKT) |
| `--batch N` | Commit every N rows (default: 5000, use smaller for less RAM) |

**Required CSV columns:**

| Column | Description |
|--------|-------------|
| `payment_id` | Unique transaction identifier (for idempotency) |
| `merchant_id` | Merchant identifier |
| `total_amount` | Transaction amount (positive number) |
| `hkt_transaction_time` | Transaction timestamp in Hong Kong local time |

**Optional CSV columns** (mapped automatically if present):

| CSV Column | Maps To |
|------------|---------|
| `mcc` | Merchant category code |
| `city` | Merchant city |
| `merchant_subdistrict` | Merchant district |
| `card_issuing_country` | Card issuing country |
| `card_type` | Card type |
| `currency` | Transaction currency |
| `net_amount` | Net amount after fees |
| `transaction_status` | Transaction status |

**Column mapping is flexible** — common variations like `amount`, `date`,
`txn_id`, `merchant` are automatically resolved to the internal field names.

**Example CSV:**

```csv
payment_id,merchant_id,total_amount,hkt_transaction_time,mcc,city
TXN001,SHOP_A,45.00,2026-07-25 10:15:00,5812,Central
TXN002,SHOP_A,32.50,2026-07-25 11:30:00,5812,Central
TXN003,SHOP_A,2800.00,2026-07-25 14:45:00,5812,Central
```

**After upload:**

1. Check what was ingested: `uv run python -m compliance.inspect_data -m SHOP_A`
2. Inspect baselines: `uv run python -m compliance.inspect_baselines -m SHOP_A`
3. Study detection results: `compliance-study SHOP_A`

**Notes:**
- Streams from disk — handles files of any size (tested with 1.8GB / 3.7M rows)
- Idempotent at the database level (`ON CONFLICT DO NOTHING`) — safe to re-run
- Merchants are auto-created from the CSV data
- Progress is printed every 100K rows (dry-run) or every batch (ingest)
- For meaningful detection, upload at least 14 days of history per merchant
- Peer detection requires multiple merchants in the same MCC

---

## 2. Merchant Study — `compliance-study`

Run all 12 detectors against a single merchant and see which ones pass, fail,
or are skipped. Shows the root cause at the bottom.

```bash
compliance-study <MERCHANT_ID>
compliance-study <MERCHANT_ID> --as-of YYYY-MM-DD
```

**Examples:**

```bash
compliance-study NIGHT       # 3AM trading — expect hour_vs_mcc_peers FAIL
compliance-study SPIKE       # huge ticket — expect amount_vs_own_baseline FAIL
compliance-study STEADY      # well-behaved — expect ALL PASS
compliance-study FLOOD       # volume burst — expect count_vs_own_baseline FAIL
compliance-study TOURIST     # foreign cards — expect foreign_card_ratio FAIL
compliance-study NEWBIE      # cold start — most detectors SKIP, peer may FAIL
compliance-study --as-of 2026-07-27 NIGHT
```

**Output columns:**

| Column | Meaning |
|--------|---------|
| `#` | Detector display order (1–12) |
| `Detector` | Internal detector name |
| `Status` | `✓ OK` (passed) · `✗ FAIL` (outlier) · `– SKIP` (baseline not usable) |
| `Merchant` | The merchant's value on the scored day |
| `Baseline` | What the baseline expected |
| `Dev` | Modified z-score, ratio, or density value |
| `Band` | `normal` · `moderate` · `outlier` |

**Available merchant IDs (synthetic):**
`STEADY` `SPIKE` `FIXED` `NEWBIE` `RAMP` `PEEROUT` `FLOOD` `BURST` `NIGHT` `TOURIST`
plus cohort fillers: `GROCER2` `GROCER3` `GROCER4` `JEWEL2` `JEWEL3` `LOCAL2` `LOCAL3`

---

## 3. Data Inspector — `python -m compliance.inspect_data`

Browse merchants and their raw transaction history.

```bash
uv run python -m compliance.inspect_data                    # list all merchants
uv run python -m compliance.inspect_data --merchant SPIKE   # transaction history
uv run python -m compliance.inspect_data -m NIGHT --tail 10 # last 10 txns only
```

**Flags:**

| Flag | Effect |
|------|--------|
| `--merchant`, `-m` | Show detailed transaction history for one merchant |
| `--tail`, `-n` | Limit to last N transactions (use with `--merchant`) |

**Output:** merchant table with MCC, lane, subdistrict, transaction count,
average amount, and ground-truth label. Transaction detail shows each txn
with timestamp, amount, and an ASCII bar chart.

---

## 4. Baseline Inspector — `python -m compliance.inspect_baselines`

See the fitted baselines that the pipeline uses for scoring. Shows amount
baselines, volume baselines, peer cohort distributions, and trading-hour
density for every merchant.

```bash
uv run python -m compliance.inspect_baselines                         # all merchants
uv run python -m compliance.inspect_baselines --merchant NIGHT        # one merchant
uv run python -m compliance.inspect_baselines -m NIGHT --kde         # with KDE plot
uv run python -m compliance.inspect_baselines --as-of 2026-07-27     # specific date
```

**Flags:**

| Flag | Effect |
|------|--------|
| `--merchant`, `-m` | Focus on a single merchant |
| `--kde` | Draw ASCII KDE probability distribution curves for trading hours |
| `--as-of` | Score date (YYYY-MM-DD, default: today in HKT) |

**Output shows per merchant:**
- Amount baseline: center (median), dispersion (MAD), method, usability
- Volume baseline: daily transaction count center and dispersion
- Peer (MCC cohort): center, dispersion, outlier fence, merchant count
- Time density: observations, bandwidth, peak hour, threshold
- Cohort hours: MCC-level trading hour pattern

The `--kde` flag draws a 72-column ASCII bar chart of the 24-hour trading
density curve, with the threshold line marked.

---

## Quick Reference

| Task | Command |
|------|---------|
| Upload CSV and run pipeline | `compliance-upload data.csv --run` |
| Upload CSV (dry run) | `compliance-upload data.csv --dry-run` |
| Generate synthetic data + pipeline | `compliance` |
| Generate synthetic data for past date | `compliance --as-of 2026-05-01` |
| See why a merchant was flagged | `compliance-study NIGHT` |
| Check if a merchant is clean | `compliance-study STEADY` |
| Browse all merchants | `uv run python -m compliance.inspect_data` |
| See raw transactions for SPIKE | `uv run python -m compliance.inspect_data -m SPIKE` |
| Inspect baselines for all | `uv run python -m compliance.inspect_baselines` |
| See KDE curve for NIGHT | `uv run python -m compliance.inspect_baselines -m NIGHT --kde` |
| Score a past date | `compliance-study NIGHT --as-of 2026-07-20` |

---

## Typical Workflow

```bash
# 1. Upload your CSV data
compliance-upload transactions.csv --run

# 2. See which merchants were flagged
uv run python -m compliance.inspect_data

# 3. Study a specific merchant's detectors
compliance-study SHOP_A

# 4. Inspect the baselines that drove the results
uv run python -m compliance.inspect_baselines -m SHOP_A --kde

# 5. Upload more data (idempotent — skips duplicates)
compliance-upload next_batch.csv --run

# 6. Re-study with updated baselines
compliance-study SHOP_A
```
