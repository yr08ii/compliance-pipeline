export default function FollowThrough() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Accountability</p>
        <h2 className="mt-2 text-2xl text-slate-950">Case follow-through</h2>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="soft-panel rounded-[28px] p-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium text-slate-950">Open case trail</p>
              <p className="mt-1 text-sm text-slate-500">Sample layout — real case timelines arrive in Phase 2.</p>
            </div>
            <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">Sample</span>
          </div>
          <div className="mt-6 space-y-4">
            {[
              ["Monitor", "Merchant response pending", "Today"],
              ["Reserve", "Action recorded in payment system", "Yesterday"],
              ["Closed", "Case cleared after review", "2 days ago"],
            ].map(([status, note, when]) => (
              <div key={status} className="rounded-2xl border border-slate-200/80 bg-slate-50/80 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-[0.98rem] font-semibold text-slate-950">{status}</p>
                    <p className="mt-1 text-[0.95rem] leading-7 text-slate-700">{note}</p>
                  </div>
                  <span className="text-xs text-slate-500">{when}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        <aside className="soft-panel rounded-[28px] p-6">
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Why it matters</p>
          <p className="mt-3 text-[1.1rem] font-semibold text-slate-950">Follow-through keeps decisions visible.</p>
          <p className="mt-2 text-[0.95rem] leading-7 text-slate-700">
            Confirmed cases need a durable timeline, not just a verdict. This screen will become the operational record for actions taken outside the portal.
          </p>
          <div className="mt-5 rounded-2xl bg-white p-4 text-[0.95rem] leading-7 text-slate-700 shadow-sm">
            No data is wired yet, but the layout is ready for case timelines, stale-case warnings, and signed updates.
          </div>
        </aside>
      </div>
    </div>
  );
}
