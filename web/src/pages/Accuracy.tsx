import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Accuracy as Acc, type Level } from "../api";

// The run's submission against its case's expected answer (only the worked example has one): regulation, page and
// paragraph level, per regulation, and which expected requirements we found or missed. Plain code, no model.

type Tab = "expected" | "ours";
type Show = "all" | "hit" | "miss";
const pct = (x: number) => `${Math.round(x * 1000) / 10}%`;

export function Accuracy() {
  const { runId } = useParams();
  const q = useQuery({ queryKey: ["accuracy", runId], queryFn: () => api.accuracy(runId!) });
  const a = q.data;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Accuracy</h1>
          <p>
            This analysis against the case's expected answer. Regulation and page level are the organisers' own offline
            check; paragraph level counts an expected requirement as found when we cite its paragraph. The platform
            matches requirement wording with a language model, so its score is lower.
          </p>
        </div>
      </div>
      {q.isLoading && <div className="skeleton" style={{ height: 420 }} />}
      {q.isError && <div className="notice warn">{(q.error as Error).message}</div>}
      {a && !a.case && <div className="notice">There is no expected answer for this analysis. Only the worked example (cases/dev-rear-lamp) comes with one.</div>}
      {a?.case && <Report a={a} runId={runId!} />}
    </div>
  );
}

function Ring({ value, label, level, note }: { value: number; label: string; level?: Level; note?: string }) {
  const r = 30, c = 2 * Math.PI * r;
  return (
    <div className="acc-ring">
      <svg viewBox="0 0 76 76" aria-hidden>
        <circle cx="38" cy="38" r={r} className="acc-ring-bg" />
        <circle cx="38" cy="38" r={r} className="acc-ring-fg" strokeDasharray={`${c * value} ${c}`} transform="rotate(-90 38 38)" />
      </svg>
      <div>
        <b>{pct(value)}</b>
        <span>{label}</span>
        {level && <span className="small muted">precision {pct(level.precision)} · recall {pct(level.recall)}</span>}
        {note && <span className="small muted">{note}</span>}
      </div>
    </div>
  );
}

function Report({ a, runId }: { a: Acc; runId: string }) {
  const [tab, setTab] = useState<Tab>("expected");
  const [show, setShow] = useState<Show>("all");
  const [reg, setReg] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const ours = useMemo(() => new Map(a.ours.map((o) => [o.id, o])), [a]);
  const max = Math.max(...a.per_regulation.map((r) => Math.max(r.expected, r.ours)));

  const rows = useMemo(() => {
    const t = query.trim().toLowerCase();
    const base = tab === "expected"
      ? a.expected.map((e) => ({ id: e.id, text: e.text, source: e.source, hit: e.found, e }))
      : a.ours.map((o) => ({ id: o.id, text: o.text, source: o.source, hit: o.on_expected, e: null }));
    return base.filter((r) => (!reg || r.source.regulation === reg) && (show === "all" || (show === "hit") === r.hit)
      && (!t || `${r.id} ${r.text} ${r.source.paragraph}`.toLowerCase().includes(t)));
  }, [a, tab, show, reg, query]);

  return (
    <>
      <div className="acc-rings">
        <Ring value={a.regulation_level.f2} label="F2 · regulations" level={a.regulation_level} />
        <Ring value={a.page_level.f2} label="F2 · pages" level={a.page_level} />
        <Ring value={a.paragraph_level.f2} label="F2 · paragraphs" level={a.paragraph_level} />
        <Ring value={a.found / a.n_expected} label="expected requirements found"
          note={`${a.found} of ${a.n_expected} have their paragraph cited`} />
      </div>

      <div className="card acc-regs">
        <div className="small muted">Per regulation (click to filter): top bar is the expected requirements, green where we found them; bottom bar is ours, dark blue where they sit on an expected paragraph.</div>
        {a.per_regulation.map((r) => (
          <button key={r.regulation} className={`acc-reg${reg === r.regulation ? " on" : ""}`} onClick={() => setReg(reg === r.regulation ? null : r.regulation)}>
            <span className="acc-reg-name">{r.regulation}</span>
            <span className="acc-reg-bars">
              <span className="acc-bar exp" style={{ width: `${(100 * r.expected) / max}%` }}><i style={{ width: `${(100 * r.found) / Math.max(1, r.expected)}%` }} /></span>
              <span className="acc-bar ours" style={{ width: `${(100 * r.ours) / max}%` }}><i style={{ width: `${(100 * r.ours_on_expected) / Math.max(1, r.ours)}%` }} /></span>
            </span>
            <span className="small acc-reg-n">
              found <b>{r.found}</b>/{r.expected} expected<br />
              <b>{r.ours_on_expected}</b>/{r.ours} of ours on them
            </span>
          </button>
        ))}
      </div>

      <div className="card acc-list-card">
        <div className="acc-toolbar">
          <div className="seg" role="tablist">
            <button className={tab === "expected" ? "on" : ""} onClick={() => { setTab("expected"); setShow("all"); }}>Expected {a.n_expected}</button>
            <button className={tab === "ours" ? "on" : ""} onClick={() => { setTab("ours"); setShow("all"); }}>Ours {a.n_ours}</button>
          </div>
          <div className="lens">
            <button className={show === "all" ? "on" : ""} onClick={() => setShow("all")}>All</button>
            <button className={show === "hit" ? "on" : ""} onClick={() => setShow("hit")}>{tab === "expected" ? "Found" : "On an expected paragraph"}</button>
            <button className={show === "miss" ? "on" : ""} onClick={() => setShow("miss")}>{tab === "expected" ? "Missed" : "Extra"}</button>
          </div>
          {reg && <button className="chip chip-btn" onClick={() => setReg(null)}>{reg} ✕</button>}
          <input className="input search" placeholder="Search text, id or paragraph…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <span className="small muted">{rows.length} shown</span>
        </div>
        <ul className="acc-list">
          {rows.slice(0, 200).map((r) => {
            const isOpen = open === `${tab}-${r.id}`;
            return (
              <li key={r.id} data-hit={r.hit}>
                <button className="acc-row" onClick={() => setOpen(isOpen ? null : `${tab}-${r.id}`)} aria-expanded={isOpen}>
                  <span className="acc-mark" aria-hidden>{r.hit ? "✓" : "✕"}</span>
                  <span className="acc-row-text">{r.text}</span>
                  <span className="small muted acc-row-src">{r.source.regulation} ¶ {r.source.paragraph} · p.{r.source.page}</span>
                  {r.e && r.e.similarity !== null && <span className="chip" title="Wording similarity of our closest requirement on this paragraph">{r.e.similarity}%</span>}
                </button>
                {isOpen && (
                  <div className="acc-open">
                    {r.e ? (r.e.ours.length ? (
                      <>
                        <div className="small muted">Ours on paragraph {r.source.paragraph}:</div>
                        {r.e.ours.map((id) => (
                          <div key={id} className={`acc-ours${id === r.e!.closest ? " best" : ""}`}>
                            <Link className="chip chip-cite" to={`/runs/${runId}?r=${id}`}>{id}</Link> {ours.get(id)?.text}
                          </div>
                        ))}
                      </>
                    ) : <div className="small muted">We cite nothing on paragraph {r.source.paragraph} of {r.source.regulation}.</div>)
                      : <div className="small">{r.hit ? "Its paragraph is in the expected answer." : "Its paragraph is not in the expected answer: it lowers precision unless the platform's judge matches it to an expected requirement."}{" "}
                        <Link to={`/runs/${runId}?r=${r.id}`}>Open in Requirements</Link></div>}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {rows.length > 200 && <div className="small muted">{rows.length - 200} more; filter to narrow.</div>}
      </div>
    </>
  );
}
