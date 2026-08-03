import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  apiGet,
  type AlertOut,
  type Diagnostics,
  type Ledger,
} from "../api/client";
import { useGlossary } from "../api/glossary";
import { HourDensityPlot, PeerBoxPlot } from "../lib/charts";
import { Card, ErrorNote, Loading, Pill, type Tone } from "../lib/ui";
import { DecisionPanel } from "./DecisionPanel";
import { cn } from "../lib/utils";

const ALERT_TYPE_TONE: Record<string, Tone> = {
  single_txn_spike: "danger",
  mcc_peer_discrepancy: "blue",
  subdistrict_anomaly: "warning",
  temporal_anomaly: "navy",
};

function num(v: number | string | null | undefined, digits = 2) {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** Everything identifying the merchant, in one band, so an analyst never has
 *  to leave the case to know who they are looking at. */
function MerchantHeader({ alert, g }: { alert: AlertOut; g: ReturnType<typeof useGlossary> }) {
  const location = [alert.merchant_subdistrict, alert.merchant_district]
    .filter(Boolean)
    .join(" · ");

  const fields: [string, string][] = [
    // Merchant name is hashed in the source, so the field is shown as
    // withheld rather than omitted — the analyst should know it exists.
    ["Merchant name", "Withheld (hashed at source)"],
    ["Merchant ID", alert.merchant_id],
    [
      "MCC",
      alert.mcc
        ? `${alert.mcc}${alert.mcc_description ? ` — ${alert.mcc_description}` : ""}`
        : "—",
    ],
    ["Location", location || "—"],
    ["Group", `${g.lane(alert.lane)}`],
    ["Scored date", alert.scored_date ?? "—"],
    ["Business nature", alert.business_nature ?? "—"],
    ["Merchant status", alert.merchant_status ?? "—"],
  ];

  return (
    <Card>
      <div className="grid gap-x-8 gap-y-3 p-5 sm:grid-cols-2 xl:grid-cols-4">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[0.72rem] uppercase tracking-[0.12em] text-[var(--muted)]">
              {label}
            </dt>
            <dd className="mt-0.5 text-[0.94rem] font-medium text-[var(--text-strong)]">
              {value}
            </dd>
          </div>
        ))}
      </div>
    </Card>
  );
}

type TabKey = "why" | "ledger" | "stats";

/** Which indicators each piece of evidence actually speaks to.
 *
 *  The three views are not interchangeable: the amount table addresses the
 *  amount comparisons, the box plot addresses only the merchant-vs-peer
 *  level question, and the hours curve addresses only timing. Showing all
 *  three for every alert implies each is evidence for the alert at hand,
 *  which is how a KDE plot ends up next to an amount anomaly. */
const AMOUNT_INDICATORS = [
  "amount_vs_own_baseline",
  "amount_vs_payment_method_baseline",
  "ticket_vs_mcc_peers",
  "ticket_vs_subdistrict_peers",
  "merchant_level_vs_mcc_peers",
];
const PEER_LEVEL_INDICATORS = ["merchant_level_vs_mcc_peers", "ticket_vs_mcc_peers"];
const TIMING_INDICATORS = ["hour_vs_own_pattern", "hour_vs_mcc_peers"];

export default function CaseReview() {
  const g = useGlossary();
  const { id } = useParams();
  const [alert, setAlert] = useState<AlertOut | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [tab, setTab] = useState<TabKey>("why");
  const [error, setError] = useState(false);
  // Which indicator the analyst is looking at. Defaults to the one that
  // raised the alert they clicked — landing on all twelve is what made the
  // page unreadable. "all" widens it to everything that fired for the merchant.
  const [focus, setFocus] = useState<string>("firing");
  const [decided, setDecided] = useState(false);

  useEffect(() => {
    if (!id) return;
    setError(false);
    apiGet<AlertOut>(`/api/alerts/${id}`).then(setAlert).catch(() => setError(true));
    apiGet<Diagnostics>(`/api/alerts/${id}/diagnostics`).then(setDiag).catch(() => undefined);
  }, [id]);

  useEffect(() => {
    if (!alert?.scored_date) return;
    apiGet<Ledger>(
      `/api/merchants/${encodeURIComponent(alert.merchant_id)}/transactions?date=${alert.scored_date}`
    )
      .then(setLedger)
      .catch(() => undefined);
  }, [alert?.merchant_id, alert?.scored_date]);

  if (error) return <ErrorNote>Could not load this case.</ErrorNote>;
  if (!alert) return <Loading />;

  const fired = diag?.detectors.filter((d) => d.status === "FAIL") ?? [];
  const passed = diag?.detectors.filter((d) => d.status === "OK") ?? [];
  const skipped = diag?.detectors.filter((d) => d.status === "SKIP") ?? [];

  // The detector that raised THIS alert, as opposed to everything that
  // happened to fire for the merchant that night.
  const firingDetector = alert.triggering_detectors[0]?.detector ?? "";

  // The headline must describe the detector named directly above it. The
  // API's root_cause is a merchant-level summary — it reports the first
  // failing check of the night, which is often a different one — so using it
  // here put a subtitle about the own-history baseline under a heading about
  // the subdistrict baseline.
  const firingVerdict = diag?.detectors.find((d) => d.detector === firingDetector);

  const focusOptions: [string, string][] = [
    ["firing", `This alert — ${g.detector(firingDetector)}`],
    ["all", `All indicators that fired (${fired.length})`],
    ...fired
      .filter((d) => d.detector !== firingDetector)
      .map((d) => [d.detector, d.label] as [string, string]),
  ];

  const inFocus = (detector: string) =>
    focus === "all" ? true : focus === "firing" ? detector === firingDetector : detector === focus;

  const visibleDetectors =
    focus === "all"
      ? diag?.detectors ?? []
      : (diag?.detectors ?? []).filter((d) => inFocus(d.detector));

  const TABS: [TabKey, string, string][] = [
    ["why", "Why it fired", "The checks that ran, and what each concluded."],
    [
      "ledger",
      `Transactions${ledger ? ` (${ledger.count})` : ""}`,
      "Every transaction on the day this alert evaluated.",
    ],
    [
      "stats",
      "Statistical proof",
      "The numbers and distributions the decision was based on.",
    ],
  ];

  return (
    <div className="space-y-4">
      <MerchantHeader alert={alert} g={g} />

      {/* The headline: what fired, in one sentence, before any table. */}
      <Card>
        <div className="flex flex-col gap-4 p-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              {alert.alert_type && (
                <Pill tone={ALERT_TYPE_TONE[alert.alert_type] ?? "neutral"}>
                  {g.alertType(alert.alert_type)}
                </Pill>
              )}
              <span className="text-[0.85rem] text-[var(--muted)]">
                rank {alert.rank} · score {alert.blended_score.toFixed(2)}
              </span>
            </div>
            <h2 className="mt-2 text-[1.35rem]">
              {g.detector(alert.triggering_detectors[0]?.detector ?? "")}
            </h2>
            {firingVerdict && (
              <p className="mt-1.5 max-w-3xl text-[0.94rem] leading-6 text-[var(--text)]">
                {firingVerdict.message}
                {firingVerdict.deviation != null && (
                  <span className="text-[var(--muted)]">
                    {" "}
                    · {firingVerdict.deviation.toFixed(1)}σ from{" "}
                    {firingVerdict.compared_against?.toLowerCase() ?? "baseline"}
                  </span>
                )}
              </p>
            )}
          </div>
          {diag && (
            <div className="flex shrink-0 gap-4 text-center">
              {[
                ["Fired", fired.length],
                ["Passed", passed.length],
                ["Skipped", skipped.length],
              ].map(([label, count]) => (
                <div key={label as string}>
                  <p className="metric-number text-[1.35rem] font-semibold text-[var(--text-strong)]">
                    {count as number}
                  </p>
                  <p className="text-[0.78rem] text-[var(--muted)]">{label as string}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </Card>

      {decided ? (
        <Card>
          <p className="px-5 py-4 text-[0.92rem] text-[var(--success)]">
            Decision recorded. This alert has left the review queue.
          </p>
        </Card>
      ) : (
        <DecisionPanel alert={alert} onDecided={() => setDecided(true)} />
      )}

      {/* One selector drives all three tabs, so switching tabs keeps the
          analyst looking at the same indicator rather than resetting. */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[0.82rem] font-medium text-[var(--muted)]" htmlFor="focus">
          Showing
        </label>
        <select
          id="focus"
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
          title="Narrow every tab to one indicator, or widen to everything that fired for this merchant."
          className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.85rem] font-medium text-[var(--text-strong)]"
        >
          {focusOptions.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        {focus === "firing" && fired.length > 1 && (
          <span className="text-[0.82rem] text-[var(--muted)]">
            {fired.length - 1} other indicator{fired.length === 2 ? "" : "s"} also fired for
            this merchant — switch to see them.
          </span>
        )}
      </div>

      <div className="flex gap-1 border-b border-[var(--border)]" role="tablist">
        {TABS.map(([key, label, hint]) => (
          <button
            key={key}
            type="button"
            role="tab"
            title={hint}
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={cn(
              "focus-ring -mb-px rounded-t-[var(--radius)] px-4 py-2.5 text-[0.9rem] font-medium transition-colors",
              tab === key
                ? "border-b-2 border-[var(--blue-600)] text-[var(--navy-800)]"
                : "text-[var(--muted)] hover:text-[var(--text-strong)]"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "why" && (
        <Card
          title={
            focus === "all"
              ? "Every check that ran"
              : focus === "firing"
                ? "The indicator that raised this alert"
                : "Selected indicator"
          }
          subtitle={
            focus === "all"
              ? "Passes matter as much as failures — they show what was ruled out"
              : "Widen the selector above to see the other checks"
          }
        >
          {!diag ? (
            <p className="px-5 py-8 text-center text-[0.9rem] text-[var(--muted)]">
              Loading diagnostics…
            </p>
          ) : (
            <table className="w-full text-left text-[0.88rem]">
              <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
                <tr>
                  <th className="px-5 py-3 font-semibold">Check</th>
                  <th className="px-5 py-3 font-semibold">Compared against</th>
                  <th className="px-5 py-3 text-right font-semibold">Merchant</th>
                  <th className="px-5 py-3 text-right font-semibold">Baseline</th>
                  <th className="px-5 py-3 text-right font-semibold">Deviation</th>
                  <th className="px-5 py-3 font-semibold">Result</th>
                </tr>
              </thead>
              <tbody>
                {visibleDetectors.map((d) => (
                  <tr
                    key={d.detector}
                    className={cn(
                      "border-b border-[var(--border)] last:border-b-0 align-top",
                      d.status === "FAIL" && "bg-[var(--danger-bg)]/40"
                    )}
                  >
                    <td className="px-5 py-3">
                      <p className="font-medium text-[var(--text-strong)]">
                        {d.label}
                        {d.detector === firingDetector && (
                          <span className="ml-2 rounded-full bg-[var(--danger-bg)] px-2 py-0.5 text-[0.7rem] font-semibold text-[var(--danger)]">
                            raised this alert
                          </span>
                        )}
                      </p>
                      <p className="mt-0.5 text-[0.82rem] leading-5 text-[var(--muted)]">
                        {d.message}
                      </p>
                    </td>
                    <td className="px-5 py-3 text-[var(--muted)]">{d.compared_against}</td>
                    <td className="px-5 py-3 text-right metric-number">{num(d.merchant_value)}</td>
                    <td className="px-5 py-3 text-right metric-number text-[var(--muted)]">
                      {num(d.baseline_value)}
                    </td>
                    <td className="px-5 py-3 text-right metric-number">
                      {d.deviation == null ? "—" : d.deviation.toFixed(1)}
                    </td>
                    <td className="px-5 py-3">
                      {d.status === "FAIL" && <Pill tone="danger">Fired</Pill>}
                      {d.status === "OK" && <Pill tone="success">Passed</Pill>}
                      {d.status === "SKIP" && <Pill tone="neutral">Not applicable</Pill>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {tab === "ledger" && (
        <Card
          title={`Transactions on ${alert.scored_date}`}
          subtitle={
            ledger
              ? `${ledger.count} transactions · ${num(ledger.total_amount, 0)} total · ${
                  ledger.outlier_count
                } marked as contributing`
              : "The day this alert evaluated"
          }
        >
          {!ledger ? (
            <p className="px-5 py-8 text-center text-[0.9rem] text-[var(--muted)]">Loading…</p>
          ) : ledger.count === 0 ? (
            <p className="px-5 py-8 text-center text-[0.9rem] text-[var(--muted)]">
              No transactions on this day. This alert is a merchant-level discrepancy, judged
              against the rolling baseline rather than a single day's activity.
            </p>
          ) : (
            <>
            <p className="border-b border-[var(--border)] px-5 py-3 text-[0.86rem] leading-6 text-[var(--muted)]">
              Highlighted rows exceeded this merchant&rsquo;s own amount threshold —
              the transactions that drove{" "}
              {focus === "all" ? "the amount indicators" : g.detector(focus === "firing" ? firingDetector : focus)}.
              Indicators judged on count, timing or card origin have no single driving
              transaction; the whole day is the evidence.
            </p>
            <table className="w-full text-left text-[0.86rem]">
              <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
                <tr>
                  <th className="px-5 py-3 font-semibold">Time</th>
                  <th className="px-5 py-3 text-right font-semibold">Amount</th>
                  <th className="px-5 py-3 font-semibold">Card</th>
                  <th className="px-5 py-3 font-semibold">Issued in</th>
                  <th className="px-5 py-3 font-semibold">Status</th>
                  <th className="px-5 py-3 font-semibold">Transaction ID</th>
                </tr>
              </thead>
              <tbody>
                {ledger.transactions.map((t) => (
                  <tr
                    key={t.source_txn_id}
                    className={cn(
                      "border-b border-[var(--border)] last:border-b-0",
                      t.is_outlier && "bg-[var(--danger-bg)]/50"
                    )}
                  >
                    <td className="px-5 py-2.5 metric-number">
                      {new Date(t.occurred_at).toLocaleTimeString("en-GB", {
                        hour: "2-digit",
                        minute: "2-digit",
                        timeZone: "Asia/Hong_Kong",
                      })}
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      <span
                        className={cn(
                          "metric-number",
                          t.is_outlier && "font-semibold text-[var(--danger)]"
                        )}
                      >
                        {num(t.total_amount)}
                      </span>
                      {t.is_refund && (
                        <span className="ml-2 text-[0.72rem] text-[var(--muted)]">refund</span>
                      )}
                      {t.is_outlier && (
                        <span className="ml-2 text-[0.72rem] font-semibold text-[var(--danger)]">
                          outlier
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-2.5 text-[var(--muted)]">{t.card_type ?? "—"}</td>
                    <td className="px-5 py-2.5">{t.card_issuing_country ?? "—"}</td>
                    <td className="px-5 py-2.5 text-[var(--muted)]">
                      {t.transaction_status ?? "—"}
                    </td>
                    <td className="px-5 py-2.5">
                      <code className="text-[0.76rem] text-[var(--muted)]">{t.source_txn_id}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </Card>
      )}

      {tab === "stats" && diag && (
        <div className="space-y-4">
          {(() => {
            const shows = (group: string[]) =>
              focus === "all"
                ? true
                : group.includes(focus === "firing" ? firingDetector : focus);
            const anything =
              shows(AMOUNT_INDICATORS) || shows(PEER_LEVEL_INDICATORS) || shows(TIMING_INDICATORS);

            return (
              <>
                {!anything && (
                  <Card>
                    <p className="px-5 py-6 text-[0.92rem] text-[var(--muted)]">
                      No distribution evidence applies to this indicator. Counts and
                      velocity are judged directly against the merchant&rsquo;s own
                      history — see the &ldquo;Why it fired&rdquo; tab for the figures.
                      Widen the selector above to see all evidence for this merchant.
                    </p>
                  </Card>
                )}

                {shows(AMOUNT_INDICATORS) && (
                  <Card
                    title="Amount comparison"
                    subtitle="Evidence for the amount indicators — not for timing or volume"
                  >
                    <p className="border-b border-[var(--border)] px-5 py-3 text-[0.88rem] leading-6 text-[var(--muted)]">
                      This table addresses whether transaction <em>amounts</em> were
                      unusual. Baseline: {diag.window.window_days ?? "—"}-day window
                      ending {diag.window.window_end?.slice(0, 10) ?? "—"}, lagged{" "}
                      {diag.window.lag_days ?? "—"} days.
                    </p>
                    <table className="w-full text-left text-[0.88rem]">
                      <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
                        <tr>
                          <th className="px-5 py-3 font-semibold">Group</th>
                          <th className="px-5 py-3 text-right font-semibold">Mean</th>
                          <th className="px-5 py-3 text-right font-semibold">Median</th>
                          <th className="px-5 py-3 text-right font-semibold">MAD</th>
                          <th className="px-5 py-3 text-right font-semibold">N</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[
                          ["This merchant", diag.statistics.merchant],
                          [`MCC ${alert.mcc ?? ""} peers`, diag.statistics.peer_mcc],
                          [
                            `${alert.merchant_subdistrict ?? "District"} peers`,
                            diag.statistics.peer_subdistrict,
                          ],
                        ].map(([label, s]) => {
                          const stat = s as typeof diag.statistics.merchant;
                          return (
                            <tr
                              key={label as string}
                              className="border-b border-[var(--border)] last:border-b-0"
                            >
                              <td className="px-5 py-3 font-medium text-[var(--text-strong)]">
                                {label as string}
                              </td>
                              <td className="px-5 py-3 text-right metric-number">
                                {num(stat.mean)}
                              </td>
                              <td className="px-5 py-3 text-right metric-number">
                                {num(stat.median)}
                              </td>
                              <td className="px-5 py-3 text-right metric-number">
                                {num(stat.mad)}
                              </td>
                              <td className="px-5 py-3 text-right metric-number">{stat.n}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-[var(--border)] px-5 py-3 text-[0.88rem]">
                      <span className="text-[var(--muted)]">
                        Modified z-score{" "}
                        <span className="metric-number font-semibold text-[var(--text-strong)]">
                          {diag.statistics.modified_z == null
                            ? "—"
                            : diag.statistics.modified_z.toFixed(1)}
                        </span>
                      </span>
                      <span className="text-[0.82rem] text-[var(--muted)]">
                        Mean sits beside median so the skew is visible — the detectors use
                        the median because a few large transactions drag a mean.
                      </span>
                    </div>
                  </Card>
                )}

                {shows(PEER_LEVEL_INDICATORS) && (
                  <Card
                    title="Position within the MCC peer distribution"
                    subtitle="Evidence for the merchant-level peer comparison only"
                  >
                    <p className="border-b border-[var(--border)] px-5 py-3 text-[0.88rem] leading-6 text-[var(--muted)]">
                      This plot addresses one question: is this merchant&rsquo;s typical
                      transaction amount out of line with others sharing its MCC? It says
                      nothing about timing, volume, or any single transaction.
                    </p>
                    <PeerBoxPlot
                      label="Typical transaction amount against MCC peers"
                      merchantValue={diag.peer_distribution.merchant_value}
                      peerMedian={diag.peer_distribution.peer_median}
                      peerQ1={diag.peer_distribution.peer_q1}
                      peerQ3={diag.peer_distribution.peer_q3}
                      peerFence={diag.peer_distribution.peer_upper_fence}
                      peerValues={diag.peer_distribution.peer_values}
                    />
                  </Card>
                )}

                {shows(TIMING_INDICATORS) && (
                  <Card
                    title="Trading hours"
                    subtitle="Evidence for the timing indicators only"
                  >
                    <p className="border-b border-[var(--border)] px-5 py-3 text-[0.88rem] leading-6 text-[var(--muted)]">
                      This plot addresses <em>when</em> the merchant trades, against its own
                      history and its MCC peers. It says nothing about amounts. Marks along
                      the axis are the scored day&rsquo;s transactions.
                    </p>
                    <HourDensityPlot
                      merchant={diag.hour_density.merchant}
                      cohort={diag.hour_density.cohort}
                      threshold={diag.hour_density.threshold}
                      scoredHours={diag.hour_density.scored_day_hours}
                    />
                  </Card>
                )}
              </>
            );
          })()}
        </div>
      )}

    </div>
  );
}
