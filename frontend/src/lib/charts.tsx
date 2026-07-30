/** Plots drawn as inline SVG.
 *
 *  No charting library: the platform runs air-gapped, and these two shapes are
 *  simple enough that a dependency would cost more than it saves. Each plot
 *  carries a text summary for screen readers, and never relies on colour alone
 *  to say which mark is the merchant. */

const NAVY = "#0f2b45";
const BLUE = "#3e8acb";
const DANGER = "#b91c1c";
const GRID = "#e3e9f0";
const MUTED = "#64748b";

function fmt(v: number) {
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** Where this merchant sits against its peer cohort.
 *
 *  A box plot on a log scale: amounts in a cohort span orders of magnitude, so
 *  a linear axis collapses every peer into one pixel beside a large outlier. */
export function PeerBoxPlot({
  merchantValue,
  peerMedian,
  peerQ1,
  peerQ3,
  peerFence,
  peerValues,
  label,
}: {
  merchantValue: number | null;
  peerMedian: number | null;
  peerQ1: number | null;
  peerQ3: number | null;
  peerFence: number | null;
  peerValues: number[];
  label: string;
}) {
  if (merchantValue == null || peerMedian == null) {
    return <p className="px-5 py-6 text-[0.9rem] text-[var(--muted)]">No peer distribution available.</p>;
  }

  const W = 640;
  const H = 150;
  const PAD = 48;

  const all = [...peerValues, merchantValue, peerMedian].filter((v) => v > 0);
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const logLo = Math.log10(lo * 0.8);
  const logHi = Math.log10(hi * 1.2);
  const x = (v: number) =>
    PAD + ((Math.log10(Math.max(v, 1e-6)) - logLo) / (logHi - logLo)) * (W - 2 * PAD);

  const boxY = 44;
  const boxH = 34;
  const q1 = peerQ1 ?? peerMedian;
  const q3 = peerQ3 ?? peerMedian;

  const ticks = [lo, peerMedian, hi].filter((v, i, a) => a.indexOf(v) === i);

  return (
    <figure className="px-5 py-4">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`${label}. This merchant ${fmt(merchantValue)}; peer median ${fmt(
          peerMedian
        )} across ${peerValues.length} peers. Log scale.`}
      >
        {/* whiskers */}
        <line x1={x(lo)} x2={x(hi)} y1={boxY + boxH / 2} y2={boxY + boxH / 2} stroke={GRID} strokeWidth="2" />
        {/* interquartile box */}
        <rect x={x(q1)} y={boxY} width={Math.max(x(q3) - x(q1), 2)} height={boxH} fill="#e7edf4" stroke={BLUE} />
        {/* peer median */}
        <line x1={x(peerMedian)} x2={x(peerMedian)} y1={boxY - 4} y2={boxY + boxH + 4} stroke={NAVY} strokeWidth="2.5" />
        {/* individual peers, so the analyst sees the population not just its summary */}
        {peerValues.map((v, i) => (
          <circle key={i} cx={x(v)} cy={boxY + boxH / 2} r="2.5" fill={MUTED} opacity="0.45" />
        ))}
        {/* outlier fence */}
        {peerFence != null && peerFence > 0 && peerFence < hi * 1.2 && (
          <line
            x1={x(peerFence)}
            x2={x(peerFence)}
            y1={boxY - 8}
            y2={boxY + boxH + 8}
            stroke={DANGER}
            strokeWidth="1.5"
            strokeDasharray="4 3"
          />
        )}
        {/* this merchant — marked by shape and label, not colour alone */}
        <polygon
          points={`${x(merchantValue)},${boxY - 12} ${x(merchantValue) - 6},${boxY - 22} ${
            x(merchantValue) + 6
          },${boxY - 22}`}
          fill={DANGER}
        />
        <text x={x(merchantValue)} y={boxY - 26} textAnchor="middle" fontSize="11" fontWeight="600" fill={DANGER}>
          This merchant
        </text>
        {/* axis */}
        <line x1={PAD} x2={W - PAD} y1={H - 28} y2={H - 28} stroke={GRID} />
        {ticks.map((v) => (
          <g key={v}>
            <line x1={x(v)} x2={x(v)} y1={H - 32} y2={H - 24} stroke={MUTED} />
            <text x={x(v)} y={H - 10} textAnchor="middle" fontSize="10" fill={MUTED}>
              {fmt(v)}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[0.78rem] text-[var(--muted)]">
        <span>▲ this merchant</span>
        <span>│ peer median</span>
        <span>▭ middle 50% of peers</span>
        <span>┆ outlier threshold</span>
        <span>log scale</span>
      </figcaption>
    </figure>
  );
}

/** The merchant's trading-hours curve, its cohort's, and where the scored
 *  day's transactions actually landed. */
export function HourDensityPlot({
  merchant,
  cohort,
  threshold,
  scoredHours,
}: {
  merchant: number[];
  cohort: number[];
  threshold: number;
  scoredHours: number[];
}) {
  if (!merchant.length) {
    return <p className="px-5 py-6 text-[0.9rem] text-[var(--muted)]">No hour density available.</p>;
  }

  const W = 640;
  const H = 190;
  const PAD_L = 40;
  const PAD_B = 34;
  const bins = merchant.length;
  const peak = Math.max(...merchant, ...(cohort.length ? cohort : [0]), threshold);

  const x = (bin: number) => PAD_L + (bin / bins) * (W - PAD_L - 12);
  const y = (v: number) => H - PAD_B - (v / (peak || 1)) * (H - PAD_B - 24);
  const path = (series: number[]) =>
    series.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");

  return (
    <figure className="px-5 py-4">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Trading hours density. ${scoredHours.length} transactions on the scored day; the dashed line is the quiet-hour threshold below which a transaction is flagged.`}
      >
        {[0, 6, 12, 18, 24].map((h) => (
          <g key={h}>
            <line x1={x((h / 24) * bins)} x2={x((h / 24) * bins)} y1={20} y2={H - PAD_B} stroke={GRID} />
            <text x={x((h / 24) * bins)} y={H - 14} textAnchor="middle" fontSize="10" fill={MUTED}>
              {String(h).padStart(2, "0")}:00
            </text>
          </g>
        ))}

        {cohort.length > 0 && (
          <path d={path(cohort)} fill="none" stroke={MUTED} strokeWidth="1.5" strokeDasharray="5 3" opacity="0.7" />
        )}
        <path d={`${path(merchant)} L${x(bins - 1)},${H - PAD_B} L${x(0)},${H - PAD_B} Z`} fill={BLUE} opacity="0.14" />
        <path d={path(merchant)} fill="none" stroke={BLUE} strokeWidth="2" />

        {threshold > 0 && (
          <line x1={PAD_L} x2={W - 12} y1={y(threshold)} y2={y(threshold)} stroke={DANGER} strokeWidth="1.2" strokeDasharray="4 3" />
        )}

        {/* the scored day's transactions, as rug marks along the axis */}
        {scoredHours.map((h, i) => (
          <line
            key={i}
            x1={x((h / 24) * bins)}
            x2={x((h / 24) * bins)}
            y1={H - PAD_B}
            y2={H - PAD_B - 12}
            stroke={NAVY}
            strokeWidth="2"
          />
        ))}

        <text x={4} y={26} fontSize="10" fill={MUTED}>
          density
        </text>
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[0.78rem] text-[var(--muted)]">
        <span style={{ color: BLUE }}>▬ this merchant's usual hours</span>
        <span>┅ its MCC peers</span>
        <span style={{ color: DANGER }}>┄ quiet-hour threshold</span>
        <span style={{ color: NAVY }}>▏scored-day transactions</span>
      </figcaption>
    </figure>
  );
}
