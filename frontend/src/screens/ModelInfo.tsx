import { useState } from "react";
import { useGlossary } from "../api/glossary";
import type { GlossaryTerm } from "../api/client";

const FRAME_TONE: Record<string, string> = {
  "Its own history": "bg-violet-50 text-violet-700",
  "Same trade": "bg-emerald-50 text-emerald-700",
  "Same district": "bg-amber-50 text-amber-700",
};

function TermTable({ terms, showFrame }: { terms: GlossaryTerm[]; showFrame?: boolean }) {
  return (
    <div className="soft-panel overflow-hidden rounded-[24px]">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200/80 bg-slate-50/80 text-xs uppercase tracking-[0.18em] text-slate-500">
          <tr>
            <th className="px-5 py-3 font-medium">Shown as</th>
            {showFrame && <th className="px-5 py-3 font-medium">Compared against</th>}
            <th className="px-5 py-3 font-medium">What it means</th>
            <th className="px-5 py-3 font-medium">Internal name</th>
          </tr>
        </thead>
        <tbody>
          {terms.map((t) => (
            <tr key={t.key} className="border-b border-slate-100 last:border-b-0">
              <td className="px-5 py-3 font-medium text-slate-950">{t.label}</td>
              {showFrame && (
                <td className="px-5 py-3">
                  {t.compared_against && (
                    <span
                      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${
                        FRAME_TONE[t.compared_against] ?? "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {t.compared_against}
                    </span>
                  )}
                </td>
              )}
              <td className="px-5 py-3 leading-6 text-slate-700">{t.meaning}</td>
              <td className="px-5 py-3">
                <code className="text-xs text-slate-500">{t.key}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ModelInfo() {
  const g = useGlossary();
  const [open, setOpen] = useState(false);

  const detectors = g.all?.detectors ?? [];
  const byFrame = (frame: string) => detectors.filter((d) => d.compared_against === frame);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">
            How alerts are raised
          </p>
          <h2 className="mt-2 text-2xl text-slate-950">Model info</h2>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="focus-ring w-fit rounded-full bg-slate-950 px-4 py-2.5 text-sm font-medium text-white transition hover:-translate-y-0.5"
        >
          {open ? "Hide reason glossary" : "Show reason glossary"}
        </button>
      </div>

      <section className="grid gap-4 md:grid-cols-3">
        {[
          ["Its own history", "Is this unusual for this merchant?", "text-violet-700"],
          ["Same trade", "Is this unusual for this line of business?", "text-emerald-700"],
          ["Same district", "Is this unusual for this area?", "text-amber-700"],
        ].map(([frame, question, tone]) => (
          <article key={frame} className="soft-panel rounded-[24px] p-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">
              {frame}
            </p>
            <p className={`mt-3 text-[1.05rem] font-semibold ${tone}`}>{question}</p>
            <p className="mt-2 text-[0.95rem] leading-7 text-slate-700">
              {byFrame(frame).length} reason{byFrame(frame).length === 1 ? "" : "s"} check this.
            </p>
          </article>
        ))}
      </section>

      {open && (
        <div className="space-y-6">
          <section className="space-y-3">
            <div>
              <h3 className="text-lg text-slate-950">Reasons an alert is raised</h3>
              <p className="mt-1 text-sm text-slate-500">
                The internal name is what is written to the record and never changes; the
                label is what the portal shows.
              </p>
            </div>
            <TermTable terms={detectors} showFrame />
          </section>

          <section className="space-y-3">
            <h3 className="text-lg text-slate-950">Values shown on a case</h3>
            <TermTable terms={g.all?.features ?? []} />
          </section>

          <section className="space-y-3">
            <h3 className="text-lg text-slate-950">Merchant groups</h3>
            <TermTable terms={g.all?.lanes ?? []} showFrame />
          </section>

          <section className="space-y-3">
            <h3 className="text-lg text-slate-950">Baseline states</h3>
            <TermTable terms={g.all?.baseline_methods ?? []} />
          </section>
        </div>
      )}

      {!open && (
        <p className="text-[0.95rem] leading-7 text-slate-600">
          Every alert names the reason it was raised. Open the glossary to see what each
          reason means and the internal name it is recorded under.
        </p>
      )}

      <section className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="soft-panel rounded-[28px] p-6">
          <p className="text-[1.05rem] font-semibold text-slate-950">Pipeline stages</p>
          <p className="mt-1 text-sm text-slate-500">
            The nightly flow, in order. Live run status arrives with Prefect wiring.
          </p>
          <div className="mt-4 overflow-hidden rounded-2xl border border-slate-200/80 bg-white">
            {["Pull", "Profile", "Route", "Detect", "Score"].map((stage, index) => (
              <div
                key={stage}
                className={`flex items-center justify-between gap-4 px-4 py-3 ${
                  index === 0 ? "" : "border-t border-slate-100"
                }`}
              >
                <span className="text-sm text-slate-600">{stage}</span>
                <span className="metric-number rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500">
                  {index + 1}
                </span>
              </div>
            ))}
          </div>
        </div>

        <aside className="soft-panel rounded-[28px] p-6">
          <p className="text-[1.05rem] font-semibold text-slate-950">Not yet measured</p>
          <ul className="mt-4 space-y-3 text-[0.95rem] text-slate-700">
            <li className="rounded-2xl bg-slate-50 px-4 py-3 leading-7">
              False-positive rate — needs analyst decisions first.
            </li>
            <li className="rounded-2xl bg-slate-50 px-4 py-3 leading-7">
              Label completeness — arrives with disposition capture.
            </li>
            <li className="rounded-2xl bg-slate-50 px-4 py-3 leading-7">
              Thresholds are starting points, not yet calibrated on real merchants.
            </li>
          </ul>
        </aside>
      </section>
    </div>
  );
}
