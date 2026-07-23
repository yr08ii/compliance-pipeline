import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";
import { useGlossary } from "../api/glossary";

function severityTone(deviation: number) {
  if (deviation >= 5) return "bg-red-50 text-red-700 border-red-200";
  if (deviation >= 3) return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-emerald-50 text-emerald-700 border-emerald-200";
}

export default function CaseReview() {
  const g = useGlossary();
  const { id } = useParams();
  const [alert, setAlert] = useState<AlertOut | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (id) {
      setError(false);
      apiGet<AlertOut>(`/api/alerts/${id}`)
        .then(setAlert)
        .catch(() => setError(true));
    }
  }, [id]);

  if (error) {
    return (
      <div className="soft-panel rounded-[24px] p-6 text-sm text-red-700">
        Could not load this case. Open the queue and select a valid alert.
      </div>
    );
  }
  if (!alert) {
    return <div className="soft-panel rounded-[24px] p-6 text-sm text-slate-500">Loading case…</div>;
  }

  const primaryFeature = [...alert.feature_snapshot].sort((a, b) => b.deviation - a.deviation)[0];

  return (
    <div className="space-y-6">
      <section className="soft-panel rounded-[28px] p-6 lg:p-7">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Case review</p>
            <h2 className="mt-2 text-3xl text-slate-950">{alert.merchant_id}</h2>
            <p className="mt-2 text-sm text-slate-600">
              {g.lane(alert.lane)} · rank {alert.rank} · score {alert.blended_score.toFixed(2)}
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-2xl bg-slate-50 p-4">
              <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Merchant</div>
              <div className="mt-2 text-lg font-semibold text-slate-950">{alert.merchant_id}</div>
            </div>
            <div className="rounded-2xl bg-violet-50 p-4">
              <div className="text-xs uppercase tracking-[0.2em] text-violet-700/80">Group</div>
              <div className="mt-2 text-lg font-semibold text-violet-700">{g.lane(alert.lane)}</div>
            </div>
            <div className="rounded-2xl bg-emerald-50 p-4">
              <div className="text-xs uppercase tracking-[0.2em] text-emerald-700/80">Score</div>
              <div className="mt-2 text-lg font-semibold text-emerald-700">{alert.blended_score.toFixed(2)}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <div className="soft-panel overflow-hidden rounded-[28px]">
          <div className="border-b border-slate-200/80 px-6 py-5">
            <h3 className="text-xl text-slate-950">What diverged from baseline</h3>
            <p className="mt-1 text-sm text-slate-500">The exact features behind the flag, preserved as of alert time.</p>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-slate-50/80 text-left text-xs uppercase tracking-[0.18em] text-slate-500">
              <tr>
                <th className="px-6 py-4 font-medium">Measure</th>
                <th className="px-6 py-4 font-medium">Merchant</th>
                <th className="px-6 py-4 font-medium">Baseline</th>
                <th className="px-6 py-4 font-medium">Deviation</th>
              </tr>
            </thead>
            <tbody>
              {alert.feature_snapshot.map((f, i) => (
                <tr key={i} className="border-t border-slate-100 transition hover:bg-slate-50/80">
                  <td className="px-6 py-4 font-medium text-slate-950">{g.feature(f.feature_name)}</td>
                  <td className="px-6 py-4 tabular-nums">{f.merchant_value.toLocaleString()}</td>
                  <td className="px-6 py-4 tabular-nums text-slate-600">{f.baseline_value.toLocaleString()}</td>
                  <td className="px-6 py-4">
                    <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${severityTone(f.deviation)}`}>
                      {f.deviation.toFixed(2)}×
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <aside className="space-y-4">
          <div className="soft-panel rounded-[24px] p-5">
            <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Analyst posture</p>
            <p className="mt-3 text-lg font-medium text-slate-950">Disposition arrives next</p>
            <p className="mt-2 text-sm text-slate-600">
              The next plan adds verdict capture, reason codes, and signed case actions.
            </p>
          </div>
          <div className="soft-panel rounded-[24px] p-5">
            <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Signal summary</p>
            <div className="mt-4 space-y-3 text-sm">
              <div className="flex items-center justify-between gap-3 rounded-2xl bg-slate-50 px-4 py-3">
                <span className="text-slate-600">Reason</span>
                <span className="font-medium text-slate-950">
                  {alert.triggering_detectors[0]
                    ? g.detector(alert.triggering_detectors[0].detector)
                    : "—"}
                </span>
              </div>
              <div className="flex items-center justify-between gap-3 rounded-2xl bg-slate-50 px-4 py-3">
                <span className="text-slate-600">Baseline</span>
                <span className="metric-number font-medium text-slate-950">
                  {primaryFeature ? primaryFeature.baseline_value.toLocaleString() : "—"}
                </span>
              </div>
              <div className="flex items-center justify-between gap-3 rounded-2xl bg-slate-50 px-4 py-3">
                <span className="text-slate-600">Observed value</span>
                <span className="metric-number font-medium text-slate-950">
                  {primaryFeature ? primaryFeature.merchant_value.toLocaleString() : "—"}
                </span>
              </div>
            </div>
          </div>
        </aside>
      </section>

      <p className="text-sm text-slate-500">Disposition form and digital signature arrive in the Phase 2 plan.</p>
    </div>
  );
}
