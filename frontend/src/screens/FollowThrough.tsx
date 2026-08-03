import { Fragment, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  apiGet,
  apiSend,
  type CaseDetail,
  type CasePage,
} from "../api/client";
import { reasonLabel } from "../lib/reasons";
import { Card, ErrorNote, Pager, Pill, type Tone } from "../lib/ui";
import { cn } from "../lib/utils";

/** The stages a case can move to next. Free text would make the board
 *  unsortable and the timeline unauditable, so the vocabulary is fixed. */
const STAGES: [string, string][] = [
  ["MERCHANT_CONTACTED", "Merchant contacted"],
  ["DOCUMENTS_REQUESTED", "Documents requested"],
  ["DOCUMENTS_RECEIVED", "Documents received"],
  ["DOCUMENTS_VERIFIED", "Documents verified"],
  ["NO_RESPONSE", "No response"],
  ["ESCALATED_LEGAL", "Escalated to legal"],
  ["STR_FILED", "STR filed"],
  ["CLEARED", "Cleared — merchant legitimate"],
  ["CONFIRMED_ILLICIT", "Confirmed illicit"],
  ["OFFBOARDED", "Offboarded"],
];

const STAGE_TONE: Record<string, Tone> = {
  OPENED: "warning",
  MERCHANT_CONTACTED: "blue",
  DOCUMENTS_REQUESTED: "blue",
  DOCUMENTS_RECEIVED: "blue",
  DOCUMENTS_VERIFIED: "navy",
  NO_RESPONSE: "warning",
  ESCALATED_LEGAL: "danger",
  STR_FILED: "danger",
  CLEARED: "success",
  CONFIRMED_ILLICIT: "danger",
  OFFBOARDED: "danger",
};

function CaseTimeline({
  caseId,
  onUpdated,
}: {
  caseId: number;
  onUpdated: () => void;
}) {
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [stage, setStage] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiGet<CaseDetail>(`/api/cases/${caseId}`).then(setDetail).catch(() => undefined);
  }, [caseId]);

  useEffect(load, [load]);

  async function addStage() {
    if (!stage) return;
    setBusy(true);
    setError(null);
    try {
      await apiSend(`/api/cases/${caseId}/events`, {
        event_type: stage,
        note: note || null,
        actor: "analyst",
      });
      setStage("");
      setNote("");
      load();
      onUpdated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!detail) return <p className="px-5 py-4 text-[0.88rem] text-[var(--muted)]">Loading…</p>;

  return (
    <div className="space-y-4 bg-[var(--bg)] px-5 py-4">
      <ol className="space-y-2.5">
        {detail.events.map((e) => (
          <li key={e.id} className="flex items-start gap-3">
            <span
              className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[var(--blue-500)]"
              aria-hidden
            />
            <div className="min-w-0">
              <p className="text-[0.9rem] font-medium text-[var(--text-strong)]">
                {e.label}
              </p>
              {e.note && (
                <p className="mt-0.5 text-[0.86rem] leading-6 text-[var(--text)]">{e.note}</p>
              )}
              <p className="mt-0.5 text-[0.78rem] text-[var(--muted)]">
                {new Date(e.occurred_at).toLocaleString("en-GB", {
                  dateStyle: "medium",
                  timeStyle: "short",
                  timeZone: "Asia/Hong_Kong",
                })}{" "}
                · {e.actor}
              </p>
            </div>
          </li>
        ))}
      </ol>

      {detail.is_resolved ? (
        <p className="rounded-[var(--radius)] bg-[var(--success-bg)] px-4 py-2.5 text-[0.88rem] text-[var(--success)]">
          This case is resolved. Reopening means recording a new stage on a new alert.
        </p>
      ) : (
        <div className="flex flex-wrap items-end gap-2 border-t border-[var(--border)] pt-4">
          <label className="min-w-[200px] flex-1">
            <span className="text-[0.78rem] font-medium text-[var(--text-strong)]">
              Next stage
            </span>
            <select
              value={stage}
              onChange={(e) => setStage(e.target.value)}
              className="focus-ring mt-1 w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.88rem]"
            >
              <option value="">Select…</option>
              {STAGES.map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-[240px] flex-[2]">
            <span className="text-[0.78rem] font-medium text-[var(--text-strong)]">Note</span>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="What happened, and what it showed."
              className="focus-ring mt-1 w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.88rem]"
            />
          </label>
          <button
            type="button"
            onClick={addStage}
            disabled={!stage || busy}
            className="focus-ring rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2 text-[0.88rem] font-medium text-white transition hover:bg-[var(--navy-700)] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {busy ? "Saving…" : "Add stage"}
          </button>
        </div>
      )}

      {error && <p className="text-[0.86rem] text-[var(--danger)]">{error}</p>}
    </div>
  );
}

export default function FollowThrough() {
  const [data, setData] = useState<CasePage | null>(null);
  const [page, setPage] = useState(1);
  const [resolved, setResolved] = useState<boolean | null>(false);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(() => {
    const q = new URLSearchParams({ page: String(page), page_size: "20" });
    if (resolved !== null) q.set("resolved", String(resolved));
    apiGet<CasePage>(`/api/cases?${q}`).then(setData).catch(() => setError(true));
  }, [page, resolved]);

  useEffect(load, [load]);

  if (error) return <ErrorNote>Could not load cases.</ErrorNote>;

  const stale = data?.items.filter((c) => c.is_stale).length ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {[
          [false, "Open"],
          [true, "Resolved"],
          [null, "All"],
        ].map(([value, label]) => (
          <button
            key={String(label)}
            type="button"
            onClick={() => {
              setResolved(value as boolean | null);
              setPage(1);
            }}
            className={cn(
              "focus-ring rounded-full border px-3.5 py-1.5 text-[0.85rem] font-medium transition-colors",
              resolved === value
                ? "border-[var(--navy-800)] bg-[var(--navy-800)] text-white"
                : "border-[var(--border-strong)] bg-white text-[var(--text-strong)] hover:bg-[var(--blue-50)]"
            )}
          >
            {label as string}
          </button>
        ))}
      </div>

      {stale > 0 && (
        <Card>
          <p className="px-5 py-3 text-[0.9rem] text-[var(--warning)]">
            {stale} case{stale === 1 ? " has" : "s have"} had no update for a week or more.
            They are listed first.
          </p>
        </Card>
      )}

      <Card
        title={`${data?.total ?? 0} case${data?.total === 1 ? "" : "s"}`}
        subtitle="Confirmed alerts, tracked to resolution. Select a row for its timeline."
      >
        {!data ? (
          <p className="px-5 py-10 text-center text-[0.9rem] text-[var(--muted)]">Loading…</p>
        ) : data.items.length === 0 ? (
          <p className="px-5 py-10 text-center text-[0.92rem] text-[var(--muted)]">
            No cases here. A case opens when an analyst confirms an alert.
          </p>
        ) : (
          <>
            <table className="w-full text-left text-[0.9rem]">
              <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
                <tr>
                  <th className="px-5 py-3 font-semibold">Merchant</th>
                  <th className="px-5 py-3 font-semibold">Reason</th>
                  <th className="px-5 py-3 font-semibold">Stage</th>
                  <th className="px-5 py-3 text-right font-semibold">Last update</th>
                  <th className="px-5 py-3" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  // Fragment carries the key: the row and its expanded
                  // timeline are two siblings for one case.
                  <Fragment key={c.disposition_id}>
                    <tr
                      onClick={() =>
                        setOpen(open === c.disposition_id ? null : c.disposition_id)
                      }
                      className={cn(
                        "cursor-pointer border-b border-[var(--border)] hoverable",
                        c.is_stale && "bg-[var(--warning-bg)]/50"
                      )}
                    >
                      <td className="px-5 py-3.5">
                        <p className="font-medium text-[var(--text-strong)]">
                          {c.merchant_id}
                        </p>
                        <p className="mt-0.5 text-[0.78rem] text-[var(--muted)]">
                          {c.mcc ? `MCC ${c.mcc}` : ""}
                          {c.mcc_description ? ` · ${c.mcc_description}` : ""}
                        </p>
                      </td>
                      <td className="px-5 py-3.5 text-[var(--text)]">
                        {reasonLabel(c.reason_code)}
                      </td>
                      <td className="px-5 py-3.5">
                        <Pill tone={STAGE_TONE[c.stage] ?? "neutral"}>{c.stage_label}</Pill>
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <span className="metric-number text-[var(--text)]">
                          {c.days_since_update}d ago
                        </span>
                        {c.is_stale && (
                          <span className="ml-2 rounded-full bg-[var(--warning-bg)] px-2 py-0.5 text-[0.7rem] font-semibold text-[var(--warning)]">
                            stale
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <Link
                          to={`/case/${c.alert_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="focus-ring text-[0.82rem] font-medium text-[var(--blue-600)] hover:underline"
                        >
                          Alert
                        </Link>
                      </td>
                    </tr>
                    {open === c.disposition_id && (
                      <tr className="border-b border-[var(--border)]">
                        <td colSpan={5} className="p-0">
                          <CaseTimeline caseId={c.disposition_id} onUpdated={load} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
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
