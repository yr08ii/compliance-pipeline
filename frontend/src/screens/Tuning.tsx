import { useEffect, useState } from "react";
import {
  apiGet,
  apiSend,
  type RuleInstanceIn,
  type RuleParam,
  type RuleSet,
  type RuleTemplateOut,
} from "../api/client";
import { Card, ErrorNote, Pill } from "../lib/ui";
import { cn } from "../lib/utils";

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

/** The three families are tuned as different kinds of thing, so they get
 *  different sections rather than one long list of numbers.
 *
 *  Family A is a handful of global statistical thresholds — sliders. Families
 *  B and C are a *set* of named rules that can be switched off, retuned,
 *  scoped to a trade, and added to. Presenting a structuring rule as another
 *  slider would hide that it has four interacting parameters and that moving
 *  any one of them changes what the rule means. */
type Section = "A" | "B" | "C";

const SECTIONS: { key: Section; label: string; blurb: string }[] = [
  {
    key: "A",
    label: "Baseline thresholds",
    blurb:
      "How far from normal a number must sit before it counts. These apply to every statistical comparison — a merchant against its own history, its trade, and its district.",
  },
  {
    key: "B",
    label: "Typology rules",
    blurb:
      "Known laundering patterns. Unlike the baselines these do not measure a deviation: every transaction in a structuring run looks ordinary on its own, and the shape is the signal. Each rule can be retuned, scoped to a merchant category, switched off, or duplicated with your own parameters.",
  },
  {
    key: "C",
    label: "Ring detection",
    blurb:
      "Coordination between merchants — shared ownership, a shared onboarding agent, or a card connecting them. These run across the whole portfolio rather than inside one merchant, so they cannot be scoped to a single category.",
  },
];

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

const UNIT: Record<string, string> = {
  money: "HKD",
  count: "",
  ratio: "",
  days: "days",
  minutes: "min",
  speed: "km/h",
};

function formatParam(p: RuleParam, value: number) {
  if (p.kind === "ratio") return `${(value * 100).toFixed(0)}%`;
  return `${value.toLocaleString()} ${UNIT[p.kind] ?? ""}`.trim();
}

/** Per-MCC exceptions to the outlier threshold.
 *
 *  Sits directly under the global outlier slider rather than in a card of its
 *  own further down, because it is not a separate setting: it is the *same*
 *  setting for one trade, and it is the only one of these thresholds that can
 *  be overridden at all. Presenting it as a peer of the other sliders implied
 *  the rest could be overridden too, and put the exception a screen away from
 *  the rule it excepts. */
function MccOverrides({
  overrides,
  onChange,
}: {
  overrides: Record<string, Record<string, number>>;
  onChange: (next: Record<string, Record<string, number>>) => void;
}) {
  const [mcc, setMcc] = useState("");
  const [z, setZ] = useState("4.5");
  const entries = Object.entries(overrides ?? {});

  return (
    <div className="mt-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--blue-50)]/40 p-4">
      <p className="text-[0.84rem] font-medium text-[var(--text-strong)]">
        Exceptions for one merchant category
      </p>
      <p className="mt-0.5 text-[0.81rem] leading-5 text-[var(--muted)]">
        The only threshold above that can be overridden. Some trades are
        legitimately volatile — a jeweller&rsquo;s tickets are lumpy where a
        grocer&rsquo;s are not — and holding both to one number means either
        flooding the queue with jewellers or going blind to grocers. Categories
        not listed here use the global threshold.
      </p>

      {entries.length > 0 && (
        <table className="mt-3 w-full text-left text-[0.86rem]">
          <thead className="border-b border-[var(--border)] text-[0.68rem] uppercase tracking-[0.12em] text-[var(--muted)]">
            <tr>
              <th className="py-2 font-semibold">MCC</th>
              <th className="py-2 text-right font-semibold">Outlier threshold</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {entries.map(([code, o]) => (
              <tr key={code} className="border-b border-[var(--border)] last:border-b-0">
                <td className="py-2 font-medium text-[var(--text-strong)]">{code}</td>
                <td className="py-2 text-right metric-number">{o.outlier_z ?? "—"} σ</td>
                <td className="py-2 text-right">
                  <button
                    type="button"
                    onClick={() => {
                      const next = { ...overrides };
                      delete next[code];
                      onChange(next);
                    }}
                    className="focus-ring text-[0.8rem] font-medium text-[var(--danger)] hover:underline"
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label>
          <span className="text-[0.76rem] font-medium text-[var(--text-strong)]">
            MCC code
          </span>
          <input
            value={mcc}
            onChange={(e) => setMcc(e.target.value)}
            placeholder="5944"
            className="focus-ring mt-1 block w-24 rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.86rem]"
          />
        </label>
        <label>
          <span className="text-[0.76rem] font-medium text-[var(--text-strong)]">
            Threshold for it
          </span>
          <input
            value={z}
            onChange={(e) => setZ(e.target.value)}
            className="focus-ring mt-1 block w-24 rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.86rem]"
          />
        </label>
        <button
          type="button"
          disabled={!mcc || Number.isNaN(Number(z))}
          onClick={() => {
            onChange({ ...overrides, [mcc]: { outlier_z: Number(z) } });
            setMcc("");
          }}
          className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.86rem] font-medium text-[var(--text-strong)] transition hover:bg-white disabled:opacity-45"
        >
          Add exception
        </button>
      </div>
    </div>
  );
}

/** One rule instance: its parameters, its scope, and whether it runs. */
function RuleCard({
  instance,
  template,
  onChange,
  onRemove,
}: {
  instance: RuleInstanceIn;
  template: RuleTemplateOut;
  onChange: (next: RuleInstanceIn) => void;
  onRemove: (() => void) | null;
}) {
  const [open, setOpen] = useState(false);
  const value = (p: RuleParam) => instance.params[p.key] ?? p.default;
  const changed = template.params.some((p) => instance.params[p.key] !== undefined);

  return (
    <div
      className={cn(
        "rounded-[var(--radius)] border border-[var(--border)] p-4",
        !instance.enabled && "opacity-60"
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-[0.95rem] font-semibold text-[var(--text-strong)]">
              {instance.label || template.label}
            </h4>
            {instance.custom && <Pill tone="blue">Added by you</Pill>}
            {changed && !instance.custom && <Pill tone="warning">Retuned</Pill>}
            {instance.mcc_scope.length > 0 && (
              <Pill tone="neutral">MCC {instance.mcc_scope.join(", ")}</Pill>
            )}
          </div>
          <p className="mt-1 max-w-2xl text-[0.85rem] leading-6 text-[var(--muted)]">
            {template.description}
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-[0.85rem] text-[var(--text-strong)]">
          <input
            type="checkbox"
            checked={instance.enabled}
            onChange={(e) => onChange({ ...instance, enabled: e.target.checked })}
            className="focus-ring h-4 w-4 accent-[var(--blue-600)]"
          />
          Enabled
        </label>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="focus-ring text-[0.82rem] font-medium text-[var(--blue-600)] hover:underline"
        >
          {open ? "Hide" : "Show"} {template.params.length} parameter
          {template.params.length === 1 ? "" : "s"} and rationale
        </button>
        {!open && (
          <span className="text-[0.8rem] text-[var(--muted)]">
            {template.params
              .map((p) => `${p.label} ${formatParam(p, value(p))}`)
              .join(" · ")}
          </span>
        )}
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            className="focus-ring ml-auto text-[0.82rem] font-medium text-[var(--danger)] hover:underline"
          >
            Remove
          </button>
        )}
      </div>

      {open && (
        <div className="mt-4 space-y-5 border-t border-[var(--border)] pt-4">
          {/* Why the test is built this way. An AML rule that cannot say that
              is not defensible to a regulator, so it sits with the controls
              rather than in a document nobody opens. */}
          <p className="whitespace-pre-line rounded-[var(--radius)] bg-[var(--blue-50)] px-4 py-3 text-[0.84rem] leading-6 text-[var(--text)]">
            {template.rationale}
          </p>

          {template.params.map((p) => (
            <div key={p.key}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <label
                  htmlFor={`${instance.instance_id}-${p.key}`}
                  className="text-[0.88rem] font-medium text-[var(--text-strong)]"
                >
                  {p.label}
                </label>
                <span className="metric-number text-[0.88rem] font-semibold text-[var(--text-strong)]">
                  {formatParam(p, value(p))}
                  {value(p) !== p.default && (
                    <span className="ml-2 text-[0.78rem] font-normal text-[var(--muted)]">
                      default {formatParam(p, p.default)}
                    </span>
                  )}
                </span>
              </div>
              <input
                id={`${instance.instance_id}-${p.key}`}
                type="range"
                min={p.minimum}
                max={p.maximum}
                step={p.step}
                value={value(p)}
                onChange={(e) =>
                  onChange({
                    ...instance,
                    params: { ...instance.params, [p.key]: Number(e.target.value) },
                  })
                }
                className="focus-ring mt-2 w-full accent-[var(--blue-600)]"
              />
              <p className="mt-1 text-[0.81rem] leading-5 text-[var(--muted)]">
                {p.hint}
              </p>
            </div>
          ))}

          {template.scopable && (
            <div>
              <label
                htmlFor={`${instance.instance_id}-scope`}
                className="text-[0.88rem] font-medium text-[var(--text-strong)]"
              >
                Merchant categories
              </label>
              <input
                id={`${instance.instance_id}-scope`}
                value={instance.mcc_scope.join(", ")}
                placeholder="All categories — or e.g. 5944, 5732"
                onChange={(e) =>
                  onChange({
                    ...instance,
                    mcc_scope: e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
                className="focus-ring mt-1 block w-full rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.88rem]"
              />
              <p className="mt-1 text-[0.81rem] leading-5 text-[var(--muted)]">
                Leave empty to run against every merchant. Scoping is how you run
                one rule loosely everywhere and a stricter copy on the trades that
                need it.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Tuning() {
  const [section, setSection] = useState<Section>("A");
  const [data, setData] = useState<Payload | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [rules, setRules] = useState<RuleSet | null>(null);
  const [ruleDraft, setRuleDraft] = useState<RuleInstanceIn[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState("");

  useEffect(() => {
    apiGet<Payload>("/api/settings")
      .then((p) => {
        setData(p);
        setDraft(p.current);
      })
      .catch(() => setError("Could not load settings."));
    apiGet<RuleSet>("/api/rules")
      .then((r) => {
        setRules(r);
        setRuleDraft(r.instances);
      })
      .catch(() => setError("Could not load the rule set."));
  }, []);

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (!data || !draft || !rules || !ruleDraft)
    return (
      <Card>
        <p className="px-5 py-8 text-[0.9rem] text-[var(--muted)]">Loading…</p>
      </Card>
    );

  const thresholdsChanged = JSON.stringify(draft) !== JSON.stringify(data.current);
  const rulesChanged = JSON.stringify(ruleDraft) !== JSON.stringify(rules.instances);
  const changed = section === "A" ? thresholdsChanged : rulesChanged;

  const templatesFor = (family: Section) =>
    rules.templates.filter((t) => t.family === family);
  const instancesFor = (family: Section) =>
    ruleDraft.filter((i) => {
      const t = rules.templates.find((x) => x.key === i.template);
      return t?.family === family;
    });

  async function save() {
    try {
      if (section === "A") {
        const next = await apiSend<Payload>("/api/settings", draft, "PUT");
        setData(next);
        setDraft(next.current);
      } else {
        const next = await apiSend<RuleSet>(
          "/api/rules",
          { instances: ruleDraft },
          "PUT"
        );
        setRules(next);
        setRuleDraft(next.instances);
      }
      setError(null);
      setSaved(true);
      setTimeout(() => setSaved(false), 4000);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setDraft({ ...draft!, [key]: value });
  }

  function updateRule(index: number, next: RuleInstanceIn) {
    const copy = [...ruleDraft!];
    copy[index] = next;
    setRuleDraft(copy);
  }

  /** Add a second instance of a template with its own parameters and scope —
   *  "structuring, but HKD 100,000 and only for jewellers". The instance is
   *  data, so it needs no deploy. */
  function addRule(templateKey: string) {
    const template = rules!.templates.find((t) => t.key === templateKey);
    if (!template) return;
    const existing = ruleDraft!.filter((i) => i.template === templateKey).length;
    setRuleDraft([
      ...ruleDraft!,
      {
        instance_id: `${templateKey}_custom_${existing + 1}`,
        template: templateKey,
        enabled: true,
        params: {},
        mcc_scope: [],
        custom: true,
        label: `${template.label} (copy ${existing})`,
      },
    ]);
    setAdding("");
  }

  const current = SECTIONS.find((s) => s.key === section)!;

  return (
    <div className="space-y-4">
      <Card>
        <p className="px-5 py-3.5 text-[0.9rem] leading-6 text-[var(--text)]">
          Changes apply to the <strong>next pipeline run</strong>, not retroactively.
          Existing alerts were judged under the thresholds in force at the time, and
          rewriting that would break the audit trail.
        </p>
      </Card>

      <div className="flex flex-wrap gap-1 border-b border-[var(--border)]" role="tablist">
        {SECTIONS.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={section === s.key}
            onClick={() => setSection(s.key)}
            className={cn(
              "focus-ring -mb-px rounded-t-[var(--radius)] px-4 py-2.5 text-[0.9rem] font-medium transition-colors",
              section === s.key
                ? "border-b-2 border-[var(--blue-600)] text-[var(--navy-800)]"
                : "text-[var(--muted)] hover:text-[var(--text-strong)]"
            )}
          >
            Family {s.key} · {s.label}
            {s.key !== "A" && (
              <span className="ml-2 text-[0.78rem] text-[var(--muted)]">
                {instancesFor(s.key).filter((i) => i.enabled).length}/
                {instancesFor(s.key).length}
              </span>
            )}
          </button>
        ))}
      </div>

      <Card>
        <p className="px-5 py-3.5 text-[0.88rem] leading-6 text-[var(--muted)]">
          {current.blurb}
        </p>
      </Card>

      {section === "A" && (
        <>
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
                    <p className="mt-1 text-[0.82rem] leading-5 text-[var(--muted)]">
                      {c.hint}
                    </p>
                    {/* The exception belongs against the rule it excepts. */}
                    {c.key === "outlier_z" && (
                      <MccOverrides
                        overrides={draft.mcc_overrides ?? {}}
                        onChange={(next) => set("mcc_overrides", next)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </Card>

        </>
      )}

      {section !== "A" && (
        <Card
          title={`${current.label} — ${instancesFor(section).length} configured`}
          subtitle="A disabled rule still appears on a case, reported as not having matched"
        >
          <div className="space-y-3 p-5">
            {instancesFor(section).map((instance) => {
              const template = rules.templates.find((t) => t.key === instance.template)!;
              const index = ruleDraft.indexOf(instance);
              return (
                <RuleCard
                  key={instance.instance_id}
                  instance={instance}
                  template={template}
                  onChange={(next) => updateRule(index, next)}
                  onRemove={
                    instance.custom
                      ? () =>
                          setRuleDraft(
                            ruleDraft.filter(
                              (i) => i.instance_id !== instance.instance_id
                            )
                          )
                      : null
                  }
                />
              );
            })}

            <div className="flex flex-wrap items-end gap-2 border-t border-[var(--border)] pt-4">
              <label>
                <span className="text-[0.78rem] font-medium text-[var(--text-strong)]">
                  Add your own rule
                </span>
                <select
                  value={adding}
                  onChange={(e) => setAdding(e.target.value)}
                  className="focus-ring mt-1 block rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-2 text-[0.88rem]"
                >
                  <option value="">Choose a pattern to base it on…</option>
                  {templatesFor(section).map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={!adding}
                onClick={() => addRule(adding)}
                className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-4 py-2 text-[0.88rem] font-medium text-[var(--text-strong)] transition hover:bg-[var(--blue-50)] disabled:opacity-45"
              >
                Add rule
              </button>
              <p className="w-full text-[0.82rem] leading-6 text-[var(--muted)]">
                A rule you add is a second copy of a pattern with your own
                parameters and scope — run structuring loosely across the portfolio
                and a stricter copy on the trades that warrant it. Rules are chosen
                from these patterns rather than written as free text, so every alert
                can still name the test that raised it and an auditor can review the
                logic without reading code.
              </p>
            </div>
          </div>
        </Card>
      )}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={!changed}
          className="focus-ring rounded-[var(--radius)] bg-[var(--navy-800)] px-4 py-2.5 text-[0.9rem] font-medium text-white transition hover:bg-[var(--navy-700)] disabled:cursor-not-allowed disabled:opacity-45"
        >
          {section === "A" ? "Save thresholds" : "Save rules"}
        </button>
        <button
          type="button"
          onClick={() =>
            section === "A" ? setDraft(data.defaults) : setRuleDraft(rules.instances)
          }
          className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-4 py-2.5 text-[0.9rem] font-medium text-[var(--text-strong)] transition hover:bg-[var(--blue-50)]"
        >
          {section === "A" ? "Reset to defaults" : "Discard changes"}
        </button>
        {saved && <span className="text-[0.88rem] text-[var(--success)]">Saved.</span>}
        {changed && !saved && (
          <span className="text-[0.88rem] text-[var(--muted)]">Unsaved changes.</span>
        )}
      </div>
    </div>
  );
}
