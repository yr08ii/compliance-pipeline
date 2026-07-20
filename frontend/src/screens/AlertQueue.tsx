import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";

export default function AlertQueue() {
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<AlertOut[]>("/api/alerts").then(setAlerts).catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="text-red-600">Failed to load alerts: {error}</p>;

  return (
    <div>
      <h2 className="mb-4 text-lg font-medium">Alert queue</h2>
      <table className="w-full text-sm">
        <thead className="text-left text-slate-500">
          <tr><th className="py-2">Rank</th><th>Merchant</th><th>Lane</th><th>Score</th><th></th></tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id} className="border-t">
              <td className="py-2">{a.rank}</td>
              <td>{a.merchant_id}</td>
              <td>{a.lane}</td>
              <td>{a.blended_score.toFixed(2)}</td>
              <td><Link className="text-blue-600" to={`/case/${a.id}`}>Review</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
      {alerts.length === 0 && <p className="mt-4 text-slate-500">No alerts in the queue.</p>}
    </div>
  );
}
