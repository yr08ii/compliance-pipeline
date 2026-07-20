import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";

export default function CaseReview() {
  const { id } = useParams();
  const [alert, setAlert] = useState<AlertOut | null>(null);

  useEffect(() => {
    if (id) apiGet<AlertOut>(`/api/alerts/${id}`).then(setAlert).catch(() => setAlert(null));
  }, [id]);

  if (!alert) return <p className="text-slate-500">Loading case…</p>;

  return (
    <div className="max-w-3xl">
      <h2 className="text-lg font-medium">Case review — {alert.merchant_id}</h2>
      <p className="mb-6 text-sm text-slate-500">
        Lane {alert.lane} · score {alert.blended_score.toFixed(2)} · rank {alert.rank}
      </p>

      <h3 className="mb-2 text-sm font-medium">What diverged from baseline</h3>
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr><th className="py-2">Feature</th><th>Merchant</th><th>Baseline</th><th>Deviation</th></tr>
        </thead>
        <tbody>
          {alert.feature_snapshot.map((f, i) => (
            <tr key={i} className="border-t">
              <td className="py-2">{f.feature_name}</td>
              <td>{f.merchant_value.toLocaleString()}</td>
              <td>{f.baseline_value.toLocaleString()}</td>
              <td className={f.deviation >= 3 ? "font-medium text-red-600" : ""}>
                {f.deviation.toFixed(2)}×
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-6 text-sm text-slate-400">
        Disposition form and digital signature arrive in the Phase 2 plan.
      </p>
    </div>
  );
}
