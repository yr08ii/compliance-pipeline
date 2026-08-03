import { useState } from "react";
import { apiSend, type AlertOut } from "../api/client";
import { Card } from "../lib/ui";
import { cn } from "../lib/utils";

/** Controlled vocabulary, split by verdict. A reason code is what the model
 *  learns from, so free text alone would leave the loop with nothing usable. */
const TRUE_REASONS: [string, string][] = [
  ["STRUCTURING_CONFIRMED", "Structuring — amounts split to stay under thresholds"],
  ["UNEXPLAINED_ACTIVITY", "Merchant could not explain the activity"],
  ["MCC_MISMATCH", "Activity inconsistent with the declared business"],
  ["REFUND_ABUSE", "Refunds used to move value"],
  ["SUSPECTED_LAUNDERING", "Pattern consistent with laundering"],
  ["OTHER_CONFIRMED", "Other — described in notes"],
];

const FALSE_REASONS: [string, string][] = [
  ["SEASONAL_PROMOTION", "Seasonal or promotional surge"],
  ["VERIFIED_BUSINESS_EXPANSION", "Genuine business growth, verified"],
  ["LEGITIMATE_LARGE_SALE", "One-off legitimate large transaction"],
  ["KNOWN_CUSTOMER_PATTERN", "Known and expected customer behaviour"],
  ["DATA_QUALITY", "Data quality issue, not merchant behaviour"],
  ["OTHER_CLEARED", "Other — described in notes"],
];

type Verdict = "TRUE_POSITIVE" | "FALSE_POSITIVE" | "INCONCLUSIVE";

export function DecisionPanel({
  alert,
  onDecided,
}: {
  alert: AlertOut;
  onDecided: () => void;
}) {
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [reason, setReason] = useState("");
  const [notes, setNotes] = useState("");
  const [risk, setRisk] = useState("REGULATORY");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reasons =
    verdict === "FALSE_POSITIVE" ? FALSE_REASONS : verdict === "TRUE_POSITIVE" ? TRUE_REASONS : [];

  async function submit() {
    if (!verdict || !reason) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend(`/api/alerts/${alert.id}/disposition`, {
        verdict,
        reason_code: reason,
        risk_axis: risk,
        analyst_id: "analyst",
        notes: notes || null,
      });
      onDecided();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-[1.02rem]">Decision</h2>
            <p className="mt-0.5 text-[0.86rem] text-[var(--muted)]">
              Recorded here for the audit trail and the training loop. Any real-world
              action happens in the payment and filing systems.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                setVerdict("TRUE_POSITIVE");
                setReason("");
              }}
              className={cn(
                "focus-ring rounded-[var(--radius)] px-4 py-2.5 text-[0.9rem] font-semibold transition",
                verdict === "TRUE_POSITIVE"
                  ? "bg-[var(--danger)] text-white"
                  : "border border-[#f0cfcd] bg-[var(--danger-bg)] text-[var(--danger)] hover:brightness-95"
              )}
            >
              Confirm alert
            </button>
            <button
              type="button"
              onClick={() => {
                setVerdict("FALSE_POSITIVE");
                setReason("");
              }}
              className={cn(
                "focus-ring rounded-[var(--radius)] px-4 py-2.5 text-[0.9rem] font-semibold transition",
                verdict === "FALSE_POSITIVE"
                  ? "bg-[var(--success)] text-white"
                  : "border border-[#c6e3d1] bg-[var(--success-bg)] text-[var(--success)] hover:brightness-95"
              )}
            >
              False alert
            </button>
          </div>
        </div>

        {verdict && (
          <div className="mt-5 space-y-4 border-t border-[var(--border)] pt-4">
            {/* What each verdict does downstream — stated, because the
                consequence for the baseline is not obvious from the button. */}
            <p
              className={cn(
                "rounded-[var(--radius)] px-4 py-2.5 text-[0.88rem] leading-6",
                verdict === "TRUE_POSITIVE"
                  ? "bg-[var(--danger-bg)] text-[var(--danger)]"
                  : "bg-[var(--success-bg)] text-[var(--success)]"
              )}
            >
              {verdict === "TRUE_POSITIVE"
                ? "Opens a case for follow-up, and excludes this day from future baselines so the system does not learn the activity as normal."
                : "Keeps this day in the baseline data — the trading was legitimate, and removing it would teach the system that normal trading is abnormal."}
            </p>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block">
                <span className="text-[0.8rem] font-medium text-[var(--text-strong)]">
                  Reason <span className="text-[var(--danger)]">*</span>
                </span>
                <select
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="focus-ring mt-1 w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.9rem]"
                >
                  <option value="">Select a reason…</option>
                  {reasons.map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="text-[0.8rem] font-medium text-[var(--text-strong)]">
                  Risk type
                </span>
                <select
                  value={risk}
                  onChange={(e) => setRisk(e.target.value)}
                  title="Regulatory risk is a reporting obligation; commercial risk is exposure to loss. They are tuned and escalated differently."
                  className="focus-ring mt-1 w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.9rem]"
                >
                  <option value="REGULATORY">Regulatory — reporting obligation</option>
                  <option value="COMMERCIAL">Commercial — exposure to loss</option>
                  <option value="BOTH">Both</option>
                </select>
              </label>
            </div>

            <label className="block">
              <span className="text-[0.8rem] font-medium text-[var(--text-strong)]">
                Notes
              </span>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="What you checked, who you spoke to, what they said."
                className="focus-ring mt-1 w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.9rem]"
              />
            </label>

            {error && (
              <p className="rounded-[var(--radius)] bg-[var(--danger-bg)] px-4 py-2.5 text-[0.88rem] text-[var(--danger)]">
                {error}
              </p>
            )}

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={submit}
                disabled={!reason || busy}
                className="focus-ring rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2.5 text-[0.9rem] font-medium text-white transition hover:bg-[var(--navy-700)] disabled:cursor-not-allowed disabled:opacity-45"
              >
                {busy ? "Recording…" : "Record decision"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setVerdict(null);
                  setReason("");
                  setError(null);
                }}
                className="focus-ring rounded-[var(--radius)] px-3 py-2.5 text-[0.9rem] text-[var(--muted)] hover:text-[var(--text-strong)]"
              >
                Cancel
              </button>
              {!reason && (
                <span className="text-[0.82rem] text-[var(--muted)]">
                  A reason is required — it is what the model learns from.
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}
