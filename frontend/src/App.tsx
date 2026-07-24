import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import AlertQueue from "./screens/AlertQueue";
import Baselines from "./screens/Baselines";
import CaseReview from "./screens/CaseReview";
import Dashboard from "./screens/Dashboard";
import FollowThrough from "./screens/FollowThrough";
import ModelInfo from "./screens/ModelInfo";
import {
  IconBaselines,
  IconCases,
  IconDashboard,
  IconModel,
  IconQueue,
} from "./lib/icons";
import { cn } from "./lib/utils";

const NAV = [
  { to: "/", label: "Dashboard", Icon: IconDashboard, end: true },
  { to: "/queue", label: "Alert queue", Icon: IconQueue },
  { to: "/cases", label: "Case follow-through", Icon: IconCases },
  { to: "/baselines", label: "Baselines", Icon: IconBaselines },
  { to: "/model", label: "Model info", Icon: IconModel },
];

/** Title and subtitle come from the route, so the header states where you are
 *  rather than repeating one slogan on every screen. */
const PAGE: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Overview", subtitle: "Last night's run and what needs attention today" },
  "/queue": { title: "Alert queue", subtitle: "Merchants to review, highest risk first" },
  "/cases": { title: "Case follow-through", subtitle: "Confirmed cases tracked to resolution" },
  "/baselines": { title: "Baselines", subtitle: "What each merchant is currently judged against" },
  "/model": { title: "Model info", subtitle: "How alerts are raised, and what each reason means" },
};

function Header() {
  const { pathname } = useLocation();
  const page =
    PAGE[pathname] ??
    (pathname.startsWith("/case/")
      ? { title: "Case review", subtitle: "Everything behind this alert" }
      : { title: "Compliance monitoring", subtitle: "" });

  return (
    <header className="flex flex-wrap items-end justify-between gap-4 border-b border-[var(--border)] pb-5">
      <div>
        <h1 className="text-[1.6rem]">{page.title}</h1>
        {page.subtitle && (
          <p className="mt-1 text-[0.95rem] text-[var(--muted)]">{page.subtitle}</p>
        )}
      </div>
      <div className="flex items-center gap-2 text-[0.8rem] text-[var(--muted)]">
        <span className="inline-flex h-2 w-2 rounded-full bg-[var(--success)]" aria-hidden />
        Pipeline ready · runs 00:00
      </div>
    </header>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen">
        <a
          href="#main"
          className="focus-ring sr-only absolute left-4 top-4 z-50 rounded-lg bg-white px-4 py-2 text-sm font-medium shadow-lg focus:not-sr-only"
        >
          Skip to content
        </a>

        <aside className="sticky top-0 flex h-screen w-60 shrink-0 flex-col bg-[var(--navy-900)] px-3 py-5">
          <div className="px-3 pb-6">
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-[var(--blue-500)]">
              Compliance
            </p>
            <p className="mt-1 text-[1.05rem] font-semibold text-white">Monitoring</p>
          </div>

          <nav className="flex flex-col gap-1" aria-label="Primary">
            {NAV.map(({ to, label, Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "focus-ring flex items-center gap-3 rounded-[var(--radius)] px-3 py-2.5 text-[0.92rem] transition-colors duration-150",
                    isActive
                      ? "bg-[var(--navy-700)] font-semibold text-white"
                      : "text-slate-300 hover:bg-[var(--navy-800)] hover:text-white"
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon
                      className={cn("h-[18px] w-[18px]", isActive ? "text-[var(--blue-500)]" : "")}
                    />
                    {label}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          <div className="mt-auto rounded-[var(--radius)] bg-[var(--navy-800)] px-3 py-3">
            <p className="text-[0.72rem] font-semibold uppercase tracking-[0.18em] text-slate-400">
              Environment
            </p>
            <p className="mt-1.5 text-[0.86rem] text-slate-200">Local · self-contained</p>
            <p className="mt-0.5 text-[0.78rem] text-slate-400">No data leaves this machine</p>
          </div>
        </aside>

        <main id="main" className="min-w-0 flex-1 px-6 py-6 lg:px-8">
          <div className="mx-auto max-w-[1280px] space-y-6">
            <Header />
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/queue" element={<AlertQueue />} />
              <Route path="/case/:id" element={<CaseReview />} />
              <Route path="/cases" element={<FollowThrough />} />
              <Route path="/baselines" element={<Baselines />} />
              <Route path="/model" element={<ModelInfo />} />
            </Routes>
          </div>
        </main>
      </div>
    </BrowserRouter>
  );
}
