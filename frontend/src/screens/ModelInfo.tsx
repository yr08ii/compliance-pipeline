import { useState } from "react";
import { useGlossary } from "../api/glossary";
import type { GlossaryTerm } from "../api/client";
import { Card, Pill, type Tone } from "../lib/ui";

/** A detector's frame is the head of its `compared_against`; anything after a
 *  comma refines it ("Own history, per payment method" is still own history).
 *  Derived rather than listed, so a new frame in the backend appears here
 *  instead of silently counting zero — which is what happened when the
 *  glossary was reworded and this screen kept the old strings. */
function frameOf(comparedAgainst: string | null | undefined): string {
  return (comparedAgainst ?? "").split(",")[0].trim();
}

const FRAME_TONE: Record<string, Tone> = {
  "Own history": "blue",
  "MCC peer group": "success",
  "Subdistrict peer group": "warning",
};

/** The question each frame answers, in the analyst's words. Frames without an
 *  entry still render — they just show their own name as the heading. */
const FRAME_QUESTION: Record<string, string> = {
  "Own history": "Is this unusual for this merchant?",
  "MCC peer group": "Is this unusual for this line of business?",
  "Subdistrict peer group": "Is this unusual for this area?",
};

function TermTable({ terms, showFrame }: { terms: GlossaryTerm[]; showFrame?: boolean }) {
  return (
    <table className="w-full text-left text-[0.88rem]">
      <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
        <tr>
          <th className="px-5 py-3 font-semibold">Shown as</th>
          {showFrame && <th className="px-5 py-3 font-semibold">Compared against</th>}
          <th className="px-5 py-3 font-semibold">What it means</th>
          <th className="px-5 py-3 font-semibold">Internal name</th>
        </tr>
      </thead>
      <tbody>
        {terms.map((t) => (
          <tr key={t.key} className="border-b border-[var(--border)] last:border-b-0 align-top">
            <td className="px-5 py-3 font-medium text-[var(--text-strong)]">{t.label}</td>
            {showFrame && (
              <td className="px-5 py-3">
                {t.compared_against && (
                  <Pill tone={FRAME_TONE[frameOf(t.compared_against)] ?? "neutral"}>
                    {t.compared_against}
                  </Pill>
                )}
              </td>
            )}
            <td className="px-5 py-3 leading-6 text-[var(--text)]">{t.meaning}</td>
            <td className="px-5 py-3">
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[0.76rem] text-[var(--muted)]">
                {t.key}
              </code>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ModelInfo() {
  const g = useGlossary();
  const [open, setOpen] = useState(false);
  const detectors = g.all?.detectors ?? [];

  const counts = new Map<string, number>();
  for (const d of detectors) {
    const f = frameOf(d.compared_against);
    if (f) counts.set(f, (counts.get(f) ?? 0) + 1);
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-3">
        {[...counts].map(([frame, count]) => (
          <div key={frame} className="card p-5">
            <Pill tone={FRAME_TONE[frame] ?? "neutral"}>{frame}</Pill>
            <p className="mt-3 text-[1.02rem] font-semibold text-[var(--text-strong)]">
              {FRAME_QUESTION[frame] ?? frame}
            </p>
            <p className="mt-1 text-[0.9rem] text-[var(--muted)]">
              {count} reason{count === 1 ? "" : "s"} check this.
            </p>
          </div>
        ))}
      </div>

      <Card
        title="Reason glossary"
        subtitle="Every alert names a reason; the internal name is what is written to the record"
        action={
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="focus-ring rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2 text-[0.85rem] font-medium text-white transition hover:bg-[var(--navy-700)]"
          >
            {open ? "Hide glossary" : "Show glossary"}
          </button>
        }
      >
        {open ? (
          <div className="space-y-8 p-5">
            <section>
              <h3 className="mb-3 text-[0.95rem]">Reasons an alert is raised</h3>
              <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)]">
                <TermTable terms={detectors} showFrame />
              </div>
            </section>
            <section>
              <h3 className="mb-3 text-[0.95rem]">Measures shown on a case</h3>
              <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)]">
                <TermTable terms={g.all?.features ?? []} />
              </div>
            </section>
            <section>
              <h3 className="mb-3 text-[0.95rem]">Merchant groups</h3>
              <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)]">
                <TermTable terms={g.all?.lanes ?? []} showFrame />
              </div>
            </section>
            <section>
              <h3 className="mb-3 text-[0.95rem]">Baseline states</h3>
              <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)]">
                <TermTable terms={g.all?.baseline_methods ?? []} />
              </div>
            </section>
          </div>
        ) : (
          <p className="px-5 py-4 text-[0.92rem] leading-7 text-[var(--muted)]">
            Open the glossary to see what each reason means and the internal name it is recorded
            under.
          </p>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <Card title="Pipeline stages" subtitle="The nightly flow, in order">
          <div className="p-5">
            <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)]">
              {["Pull", "Profile", "Route", "Detect", "Score"].map((stage, i) => (
                <div
                  key={stage}
                  className={`flex items-center justify-between gap-4 px-4 py-2.5 ${
                    i === 0 ? "" : "border-t border-[var(--border)]"
                  }`}
                >
                  <span className="text-[0.9rem] text-[var(--text)]">{stage}</span>
                  <span className="metric-number rounded-full bg-slate-100 px-2.5 py-0.5 text-[0.76rem] font-semibold text-[var(--muted)]">
                    {i + 1}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </Card>

        <Card title="Not yet measured">
          <ul className="space-y-2.5 p-5 text-[0.9rem] text-[var(--text)]">
            {[
              "False-positive rate — needs analyst decisions first.",
              "Label completeness — arrives with disposition capture.",
              "Thresholds are starting points, not yet calibrated on real merchants.",
            ].map((t) => (
              <li key={t} className="rounded-[var(--radius)] bg-[var(--bg)] px-4 py-2.5 leading-6">
                {t}
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
