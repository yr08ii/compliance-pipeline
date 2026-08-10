import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  apiGet,
  type AlertOut,
  type CardLink,
  type Diagnostics,
  type DetectorVerdict,
  type Ledger,
  type LinkedTransactions,
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

type TabKey = "why" | "ledger" | "linked" | "stats";

/** Everything identifying the *card* this case is about.
 *
 *  A card-linkage finding has no single merchant as its subject. One card at
 *  ten merchants is one investigation, and heading its page with one of those
 *  ten — the merchant the alert happened to be attributed to — asks the
 *  analyst to work out that the other nine exist. So for these alerts the
 *  merchant band is replaced by this: the card, everywhere it went, when, and
 *  for how much. The attributed merchant is still marked, because the alert
 *  does sit in somebody's queue, but it is one row among the others rather
 *  than the frame of the whole page. */
function CardHeader({ link }: { link: CardLink }) {
  const span =
    link.first_seen && link.last_seen
      ? `${hhmm(link.first_seen)} – ${hhmm(link.last_seen)}`
      : "—";
  const places = [
    ...new Set(link.trail.map((m) => m.subdistrict).filter(Boolean)),
  ];

  const facts: [string, string][] = [
    // The card is the subject, and the one thing that cannot be shown. Naming
    // it as withheld is more honest than leaving the subject blank.
    ["Card", `${link.card_ref} · identifier withheld`],
    ["Merchants touched", String(link.trail.length)],
    ["Districts", places.length ? places.join(" · ") : "—"],
    ["Window", span],
    ["Transactions", String(link.transactions.length)],
    ["Total value", num(link.total_amount, 0)],
    ["Rails", link.rails.length ? link.rails.join(", ") : "—"],
    [
      "Common ownership",
      link.related ? "Yes — branches of one chain" : "No — unrelated merchants",
    ],
  ];

  return (
    <Card>
      <div className="grid gap-x-8 gap-y-3 p-5 sm:grid-cols-2 xl:grid-cols-4">
        {facts.map(([label, value]) => (
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

      {/* The merchants themselves, which are the substance of the case. */}
      <div className="overflow-x-auto border-t border-[var(--border)]">
        <table className="w-full text-left text-[0.86rem]">
          <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
            <tr>
              <th className="px-5 py-3 font-semibold">Merchant</th>
              <th className="px-5 py-3 font-semibold">Trade</th>
              <th className="px-5 py-3 font-semibold">Where</th>
              <th className="px-5 py-3 font-semibold">Seen</th>
              <th className="px-5 py-3 text-right font-semibold">Txns</th>
              <th className="px-5 py-3 text-right font-semibold">Value</th>
            </tr>
          </thead>
          <tbody>
            {link.trail.map((m) => (
              <tr
                key={m.merchant_id}
                className="border-b border-[var(--border)] last:border-b-0"
              >
                <td className="px-5 py-2.5">
                  <span className="font-medium text-[var(--text-strong)]">
                    {m.merchant_id}
                  </span>
                  {m.is_alert_merchant && (
                    <span
                      className="ml-2 rounded-full bg-[var(--blue-50)] px-2 py-0.5 text-[0.68rem] font-semibold text-[var(--blue-600)]"
                      title="The alert sits in this merchant's queue. The case is about the card, not about them alone."
                    >
                      alert filed here
                    </span>
                  )}
                  {m.owner_group && (
                    <span
                      className="ml-2 rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-[0.68rem] font-medium text-[var(--muted)]"
                      title="Shares a registration, address or trading name with another merchant on this trail"
                    >
                      {m.owner_group}
                    </span>
                  )}
                </td>
                <td className="px-5 py-2.5 text-[var(--muted)]">
                  {m.mcc ? `${m.mcc}${m.mcc_description ? ` — ${m.mcc_description}` : ""}` : "—"}
                </td>
                <td className="px-5 py-2.5 text-[var(--muted)]">
                  {[m.subdistrict, m.district].filter(Boolean).join(" · ") || "—"}
                </td>
                <td className="px-5 py-2.5 metric-number text-[var(--muted)]">
                  {hhmm(m.first_seen)}
                  {m.last_seen !== m.first_seen && ` – ${hhmm(m.last_seen)}`}
                </td>
                <td className="px-5 py-2.5 text-right metric-number">
                  {m.transactions}
                </td>
                <td className="px-5 py-2.5 text-right metric-number">
                  {num(m.total_amount, 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Hong_Kong",
  });

/** One card's transactions across every merchant it touched.
 *
 *  The merchant's own day ledger cannot carry this. A card-linkage finding
 *  claims "the same card was at these merchants at these times", and half of
 *  that happened at somebody else's shop — so an analyst either took the
 *  verdict on trust or opened each counterparty and lined the timestamps up by
 *  hand. The gap column is the claim: it is what makes two rows minutes apart
 *  in different districts a finding rather than a coincidence. */
function CardTrail({ link }: { link: CardLink }) {
  const focusCount = link.transactions.filter((t) => t.is_focus).length;

  return (
    <Card
      title={`${link.label} — ${link.card_ref}`}
      subtitle={`The card's whole day: ${link.transactions.length} transactions across ${link.trail.length} merchants, ${focusCount} of them the finding`}
    >
      <p className="border-b border-[var(--border)] px-5 py-3 text-[0.86rem] leading-6 text-[var(--muted)]">
        {link.related ? (
          <>
            These merchants{" "}
            <strong className="text-[var(--text-strong)]">
              share a registration, address or trading name
            </strong>
            , so they are branches of one chain rather than separate shops —
            which is what makes one card crossing them in a day worth asking
            about.
          </>
        ) : (
          <>
            These merchants share no registration, address or trading name, so
            common ownership does not explain the card being at all of them.
          </>
        )}{" "}
        The card itself is not shown: a 1:1 PAN hash is reversible, so it stays
        in the detection layer. {link.card_ref} is a label for this run only.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-[0.86rem]">
          <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
            <tr>
              <th className="px-5 py-3 font-semibold">Time</th>
              <th className="px-5 py-3 text-right font-semibold">Gap</th>
              <th className="px-5 py-3 font-semibold">Merchant</th>
              <th className="px-5 py-3 font-semibold">Where</th>
              <th className="px-5 py-3 text-right font-semibold">Travel</th>
              <th className="px-5 py-3 text-right font-semibold">Amount</th>
              <th className="px-5 py-3 font-semibold">Card</th>
              <th className="px-5 py-3 font-semibold">Transaction ID</th>
            </tr>
          </thead>
          <tbody>
            {link.transactions.map((t) => {
              // The rule fired on this row, as against it being the rest of
              // the card's day shown around it.
              const mark = t.is_focus
                ? "bg-[var(--danger-bg)] font-semibold text-[var(--danger)] ring-1 ring-[var(--danger)]/30"
                : "";
              return (
                <tr
                  key={t.source_txn_id}
                  className={cn(
                    "border-b border-[var(--border)] last:border-b-0",
                    t.is_focus
                      ? "bg-[var(--danger-bg)]/25"
                      : "text-[var(--muted)]"
                  )}
                >
                  <td className="px-5 py-2.5">
                    <span className={cn("metric-number rounded px-1", mark)}>
                      {hhmm(t.occurred_at)}
                    </span>
                  </td>
                  <td className="px-5 py-2.5 text-right">
                    <span
                      className={cn("metric-number rounded px-1", mark)}
                      title={
                        t.is_focus
                          ? "Part of what the rule fired on"
                          : undefined
                      }
                    >
                      {t.minutes_since_previous == null
                        ? "—"
                        : `${num(t.minutes_since_previous, 0)} min`}
                    </span>
                  </td>
                  <td className="px-5 py-2.5">
                    <span className="font-medium text-[var(--text-strong)]">
                      {t.merchant_id}
                    </span>
                    {t.is_alert_merchant && (
                      <span className="ml-2 rounded-full bg-[var(--blue-50)] px-2 py-0.5 text-[0.68rem] font-semibold text-[var(--blue-600)]">
                        this case
                      </span>
                    )}
                    {t.owner_group && (
                      <span
                        className="ml-2 rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-[0.68rem] font-medium text-[var(--muted)]"
                        title="Shares a registration, address or trading name with another merchant in this list"
                      >
                        {t.owner_group}
                      </span>
                    )}
                    {t.mcc && (
                      <p className="mt-0.5 text-[0.78rem] text-[var(--muted)]">
                        MCC {t.mcc}
                        {t.mcc_description ? ` — ${t.mcc_description}` : ""}
                      </p>
                    )}
                  </td>
                  <td className="px-5 py-2.5 text-[var(--muted)]">
                    {[t.merchant_subdistrict, t.merchant_district]
                      .filter(Boolean)
                      .join(" · ") || "—"}
                  </td>
                  {/* The speed sits on the row it condemns, not only in a
                      separate table further down the page. */}
                  <td className="px-5 py-2.5 text-right">
                    {t.arrived_at_kmh == null ? (
                      <span className="text-[var(--muted)]">—</span>
                    ) : (
                      <span
                        className="metric-number rounded bg-[var(--danger-bg)] px-1 font-semibold text-[var(--danger)] ring-1 ring-[var(--danger)]/30"
                        title={`${num(t.arrived_from_km, 1)} km from the previous merchant in ${num(
                          t.minutes_since_previous,
                          0
                        )} minutes`}
                      >
                        {num(t.arrived_at_kmh, 0)} km/h
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-2.5 text-right metric-number">
                    {num(t.total_amount)}
                  </td>
                  <td className="px-5 py-2.5 text-[var(--muted)]">
                    {t.card_type ?? "—"}
                  </td>
                  <td className="px-5 py-2.5">
                    <code className="text-[0.76rem] text-[var(--muted)]">
                      {t.source_txn_id}
                    </code>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** The arithmetic behind an impossible-travel verdict.
 *
 *  Being told a journey was impossible is not evidence. The distance, the
 *  elapsed time, the speed they imply and the limit being compared against
 *  are — and every one of them is a number the analyst can check, which is the
 *  difference between a finding they can put in a report and one they cannot. */
function JourneyTable({ legs }: { legs: CardLink["legs"] }) {
  return (
    <Card
      title="Distance, time and implied speed"
      subtitle="Evidence for the impossible-travel indicator only"
    >
      <p className="border-b border-[var(--border)] px-5 py-3 text-[0.88rem] leading-6 text-[var(--muted)]">
        Distance is measured between the two subdistrict centroids, so it
        ignores terrain, harbour crossings and road routing. That makes it a{" "}
        <em>lower bound</em> on the real journey, and therefore a lower bound on
        the implied speed — the test under-flags rather than over-flags by
        construction.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-[0.88rem]">
          <thead className="border-b border-[var(--border)] text-[0.7rem] uppercase tracking-[0.12em] text-[var(--muted)]">
            <tr>
              <th className="px-5 py-3 font-semibold">From</th>
              <th className="px-5 py-3 font-semibold">To</th>
              <th className="px-5 py-3 text-right font-semibold">Distance</th>
              <th className="px-5 py-3 text-right font-semibold">Time apart</th>
              <th className="px-5 py-3 text-right font-semibold">Implied speed</th>
              <th className="px-5 py-3 text-right font-semibold">Limit</th>
              <th className="px-5 py-3 text-right font-semibold">Over by</th>
            </tr>
          </thead>
          <tbody>
            {legs.map((leg) => (
              <tr
                key={`${leg.from_txn_id}-${leg.to_txn_id}`}
                className="border-b border-[var(--border)] last:border-b-0 align-top"
              >
                <td className="px-5 py-3">
                  <p className="font-medium text-[var(--text-strong)]">
                    {leg.from_place}
                  </p>
                  <p className="mt-0.5 text-[0.8rem] text-[var(--muted)]">
                    {leg.from_merchant} · {hhmm(leg.from_time)}
                  </p>
                </td>
                <td className="px-5 py-3">
                  <p className="font-medium text-[var(--text-strong)]">
                    {leg.to_place}
                  </p>
                  <p className="mt-0.5 text-[0.8rem] text-[var(--muted)]">
                    {leg.to_merchant} · {hhmm(leg.to_time)}
                  </p>
                </td>
                <td className="px-5 py-3 text-right metric-number">
                  {num(leg.distance_km, 1)} km
                </td>
                <td className="px-5 py-3 text-right metric-number">
                  {num(leg.minutes, 0)} min
                </td>
                <td className="px-5 py-3 text-right metric-number font-semibold text-[var(--danger)]">
                  {num(leg.kmh, 0)} km/h
                </td>
                <td className="px-5 py-3 text-right metric-number text-[var(--muted)]">
                  {num(leg.limit_kmh, 0)} km/h
                </td>
                <td className="px-5 py-3 text-right metric-number font-semibold">
                  {num(leg.over_limit_multiple, 1)}×
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t border-[var(--border)] px-5 py-3 text-[0.84rem] leading-6 text-[var(--muted)]">
        Speed is distance ÷ time. A journey above the limit means the card could
        not have been physically present at both merchants, so at least one of
        the two acceptances did not happen as recorded — which is a merchant
        acceptance question, not a verdict about the cardholder.
      </p>
    </Card>
  );
}

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
const GEO_INDICATORS = ["impossible_geo_velocity"];

/** Which ledger column each source field maps to, so the cell that carries
 *  the cause can be marked rather than the whole row. A high-amount alert and
 *  an unusual-origin alert can implicate the same transaction for completely
 *  different reasons, and the row alone cannot say which. */
const FIELD_COLUMN: Record<string, string> = {
  total_amount: "amount",
  net_amount: "amount",
  occurred_at: "time",
  card_issuing_country: "country",
  card_type: "card",
  transaction_status: "status",
  merchant_id: "id",
};

const FAMILY_LABEL: Record<string, string> = {
  A: "Baseline",
  B: "Typology rule",
  C: "Ring signal",
};

/** The cause attached to one transaction by the indicator in focus. */
type Cause = { field: string; column: string; reason: string };

/** Index the diagnostics' contributions by transaction, for the indicators
 *  currently in focus. The ledger then colours a row only when the indicator
 *  the analyst is actually looking at implicated it — highlighting every row
 *  every detector ever touched would be no highlighting at all. */
function causesByTxn(
  detectors: DetectorVerdict[],
  inFocus: (detector: string) => boolean
): Map<string, Cause[]> {
  const out = new Map<string, Cause[]>();
  for (const d of detectors) {
    if (d.status !== "FAIL" || !inFocus(d.detector)) continue;
    for (const c of d.contributions ?? []) {
      const existing = out.get(c.source_txn_id) ?? [];
      // One transaction can be implicated by several indicators at once; keep
      // each distinct reason so the tooltip explains all of them.
      if (!existing.some((e) => e.field === c.field && e.reason === c.reason)) {
        existing.push({
          field: c.field,
          column: FIELD_COLUMN[c.field] ?? "",
          reason: c.reason,
        });
      }
      out.set(c.source_txn_id, existing);
    }
  }
  return out;
}

export default function CaseReview() {
  const g = useGlossary();
  const { id } = useParams();
  const [alert, setAlert] = useState<AlertOut | null>(null);
  const [diag, setDiag] = useState<Diagnostics | null>(null);
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [linked, setLinked] = useState<LinkedTransactions | null>(null);
  // Null until the analyst picks one, so the default can depend on what kind
  // of case this turned out to be without an effect racing the fetch and
  // yanking the tab out from under a click.
  const [tab, setTab] = useState<TabKey | null>(null);
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
    // Empty for everything except ring alerts, which is most of the queue —
    // the tab it feeds only appears when there is something in it.
    apiGet<LinkedTransactions>(`/api/alerts/${id}/linked-transactions`)
      .then(setLinked)
      .catch(() => undefined);
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

  // The transactions the in-focus indicators actually named, and the column of
  // each that carries the cause.
  const implicated = causesByTxn(diag?.detectors ?? [], inFocus);

  // Card-linkage findings only. A ring alert's evidence lives across several
  // merchants, so it needs a view the single-merchant ledger cannot give.
  const links = linked?.links ?? [];
  const linkedCount = links.reduce((n, l) => n + l.transactions.length, 0);
  const legs = links.flatMap((l) => l.legs);

  const isCardCase = links.length > 0;

  const CARD_TRAIL_TAB: [TabKey, string, string] = [
    "linked",
    `Card trail (${linkedCount})`,
    "The same card's transactions at every merchant it touched.",
  ];
  const WHY_TAB: [TabKey, string, string] = [
    "why",
    "Why it fired",
    "The checks that ran, and what each concluded.",
  ];
  const STATS_TAB: [TabKey, string, string] = [
    "stats",
    "Statistical proof",
    "The numbers and distributions the decision was based on.",
  ];
  // On a card case this is context, not evidence — the finding is about a card
  // across merchants, and one merchant's day cannot contain it. Named so, and
  // placed last, so it does not read as the transaction list for the alert.
  const LEDGER_TAB: [TabKey, string, string] = isCardCase
    ? [
        "ledger",
        `This merchant's day${ledger ? ` (${ledger.count})` : ""}`,
        "Everything the merchant the alert was filed against did that day. Context, not the evidence.",
      ]
    : [
        "ledger",
        `Transactions${ledger ? ` (${ledger.count})` : ""}`,
        "Every transaction on the day this alert evaluated.",
      ];

  const TABS: [TabKey, string, string][] = isCardCase
    ? [CARD_TRAIL_TAB, WHY_TAB, STATS_TAB, LEDGER_TAB]
    : [WHY_TAB, LEDGER_TAB, STATS_TAB];

  // The card trail *is* the case when there is one, so it opens on it.
  const activeTab: TabKey = tab ?? (isCardCase ? "linked" : "why");

  // A card-linkage case is about the card. Heading it with one of the ten
  // merchants the card visited — the one the alert was filed against — buries
  // the subject and asks the analyst to discover the other nine.
  const cardCase = links.find((l) => l.detector === firingDetector) ?? null;

  return (
    <div className="space-y-4">
      {cardCase ? (
        <CardHeader link={cardCase} />
      ) : (
        <MerchantHeader alert={alert} g={g} />
      )}

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
            aria-selected={activeTab === key}
            onClick={() => setTab(key)}
            className={cn(
              "focus-ring -mb-px rounded-t-[var(--radius)] px-4 py-2.5 text-[0.9rem] font-medium transition-colors",
              activeTab === key
                ? "border-b-2 border-[var(--blue-600)] text-[var(--navy-800)]"
                : "text-[var(--muted)] hover:text-[var(--text-strong)]"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === "why" && (
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
                        {/* A typology match and a statistical outlier call for
                            different follow-up, so the family is named rather
                            than left for the analyst to infer. */}
                        <span className="ml-2 rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-[0.68rem] font-medium text-[var(--muted)]">
                          {FAMILY_LABEL[d.family] ?? d.family}
                        </span>
                      </p>
                      <p className="mt-0.5 text-[0.82rem] leading-5 text-[var(--muted)]">
                        {d.message}
                      </p>
                      {d.status === "FAIL" && (d.contributions?.length ?? 0) > 0 && (
                        <p className="mt-1 text-[0.78rem] text-[var(--muted)]">
                          {d.contributions.length} transaction
                          {d.contributions.length === 1 ? "" : "s"} implicated —
                          {/* A ring finding's transactions are spread across
                              merchants, so they are not in this merchant's day
                              ledger. Pointing there would send the analyst to a
                              table that cannot contain them. */}
                          {links.some((l) => l.detector === d.detector)
                            ? " see the Card trail tab."
                            : " see the Transactions tab."}
                        </p>
                      )}
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

      {activeTab === "ledger" && (
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
          {/* A ring finding's evidence is not on this page: it is the same
              card at other merchants. Say so here, where the analyst looks
              first, rather than letting them conclude the day looks fine. */}
          {links.length > 0 && (
            <p className="border-b border-[var(--border)] bg-[var(--blue-50)]/50 px-5 py-3 text-[0.86rem] leading-6 text-[var(--text)]">
              This alert is about a card, not about this day. The evidence is the
              same card&rsquo;s transactions at{" "}
              {links[0].merchants.length} merchants —{" "}
              <button
                type="button"
                onClick={() => setTab("linked")}
                className="focus-ring font-semibold text-[var(--blue-600)] underline underline-offset-2"
              >
                open the card trail
              </button>
              .
            </p>
          )}
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
              {implicated.size > 0 ? (
                <>
                  <strong className="text-[var(--text-strong)]">
                    {implicated.size} of {ledger.count} transactions
                  </strong>{" "}
                  contributed to{" "}
                  {focus === "all"
                    ? "the indicators that fired"
                    : g.detector(focus === "firing" ? firingDetector : focus)}
                  . The highlighted cell in each row is the value that implicated
                  it — hover it for the reason.
                </>
              ) : (
                <>
                  No individual transaction drove this indicator. It is judged on
                  the merchant rather than on any one checkout, so the whole day is
                  the evidence.
                </>
              )}
            </p>
            <div className="overflow-x-auto">
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
                {ledger.transactions.map((t) => {
                  const causes = implicated.get(t.source_txn_id) ?? [];
                  const marks = new Set(causes.map((c) => c.column));
                  const why = causes.map((c) => c.reason).join(" · ");
                  // One shared cell treatment, so "this is the reason" reads
                  // the same whichever column carries it.
                  const cell = (column: string) =>
                    marks.has(column)
                      ? "rounded bg-[var(--danger-bg)] font-semibold text-[var(--danger)] ring-1 ring-[var(--danger)]/30"
                      : "";
                  return (
                    <tr
                      key={t.source_txn_id}
                      title={why || undefined}
                      className={cn(
                        "border-b border-[var(--border)] last:border-b-0",
                        causes.length > 0 && "bg-[var(--danger-bg)]/30"
                      )}
                    >
                      <td className="px-5 py-2.5">
                        <span className={cn("metric-number px-1", cell("time"))}>
                          {new Date(t.occurred_at).toLocaleTimeString("en-GB", {
                            hour: "2-digit",
                            minute: "2-digit",
                            timeZone: "Asia/Hong_Kong",
                          })}
                        </span>
                      </td>
                      <td className="px-5 py-2.5 text-right">
                        <span className={cn("metric-number px-1", cell("amount"))}>
                          {num(t.total_amount)}
                        </span>
                        {t.is_refund && (
                          <span className="ml-2 text-[0.72rem] text-[var(--muted)]">
                            refund
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-2.5 text-[var(--muted)]">
                        <span className={cn("px-1", cell("card"))}>
                          {t.card_type ?? "—"}
                        </span>
                      </td>
                      <td className="px-5 py-2.5">
                        <span className={cn("px-1", cell("country"))}>
                          {t.card_issuing_country ?? "—"}
                        </span>
                      </td>
                      <td className="px-5 py-2.5 text-[var(--muted)]">
                        <span className={cn("px-1", cell("status"))}>
                          {t.transaction_status ?? "—"}
                        </span>
                      </td>
                      <td className="px-5 py-2.5">
                        <code className="text-[0.76rem] text-[var(--muted)]">
                          {t.source_txn_id}
                        </code>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
            {implicated.size > 0 && (
              <ul className="border-t border-[var(--border)] px-5 py-3 text-[0.82rem] leading-6 text-[var(--muted)]">
                {[...new Set(
                  [...implicated.values()].flat().map((c) => c.reason)
                )].map((reason) => (
                  <li key={reason}>
                    <span className="mr-2 inline-block h-2 w-2 rounded-sm bg-[var(--danger)]/60 align-middle" />
                    {reason}
                  </li>
                ))}
              </ul>
            )}
            </>
          )}
        </Card>
      )}

      {activeTab === "linked" && (
        <div className="space-y-4">
          {links.map((link) => (
            <CardTrail key={link.detector} link={link} />
          ))}
        </div>
      )}

      {activeTab === "stats" && diag && (
        <div className="space-y-4">
          {(() => {
            const shows = (group: string[]) =>
              focus === "all"
                ? true
                : group.includes(focus === "firing" ? firingDetector : focus);
            // Impossible travel proves itself with arithmetic rather than a
            // distribution, so it belongs here beside the other proofs — this
            // tab is where an analyst comes to check a verdict's working.
            const showsJourney = legs.length > 0 && shows(GEO_INDICATORS);
            const anything =
              shows(AMOUNT_INDICATORS) ||
              shows(PEER_LEVEL_INDICATORS) ||
              shows(TIMING_INDICATORS) ||
              showsJourney;

            return (
              <>
                {showsJourney && <JourneyTable legs={legs} />}

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
