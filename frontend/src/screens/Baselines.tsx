import { useEffect, useState } from "react";
import { apiGet, type BaselineOverview, type BaselineRow } from "../api/client";
import { useGlossary } from "../api/glossary";
import { Card, ErrorNote, Loading, Pill, StatTile } from "../lib/ui";
import { IconBaselines, IconClock, IconMerchants, IconShield } from "../lib/icons";

function short(iso: string | null | undefined) {
  return iso ? iso.slice(0, 10) : "—";
}

function Coverage({ row }: { row: BaselineRow }) {
  const on: [string, boolean][] = [
    ["Amount", row.usable],
    ["Volume", row.volume_usable],
    ["Speed", row.velocity_usable],
    ["Peers", row.peer_usable],
  ];
  return (
    <div className="flex flex-wrap gap-1">
      {on.map(([label, active]) => (
        <span
          key={label}
          className={`rounded px-1.5 py-0.5 text-[0.7rem] font-semibold ${
            active ? "bg-[var(--success-bg)] text-[var(--success)]" : "bg-slate-100 text-slate-400"
          }`}
        >
          {label}
        </span>
      ))}
    </div>
  );
}

export default function Baselines() {
  const g = useGlossary();
  const [data, setData] = useState<BaselineOverview | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    apiGet<BaselineOverview>("/api/baselines").then(setData).catch(() => setError(true));
  }, []);

  if (error) return <ErrorNote>Could not load baselines.</ErrorNote>;
  if (!data) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Built from"
          value={short(data.window_start)}
          hint={`to ${short(data.window_end)}`}
          icon={<IconBaselines className="h-5 w-5" />}
          tone="navy"
        />
        <StatTile
          label="Enters next run"
          value={short(data.next_inclusion_date)}
          hint={`held ${data.lag_days} days for review`}
          icon={<IconClock className="h-5 w-5" />}
          tone="blue"
        />
        <StatTile
          label="Scoreable"
          value={`${data.usable_count}/${data.total_count}`}
          hint="have a usable baseline"
          icon={<IconShield className="h-5 w-5" />}
          tone="success"
        />
        <StatTile
          label="Days withheld"
          value={data.quarantined_total}
          hint="confirmed bad, excluded"
          icon={<IconMerchants className="h-5 w-5" />}
          tone="warning"
        />
      </div>

      <Card>
        <p className="px-5 py-4 text-[0.92rem] leading-7 text-[var(--text)]">
          Baselines use a {data.window_days}-day window ending {short(data.window_end)}. The{" "}
          {data.lag_days}-day gap is the review period: {short(data.next_inclusion_date)} joins on
          the next run, minus any day confirmed bad.
        </p>
      </Card>

      <Card title="Per merchant" subtitle="What each merchant is judged against">
        <table className="w-full text-left text-[0.9rem]">
          <thead className="border-b border-[var(--border)] text-[0.72rem] uppercase tracking-[0.12em] text-[var(--muted)]">
            <tr>
              <th className="px-5 py-3 font-semibold">Merchant</th>
              <th className="px-5 py-3 font-semibold">Group</th>
              <th className="px-5 py-3 font-semibold">Baseline</th>
              <th className="px-5 py-3 text-right font-semibold">Transactions</th>
              <th className="px-5 py-3 font-semibold">Detectors</th>
            </tr>
          </thead>
          <tbody>
            {data.merchants.map((row) => (
              <tr
                key={row.merchant_id}
                onClick={() => setOpen(open === row.merchant_id ? null : row.merchant_id)}
                className="cursor-pointer border-b border-[var(--border)] last:border-b-0 hoverable"
              >
                <td className="px-5 py-3.5">
                  <p className="font-medium text-[var(--text-strong)]">{row.merchant_id}</p>
                  <p className="mt-0.5 text-[0.78rem] text-[var(--muted)]">MCC {row.mcc ?? "—"}</p>
                </td>
                <td className="px-5 py-3.5">
                  <Pill tone={row.lane === "A" ? "blue" : "warning"}>{g.lane(row.lane)}</Pill>
                </td>
                <td className="px-5 py-3.5 text-[var(--text)]">{g.method(row.method)}</td>
                <td className="px-5 py-3.5 text-right metric-number">
                  {row.observations}
                  {row.quarantined_days > 0 && (
                    <span className="ml-2 rounded bg-[var(--danger-bg)] px-1.5 py-0.5 text-[0.7rem] font-semibold text-[var(--danger)]">
                      −{row.quarantined_days}
                    </span>
                  )}
                </td>
                <td className="px-5 py-3.5">
                  <Coverage row={row} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
