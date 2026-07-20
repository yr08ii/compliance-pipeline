import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./screens/Dashboard";
import AlertQueue from "./screens/AlertQueue";
import CaseReview from "./screens/CaseReview";
import FollowThrough from "./screens/FollowThrough";
import ModelHealth from "./screens/ModelHealth";
import { cn } from "./lib/utils";

const nav = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/queue", label: "Alert queue" },
  { to: "/cases", label: "Case follow-through" },
  { to: "/health", label: "Model health" },
];

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex min-h-screen text-slate-900">
        <aside className="w-56 border-r bg-slate-50 p-4">
          <h1 className="mb-6 text-sm font-medium text-slate-500">Compliance monitoring</h1>
          <nav className="flex flex-col gap-1">
            {nav.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end}
                className={({ isActive }) => cn("rounded px-3 py-2 text-sm",
                  isActive ? "bg-slate-900 text-white" : "hover:bg-slate-200")}>
                {n.label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className="flex-1 p-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/queue" element={<AlertQueue />} />
            <Route path="/case/:id" element={<CaseReview />} />
            <Route path="/cases" element={<FollowThrough />} />
            <Route path="/health" element={<ModelHealth />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
