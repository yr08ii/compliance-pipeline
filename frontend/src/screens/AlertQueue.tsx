import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertPage, type RunOut } from "../api/client";
import { useGlossary } from "../api/glossary";
import { Card, ErrorNote, Pager, Pill, type Tone } from "../lib/ui";
import { IconChevron } from "../lib/icons";
import RunPicker from "./RunPicker";
import { cn } from "../lib/utils";

const TYPE_TONE: Record<string, Tone> = {
  single_txn_spike: "danger",
  mcc_peer_discrepancy: "blue",
  subdistrict_anomaly: "warning",
  temporal_anomaly: "navy",
  // Family B and C. A typology match and a ring signal are not statistical
  // outliers, and an analyst works them differently, so they carry their own
  // badges rather than being folded into the four above.
  typology_match: "danger",
  ring_signal: "navy",
  // Attempts that moved no money. Its own badge because the analyst opens it
  // expecting a terminal being tested, not a large sale to explain.
  failed_txn_rate: "warning",
};

const TYPE_ORDER = [
  "single_txn_spike",
  "typology_match",
  "ring_signal",
  "failed_txn_rate",
  "mcc_peer_discrepancy",
  "subdistrict_anomaly",
  "temporal_anomaly",
];

type Counts = { total: number; by_type: Record<string, number>; scored_date: string | null };

export default function AlertQueue() {
  const g = useGlossary();
  const [data, setData] = useState<AlertPage | null>(null);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunOut[]>([]);
  // Which run the queue is showing. null is the working queue: whatever
  // currently speaks for each scored day.
  const [viewing, setViewing] = useState<number | null>(null);

  const refreshRuns = useCallback(() => {
    apiGet<RunOut[]>("/api/runs").then(setRuns).catch(() => undefined);
  }, []);

  useEffect(refreshRuns, [refreshRuns]);

  // Counted server-side across the whole queue: tallying only the visible
  // page would make these chips lie about what is waiting.
  const refreshCounts = useCallback(() => {
    apiGet<Counts>("/api/alerts/counts").then(setCounts).catch(() => undefined);
  }, []);

  useEffect(refreshCounts, [refreshCounts]);

  useEffect(() => {
    const query = new URLSearchParams({ page: String(page), page_size: "20" });
    if (filter) query.set("alert_type", filter);
    if (viewing != null) query.set("run_id", String(viewing));
    apiGet<AlertPage>(`/api/alerts?${query}`).then(setData).catch((e) => setError(String(e)));
  }, [page, filter, viewing]);

  if (error) return <ErrorNote>Failed to load alerts: {error}</ErrorNote>;

  const singleTxn = counts?.by_type.single_txn_spike ?? 0;
  const total = counts?.total ?? 0;

  return (
    <div className="space-y-4">
      <RunPicker
        runs={runs}
        viewing={viewing}
        onView={(runId) => {
          setViewing(runId);
          setPage(1);
          setFilter(null);
        }}
        onCleared={() => {
          refreshRuns();
          refreshCounts();
        }}
      />

      {viewing == null && counts?.scored_date && (
        <Card>
          <p className="px-5 py-3.5 text-[0.92rem] text-[var(--text)]">
            Evaluating{" "}
            <span className="font-semibold text-[var(--text-strong)]">
              {counts.scored_date}
            </span>{" "}
            — the last completed trading day.{" "}
            <span className="text-[var(--muted)]">
              {singleTxn === 0
                ? "No single-transaction anomalies on this day; the queue is entirely merchant-level discrepancies against the rolling baseline."
                : `${singleTxn} single-transaction ${
                    singleTxn === 1 ? "anomaly" : "anomalies"
                  } plus ${total - singleTxn} merchant-level discrepancies.`}
            </span>
          </p>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => {
            setFilter(null);
            setPage(1);
          }}
          className={cn(
            "focus-ring rounded-full border px-3.5 py-1.5 text-[0.85rem] font-medium transition-colors",
            filter === null
              ? "border-[var(--navy-800)] bg-[var(--navy-800)] text-white"
              : "border-[var(--border-strong)] bg-white text-[var(--text-strong)] hover:bg-[var(--blue-50)]"
          )}
        >
          All {total}
        </button>
        {TYPE_ORDER.map((key) => {
          const n = counts?.by_type[key] ?? 0;
          return (
            <button
              key={key}
              type="button"
              title={g.alertTypeMeaning(key)}
              onClick={() => {
                setFilter(filter === key ? null : key);
                setPage(1);
              }}
              disabled={!n}
              className={cn(
                "focus-ring rounded-full border px-3.5 py-1.5 text-[0.85rem] font-medium transition-colors",
                filter === key
                  ? "border-[var(--navy-800)] bg-[var(--navy-800)] text-white"
                  : "border-[var(--border-strong)] bg-white text-[var(--text-strong)] hover:bg-[var(--blue-50)]",
                !n && "cursor-not-allowed opacity-45"
              )}
            >
              {g.alertType(key)} {n}
            </button>
          );
        })}
      </div>

      <Card
        title={
          filter
            ? `${g.alertType(filter)} — ${data?.total ?? 0} awaiting review`
            : `${data?.total ?? 0} alerts awaiting review`
        }
        subtitle="Highest blended risk first. Alerts leave this list once decided."
      >
        {!data ? (
          <p className="px-5 py-10 text-center text-[0.9rem] text-[var(--muted)]">Loading…</p>
        ) : data.items.length === 0 ? (
          <p className="px-5 py-10 text-center text-[0.92rem] text-[var(--muted)]">
            {filter
              ? `No ${g.alertType(filter).toLowerCase()} alerts awaiting review. That is a finding, not an error — the other types above may still hold work.`
              : "Nothing awaiting review. Every alert from the last run has been decided."}
          </p>
        ) : (
          <>
            <table className="w-full text-left text-[0.9rem]">
              <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
                <tr>
                  <th className="px-5 py-3 font-semibold">Rank</th>
                  <th className="px-5 py-3 font-semibold">Merchant</th>
                  <th className="px-5 py-3 font-semibold">Alert type</th>
                  <th className="px-5 py-3 font-semibold">What fired</th>
                  <th className="px-5 py-3 text-right font-semibold">Score</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
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
            <Pager
              page={data.page}
              pages={data.pages}
              total={data.total}
              pageSize={data.page_size}
              onChange={setPage}
            />
          </>
        )}
      </Card>
    </div>
  );
}
