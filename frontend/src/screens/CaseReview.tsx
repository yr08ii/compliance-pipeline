import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { apiGet, type AlertOut } from "../api/client";
import { useGlossary } from "../api/glossary";
import { Card, ErrorNote, Loading, Pill } from "../lib/ui";

function tone(deviation: number) {
  if (deviation >= 5) return "danger" as const;
  if (deviation >= 3) return "warning" as const;
  return "success" as const;
}

export default function CaseReview() {
  const g = useGlossary();
  const { id } = useParams();
  const [alert, setAlert] = useState<AlertOut | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (id) {
      setError(false);
      apiGet<AlertOut>(`/api/alerts/${id}`).then(setAlert).catch(() => setError(true));
    }
  }, [id]);

  if (error) return <ErrorNote>Could not load this case. Open the queue and pick a valid alert.</ErrorNote>;
  if (!alert) return <Loading />;

  const primary = [...alert.feature_snapshot].sort((a, b) => b.deviation - a.deviation)[0];

  return (
    <div className="space-y-6">
      <Card>
        <div className="flex flex-col gap-5 p-5 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-[0.72rem] uppercase tracking-[0.16em] text-[var(--muted)]">
              Case review
            </p>
            <h2 className="mt-1 text-[1.7rem]">{alert.merchant_id}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[0.9rem] text-[var(--muted)]">
              <Pill tone={alert.lane === "A" ? "blue" : "warning"}>{g.lane(alert.lane)}</Pill>
              <span>rank {alert.rank}</span>
              <span aria-hidden>·</span>
              <span>
                score{" "}
                <span className="metric-number font-semibold text-[var(--text-strong)]">
                  {alert.blended_score.toFixed(2)}
                </span>
              </span>
            </div>
          </div>
          <div className="rounded-[var(--radius)] bg-[var(--blue-50)] px-5 py-4">
            <p className="text-[0.75rem] uppercase tracking-[0.14em] text-[var(--blue-600)]">
              Primary reason
            </p>
            <p className="mt-1 text-[1.02rem] font-semibold text-[var(--navy-800)]">
              {g.detector(alert.triggering_detectors[0]?.detector ?? "")}
            </p>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <Card title="What was unusual" subtitle="The exact measures behind the flag, as of the run">
          <table className="w-full text-[0.9rem]">
            <thead className="border-b border-[var(--border)] text-left text-[0.72rem] uppercase tracking-[0.12em] text-[var(--muted)]">
              <tr>
                <th className="px-5 py-3 font-semibold">Measure</th>
                <th className="px-5 py-3 text-right font-semibold">This merchant</th>
                <th className="px-5 py-3 text-right font-semibold">Baseline</th>
                <th className="px-5 py-3 text-right font-semibold">Deviation</th>
              </tr>
            </thead>
            <tbody>
              {alert.feature_snapshot.map((f, i) => (
                <tr key={i} className="border-b border-[var(--border)] last:border-b-0">
                  <td className="px-5 py-3.5 font-medium text-[var(--text-strong)]">
                    {g.feature(f.feature_name)}
                  </td>
                  <td className="px-5 py-3.5 text-right metric-number">
                    {f.merchant_value.toLocaleString()}
                  </td>
                  <td className="px-5 py-3.5 text-right metric-number text-[var(--muted)]">
                    {f.baseline_value.toLocaleString()}
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <Pill tone={tone(f.deviation)}>{f.deviation.toFixed(1)}σ</Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <div className="space-y-4">
          <Card title="Summary">
            <dl className="divide-y divide-[var(--border)]">
              {[
                ["Reason", g.detector(alert.triggering_detectors[0]?.detector ?? "")],
                ["Compared against", primary ? g.feature(primary.feature_name) : "—"],
                ["Baseline", primary ? primary.baseline_value.toLocaleString() : "—"],
                ["Observed", primary ? primary.merchant_value.toLocaleString() : "—"],
              ].map(([k, v]) => (
                <div key={k} className="flex items-center justify-between gap-4 px-5 py-3 text-[0.9rem]">
                  <dt className="text-[var(--muted)]">{k}</dt>
                  <dd className="metric-number font-medium text-[var(--text-strong)] text-right">{v}</dd>
                </div>
              ))}
            </dl>
          </Card>

          <Card>
            <div className="p-5">
              <p className="text-[0.72rem] uppercase tracking-[0.14em] text-[var(--muted)]">
                Analyst decision
              </p>
              <p className="mt-2 text-[0.92rem] leading-7 text-[var(--text)]">
                Verdict capture, reason codes, and a signed decision arrive in the disposition
                phase. Deviation is shown in standard deviations from the merchant's baseline.
              </p>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
