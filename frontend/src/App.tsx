import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./screens/Dashboard";
import AlertQueue from "./screens/AlertQueue";
import CaseReview from "./screens/CaseReview";
import FollowThrough from "./screens/FollowThrough";
import ModelHealth from "./screens/ModelHealth";
import { apiGet, type AlertOut } from "./api/client";
import { cn } from "./lib/utils";

const nav = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/queue", label: "Alert queue" },
  { to: "/cases", label: "Case follow-through" },
  { to: "/health", label: "Model health" },
];

export default function App() {
  const [alertCount, setAlertCount] = useState<number | null>(null);

  useEffect(() => {
    apiGet<AlertOut[]>("/api/alerts")
      .then((a) => setAlertCount(a.length))
      .catch(() => setAlertCount(null));
  }, []);

  return (
    <BrowserRouter>
      <div className="min-h-screen text-slate-900">
        <a
          href="#main-content"
          className="focus-ring sr-only absolute left-4 top-4 z-50 rounded-full bg-white px-4 py-2 text-sm font-medium text-slate-900 shadow-lg focus:not-sr-only"
        >
          Skip to content
        </a>
        <div className="mx-auto flex min-h-screen w-full max-w-[1600px] gap-6 p-4 lg:p-6">
          <aside className="glass-panel sticky top-6 flex h-[calc(100vh-3rem)] w-72 flex-col rounded-[28px] p-4 lg:p-5">
            <div className="mb-6 rounded-[22px] border border-slate-200/80 bg-white/95 p-4 shadow-sm">
              <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Local command center</p>
              <h1 className="mt-3 text-[2.15rem] leading-tight text-balance">Compliance monitoring</h1>
              <p className="mt-3 text-[0.95rem] leading-7 text-slate-700">
                Closed-loop alerting, human review, and training feedback in one local workspace.
              </p>
            </div>
            <nav className="flex flex-col gap-2" aria-label="Primary">
              {nav.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.end}
                  className={({ isActive }) =>
                    cn(
                      "nav-chip rounded-2xl border px-4 py-3 text-[0.95rem] font-semibold tracking-[-0.01em]",
                      isActive
                        ? "border-slate-950 bg-slate-950 text-white shadow-lg shadow-slate-950/20"
                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-950"
                    )
                  }
                >
                  {n.label}
                </NavLink>
              ))}
            </nav>
            <div className="mt-auto rounded-[22px] border border-slate-200/80 bg-white/95 p-4 text-[0.95rem] text-slate-700">
              <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-400">Status</p>
              <div className="mt-3 flex items-center justify-between gap-3">
                <span>Pipeline</span>
                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700">
                  Ready
                </span>
              </div>
              <div className="mt-2 flex items-center justify-between gap-3">
                <span>Serving UI</span>
                <span className="rounded-full bg-violet-50 px-2.5 py-1 text-[11px] font-semibold text-violet-700">
                  FastAPI
                </span>
              </div>
            </div>
          </aside>
          <main id="main-content" className="flex-1 min-w-0 rounded-[32px] glass-panel p-5 shadow-2xl lg:p-8">
            <div className="mb-8 flex flex-col gap-4 border-b border-slate-200/70 pb-6 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.3em] text-slate-500">Operations view</p>
                <h2 className="mt-2 text-[2.15rem] leading-tight text-balance text-slate-950">Queue, review, & model health in one place.</h2>
              </div>
              <div className="grid grid-cols-2 gap-3 text-[0.9rem] text-slate-600 sm:text-[0.95rem]">
                <div className="soft-panel rounded-2xl px-4 py-3 text-center">
                  <div className="metric-number text-[1.55rem] font-semibold text-slate-950">
                    {alertCount ?? "—"}
                  </div>
                  <div>open alerts</div>
                </div>
                <div className="soft-panel rounded-2xl px-4 py-3 text-center">
                  <div className="metric-number text-[1.55rem] font-semibold text-slate-950">Local</div>
                  <div>self-contained</div>
                </div>
              </div>
            </div>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/queue" element={<AlertQueue />} />
              <Route path="/case/:id" element={<CaseReview />} />
              <Route path="/cases" element={<FollowThrough />} />
              <Route path="/health" element={<ModelHealth />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  );
}
