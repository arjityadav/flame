import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, type AgentStep, type Journey } from "../api";
import { AgentFunnel, STOPS, type Stop } from "../components/AgentFunnel";
import { AgentTracer, outcome, pathState } from "../components/AgentTracer";

// The run as its agents saw it: one card per agent (Claude call) or code step, in hand-off order, with what each one
// decided. A finished run can be replayed step by step; a running one fills in live as each stage file appears.
// The stream above shows how many requirements each agent passed on; any requirement can be followed through them.

const STEP_MS = 1400;
const SPEEDS = [1, 2, 4];

export function Agents() {
  const { runId } = useParams();
  const trace = useQuery({
    queryKey: ["trace", runId],
    queryFn: () => api.trace(runId!),
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 2000 : false),
  });
  const agents = trace.data?.agents ?? [];
  const live = !!trace.data && ["queued", "running"].includes(trace.data.status);
  const journeys = useQuery({
    queryKey: ["journeys", runId, live ? agents.filter((a) => a.status === "done").length : "done"],
    queryFn: () => api.journeys(runId!),
    enabled: !!trace.data,
  });

  // replay: `shown` agents are revealed, the next one is "working"; null = show everything as it is
  const [shown, setShown] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [search] = useSearchParams();
  const [open, setOpen] = useState<string | null>(search.get("open"));
  const [reg, setReg] = useState<string | null>(null);
  const [filter, setFilter] = useState<Stop | "submitted" | null>(null);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<number | null>(null);
  const cards = useRef<Record<string, HTMLLIElement | null>>({});
  const side = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!playing || shown === null) return;
    if (shown >= agents.length) { setPlaying(false); return; }
    const t = setTimeout(() => setShown((s) => (s === null ? null : s + 1)), STEP_MS / speed);
    return () => clearTimeout(t);
  }, [playing, shown, speed, agents.length]);

  const view = (a: AgentStep, i: number): AgentStep["status"] =>
    shown === null ? a.status : i < shown ? "done" : i === shown ? "running" : "pending";
  const statusOf = (key: string) => {
    const i = agents.findIndex((a) => a.key === key);
    return i < 0 ? "pending" : view(agents[i], i);
  };
  const replaying = shown !== null && shown < agents.length;
  const doneCount = agents.filter((a, i) => view(a, i) === "done").length;
  const activeKey = shown !== null && shown < agents.length ? agents[shown].key : null;

  const all = journeys.data?.items ?? [];
  const regs = useMemo(() => [...new Set(all.map((j) => j.regulation))].sort(), [all]);
  const inLens = useMemo(() => all.filter((j) => !reg || j.regulation === reg), [all, reg]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return inLens.filter((j) => (filter === null ? true : filter === "submitted" ? !j.stopped : j.stopped === filter)
      && (!q || `${j.text} ${j.paragraph} ${j.id ?? ""} ${j.section}`.toLowerCase().includes(q)));
  }, [inLens, filter, query]);
  const journey: Journey | null = picked !== null ? all[picked] ?? null : null;
  const path = journey ? pathState(journey) : null;

  const step = (to: number) => { setPlaying(false); setOpen(null); setShown(Math.max(0, Math.min(agents.length, to))); };
  const goAgent = (key: string) => {
    if (statusOf(key) !== "done") return;
    setOpen(key);
    cards.current[key]?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const pickId = (id: string) => {
    const i = all.findIndex((j) => j.id === id);
    if (i >= 0) setPicked(i);
  };
  const chooseFilter = (f: Stop | "submitted" | null) => {
    setFilter(f);
    setPicked(null);
    if (f && window.matchMedia("(max-width: 1100px)").matches) side.current?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Agents</h1>
          <p>
            How the analysis was made: each agent hands its result to the next, and plain code checks the work between
            them. Replay the run, click a drop-off in the stream, or follow one requirement through every agent.
          </p>
        </div>
        {live && <span className="small muted"><span className="status-dot live" /> running, updates live</span>}
      </div>

      {trace.isLoading && <div className="skeleton" style={{ height: 480 }} />}
      {trace.isError && <div className="notice warn">{(trace.error as Error).message}</div>}

      {agents.length > 0 && (
        <div className="agents-layout">
          <div className="agents-main">
            {!live && (
              <div className="replay-bar">
                <button className="btn btn-secondary btn-sm btn-icon" title="Back one step" aria-label="Back one step"
                  onClick={() => step((shown ?? agents.length) - 1)} disabled={shown === 0}>‹</button>
                <button className="btn btn-primary btn-sm" onClick={() => {
                  setOpen(null);
                  if (playing) setPlaying(false);
                  else { if (shown === null || shown >= agents.length) setShown(0); setPlaying(true); }
                }}>{playing ? "Pause" : replaying ? "Resume" : "Replay the run"}</button>
                <button className="btn btn-secondary btn-sm btn-icon" title="Forward one step" aria-label="Forward one step"
                  onClick={() => step((shown ?? agents.length) + 1)} disabled={shown === null || shown >= agents.length}>›</button>
                <div className="seg" role="group" aria-label="Replay speed">
                  {SPEEDS.map((s) => <button key={s} className={speed === s ? "on" : ""} onClick={() => setSpeed(s)}>{s}×</button>)}
                </div>
                {shown !== null && <button className="btn btn-ghost btn-sm" onClick={() => { setPlaying(false); setShown(null); }}>Show all</button>}
                <span className="spacer" />
                <span className="small muted">{doneCount} of {agents.length} steps</span>
              </div>
            )}
            <div className="agents-progress">
              {agents.map((a, i) => (
                <button key={a.key} data-s={view(a, i)} title={`${i + 1}. ${a.name}`} aria-label={`Jump to ${a.name}`}
                  onClick={() => (live ? goAgent(a.key) : step(i + 1))} />
              ))}
            </div>

            {all.length > 0 && (
              <div className="card funnel-card">
                <div className="funnel-head">
                  <div className="small muted">Requirements through the agents{reg ? ` · ${reg}` : ""} — click a drop-off to list it</div>
                  <div className="lens" role="group" aria-label="Regulation">
                    <button className={reg === null ? "on" : ""} onClick={() => setReg(null)}>All</button>
                    {regs.map((r) => <button key={r} className={reg === r ? "on" : ""} onClick={() => setReg(reg === r ? null : r)}>{r.replace("UN ", "")}</button>)}
                  </div>
                </div>
                <div className="funnel-scroll">
                  <AgentFunnel items={inLens} revealed={(k) => statusOf(k) === "done"} active={activeKey}
                    filter={filter} onFilter={chooseFilter} onAgent={goAgent} />
                </div>
              </div>
            )}

            <ol className="agents-flow">
              {agents.map((a, i) => {
                const s = view(a, i);
                const expanded = open === a.key && s === "done";
                return (
                  <li key={a.key} ref={(el) => { cards.current[a.key] = el; }} className="agent-step" data-s={s} data-kind={a.kind}
                    data-path={path?.[a.key]}>
                    <div className="agent-rail" aria-hidden>
                      <span className="agent-node">{a.kind === "code" ? "{ }" : i + 1}</span>
                      {i < agents.length - 1 && <span className="agent-wire">{s === "done" && <i key={shown ?? -1} />}</span>}
                    </div>
                    <div className="agent-body">
                      <button className="agent-card" onClick={() => setOpen(expanded ? null : a.key)} disabled={s !== "done"}
                        aria-expanded={expanded}>
                        <div className="agent-head">
                          <span className="agent-name">{a.name}</span>
                          <span className="chip">{a.model}</span>
                          {a.calls && a.calls > 1 && <span className="chip">×{a.calls} in parallel</span>}
                          <span className="spacer" />
                          <span className="small muted">{s === "running" ? "working…" : s === "pending" ? "waiting" : s === "failed" ? "failed" : a.seconds ? `${Math.round(a.seconds)} s` : expanded ? "▴" : "▾"}</span>
                        </div>
                        <div className="small muted">{a.role}</div>
                        {s === "running" && a.key === "applicability" && <ParallelTiles n={a.calls ?? 56} />}
                        {s === "done" && a.summary && <div className="agent-summary">{a.summary}</div>}
                      </button>
                      {expanded && <Details a={a} onRegulation={(r) => regs.includes(r) && setReg(r)} />}
                      {s === "done" && a.handoff && <div className="agent-handoff small muted">→ {a.handoff}</div>}
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>

          <aside className="card agents-side" ref={side}>
            <h2 className="agents-side-title">Follow a requirement</h2>
            {journeys.isLoading && <div className="skeleton" style={{ height: 200 }} />}
            {journey && runId ? (
              <AgentTracer runId={runId} journey={journey} agents={agents} rankMin={journeys.data!.rank_min}
                onPick={pickId} onClose={() => setPicked(null)} />
            ) : all.length > 0 && (
              <>
                <input className="input" placeholder="Search text, paragraph or R-id…" value={query} onChange={(e) => setQuery(e.target.value)} />
                <div className="lens tracer-filter" role="group" aria-label="Outcome">
                  <button className={filter === null ? "on" : ""} onClick={() => chooseFilter(null)}>All {inLens.length}</button>
                  <button className={filter === "submitted" ? "on" : ""} onClick={() => chooseFilter("submitted")}>Submitted {inLens.filter((j) => !j.stopped).length}</button>
                  {STOPS.map((s) => {
                    const n = inLens.filter((j) => j.stopped === s.key).length;
                    return n > 0 && <button key={s.key} className={filter === s.key ? "on" : ""} onClick={() => chooseFilter(s.key)}>{s.agent} {n}</button>;
                  })}
                </div>
                <ul className="tracer-list">
                  {matches.slice(0, 60).map((j) => (
                    <li key={j.i}>
                      <button onClick={() => setPicked(j.i)}>
                        <span className="tracer-list-text">{j.text}</span>
                        <span className="small muted">{j.regulation} ¶ {j.paragraph} <span className="chip" data-a={outcome(j).cls}>{outcome(j).label}</span></span>
                      </button>
                    </li>
                  ))}
                </ul>
                {matches.length > 60 && <div className="small muted">{matches.length - 60} more; narrow the search.</div>}
                {matches.length === 0 && <div className="small muted">No requirement matches.</div>}
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function ParallelTiles({ n }: { n: number }) {
  return (
    <div className="agent-tiles working">
      {Array.from({ length: n }, (_, i) => <span key={i} style={{ animationDelay: `${(i * 37) % 900}ms` }} />)}
    </div>
  );
}

function Bar({ label, value, max, note, on = true }: { label: string; value: number; max: number; note?: string; on?: boolean }) {
  return (
    <div className="agent-bar" data-on={on}>
      <span className="agent-bar-label">{label}</span>
      <span className="agent-bar-track"><span style={{ width: `${(100 * value) / Math.max(1, max)}%` }} /></span>
      <span className="small mono">{value}{note ? ` ${note}` : ""}</span>
    </div>
  );
}

function Details({ a, onRegulation }: { a: AgentStep; onRegulation: (r: string) => void }) {
  const d = a.details;
  const [tile, setTile] = useState<any>(null);
  switch (a.key) {
    case "profile":
      return (
        <div className="agent-detail">
          <div><b>{d.product}</b>{d.categories?.length ? ` · vehicle categories ${d.categories.join(", ")}` : ""}</div>
          <div className="agent-chips"><span className="small muted">Functions</span>{d.functions.map((f: string) => <span className="chip" key={f}>{f}</span>)}</div>
          <div className="agent-chips"><span className="small muted">Parts</span>{d.parts.map((p: string) => <span className="chip" key={p}>{p}</span>)}</div>
        </div>
      );
    case "applicability": {
      const order = { yes: 0, maybe: 1, no: 2 } as Record<string, number>;
      const items = [...d.items].sort((x: any, y: any) => order[x.applies] - order[y.applies]);
      return (
        <div className="agent-detail">
          <div className="agent-tiles">
            {d.items.map((it: any) => (
              <button key={it.regulation} data-a={it.applies} className={tile?.regulation === it.regulation ? "on" : ""}
                title={`${it.regulation}: ${it.applies}`} aria-label={`${it.regulation}: ${it.applies}`} onClick={() => setTile(it)} />
            ))}
          </div>
          {tile ? (
            <div className="agent-tile-note"><span className="chip" data-a={tile.applies}>{tile.applies}</span> <b>{tile.regulation}</b> <span className="muted">{tile.basis?.replaceAll("_", " ")}</span><div className="small muted">{tile.reason}</div></div>
          ) : <div className="small muted">Click a tile to read that agent's verdict.</div>}
          <ul className="agent-list">
            {items.filter((it: any) => it.applies !== "no").map((it: any) => (
              <li key={it.regulation}><span className="chip" data-a={it.applies}>{it.applies}</span> <b>{it.regulation}</b> <span className="muted">{it.basis?.replaceAll("_", " ")}</span><div className="small muted">{it.reason}</div></li>
            ))}
          </ul>
          <div className="small muted">{items.filter((it: any) => it.applies === "no").length} regulations judged not to apply, e.g. {items.filter((it: any) => it.applies === "no").slice(0, 6).map((it: any) => it.regulation).join(", ")}.</div>
        </div>
      );
    }
    case "reconciler":
      return (
        <div className="agent-detail">
          {d.superseded.length > 0 && (
            <div><span className="small muted">Replaced by a consolidated regulation (code)</span>
              <ul className="agent-list">{d.superseded.map((s: any) => <li key={s.old}><s>{s.old}</s> → <b>{s.new}</b></li>)}</ul></div>
          )}
          {d.verdicts.length > 0 && (
            <ul className="agent-list">{d.verdicts.map((v: any) => (
              <li key={v.regulation}><span className="chip" data-a={v.include ? "yes" : "no"}>{v.include ? "keep" : "drop"}</span> <b>{v.regulation}</b><div className="small muted">{v.reason}</div></li>
            ))}</ul>
          )}
          <div className="agent-chips"><span className="small muted">Selected</span>{d.selected.map((r: string) => <button className="chip chip-cite" key={r} onClick={() => onRegulation(r)} title="Show only this regulation in the stream">{r}</button>)}</div>
        </div>
      );
    case "sections":
      return (
        <div className="agent-detail">
          {d.regulations.map((r: any) => (
            <div key={r.regulation}>
              <Bar label={r.regulation} value={r.chosen} max={r.total} note={`of ${r.total} sections · ${r.paragraphs} paragraphs`} />
              <div className="small muted agent-examples">{r.examples.join(" · ")}</div>
            </div>
          ))}
        </div>
      );
    case "extractor": {
      const by = Object.entries(d.by_regulation as Record<string, number>).sort((x, y) => y[1] - x[1]);
      const max = Math.max(...by.map(([, v]) => v));
      return (
        <div className="agent-detail">
          {by.map(([r, v]) => <Bar key={r} label={r} value={v} max={max} />)}
          {d.example && (
            <figure className="agent-example">
              <div>{d.example[0]}</div>
              <blockquote>“{d.example[1]}”</blockquote>
              <figcaption className="small muted">{d.example[2]} · the quote is checked against the paragraph in code</figcaption>
            </figure>
          )}
        </div>
      );
    }
    case "grounding":
      return (
        <div className="agent-detail">
          {d.dropped.length === 0 ? <div className="small muted">Every requirement's quote was found in its paragraph.</div> : (
            <ul className="agent-list">{d.dropped.map((u: any, i: number) => (
              <li key={i}><span className="chip" data-a="no">dropped</span> {u.text}<div className="small muted">quote not in the paragraph: “{u.evidence}”</div></li>
            ))}</ul>
          )}
        </div>
      );
    case "critic": {
      const max = Math.max(...d.kinds.map((k: any) => k.count));
      return (
        <div className="agent-detail">
          {d.kinds.map((k: any) => <Bar key={k.kind} label={k.kind.replaceAll("_", " ")} value={k.count} max={max} note={k.kept ? "kept" : "dropped"} on={k.kept} />)}
        </div>
      );
    }
    case "ranker": {
      const max = Math.max(...d.histogram);
      return (
        <div className="agent-detail">
          <div className="agent-hist">
            {d.histogram.map((v: number, i: number) => (
              <div key={i} data-on={i * 10 >= d.cut} title={`${i * 10}–${i * 10 + 9}: ${v}`}>
                <span style={{ height: `${(100 * v) / Math.max(1, max)}%` }} /><small>{i * 10}</small>
              </div>
            ))}
          </div>
          <div className="small muted">Paragraph scores 0–100; bars from {d.cut} up are kept.</div>
        </div>
      );
    }
    case "dedup":
      return <div className="agent-detail"><div className="small muted">{d.final} requirements go into the submission file.</div></div>;
    case "enrich": {
      const sev = Object.entries(d.severity as Record<string, number>);
      const max = Math.max(...sev.map(([, v]) => v));
      return <div className="agent-detail">{sev.map(([k, v]) => <Bar key={k} label={`severity ${k}`} value={v} max={max} />)}</div>;
    }
    default:
      return null;
  }
}
