# 🛠️ Compliance Pipeline Feedback & Engineering Requirements

**Test Run Parameters:**  
* **Execution Mode:** Manual Terminal Execution  
* **Target `as_of` Date:** `2026-05-01`  
* **Historical Data Scope:** Full month of April 2026 data  
* **Baseline Configuration:** 30-day rolling window, lagged by 7 days  

---

## 🟢 1. What Works (Validated)
* **Merchant Classification:** Lane A (Mature) vs. Lane B (New/Low-Data) segmentation executed cleanly and correctly identified merchant types.
* **Core Risk Detection:** Systemic discrepancy scoring (Merchant Baseline vs. Peer/MCC/District Baseline) successfully triggered flagged alerts.

---

## 🔴 2. Core Problem: The Frontend is a "Black Box"
While the underlying math and backend engine are working, **the compliance dashboard currently leaves analysts uninformed**. A compliance officer cannot see *why* an alert fired, *what* transactions caused it, or *how* the statistical baseline calculated the anomaly. These reasons that are being raised are not useful for any decision making because just by reading it I don't get any understanding of the underlying cause of the alert, might aswell see it myself if that's the case. But our goal is to reduce the friction of the process so we must be clear.

We need to convert the frontend into an **Explainable Compliance Workbench**.

---

## 📋 3. Specific Changes & Feature Requirements

### A. Remove Ambiguous Terminology (Precision in UI)
* ❌ **Current UI:** Displays vague alerts like `"Amount too high for this category"`.
* ✅ **Required UI:** Eliminate the word "category." Be mathematically and operationally precise.
  * **New Alert Title:** `Amount Anomaly vs. MCC Baseline`
  * **Required Metadata Display:** Explicitly show both the **MCC Code** (e.g., `MCC 5812`) and the **Human-Readable MCC Description** (e.g., `Eating Places and Restaurants`).

---

### B. Explicit Alert Queue Categorization & Badging
The main Alert Queue must immediately distinguish between **Single-Transaction Outliers** and **Merchant-Level Peer Discrepancies**. Every card in the queue must include an **Alert Type Badge**:

| Alert Type Badge | Trigger Condition | Primary Cause |
| :--- | :--- | :--- |
| **`Single Txn Spike`** | A single checkout exceeded absolute merchant bounds on the target day. | Outlier single transaction value ($M_i > 3.5$). |
| **`MCC Peer Discrepancy`** | The merchant's overall daily profile strays too far from peer MCC averages. | Macro business divergence from category norms. |
| **`Subdistrict Anomaly`** | Terminal location or card origin distribution strays from local area baseline. | Geographic/Foreign card ratio mismatch. |
| **`Temporal Anomaly`** | Transactions occurred outside historical activity hours. | Time-of-day KDE distribution mismatch. |

---

### C. Comprehensive Merchant Header (Review Page)
When clicking **"Review"** on any alert, the top panel of the review page must display a complete **Merchant Information Summary**:

(Although Merchant name is hidden for security reasons, the UI should support it.)

```
+---------------------------------------------------------------------------------------------------+
| Merchant Name: Super Mart TST                     Merchant ID: MID-889124                        |
| MCC Code: 5411 (Grocery Stores & Supermarkets)    Location: Tsim Sha Tsui (Yau Tsim Mong District)|
| Operational Lane: Lane A (Mature - 30D Baseline)  Target Evaluation Date: 2026-04-30             |
+---------------------------------------------------------------------------------------------------+
```

---

### D. The "View Data" Diagnostic Panel (Explainable AI / Stats)
When an analyst opens the review screen, the frontend must provide **two diagnostic tabs**:

#### Tab 1: Daily Transaction Ledger (Target `as_of` Date)
* Must show **all individual transactions** executed by the merchant on the target evaluation day (e.g., `2026-04-30` for an `as_of = 2026-05-01` run).
* Allows the analyst to trace exact customer checkouts that contributed to the day's volume.

#### Tab 2: Statistical & Visual Proof ("View Data")
Depending on the alert type, this tab must show the exact math and visual distribution models that guided the algorithm:

1. **For MCC / District / Peer Discrepancies:**
   * **Statistical Summary Table:** Display Merchant Value vs. Peer Group Value side-by-side.
     * *Metrics required:* **Mean**, **Median**, **MAD Score**, **Modified Z-Score**, and **Sample Size ($N$)**.
   * **Visual Plot:** A **Box Plot / Histogram Overlay** showing where this merchant's daily metric sits along the distribution curve of their MCC peers.

2. **For Time-of-Day Alerts:**
   * **Visual Plot:** A **Kernel Density Estimation (KDE) Plot** showing the merchant's standard historical operating hours curve versus spikes on the target date.

3. **Think about anything else that might be useful to show given the context.**

---

### E. Date Handling & Zero-Alert Dashboard State
* **Date Alignment:** Running `as_of = 2026-05-01` evaluates transactions from **2026-04-30** (the most recently completed 24-hour cycle of April data). 
* **Zero-Alert State:** If zero single-transaction outliers occurred on `2026-04-30`, the dashboard must **not** show a blank screen. It must explicitly state:
  > *"0 Single-Transaction Anomalies Flagged for 2026-04-30. Active queue consists entirely of Systemic Merchant-Level Peer Discrepancies (evaluated against the 30-day baseline lagged by 7 days)."*

---

## 🚀 Actionable Developer Checklist

- [ ] **Backend (FastAPI / Data Layer):**
  - [ ] Include `mcc_description`, `district`, `subdistrict`, and `lane_type` in the `GET /v1/alerts/{alert_id}` response payload.
  - [ ] Expose an endpoint `GET /v1/merchants/{merchant_id}/transactions?date=YYYY-MM-DD` to serve the full single-day transaction ledger.
  - [ ] Package raw statistical outputs (`mean`, `median`, `mad`, `modified_z_score`, `peer_median`) into the diagnostic alert payload.

- [ ] **Frontend (UI / UX):**
  - [ ] Replace generic text ("category") with exact MCC Code + Description labels.
  - [ ] Implement Alert Type Badges in the main Alert Queue.
  - [ ] Build the Merchant Metadata Header component at the top of the Review view.
  - [ ] Add the "Daily Transactions" table and "Statistical Proof / View Data" diagnostic tabs (integrating chart libraries for KDE and Box Plot rendering).

- [ ] **Think about additional improvements based on the feedback given in order to make the dashboard more useful and to reduce the friction of the process.**