import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiGet, type AlertOut, type AlertPage, type BaselineOverview } from "../api/client";
import { useGlossary } from "../api/glossary";
import { Card, Donut, StatTile } from "../lib/ui";
import { IconAlert, IconBaselines, IconMerchants, IconShield } from "../lib/icons";

export default function Dashboard() {
  const g = useGlossary();
  const [alerts, setAlerts] = useState<AlertOut[]>([]);
  // Total across the whole queue, not just the page fetched above.
  const [total, setTotal] = useState(0);
  const [baselines, setBaselines] = useState<BaselineOverview | null>(null);

  useEffect(() => {
    // The queue endpoint is paginated, so the rows live under `items`. Asking
    // for a few is also all this panel needs — it shows the top five.
    apiGet<AlertPage>("/api/alerts?page=1&page_size=5")
      .then((p) => {
        setAlerts(p.items);
        setTotal(p.total);
      })
      .catch(() => {
        setAlerts([]);
        setTotal(0);
      });
    apiGet<BaselineOverview>("/api/baselines").then(setBaselines).catch(() => undefined);
  }, []);

  const laneA = baselines?.merchants.filter((m) => m.lane === "A").length ?? 0;
  const laneB = baselines?.merchants.filter((m) => m.lane === "B").length ?? 0;
  const top = alerts[0];

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Open alerts"
          value={total}
          hint="awaiting a decision"
          icon={<IconAlert className="h-5 w-5" />}
          tone="warning"
        />
        <StatTile
          label="Merchants"
          value={baselines?.total_count ?? "—"}
          hint="merchants monitored"
          icon={<IconMerchants className="h-5 w-5" />}
          tone="blue"
        />
        <StatTile
          label="Scoreable"
          value={baselines ? `${baselines.usable_count}/${baselines.total_count}` : "—"}
          hint="have a usable baseline"
          icon={<IconShield className="h-5 w-5" />}
          tone="success"
        />
        <StatTile
          label="Days withheld"
          value={baselines?.quarantined_total ?? 0}
          hint="confirmed bad, excluded"
          icon={<IconBaselines className="h-5 w-5" />}
          tone="navy"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.3fr_1fr]">
        <Card title="Tonight's run" subtitle="What the pipeline produced">
          <div className="p-5">
            <p className="text-[0.98rem] leading-7 text-[var(--text)]">
              <span className="font-semibold text-[var(--text-strong)]">{total}</span>{" "}
              alert{total === 1 ? "" : "s"} awaiting a decision.
              {top && (
                <>
                  {" "}
                  The top concern is{" "}
                  <span className="font-semibold text-[var(--text-strong)]">{top.merchant_id}</span> —{" "}
                  {g.detector(top.triggering_detectors[0]?.detector ?? "")}.
                </>
              )}
            </p>
            <div className="mt-5 flex flex-wrap gap-2.5">
              <Link
                to="/queue"
                className="focus-ring rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2.5 text-[0.9rem] font-medium text-white transition hover:bg-[var(--navy-700)]"
              >
                Open alert queue
              </Link>
              <Link
                to={top ? `/case/${top.id}` : "/queue"}
                className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-4 py-2.5 text-[0.9rem] font-medium text-[var(--text-strong)] transition hover:bg-[var(--blue-50)]"
              >
                Review top case
              </Link>
            </div>
          </div>
        </Card>

        <Card title="Merchants by group" subtitle="How the portfolio is being judged">
          <div className="p-5">
            <Donut
              caption="Merchants by group"
              total={laneA + laneB}
              segments={[
                { label: "Established history", value: laneA, color: "var(--blue-500)" },
                { label: "Limited history", value: laneB, color: "var(--warning)" },
              ]}
            />
          </div>
        </Card>
      </div>

      <Card
        title="Top alerts"
        subtitle="Highest risk from tonight"
        action={
          <Link
            to="/queue"
            className="focus-ring rounded-full border border-[var(--border-strong)] px-3 py-1.5 text-[0.82rem] font-medium text-[var(--text-strong)] hover:bg-[var(--blue-50)]"
          >
            View all
          </Link>
        }
      >
        <ul className="divide-y divide-[var(--border)]">
          {alerts.map((a) => (
            <li key={a.id}>
              <Link
                to={`/case/${a.id}`}
                className="hoverable flex items-center justify-between gap-4 px-5 py-3.5"
              >
                <div className="min-w-0">
                  <p className="font-medium text-[var(--text-strong)]">{a.merchant_id}</p>
                  <p className="mt-0.5 text-[0.85rem] text-[var(--muted)]">
                    {g.detector(a.triggering_detectors[0]?.detector ?? "")}
                  </p>
                </div>
                <span className="metric-number text-[0.9rem] font-semibold text-[var(--text-strong)]">
                  {a.blended_score.toFixed(2)}
                </span>
              </Link>
            </li>
          ))}
          {alerts.length === 0 && (
            <li className="px-5 py-8 text-center text-[0.92rem] text-[var(--muted)]">
              No alerts waiting.
            </li>
          )}
        </ul>
      </Card>
    </div>
  );
}
