import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { api, type Change, type ChangeAnalysis, type ChangeKind } from "../api";
import { PdfViewer } from "../components/PdfViewer";
import { wordDiff, type Seg } from "../diff";

// Regulation change (sub-goal): which of the old requirements a new version of the regulation modifies or removes,
// and which new requirements it adds, each with the paragraphs side by side and the changed words marked.

type Filter = ChangeKind | "missed" | null;
const KINDS: ChangeKind[] = ["modified", "removed", "added"];

export function ChangesIndex() {
  const cases = useQuery({ queryKey: ["change-cases"], queryFn: api.changeCases });
  if (cases.isLoading) return <div className="page"><div className="skeleton" style={{ height: 400 }} /></div>;
  if (!cases.data?.length) return <div className="page"><div className="notice">No regulation-change case found under subgoal/.</div></div>;
  return <Navigate to={`/changes/${cases.data[0].case}`} replace />;
}

export function Changes() {
  const { caseId } = useParams();
  const nav = useNavigate();
  const cases = useQuery({ queryKey: ["change-cases"], queryFn: api.changeCases });
  const data = useQuery({ queryKey: ["changes", caseId], queryFn: () => api.changes(caseId!), staleTime: Infinity });
  const d = data.data;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Regulation change</h1>
          <p>
            {d ? <>{d.regulation} <b>{d.old_version}</b> → <b>{d.new_version}</b>: which of the {d.requirements_old} old requirements the new version
              modifies or removes, and what it adds. Plain code, no model: the paragraphs of both versions are aligned and compared word by word.</> : "Loading…"}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {(cases.data?.length ?? 0) > 1 && (
            <select className="select" value={caseId} onChange={(e) => nav(`/changes/${e.target.value}`)} aria-label="Case">
              {cases.data!.map((c) => <option key={c.case} value={c.case}>{c.case_id}</option>)}
            </select>
          )}
          {d && <a className="btn btn-primary btn-sm" href={`/api/changes/${caseId}/export`}>Download changes.json</a>}
        </div>
      </div>

      {data.isLoading && <div className="skeleton" style={{ height: 420 }} />}
      {data.isError && <div className="notice warn">{(data.error as Error).message}</div>}
      {d && <ChangeExplorer key={caseId} d={d} pdf={{ old: `/api/changes/${caseId}/pdf/old`, new: `/api/changes/${caseId}/pdf/new` }} />}
    </div>
  );
}

type Explorable = Pick<ChangeAnalysis, "old_version" | "new_version" | "counts" | "requirements_old" | "changes"> & { check?: ChangeAnalysis["check"] };

/** Tiles, the list of changes and the selected change side by side; shared by the sub-goal case and a run's upgrade view. */
export function ChangeExplorer({ d, pdf, runId }: { d: Explorable; pdf: { old: string; new: string }; runId?: string }) {
  const [filter, setFilter] = useState<Filter>(null);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const missed = d.check?.missed ?? [];
  const list = useMemo(() => {
    const base: (Change & { missed?: boolean })[] = filter === "missed" ? missed.map((m) => ({ ...m, missed: true }))
      : d.changes.filter((c) => !filter || c.change === filter);
    const q = query.trim().toLowerCase();
    return q ? base.filter((c) => `${c.requirement_id ?? ""} ${c.old_requirement ?? ""} ${c.new_text ?? ""} ${c.reason}`.toLowerCase().includes(q)) : base;
  }, [d, filter, query, missed]);
  const cur = list[Math.min(sel, list.length - 1)];

  useEffect(() => setSel(0), [filter, query]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); setSel((s) => Math.min(list.length - 1, s + 1)); }
      if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [list.length]);
  useEffect(() => { document.querySelector(".chg-list .active")?.scrollIntoView({ block: "nearest" }); }, [sel, filter]);

  return (
    <>
      <div className="chg-tiles">
        <button className={`chg-tile${filter === null ? " on" : ""}`} onClick={() => setFilter(null)}>
          <b>{d.changes.length}</b><span>changes</span>
        </button>
        {KINDS.map((k) => (
          <button key={k} className={`chg-tile${filter === k ? " on" : ""}`} data-k={k} onClick={() => setFilter(filter === k ? null : k)}>
            <b>{d.counts[k]}</b><span>{k}</span>
          </button>
        ))}
        <div className="chg-tile static"><b>{d.requirements_old - d.counts.modified - d.counts.removed}</b><span>{runId ? "of our requirements unchanged" : "old requirements unchanged"}</span></div>
        {d.check && (
          <button className={`chg-tile check${filter === "missed" ? " on" : ""}`} onClick={() => setFilter(filter === "missed" ? null : "missed")}
            title="Our offline check against the case's expected answer; click to list what we missed">
            <b>{Math.round(d.check.recall * 1000) / 10}%</b>
            <span>of the expected answer found ({d.check.expected - missed.length}/{d.check.expected}) · {missed.length} missed</span>
            <span className="chg-meter"><i style={{ width: `${d.check.recall * 100}%` }} /></span>
          </button>
        )}
      </div>

      {d.changes.length === 0 ? <div className="notice">The new version changes none of these requirements and adds nothing in their chapters.</div> : (
        <div className="chg-layout">
          <aside className="card chg-side">
            <input className="input" placeholder="Search requirement, R-id or reason…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <ul className="chg-list" role="listbox" aria-label="Changes">
              {list.map((c, i) => (
                <li key={`${c.change}-${c.requirement_id ?? c.new?.paragraph}-${i}`}>
                  <button className={i === sel ? "active" : ""} onClick={() => setSel(i)} role="option" aria-selected={i === sel}>
                    <span className="chg-row-head">
                      <span className="chg-badge" data-k={c.change}>{c.change}</span>
                      <span className="mono small">{c.requirement_id ?? `¶ ${c.new?.paragraph ?? ""}`}</span>
                      {c.verdict && <span className={`chg-verdict ${c.verdict}`} title={verdictText(c.verdict)}>{c.verdict === "correct" ? "✓" : "✕"}</span>}
                      {"missed" in c && <span className="chg-verdict wrong_type" title="In the expected answer, not found by us">missed</span>}
                    </span>
                    <span className="chg-row-text">{c.new_text ?? c.old_requirement}</span>
                  </button>
                </li>
              ))}
              {list.length === 0 && <li className="small muted" style={{ padding: 12 }}>Nothing matches.</li>}
            </ul>
            <div className="small muted">↑ ↓ or j k to move through the list</div>
          </aside>

          {cur ? <Detail key={`${filter}-${sel}-${cur.requirement_id}`} c={cur} pdf={pdf} runId={runId} newVersion={d.new_version} oldVersion={d.old_version} /> : <div />}
        </div>
      )}
    </>
  );
}

/** a word diff reads well only when most words stayed in place; a reworded sentence is clearer old over new */
function similar(segs: Seg[]) {
  const words = (op: Seg["op"]) => segs.filter((s) => s.op === op).reduce((n, s) => n + s.text.split(" ").length, 0);
  const same = words("same");
  return same / Math.max(1, same + Math.max(words("del"), words("ins"))) >= 0.7;
}

function verdictText(v: string) {
  return v === "correct" ? "Matches the expected answer" : v === "wrong_type" ? "Flagged, but the expected change type differs" : "Not in the expected answer";
}

function Words({ segs, side }: { segs: Seg[]; side: "old" | "new" | "both" }) {
  const shown = segs.filter((s) => s.op === "same" || (s.op === "del" ? side !== "new" : side !== "old"));
  return (
    <>
      {shown.map((s, i) => (
        <span key={i}>{s.op === "same" ? s.text : s.op === "del" ? <del>{s.text}</del> : <ins>{s.text}</ins>}{i < shown.length - 1 ? " " : ""}</span>
      ))}
    </>
  );
}

function Detail({ c, pdf: urls, runId, oldVersion, newVersion }: { c: Change & { missed?: boolean }; pdf: { old: string; new: string }; runId?: string; oldVersion: string; newVersion: string }) {
  const [mode, setMode] = useState<"side" | "inline">("side");
  const [pdf, setPdf] = useState<"old" | "new" | null>(null);
  const reqDiff = useMemo(() => (c.old_requirement && c.new_text ? wordDiff(c.old_requirement, c.new_text) : null), [c]);
  const paraDiff = useMemo(() => (c.old?.text && c.new?.text ? wordDiff(c.old.text, c.new.text) : null), [c]);
  const pdfPage = pdf === "old" ? c.old?.page : c.new?.page;

  return (
    <section className="card chg-detail" aria-live="polite">
      <div className="chg-detail-head">
        <span className="chg-badge" data-k={c.change}>{c.change}</span>
        {c.requirement_id && <span className="mono">{c.requirement_id}</span>}
        {c.verdict && <span className={`chg-verdict ${c.verdict}`}>{verdictText(c.verdict)}</span>}
        {c.missed && <span className="chg-verdict wrong_type">In the expected answer; our analysis missed it</span>}
      </div>
      <p className="chg-reason">{c.reason}</p>
      {runId && (c.requirement_id || (c.parts?.length ?? 0) > 0) && (
        <div className="agent-chips">
          {c.requirement_id && <Link className="chip chip-cite" to={`/runs/${runId}?r=${c.requirement_id}`}>{c.requirement_id} in Requirements</Link>}
          {(c.parts ?? []).map((p) => <Link key={p} className="chip" to={`/runs/${runId}/3d`}>part: {p}</Link>)}
        </div>
      )}
      {(c.edits ?? []).length > 0 && (
        <div className="chg-edits">
          {c.edits.map((e, i) => (
            <span key={i} className="chg-edit">
              {"from" in e ? <><del>{e.from}</del> → <ins>{e.to}</ins></> : "added" in e ? <>+ <ins>{e.added}</ins></> : <>− <del>{e.removed}</del></>}
            </span>
          ))}
        </div>
      )}

      <h3 className="chg-h">Requirement</h3>
      {c.change === "modified" && reqDiff && similar(reqDiff) ? (
        <p className="chg-text"><Words segs={reqDiff} side="both" /></p>
      ) : c.change === "modified" ? (
        <div className="chg-stack">
          <div><span className="small muted">Old</span><p className="chg-text"><del>{c.old_requirement}</del></p></div>
          <div><span className="small muted">New</span><p className="chg-text"><ins>{c.new_text}</ins></p></div>
        </div>
      ) : c.change === "removed" ? (
        <p className="chg-text"><del>{c.old_requirement}</del></p>
      ) : (
        <p className="chg-text"><ins>{c.new_text}</ins></p>
      )}
      {c.expected_text && c.expected_text !== c.new_text && (
        <div className="chg-expected small"><span className="muted">Expected answer's wording:</span> {c.expected_text}</div>
      )}

      {(c.old?.text || c.new?.text) && (
        <>
          <div className="chg-h-row">
            <h3 className="chg-h">Paragraph {c.new?.paragraph ?? c.old?.paragraph}</h3>
            {paraDiff && (
              <div className="seg" role="group" aria-label="Diff layout">
                <button className={mode === "side" ? "on" : ""} onClick={() => setMode("side")}>Side by side</button>
                <button className={mode === "inline" ? "on" : ""} onClick={() => setMode("inline")}>Inline</button>
              </div>
            )}
          </div>
          {paraDiff && mode === "inline" ? (
            <p className="chg-para"><Words segs={paraDiff} side="both" /></p>
          ) : (
            <div className="chg-sides">
              <div>
                <div className="small muted">Old · version {oldVersion}{c.old?.page ? ` · p.${c.old.page}` : ""}</div>
                <p className="chg-para">{c.old?.text ? (paraDiff ? <Words segs={paraDiff} side="old" /> : c.old.text) : <span className="muted">Not in the old version.</span>}</p>
              </div>
              <div>
                <div className="small muted">New · version {newVersion}{c.new?.page ? ` · p.${c.new.page}` : ""}</div>
                <p className="chg-para">{c.new?.text ? (paraDiff ? <Words segs={paraDiff} side="new" /> : c.new.text) : <span className="muted">No longer in the new version.</span>}</p>
              </div>
            </div>
          )}
        </>
      )}

      <div className="chg-pdf-bar">
        {c.old?.page && <button className={`btn btn-sm ${pdf === "old" ? "btn-primary" : "btn-secondary"}`} onClick={() => setPdf(pdf === "old" ? null : "old")}>Old PDF p.{c.old.page}</button>}
        {c.new?.page && <button className={`btn btn-sm ${pdf === "new" ? "btn-primary" : "btn-secondary"}`} onClick={() => setPdf(pdf === "new" ? null : "new")}>New PDF p.{c.new.page}</button>}
      </div>
      {pdf && pdfPage && <div className="chg-pdf"><PdfViewer url={urls[pdf]} pages={[pdfPage]} boxes={[]} /></div>}
    </section>
  );
}
