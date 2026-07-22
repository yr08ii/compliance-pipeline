import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";

function laneTone(lane: string) {
  return lane === "A"
    ? "border-violet-200 bg-violet-50 text-violet-700"
    : "border-amber-200 bg-amber-50 text-amber-700";
}

export default function AlertQueue() {
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<AlertOut[]>("/api/alerts").then(setAlerts).catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <div className="soft-panel rounded-[24px] p-6 text-sm text-red-700">
        Failed to load alerts: {error}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Worklist</p>
          <h2 className="mt-2 text-2xl text-slate-950">Alert queue</h2>
        </div>
        <p className="text-sm text-slate-500">Ranked by blended score, with direct access to the case review panel.</p>
      </div>

      <div className="soft-panel overflow-hidden rounded-[28px]">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200/80 bg-slate-50/80 text-xs uppercase tracking-[0.18em] text-slate-500">
            <tr>
              <th className="px-5 py-4 font-medium">Rank</th>
              <th className="px-5 py-4 font-medium">Merchant</th>
              <th className="px-5 py-4 font-medium">Lane</th>
              <th className="px-5 py-4 font-medium">Score</th>
              <th className="px-5 py-4 font-medium" />
            </tr>
          </thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.id} className="border-b border-slate-100 last:border-b-0 transition hover:bg-slate-50/80">
                <td className="px-5 py-4 font-medium text-slate-950">{a.rank}</td>
                <td className="px-5 py-4">
                  <div className="font-medium text-slate-950">{a.merchant_id}</div>
                  <div className="mt-1 text-xs text-slate-500">{a.triggering_detectors[0]?.detector ?? "risk signal"}</div>
                </td>
                <td className="px-5 py-4">
                  <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${laneTone(a.lane)}`}>
                    Lane {a.lane}
                  </span>
                </td>
                <td className="px-5 py-4 font-medium tabular-nums text-slate-900">{a.blended_score.toFixed(2)}</td>
                <td className="px-5 py-4 text-right">
                  <Link
                    className="focus-ring inline-flex rounded-full bg-slate-900 px-3.5 py-2 text-xs font-medium text-white transition hover:-translate-y-0.5"
                    to={`/case/${a.id}`}
                  >
                    Review case
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {alerts.length === 0 && (
        <div className="soft-panel rounded-[24px] p-6 text-sm text-slate-500">
          No alerts are waiting. Run the bootstrap command to generate a review queue.
        </div>
      )}
    </div>
  );
}
