import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";
import { useGlossary } from "../api/glossary";
import { Card, ErrorNote, Pill, type Tone } from "../lib/ui";
import { IconChevron } from "../lib/icons";
import { cn } from "../lib/utils";

const TYPE_TONE: Record<string, Tone> = {
  single_txn_spike: "danger",
  mcc_peer_discrepancy: "blue",
  subdistrict_anomaly: "warning",
  temporal_anomaly: "navy",
};

const TYPE_ORDER = [
  "single_txn_spike",
  "mcc_peer_discrepancy",
  "subdistrict_anomaly",
  "temporal_anomaly",
];

export default function AlertQueue() {
  const g = useGlossary();
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);

  useEffect(() => {
    apiGet<AlertOut[]>("/api/alerts").then(setAlerts).catch((e) => setError(String(e)));
  }, []);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const a of alerts) if (a.alert_type) c[a.alert_type] = (c[a.alert_type] ?? 0) + 1;
    return c;
  }, [alerts]);

  const shown = filter ? alerts.filter((a) => a.alert_type === filter) : alerts;
  const scoredDate = alerts[0]?.scored_date;
  const singleTxnCount = counts.single_txn_spike ?? 0;

  if (error) return <ErrorNote>Failed to load alerts: {error}</ErrorNote>;

  return (
    <div className="space-y-4">
      {/* What the run actually looked at. Without this an empty category reads
          as a broken screen rather than a real finding about the day. */}
      {scoredDate && (
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3.5 text-[0.92rem]">
            <p className="text-[var(--text)]">
              Evaluating <span className="font-semibold text-[var(--text-strong)]">{scoredDate}</span>{" "}
              — the last completed trading day.{" "}
              {singleTxnCount === 0 ? (
                <span className="text-[var(--muted)]">
                  No single-transaction anomalies on this day; the queue is entirely
                  merchant-level discrepancies against the 30-day baseline.
                </span>
              ) : (
                <span className="text-[var(--muted)]">
                  {singleTxnCount} single-transaction{" "}
                  {singleTxnCount === 1 ? "anomaly" : "anomalies"} plus{" "}
                  {alerts.length - singleTxnCount} merchant-level discrepancies.
                </span>
              )}
            </p>
          </div>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setFilter(null)}
          className={cn(
            "focus-ring rounded-full border px-3.5 py-1.5 text-[0.85rem] font-medium transition-colors",
            filter === null
              ? "border-[var(--navy-800)] bg-[var(--navy-800)] text-white"
              : "border-[var(--border-strong)] bg-white text-[var(--text-strong)] hover:bg-[var(--blue-50)]"
          )}
        >
          All {alerts.length}
        </button>
        {TYPE_ORDER.map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setFilter(filter === key ? null : key)}
            disabled={!counts[key]}
            className={cn(
              "focus-ring rounded-full border px-3.5 py-1.5 text-[0.85rem] font-medium transition-colors",
              filter === key
                ? "border-[var(--navy-800)] bg-[var(--navy-800)] text-white"
                : "border-[var(--border-strong)] bg-white text-[var(--text-strong)] hover:bg-[var(--blue-50)]",
              !counts[key] && "cursor-not-allowed opacity-45"
            )}
          >
            {g.alertType(key)} {counts[key] ?? 0}
          </button>
        ))}
      </div>

      <Card
        title={`${shown.length} alert${shown.length === 1 ? "" : "s"}`}
        subtitle="Highest blended risk first"
      >
        {shown.length === 0 ? (
          <p className="px-5 py-10 text-center text-[0.92rem] text-[var(--muted)]">
            {filter
              ? `No ${g.alertType(filter).toLowerCase()} alerts on ${scoredDate ?? "this day"}. That is a finding, not an error — the other categories above still hold work.`
              : "No alerts are waiting. Run the pipeline to generate a review queue."}
          </p>
        ) : (
          <table className="w-full text-left text-[0.9rem]">
            <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
              <tr>
                <th className="px-5 py-3 font-semibold">Rank</th>
                <th className="px-5 py-3 font-semibold">Merchant</th>
                <th className="px-5 py-3 font-semibold">Alert type</th>
                <th className="px-5 py-3 font-semibold">Reason</th>
                <th className="px-5 py-3 text-right font-semibold">Score</th>
                <th className="px-5 py-3" />
              </tr>
            </thead>
            <tbody>
              {shown.map((a) => (
                <tr key={a.id} className="border-b border-[var(--border)] last:border-b-0 hoverable">
                  <td className="px-5 py-3.5 metric-number font-semibold text-[var(--text-strong)]">
                    {a.rank}
                  </td>
                  <td className="px-5 py-3.5">
                    <p className="font-medium text-[var(--text-strong)]">{a.merchant_id}</p>
                    <p className="mt-0.5 text-[0.78rem] text-[var(--muted)]">
                      {a.mcc ? `MCC ${a.mcc}` : "MCC —"}
                      {a.mcc_description ? ` · ${a.mcc_description}` : ""}
                      {a.merchant_subdistrict ? ` · ${a.merchant_subdistrict}` : ""}
                    </p>
                  </td>
                  <td className="px-5 py-3.5">
                    {a.alert_type && (
                      <Pill tone={TYPE_TONE[a.alert_type] ?? "neutral"}>
                        {g.alertType(a.alert_type)}
                      </Pill>
                    )}
                  </td>
                  <td className="px-5 py-3.5 text-[var(--text)]">
                    {g.detector(a.triggering_detectors[0]?.detector ?? "")}
                  </td>
                  <td className="px-5 py-3.5 text-right metric-number font-semibold text-[var(--text-strong)]">
                    {a.blended_score.toFixed(2)}
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <Link
                      to={`/case/${a.id}`}
                      className="focus-ring inline-flex items-center gap-1 rounded-full bg-[var(--navy-800)] px-3 py-1.5 text-[0.8rem] font-medium text-white transition hover:bg-[var(--navy-700)]"
                    >
                      Review
                      <IconChevron className="h-3.5 w-3.5" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
