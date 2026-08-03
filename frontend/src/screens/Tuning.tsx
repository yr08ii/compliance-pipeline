import { useEffect, useState } from "react";
import { apiGet, apiSend } from "../api/client";
import { Card, ErrorNote } from "../lib/ui";

type Settings = {
  outlier_z: number;
  moderate_z: number;
  materiality_floor: number;
  min_observations: number;
  min_span_days: number;
  lag_days: number;
  window_days: number;
  mcc_overrides: Record<string, Record<string, number>>;
};

type Payload = { current: Settings; defaults: Settings };

/** Each control says what it does and what moving it costs, because a slider
 *  with no stated trade-off invites turning sensitivity down until the queue
 *  is empty — which is not the same as having no risk. */
const CONTROLS: {
  key: keyof Settings;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
  unit?: string;
}[] = [
  {
    key: "outlier_z",
    label: "Outlier threshold",
    hint: "How far from a merchant's own normal a value must sit before it counts as an outlier, in modified standard deviations. Lower catches more and costs more false alerts; higher lets genuine anomalies through.",
    min: 2,
    max: 8,
    step: 0.1,
    unit: "σ",
  },
  {
    key: "materiality_floor",
    label: "Materiality floor",
    hint: "Amounts below this never raise an amount alert, however statistically unusual. A HKD 150 transaction can be a real outlier at a convenience store and still be worthless to a launderer. Set to 0 to disable.",
    min: 0,
    max: 20000,
    step: 100,
    unit: "HKD",
  },
  {
    key: "min_observations",
    label: "Minimum transactions",
    hint: "How many transactions a merchant needs before it can be judged against its own past. Below this it is compared to its peers instead.",
    min: 5,
    max: 200,
    step: 1,
  },
  {
    key: "min_span_days",
    label: "Minimum history",
    hint: "How many days that history must span. A merchant trading heavily for three days has the count but no weekly shape, so its 'normal' is not yet a normal.",
    min: 3,
    max: 90,
    step: 1,
    unit: "days",
  },
  {
    key: "lag_days",
    label: "Review lag",
    hint: "How long recent activity is held out of the baseline. This is the window in which an analyst can still rule on a day before it becomes part of normal. Shortening it below your review turnaround means unreviewed activity starts setting the standard.",
    min: 0,
    max: 30,
    step: 1,
    unit: "days",
  },
  {
    key: "window_days",
    label: "Baseline window",
    hint: "How much history forms the baseline. Longer is more stable but slower to reflect a merchant that has genuinely changed.",
    min: 7,
    max: 180,
    step: 1,
    unit: "days",
  },
];

export default function Tuning() {
  const [data, setData] = useState<Payload | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mcc, setMcc] = useState("");
  const [mccZ, setMccZ] = useState("4.5");

  useEffect(() => {
    apiGet<Payload>("/api/settings")
      .then((p) => {
        setData(p);
        setDraft(p.current);
      })
      .catch(() => setError("Could not load settings."));
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!data || !draft) return <Card><p className="px-5 py-8 text-[0.9rem] text-[var(--muted)]">Loading…</p></Card>;

  const changed = JSON.stringify(draft) !== JSON.stringify(data.current);

  async function save() {
    try {
      const next = await apiSend<Payload>("/api/settings", draft, "PUT");
      setData(next);
      setDraft(next.current);
      setSaved(true);
      setTimeout(() => setSaved(false), 4000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setDraft({ ...draft!, [key]: value });
  }

  return (
    <div className="space-y-4">
      <Card>
        <p className="px-5 py-3.5 text-[0.9rem] leading-6 text-[var(--text)]">
          Changes apply to the <strong>next pipeline run</strong>, not retroactively.
          Existing alerts were judged under the thresholds in force at the time, and
          rewriting that would break the audit trail.
        </p>
      </Card>

      <Card title="Detection thresholds" subtitle="Hover a label to see what it controls">
        <div className="space-y-5 p-5">
          {CONTROLS.map((c) => {
            const value = draft[c.key] as number;
            const stock = data.defaults[c.key] as number;
            return (
              <div key={String(c.key)}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <label
                    htmlFor={String(c.key)}
                    title={c.hint}
                    className="cursor-help text-[0.9rem] font-medium text-[var(--text-strong)] underline decoration-dotted underline-offset-4"
                  >
                    {c.label}
                  </label>
                  <span className="metric-number text-[0.9rem] font-semibold text-[var(--text-strong)]">
                    {value.toLocaleString()} {c.unit ?? ""}
                    {value !== stock && (
                      <span className="ml-2 text-[0.78rem] font-normal text-[var(--muted)]">
                        default {stock.toLocaleString()}
                      </span>
                    )}
                  </span>
                </div>
                <input
                  id={String(c.key)}
                  type="range"
                  min={c.min}
                  max={c.max}
                  step={c.step}
                  value={value}
                  onChange={(e) => set(c.key, Number(e.target.value) as never)}
                  className="focus-ring mt-2 w-full accent-[var(--blue-600)]"
                />
                <p className="mt-1 text-[0.82rem] leading-5 text-[var(--muted)]">{c.hint}</p>
              </div>
            );
          })}
        </div>
      </Card>

      <Card
        title="Per-MCC overrides"
        subtitle="Some trades are legitimately volatile — a jeweller's tickets are lumpy where a grocer's are not"
      >
        <div className="p-5">
          {Object.keys(draft.mcc_overrides ?? {}).length === 0 ? (
            <p className="text-[0.9rem] text-[var(--muted)]">
              No overrides. Every merchant category uses the global thresholds above.
            </p>
          ) : (
            <table className="w-full text-left text-[0.88rem]">
              <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
                <tr>
                  <th className="py-2 font-semibold">MCC</th>
                  <th className="py-2 text-right font-semibold">Outlier threshold</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {Object.entries(draft.mcc_overrides).map(([code, o]) => (
                  <tr key={code} className="border-b border-[var(--border)] last:border-b-0">
                    <td className="py-2 font-medium text-[var(--text-strong)]">{code}</td>
                    <td className="py-2 text-right metric-number">{o.outlier_z ?? "—"} σ</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        onClick={() => {
                          const next = { ...draft.mcc_overrides };
                          delete next[code];
                          set("mcc_overrides", next);
                        }}
                        className="focus-ring text-[0.82rem] font-medium text-[var(--danger)] hover:underline"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-[var(--border)] pt-4">
            <label>
              <span className="text-[0.78rem] font-medium text-[var(--text-strong)]">
                MCC code
              </span>
              <input
                value={mcc}
                onChange={(e) => setMcc(e.target.value)}
                placeholder="5944"
                className="focus-ring mt-1 block w-28 rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.88rem]"
              />
            </label>
            <label>
              <span className="text-[0.78rem] font-medium text-[var(--text-strong)]">
                Outlier threshold
              </span>
              <input
                value={mccZ}
                onChange={(e) => setMccZ(e.target.value)}
                className="focus-ring mt-1 block w-28 rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.88rem]"
              />
            </label>
            <button
              type="button"
              disabled={!mcc || Number.isNaN(Number(mccZ))}
              onClick={() => {
                set("mcc_overrides", {
                  ...draft.mcc_overrides,
                  [mcc]: { outlier_z: Number(mccZ) },
                });
                setMcc("");
              }}
              className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-4 py-2 text-[0.88rem] font-medium text-[var(--text-strong)] transition hover:bg-[var(--blue-50)] disabled:opacity-45"
            >
              Add override
            </button>
          </div>
        </div>
      </Card>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={!changed}
          className="focus-ring rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2.5 text-[0.9rem] font-medium text-white transition hover:bg-[var(--navy-700)] disabled:cursor-not-allowed disabled:opacity-45"
        >
          Save thresholds
        </button>
        <button
          type="button"
          onClick={() => setDraft(data.defaults)}
          className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-4 py-2.5 text-[0.9rem] font-medium text-[var(--text-strong)] transition hover:bg-[var(--blue-50)]"
        >
          Reset to defaults
        </button>
        {saved && <span className="text-[0.88rem] text-[var(--success)]">Saved.</span>}
        {changed && !saved && (
          <span className="text-[0.88rem] text-[var(--muted)]">Unsaved changes.</span>
        )}
      </div>
    </div>
  );
}
