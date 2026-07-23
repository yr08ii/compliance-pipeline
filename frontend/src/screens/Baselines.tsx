import { useEffect, useState } from "react";
import { apiGet, type BaselineOverview, type BaselineRow } from "../api/client";
import { useGlossary } from "../api/glossary";

function shortDate(iso: string | null | undefined) {
  return iso ? iso.slice(0, 10) : "—";
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="soft-panel rounded-2xl px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">{label}</p>
      <p className="metric-number mt-2 text-[1.5rem] font-semibold text-slate-950">{value}</p>
      {note && <p className="mt-1 text-[0.9rem] text-slate-600">{note}</p>}
    </div>
  );
}

function Coverage({ row }: { row: BaselineRow }) {
  const badges: [string, boolean][] = [
    ["Amount", row.usable],
    ["Volume", row.volume_usable],
    ["Speed", row.velocity_usable],
    ["Peers", row.peer_usable],
  ];
  return (
    <div className="flex flex-wrap gap-1.5">
      {badges.map(([label, on]) => (
        <span
          key={label}
          className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
            on ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-400"
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

  if (error) {
    return (
      <div className="soft-panel rounded-[24px] p-6 text-sm text-red-700">
        Could not load baselines.
      </div>
    );
  }
  if (!data) {
    return <div className="soft-panel rounded-[24px] p-6 text-sm text-slate-500">Loading…</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Provenance</p>
        <h2 className="mt-2 text-2xl text-slate-950">Baselines</h2>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Built from"
          value={shortDate(data.window_start)}
          note={`to ${shortDate(data.window_end)}`}
        />
        <Stat
          label="Enters next run"
          value={shortDate(data.next_inclusion_date)}
          note={`held ${data.lag_days} days for review`}
        />
        <Stat
          label="Scoreable"
          value={`${data.usable_count}/${data.total_count}`}
          note="merchants with a usable baseline"
        />
        <Stat
          label="Days withheld"
          value={String(data.quarantined_total)}
          note="confirmed bad, excluded"
        />
      </section>

      <section className="soft-panel rounded-[28px] p-5">
        <p className="text-[0.95rem] leading-7 text-slate-700">
          Baselines use a {data.window_days}-day window ending {shortDate(data.window_end)}. The{" "}
          {data.lag_days}-day gap is the review period: {shortDate(data.next_inclusion_date)} joins
          tonight, minus any day confirmed bad.
        </p>
      </section>

      <section className="soft-panel overflow-hidden rounded-[28px]">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200/80 bg-slate-50/80 text-xs uppercase tracking-[0.18em] text-slate-500">
            <tr>
              <th className="px-5 py-4 font-medium">Merchant</th>
              <th className="px-5 py-4 font-medium">Group</th>
              <th className="px-5 py-4 font-medium">Baseline</th>
              <th className="px-5 py-4 font-medium">Days</th>
              <th className="px-5 py-4 font-medium">Detectors</th>
            </tr>
          </thead>
          <tbody>
            {data.merchants.map((row) => (
              <>
                <tr
                  key={row.merchant_id}
                  onClick={() => setOpen(open === row.merchant_id ? null : row.merchant_id)}
                  className="cursor-pointer border-b border-slate-100 transition last:border-b-0 hover:bg-slate-50/80"
                >
                  <td className="px-5 py-4">
                    <div className="font-medium text-slate-950">{row.merchant_id}</div>
                    <div className="mt-1 text-xs text-slate-500">MCC {row.mcc ?? "—"}</div>
                  </td>
                  <td className="px-5 py-4">
                    <span
                      className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${
                        row.lane === "A"
                          ? "border-violet-200 bg-violet-50 text-violet-700"
                          : "border-amber-200 bg-amber-50 text-amber-700"
                      }`}
                    >
                      {g.lane(row.lane)}
                    </span>
                  </td>
                  <td className="px-5 py-4 text-slate-700">
                    {g.method(row.method)}
                  </td>
                  <td className="px-5 py-4 tabular-nums text-slate-700">
                    {row.observations}
                    {row.quarantined_days > 0 && (
                      <span className="ml-2 rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-700">
                        −{row.quarantined_days} withheld
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-4">
                    <Coverage row={row} />
                  </td>
                </tr>
                {open === row.merchant_id && (
                  <tr key={`${row.merchant_id}-detail`} className="border-b border-slate-100 bg-slate-50/60">
                    <td colSpan={5} className="px-5 py-4">
                      <dl className="grid gap-3 text-[0.95rem] sm:grid-cols-3">
                        <div>
                          <dt className="text-slate-500">Typical ticket</dt>
                          <dd className="metric-number font-medium text-slate-950">
                            {row.center != null ? row.center.toLocaleString() : "—"}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Cohort size</dt>
                          <dd className="metric-number font-medium text-slate-950">
                            {row.peer_merchants} merchants
                          </dd>
                        </div>
                        <div>
                          <dt className="text-slate-500">Trend</dt>
                          <dd className="font-medium text-slate-950">
                            {row.is_ramp ? "Rising" : "Stable"}
                          </dd>
                        </div>
                      </dl>
                      {!row.usable && (
                        <p className="mt-3 text-[0.9rem] text-slate-600">
                          No self baseline — scored by cohort comparison only.
                        </p>
                      )}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
