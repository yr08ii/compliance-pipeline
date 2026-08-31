import { useState } from "react";
import { apiSend, type RunOut } from "../api/client";
import { Card, Pill } from "../lib/ui";
import { cn } from "../lib/utils";

/** A scored day and every run that has spoken for it, newest run first. */
type Day = { as_of: string; runs: RunOut[] };

function byDay(runs: RunOut[]): Day[] {
  const days = new Map<string, RunOut[]>();
  for (const run of runs) {
    const key = run.as_of.slice(0, 10);
    days.set(key, [...(days.get(key) ?? []), run]);
  }
  return [...days]
    .map(([as_of, list]) => ({
      as_of,
      // Within a day, by when the run happened — the order the team ran them.
      runs: [...list].sort((a, b) => b.started_at.localeCompare(a.started_at)),
    }))
    .sort((a, b) => b.as_of.localeCompare(a.as_of));
}

function stamp(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}

/** Which parameters a run scored under, where they were recorded.
 *
 *  Runs reconstructed from alerts that predate the run record carry none, and
 *  say so rather than showing an empty table that reads as "no thresholds". */
function Parameters({ run }: { run: RunOut }) {
  const entries = Object.entries(run.settings ?? {});
  if (!entries.length) {
    return (
      <p className="text-[0.82rem] text-[var(--muted)]">
        Parameters were not recorded for this run.
      </p>
    );
  }
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[0.82rem] sm:grid-cols-3">
      {entries.map(([key, value]) => (
        <div key={key} className="flex justify-between gap-2">
          <dt className="text-[var(--muted)]">{key}</dt>
          <dd className="font-medium text-[var(--text-strong)]">{String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function RunPicker({
  runs,
  viewing,
  onView,
  onCleared,
}: {
  runs: RunOut[];
  /** The run being shown, or null for whatever currently speaks for each day. */
  viewing: number | null;
  onView: (runId: number | null) => void;
  onCleared: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const days = byDay(runs);

  async function clearDay(as_of: string, retired: number) {
    if (
      !window.confirm(
        `Discard ${retired} superseded run${retired === 1 ? "" : "s"} for ${as_of}?\n\n` +
          `The run currently in the queue is kept, and so is any alert somebody ` +
          `has already ruled on. This cannot be undone.`
      )
    )
      return;
    setBusy(as_of);
    setNote(null);
    try {
      const result = await apiSend<{
        runs_cleared: number;
        alerts_removed: number;
        alerts_kept: number;
      }>(`/api/runs/superseded?as_of=${as_of}`, null, "DELETE");
      setNote(
        `${as_of}: cleared ${result.runs_cleared} run${
          result.runs_cleared === 1 ? "" : "s"
        }, removed ${result.alerts_removed} alert${
          result.alerts_removed === 1 ? "" : "s"
        }${
          result.alerts_kept
            ? `, kept ${result.alerts_kept} already ruled on`
            : ""
        }.`
      );
      onView(null);
      onCleared();
    } catch (e) {
      setNote(`Could not clear ${as_of}: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Pipeline runs"
      subtitle={
        viewing
          ? "Viewing a past run — the queue below is that run's output, not today's work"
          : "Every run, by the day it scored. Re-scoring a day retires the run before it."
      }
      action={
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="focus-ring rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2 text-[0.85rem] font-medium text-white transition hover:bg-[var(--navy-700)]"
        >
          {open ? "Hide runs" : `Show runs (${runs.length})`}
        </button>
      }
    >
      {viewing && (
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--blue-50)] px-5 py-3">
          <p className="text-[0.88rem] text-[var(--text-strong)]">
            Showing run {viewing} in full, including alerts already decided.
          </p>
          <button
            type="button"
            onClick={() => onView(null)}
            className="focus-ring shrink-0 rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.82rem] font-medium text-[var(--text-strong)] hover:bg-white/60"
          >
            Back to current queue
          </button>
        </div>
      )}

      {note && (
        <p className="border-b border-[var(--border)] px-5 py-3 text-[0.85rem] text-[var(--text-strong)]">
          {note}
        </p>
      )}

      {open && (
        <div className="divide-y divide-[var(--border)]">
          {!days.length && (
            <p className="px-5 py-6 text-[0.9rem] text-[var(--muted)]">
              No runs recorded yet.
            </p>
          )}
          {days.map((day) => {
            const retired = day.runs.filter((r) => !r.is_current).length;
            return (
              <section key={day.as_of} className="px-5 py-4">
                <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h3 className="text-[0.95rem] font-semibold text-[var(--text-strong)]">
                      Scored day {day.as_of}
                    </h3>
                    <p className="text-[0.82rem] text-[var(--muted)]">
                      {day.runs.length} run{day.runs.length === 1 ? "" : "s"}
                      {retired > 0 && ` — ${retired} superseded`}
                    </p>
                  </div>
                  {retired > 0 && (
                    <button
                      type="button"
                      disabled={busy === day.as_of}
                      onClick={() => clearDay(day.as_of, retired)}
                      className="focus-ring shrink-0 rounded-[var(--radius)] border border-[var(--danger)] px-3 py-1.5 text-[0.82rem] font-medium text-[var(--danger)] transition hover:bg-[var(--danger)] hover:text-white disabled:opacity-50"
                    >
                      {busy === day.as_of
                        ? "Clearing…"
                        : `Clear ${retired} superseded run${retired === 1 ? "" : "s"}`}
                    </button>
                  )}
                </header>

                <ol className="space-y-2">
                  {day.runs.map((run) => (
                    <li
                      key={run.id}
                      className={cn(
                        "rounded-[var(--radius)] border p-3 transition-colors",
                        viewing === run.id
                          ? "border-[var(--navy-800)] bg-[var(--blue-50)]"
                          : "border-[var(--border)] bg-white"
                      )}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[0.82rem] text-[var(--muted)]">
                            run {run.id}
                          </span>
                          <Pill tone={run.is_current ? "blue" : "neutral"}>
                            {run.is_current ? "In the queue" : "Superseded"}
                          </Pill>
                          <span className="text-[0.85rem] text-[var(--text-strong)]">
                            {run.alert_count} alert
                            {run.alert_count === 1 ? "" : "s"}
                          </span>
                          <span className="text-[0.82rem] text-[var(--muted)]">
                            ran {stamp(run.started_at)}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() =>
                            onView(viewing === run.id ? null : run.id)
                          }
                          className="focus-ring shrink-0 rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.82rem] font-medium text-[var(--text-strong)] hover:bg-[var(--blue-50)]"
                        >
                          {viewing === run.id ? "Stop viewing" : "View this run"}
                        </button>
                      </div>
                      {run.label && (
                        <p className="mt-1.5 text-[0.85rem] text-[var(--text)]">
                          {run.label}
                        </p>
                      )}
                      <div className="mt-2 border-t border-[var(--border)] pt-2">
                        <Parameters run={run} />
                      </div>
                    </li>
                  ))}
                </ol>
              </section>
            );
          })}
        </div>
      )}
    </Card>
  );
}
