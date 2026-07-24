import { Card, Pill } from "../lib/ui";

const SAMPLE: [string, string, string, "warning" | "blue" | "success"][] = [
  ["Monitor", "Merchant response pending", "Today", "warning"],
  ["Reserve", "Action recorded in payment system", "Yesterday", "blue"],
  ["Closed", "Case cleared after review", "2 days ago", "success"],
];

export default function FollowThrough() {
  return (
    <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
      <Card
        title="Open case trail"
        subtitle="Sample layout — real timelines arrive in the disposition phase"
        action={<Pill tone="warning">Sample</Pill>}
      >
        <ul className="divide-y divide-[var(--border)]">
          {SAMPLE.map(([status, note, when, tone]) => (
            <li key={status} className="flex items-start justify-between gap-4 px-5 py-4">
              <div>
                <Pill tone={tone}>{status}</Pill>
                <p className="mt-2 text-[0.92rem] leading-6 text-[var(--text)]">{note}</p>
              </div>
              <span className="shrink-0 text-[0.82rem] text-[var(--muted)]">{when}</span>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Why it matters">
        <div className="space-y-3 p-5 text-[0.92rem] leading-7 text-[var(--text)]">
          <p>
            Confirmed cases need a durable timeline, not just a verdict. This screen becomes the
            operational record for actions taken outside the portal.
          </p>
          <div className="rounded-[var(--radius)] bg-[var(--blue-50)] px-4 py-3 text-[var(--navy-800)]">
            No data is wired yet — the layout is ready for case timelines, stale-case warnings, and
            signed updates.
          </div>
        </div>
      </Card>
    </div>
  );
}
