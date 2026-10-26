import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useParams } from "react-router-dom";
import { api } from "../api";
import * as I from "./Icons";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";

function Item({ to, icon, children, disabled }: { to: string; icon: JSX.Element; children: string; disabled?: boolean }) {
  return (
    <NavLink to={to} end className={({ isActive }) => `nav-item${isActive ? " active" : ""}${disabled ? " disabled" : ""}`}>
      {icon}
      <span>{children}</span>
    </NavLink>
  );
}

export function Shell() {
  const { runId } = useParams();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const fallback = runs.data?.find((r) => r.status === "done")?.id;
  const id = runId ?? fallback;
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api.run(id!), enabled: !!id, refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false) });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, staleTime: 60_000 });
  const r = run.data;
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  useEffect(() => setNavOpen(false), [location.pathname]); // close the drawer after navigating

  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <div className="nav-backdrop" onClick={() => setNavOpen(false)} aria-hidden />
      <aside className="sidebar" id="main-nav" aria-label="Main navigation">
        <NavLink to="/" className="brand" aria-label="Flame home">
          <I.Flame /> Flame
        </NavLink>
        <div className="nav-group">
          <div className="nav-label">Analyse</div>
          <Item to="/new" icon={<I.Plus />}>New analysis</Item>
          <Item to="/runs" icon={<I.Clock />}>Runs</Item>
          <Item to="/changes" icon={<I.Diff />}>Regulation change</Item>
          <Item to="/history" icon={<I.History />}>History</Item>
        </div>
        <div className="nav-group">
          <div className="nav-label">Results</div>
          <Item to={id ? `/runs/${id}` : "/runs"} icon={<I.List />} disabled={!id}>Requirements</Item>
          <Item to={id ? `/runs/${id}/profile` : "/runs"} icon={<I.Cube />} disabled={!id}>Product profile</Item>
          <Item to={id ? `/runs/${id}/3d` : "/runs"} icon={<I.Cube />} disabled={!id}>3D view</Item>
          <Item to={id ? `/runs/${id}/agents` : "/runs"} icon={<I.Agents />} disabled={!id}>Agents</Item>
          <Item to={id ? `/runs/${id}/accuracy` : "/runs"} icon={<I.Target />} disabled={!id}>Accuracy</Item>
          <Item to={id ? `/runs/${id}/upgrade` : "/runs"} icon={<I.Diff />} disabled={!id}>Next versions</Item>
        </div>
        <div className="nav-group">
          <div className="nav-label">Explore</div>
          <Item to={id ? `/runs/${id}/ask` : "/runs"} icon={<I.Chat />} disabled={!id}>Ask</Item>
          <Item to={id ? `/runs/${id}/graph` : "/runs"} icon={<I.Graph />} disabled={!id}>Graph: structure</Item>
          <Item to={id ? `/runs/${id}/graph/meaning` : "/runs"} icon={<I.Spark />} disabled={!id}>Graph: meaning</Item>
        </div>
        <div className="sidebar-foot">
          {status.data && (
            <>
              Model {status.data.model ? "connected" : "not configured"}
            </>
          )}
        </div>
      </aside>
      <div className="main">
        <header className="statusbar">
          <button className="btn btn-ghost btn-icon nav-toggle" onClick={() => setNavOpen((o) => !o)}
            aria-label={navOpen ? "Close menu" : "Open menu"} aria-expanded={navOpen} aria-controls="main-nav">
            {navOpen ? <I.Close size={20} /> : <I.Menu size={20} />}
          </button>
          {r ? (
            <>
              <span className={`status-dot ${r.status === "running" ? "live" : r.status === "done" ? "ok" : r.status === "failed" ? "bad" : ""}`} aria-hidden />
              <span className="run-title">{r.title}</span>
              <span className="muted detail">
                {r.status === "running"
                  ? `Running · ${r.stages.find((s) => s.status === "running")?.label ?? "starting"}`
                  : r.status === "done"
                    ? `Complete · ${r.counts.requirements ?? 0} requirements from ${r.counts.regulations ?? 0} regulations`
                    : r.status === "failed"
                      ? "Failed"
                      : "Queued"}
                {r.cost_usd > 0 && ` · $${r.cost_usd.toFixed(2)}`}
              </span>
              <span className="spacer" />
              {r.status === "done" && (
                <a className="btn btn-ghost btn-sm export" href={`/api/runs/${r.id}/export?format=csv`}>
                  <I.Download size={16} /> CSV
                </a>
              )}
              {r.status === "done" && (
                <a className="btn btn-ghost btn-sm export" href={`/api/runs/${r.id}/export`}>
                  <I.Download size={16} /> submission.json
                </a>
              )}
            </>
          ) : (
            <>
              <span className="muted">No analysis open</span>
              <span className="spacer" />
            </>
          )}
          <ThemeToggle />
          <UserMenu />
        </header>
        <Outlet />
      </div>
    </div>
  );
}
