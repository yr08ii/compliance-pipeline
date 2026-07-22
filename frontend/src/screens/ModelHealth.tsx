export default function ModelHealth() {
  const metrics = [
    ["False-positive rate", "Tracks over time once dispositions exist"],
    ["Label completeness", "Share of alerts closed with a reason code"],
    ["Training batches", "Exports pushed to the secondary model"],
  ] as const;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Signal quality</p>
          <h2 className="mt-2 text-2xl text-slate-950">Model &amp; pipeline health</h2>
        </div>
        <span className="w-fit rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">
          Preview · metrics not yet computed
        </span>
      </div>

      <section className="grid gap-4 md:grid-cols-3">
        {metrics.map(([label, note]) => (
          <article key={label} className="soft-panel rounded-[24px] p-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">{label}</p>
            <p className="mt-3 text-[2rem] font-semibold text-slate-300">—</p>
            <p className="mt-2 text-[0.95rem] leading-7 text-slate-600">{note}</p>
          </article>
        ))}
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="soft-panel rounded-[28px] p-6">
          <p className="text-[1.05rem] font-semibold text-slate-950">Pipeline stages</p>
          <p className="mt-1 text-sm text-slate-500">The nightly flow, in order. Live run status arrives with Prefect wiring.</p>
          <div className="mt-4 overflow-hidden rounded-2xl border border-slate-200/80 bg-white">
            {["Pull", "Profile", "Route", "Detect", "Score"].map((stage, index) => (
              <div key={stage} className={`flex items-center justify-between gap-4 px-4 py-3 ${index === 0 ? "" : "border-t border-slate-100"}`}>
                <span className="text-sm text-slate-600">{stage}</span>
                <span className="metric-number rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500">
                  {index + 1}
                </span>
              </div>
            ))}
          </div>
        </div>

        <aside className="soft-panel rounded-[28px] p-6">
          <p className="text-[1.05rem] font-semibold text-slate-950">Health notes</p>
          <ul className="mt-4 space-y-3 text-[0.95rem] text-slate-700">
            <li className="rounded-2xl bg-slate-50 px-4 py-3 leading-7">Alert and case flows are connected to live data.</li>
            <li className="rounded-2xl bg-slate-50 px-4 py-3 leading-7">The bootstrap CLI writes one deterministic alert.</li>
            <li className="rounded-2xl bg-slate-50 px-4 py-3 leading-7">Disposition capture is still deferred to the next phase.</li>
          </ul>
        </aside>
      </section>
    </div>
  );
}
