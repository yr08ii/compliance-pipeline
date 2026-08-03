/** Shared surfaces, so every screen uses the same card, tile and pill rather
 *  than each inventing its own padding and shadow. */

import type { ReactNode } from "react";
import { cn } from "./utils";

export function Card({
  children,
  className,
  title,
  subtitle,
  action,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <section className={cn("card", className)}>
      {(title || action) && (
        <div className="flex items-start justify-between gap-4 border-b border-[var(--border)] px-5 py-4">
          <div>
            {title && <h2 className="text-[1.02rem]">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[0.86rem] text-[var(--muted)]">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

const TONES = {
  blue: "bg-[var(--blue-50)] text-[var(--blue-600)]",
  navy: "bg-[#e7edf4] text-[var(--navy-700)]",
  success: "bg-[var(--success-bg)] text-[var(--success)]",
  warning: "bg-[var(--warning-bg)] text-[var(--warning)]",
  danger: "bg-[var(--danger-bg)] text-[var(--danger)]",
  neutral: "bg-slate-100 text-slate-600",
} as const;

export type Tone = keyof typeof TONES;

/** A headline number with its icon, matching the reference layout: icon chip,
 *  label, value, and one line of context underneath. */
export function StatTile({
  label,
  value,
  hint,
  icon,
  tone = "blue",
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className="card hoverable p-4">
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]",
            TONES[tone]
          )}
          aria-hidden
        >
          {icon}
        </span>
        <div className="min-w-0">
          <p className="text-[0.82rem] text-[var(--muted)]">{label}</p>
          <p className="metric-number mt-0.5 text-[1.6rem] font-semibold text-[var(--text-strong)]">
            {value}
          </p>
          {hint && <p className="mt-0.5 text-[0.82rem] text-[var(--muted)]">{hint}</p>}
        </div>
      </div>
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[0.76rem] font-semibold",
        TONES[tone]
      )}
    >
      {children}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="px-5 py-10 text-center text-[0.92rem] text-[var(--muted)]">{children}</div>
  );
}

export function Loading() {
  return <div className="card px-5 py-10 text-center text-[0.92rem] text-[var(--muted)]">Loading…</div>;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="card border-[#f0cfcd] bg-[var(--danger-bg)] px-5 py-4 text-[0.92rem] text-[var(--danger)]">
      {children}
    </div>
  );
}

/** Donut built from stroke-dasharray so it needs no charting library.
 *  Each slice carries a label in the legend, never colour alone. */
export function Donut({
  segments,
  total,
  caption,
}: {
  segments: { label: string; value: number; color: string }[];
  total: number;
  caption: string;
}) {
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="flex flex-wrap items-center gap-6">
      <svg
        viewBox="0 0 140 140"
        className="h-[140px] w-[140px] shrink-0 -rotate-90"
        role="img"
        aria-label={`${caption}: ${segments.map((s) => `${s.label} ${s.value}`).join(", ")}`}
      >
        <circle cx="70" cy="70" r={radius} fill="none" stroke="#eef2f7" strokeWidth="16" />
        {segments.map((s) => {
          const length = total > 0 ? (s.value / total) * circumference : 0;
          const dash = (
            <circle
              key={s.label}
              cx="70"
              cy="70"
              r={radius}
              fill="none"
              stroke={s.color}
              strokeWidth="16"
              strokeDasharray={`${length} ${circumference - length}`}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
            />
          );
          offset += length;
          return dash;
        })}
      </svg>

      <ul className="min-w-0 flex-1 space-y-2">
        {segments.map((s) => (
          <li key={s.label} className="flex items-center justify-between gap-3 text-[0.92rem]">
            <span className="flex items-center gap-2 text-[var(--text)]">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: s.color }}
                aria-hidden
              />
              {s.label}
            </span>
            <span className="metric-number font-semibold text-[var(--text-strong)]">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A short explanation attached to a control.
 *
 *  Native `title` rather than a custom popover: it is keyboard and screen
 *  reader accessible for free, and a tooltip that needs its own focus
 *  management is a tooltip that will get it wrong. */
export function Hint({
  text,
  children,
}: {
  text: string;
  children: ReactNode;
}) {
  return (
    <span title={text} className="inline-flex items-center gap-1">
      {children}
    </span>
  );
}

/** Page navigation for lists that run to thousands.
 *
 *  Shows the range rather than only the page number, because "1–20 of 9,036"
 *  answers "how much is left?" and "page 1 of 452" does not. */
export function Pager({
  page,
  pages,
  total,
  pageSize,
  onChange,
}: {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
}) {
  if (total === 0) return null;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] px-5 py-3">
      <p className="text-[0.85rem] text-[var(--muted)]">
        <span className="metric-number font-medium text-[var(--text-strong)]">
          {first.toLocaleString()}–{last.toLocaleString()}
        </span>{" "}
        of <span className="metric-number">{total.toLocaleString()}</span>
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onChange(page - 1)}
          disabled={page <= 1}
          className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.85rem] font-medium text-[var(--text-strong)] transition hover:bg-[var(--blue-50)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Previous
        </button>
        <span className="text-[0.85rem] text-[var(--muted)]">
          Page {page} of {pages}
        </span>
        <button
          type="button"
          onClick={() => onChange(page + 1)}
          disabled={page >= pages}
          className="focus-ring rounded-[var(--radius)] border border-[var(--border-strong)] bg-white px-3 py-1.5 text-[0.85rem] font-medium text-[var(--text-strong)] transition hover:bg-[var(--blue-50)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
}
