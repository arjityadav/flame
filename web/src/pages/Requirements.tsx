import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, PHASE_HINT, PHASES, type Requirement } from "../api";
import { SeverityChip } from "../components/Chips";
import * as I from "../components/Icons";
import { SourcePanel } from "../components/SourcePanel";

function Row({ r, selected, open, onSelect, onToggle }: { r: Requirement; selected: boolean; open: boolean; onSelect: () => void; onToggle: () => void }) {
  return (
    <li className={`req-row${selected ? " selected" : ""}`} id={`req-${r.id}`}>
      <div className="req-line">
        <button className="req-main" onClick={onSelect} aria-current={selected ? "true" : undefined}>
          <span className="req-prio">#{r.priority}</span>
          <span>
            <span className="req-summary">{r.summary}</span>
            <span className="req-meta">
              <SeverityChip s={r.severity} />
              <span className="req-cite">
                {r.source.regulation} §{r.source.paragraph} · p.{r.source.page}
              </span>
            </span>
          </span>
        </button>
        <button className={`caret-btn${open ? " open" : ""}`} onClick={onToggle} aria-expanded={open} aria-controls={`exp-${r.id}`} aria-label={open ? "Hide details" : "Show details"}>
          <I.Chevron />
        </button>
      </div>
      {open && (
        <div className="req-expand" id={`exp-${r.id}`}>
          <blockquote>{r.text}</blockquote>
          <dl>
            <dt>Why severity {r.severity}</dt>
            <dd>{r.severity_reason}</dd>
            <dt>Why phase {r.phase}: {PHASES[r.phase]}</dt>
            <dd>{r.phase_reason}</dd>
            {r.test_case && (
              <>
                <dt>How to verify</dt>
                <dd>{r.test_case}</dd>
              </>
            )}
            {r.applies_to && (
              <>
                <dt>Applies to</dt>
                <dd>{r.applies_to}</dd>
              </>
            )}
          </dl>
          <span className="small muted">
            {r.id} · {r.rated_by === "model" ? "rated by the enrich agent" : "rated by keyword rules (no model configured)"}
          </span>
        </div>
      )}
    </li>
  );
}

export function Requirements() {
  const { runId } = useParams();
  const [params, setParams] = useSearchParams();
  const reqs = useQuery({ queryKey: ["requirements", runId], queryFn: () => api.requirements(runId!) });
  const [regFilter, setRegFilter] = useState("");
  const [phaseFilter, setPhaseFilter] = useState(0);
  const [sevMin, setSevMin] = useState(1);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const selectedId = params.get("r");

  const all = reqs.data ?? [];
  const regs = useMemo(() => Array.from(new Set(all.map((r) => r.source.regulation))), [all]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all.filter(
      (r) =>
        (!regFilter || r.source.regulation === regFilter) &&
        (!phaseFilter || r.phase === phaseFilter) &&
        r.severity >= sevMin &&
        (!needle || `${r.summary} ${r.text} ${r.source.paragraph}`.toLowerCase().includes(needle)),
    );
  }, [all, regFilter, phaseFilter, sevMin, q]);
  const selected = all.find((r) => r.id === selectedId) ?? null;
  const index = selected ? shown.findIndex((r) => r.id === selected.id) : -1;

  const select = (r: Requirement | undefined) => {
    if (!r) return;
    params.set("r", r.id);
    setParams(params, { replace: true });
    document.getElementById(`req-${r.id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  useEffect(() => {
    // opened from a link (?r=R-0154): bring that row into view once the list is there
    if (selectedId && all.length) document.getElementById(`req-${selectedId}`)?.scrollIntoView({ block: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "SELECT") return;
      if (e.key === "ArrowDown" || e.key === "j") select(shown[Math.min(shown.length - 1, index + 1)]);
      if (e.key === "ArrowUp" || e.key === "k") select(shown[Math.max(0, index - 1)]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const byPhase = [1, 2, 3, 4, 5].map((p) => ({ p, items: shown.filter((r) => r.phase === p) })).filter((g) => g.items.length);
  const pages = new Set(all.map((r) => `${r.source.regulation}:${r.source.page}`)).size;
  const rules = all.length > 0 && all.every((r) => r.rated_by === "rules");

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Requirements</h1>
          <p>Ordered by when they must be met, then by how much they matter. Click one to open the regulation at the exact paragraph.</p>
        </div>
      </div>

      {reqs.isLoading && (
        <div style={{ display: "grid", gap: 8 }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 64 }} />
          ))}
        </div>
      )}
      {reqs.isError && <div className="notice warn">{(reqs.error as Error).message}</div>}
      {reqs.data && all.length === 0 && <div className="card empty">This analysis has no requirements yet.</div>}

      {all.length > 0 && (
        <>
          <div className="stats">
            <div className="card stat">
              <div className="k">Requirements</div>
              <div className="v">{all.length}</div>
            </div>
            <div className="card stat">
              <div className="k">Regulations</div>
              <div className="v">{regs.length}</div>
            </div>
            <div className="card stat">
              <div className="k">Due at concept stage</div>
              <div className="v">{all.filter((r) => r.phase === 1).length}</div>
            </div>
            <div className="card stat">
              <div className="k">Safety-critical</div>
              <div className="v">{all.filter((r) => r.severity === 5).length}</div>
            </div>
            <div className="card stat">
              <div className="k">Pages cited</div>
              <div className="v">{pages}</div>
            </div>
          </div>

          {rules && (
            <div className="notice">
              Severity and phase are estimated by keyword rules because no model is configured on this server. With a model, the enrich agent rates each requirement and explains why.
            </div>
          )}

          <div className="filters" role="search">
            <input className="input search" placeholder="Search requirements" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search requirements" />
            <select className="select" value={regFilter} onChange={(e) => setRegFilter(e.target.value)} aria-label="Regulation">
              <option value="">All regulations</option>
              {regs.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
            <select className="select" value={phaseFilter} onChange={(e) => setPhaseFilter(Number(e.target.value))} aria-label="Phase">
              <option value={0}>All phases</option>
              {[1, 2, 3, 4, 5].map((p) => (
                <option key={p} value={p}>
                  P{p} {PHASES[p]}
                </option>
              ))}
            </select>
            <select className="select" value={sevMin} onChange={(e) => setSevMin(Number(e.target.value))} aria-label="Minimum severity">
              {[1, 2, 3, 4, 5].map((s) => (
                <option key={s} value={s}>
                  Severity {s}+
                </option>
              ))}
            </select>
            <span className="small muted">{shown.length} shown</span>
          </div>

          <div className={`req-layout${selected ? "" : " no-source"}`}>
            <div>
              {byPhase.length === 0 && <div className="card empty">Nothing matches these filters.</div>}
              {byPhase.map(({ p, items }) => (
                <section className="phase-group" key={p} aria-labelledby={`phase-${p}`}>
                  <div className="phase-head">
                    <span className="phase-num">P{p}</span>
                    <h2 id={`phase-${p}`}>{PHASES[p]}</h2>
                    <span className="count">
                      {items.length} · {PHASE_HINT[p]}
                    </span>
                  </div>
                  <ul className="req-list">
                    {items.map((r) => (
                      <Row
                        key={r.id}
                        r={r}
                        selected={r.id === selectedId}
                        open={open.has(r.id)}
                        onSelect={() => select(r)}
                        onToggle={() => {
                          const next = new Set(open);
                          next.has(r.id) ? next.delete(r.id) : next.add(r.id);
                          setOpen(next);
                        }}
                      />
                    ))}
                  </ul>
                </section>
              ))}
            </div>
            {selected && (
              <SourcePanel
                node={selected.node}
                requirement={selected}
                page={selected.source.page}
                onClose={() => {
                  params.delete("r");
                  setParams(params, { replace: true });
                }}
                onPrev={index > 0 ? () => select(shown[index - 1]) : undefined}
                onNext={index >= 0 && index < shown.length - 1 ? () => select(shown[index + 1]) : undefined}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
