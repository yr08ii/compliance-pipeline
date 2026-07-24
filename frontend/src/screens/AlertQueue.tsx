import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";
import { useGlossary } from "../api/glossary";
import { Card, Empty, ErrorNote, Pill } from "../lib/ui";
import { IconChevron } from "../lib/icons";

export default function AlertQueue() {
  const g = useGlossary();
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<AlertOut[]>("/api/alerts").then(setAlerts).catch((e) => setError(String(e)));
  }, []);

  if (error) return <ErrorNote>Failed to load alerts: {error}</ErrorNote>;

  return (
    <Card title={`${alerts.length} open alert${alerts.length === 1 ? "" : "s"}`} subtitle="Highest blended risk first">
      {alerts.length === 0 ? (
        <Empty>No alerts are waiting. Run the pipeline to generate a review queue.</Empty>
      ) : (
        <table className="w-full text-left text-[0.9rem]">
          <thead className="border-b border-[var(--border)] text-[0.72rem] uppercase tracking-[0.12em] text-[var(--muted)]">
            <tr>
              <th className="px-5 py-3 font-semibold">Rank</th>
              <th className="px-5 py-3 font-semibold">Merchant</th>
              <th className="px-5 py-3 font-semibold">Reason</th>
              <th className="px-5 py-3 font-semibold">Group</th>
              <th className="px-5 py-3 text-right font-semibold">Score</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.id} className="border-b border-[var(--border)] last:border-b-0 hoverable">
                <td className="px-5 py-3.5 metric-number font-semibold text-[var(--text-strong)]">
                  {a.rank}
                </td>
                <td className="px-5 py-3.5 font-medium text-[var(--text-strong)]">{a.merchant_id}</td>
                <td className="px-5 py-3.5 text-[var(--text)]">
                  {g.detector(a.triggering_detectors[0]?.detector ?? "")}
                </td>
                <td className="px-5 py-3.5">
                  <Pill tone={a.lane === "A" ? "blue" : "warning"}>{g.lane(a.lane)}</Pill>
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
      )}
    </Card>
  );
}
