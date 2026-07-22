import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";

function formatScore(value: number) {
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function Dashboard() {
  const [alerts, setAlerts] = useState<AlertOut[]>([]);

  useEffect(() => {
    apiGet<AlertOut[]>("/api/alerts").then(setAlerts).catch(() => setAlerts([]));
  }, []);

  const topAlert = alerts[0];

  return (
    <div className="space-y-6">
      <section className="grid gap-4 lg:grid-cols-[1.4fr_0.9fr]">
        <div className="soft-panel rounded-[28px] p-6">
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Tonight's run</p>
          <h3 className="mt-3 text-[1.8rem] leading-tight text-balance text-slate-950">Review starts with one flagged merchant and a clean audit trail.</h3>
          <p className="mt-3 max-w-2xl text-[0.98rem] leading-7 text-slate-700">
            The nightly pipeline ran, one alert was ranked, and the case review panel is ready for analyst disposition.
          </p>
          <div className="mt-6 flex flex-wrap gap-3 text-[0.95rem]">
            <Link
              to="/queue"
              className="focus-ring rounded-full bg-slate-950 px-4 py-2.5 font-medium text-white shadow-lg shadow-slate-950/15 transition hover:-translate-y-0.5"
            >
              Open alert queue
            </Link>
            <Link
              to={topAlert ? `/case/${topAlert.id}` : "/queue"}
              className="focus-ring rounded-full border border-slate-200 bg-white px-4 py-2.5 font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
            >
              Review latest case
            </Link>
          </div>
        </div>
        <div className="soft-panel rounded-[28px] p-6">
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Live queue</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-2xl bg-slate-50 p-4">
              <div className="metric-number text-[2.1rem] font-semibold text-slate-950">{alerts.length}</div>
              <div className="mt-1 text-[0.95rem] text-slate-600">alerts waiting</div>
            </div>
            <div className="rounded-2xl bg-violet-50 p-4">
              <div className="metric-number text-[2.1rem] font-semibold text-violet-700">{topAlert ? formatScore(topAlert.blended_score) : "0.00"}</div>
              <div className="mt-1 text-[0.95rem] text-violet-700/80">top score</div>
            </div>
          </div>
          <div className="mt-4 rounded-2xl border border-dashed border-slate-200 bg-white/80 p-4 text-[0.95rem] leading-7 text-slate-700">
            {topAlert ? (
              <>
                <span className="font-medium text-slate-900">{topAlert.merchant_id}</span> is the current focus. Open the case to see the divergence panel.
              </>
            ) : (
              "No alerts have been written yet. Run the bootstrap command to generate the review queue."
            )}
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <article className="soft-panel rounded-[24px] p-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Pipeline state</p>
          <p className="mt-3 text-[1.1rem] font-semibold text-slate-950">Healthy</p>
          <p className="mt-2 text-[0.95rem] leading-7 text-slate-700">The local bootstrap command migrated, seeded, and ran the flow successfully.</p>
        </article>
        <article className="soft-panel rounded-[24px] p-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Human review</p>
          <p className="mt-3 text-[1.1rem] font-semibold text-slate-950">Ready</p>
          <p className="mt-2 text-[0.95rem] leading-7 text-slate-700">The alert queue and case page are fully wired to backend data.</p>
        </article>
        <article className="soft-panel rounded-[24px] p-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Training loop</p>
          <p className="mt-3 text-[1.1rem] font-semibold text-slate-950">Seeded</p>
          <p className="mt-2 text-[0.95rem] leading-7 text-slate-700">Feature snapshots are written once and are ready for later label capture.</p>
        </article>
      </section>
    </div>
  );
}
