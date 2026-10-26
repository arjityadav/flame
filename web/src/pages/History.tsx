import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { auth, type HistoryEvent } from "../api";
import * as I from "../components/Icons";

// What the signed-in user did: their analyses, questions, exports and sign-ins, newest first, grouped by day.

const FILTERS: { key: string; label: string }[] = [
  { key: "", label: "Everything" },
  { key: "run_created", label: "Analyses" },
  { key: "ask", label: "Questions" },
  { key: "export", label: "Exports" },
  { key: "login,signup,logout", label: "Sign-ins" },
  { key: "profile_updated,password_changed,sessions_revoked", label: "Account" },
];

function describe(e: HistoryEvent): { icon: JSX.Element; text: JSX.Element; link?: string } {
  const run = e.run_id ? <Link to={`/runs/${e.run_id}`}>{e.run_title ?? e.run_id}</Link> : null;
  switch (e.type) {
    case "run_created": return { icon: <I.Plus size={16} />, text: <>Started an analysis: {run}</>, link: e.run_id ? `/runs/${e.run_id}/agents` : undefined };
    case "ask": return { icon: <I.Chat size={16} />, text: <>Asked <q>{e.detail.question}</q>{run && <> about {run}</>}</>, link: e.run_id ? `/runs/${e.run_id}/ask` : undefined };
    case "export": return { icon: <I.Download size={16} />, text: <>Downloaded {e.detail.format === "csv" ? "the CSV" : e.detail.format === "changes.json" ? <>the regulation-change file ({e.detail.case})</> : "submission.json"}{run && <> of {run}</>}</> };
    case "parts_detect": return { icon: <I.Cube size={16} />, text: <>Separated the drawing's parts for the 3D view of {run}</>, link: e.run_id ? `/runs/${e.run_id}/3d` : undefined };
    case "login": return { icon: <I.Agents size={16} />, text: <>Signed in</> };
    case "signup": return { icon: <I.Spark size={16} />, text: <>Created the account</> };
    case "logout": return { icon: <I.Close size={16} />, text: <>Signed out</> };
    case "profile_updated": return { icon: <I.Doc size={16} />, text: <>Updated the profile{e.detail.fields?.length ? ` (${e.detail.fields.join(", ")})` : ""}</> };
    case "password_changed": return { icon: <I.Target size={16} />, text: <>Changed the password</> };
    case "sessions_revoked": return { icon: <I.Close size={16} />, text: <>Signed out of {e.detail.count ?? 0} other device(s)</> };
    default: return { icon: <I.Clock size={16} />, text: <>{e.type}</> };
  }
}

const day = (iso: string) => {
  const d = new Date(iso), today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
};

export function History() {
  const [search, setSearch] = useSearchParams();
  const type = search.get("type") ?? "";
  const [query, setQuery] = useState("");
  const [confirm, setConfirm] = useState(false);
  const qc = useQueryClient();
  const mine = useQuery({ queryKey: ["my-runs"], queryFn: auth.myRuns });
  const h = useInfiniteQuery({
    queryKey: ["history", type],
    queryFn: ({ pageParam }) => auth.history(type, pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next ?? undefined,
  });
  const clear = useMutation({
    mutationFn: auth.clearHistory,
    onSuccess: () => { setConfirm(false); qc.invalidateQueries({ queryKey: ["history"] }); qc.invalidateQueries({ queryKey: ["me"] }); },
  });

  const events = useMemo(() => {
    const all = (h.data?.pages ?? []).flatMap((p) => p.events);
    const q = query.trim().toLowerCase();
    return q ? all.filter((e) => JSON.stringify([e.type, e.run_title, e.detail]).toLowerCase().includes(q)) : all;
  }, [h.data, query]);
  const groups = useMemo(() => {
    const out: [string, HistoryEvent[]][] = [];
    for (const e of events) {
      const d = day(e.ts);
      if (out.length && out[out.length - 1][0] === d) out[out.length - 1][1].push(e);
      else out.push([d, [e]]);
    }
    return out;
  }, [events]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>History</h1>
          <p>Your analyses and everything you did in Flame, newest first. Only you see this.</p>
        </div>
        {confirm ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="small">Delete your whole history?</span>
            <button className="btn btn-primary btn-sm" onClick={() => clear.mutate()} disabled={clear.isPending}>Delete</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setConfirm(false)}>Cancel</button>
          </div>
        ) : (
          <button className="btn btn-secondary btn-sm" onClick={() => setConfirm(true)} disabled={!events.length}>Clear history</button>
        )}
      </div>

      {(mine.data?.length ?? 0) > 0 && (
        <section className="hist-runs">
          <h3 className="chg-h">Your analyses</h3>
          <div className="hist-run-grid">
            {mine.data!.map((r) => (
              <Link key={r.id} to={`/runs/${r.id}`} className="card hist-run">
                <span className={`status-dot ${r.status === "running" ? "live" : r.status === "done" ? "ok" : r.status === "failed" ? "bad" : ""}`} />
                <b>{r.title}</b>
                <span className="small muted">{new Date(r.created).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })} · {r.status === "done" ? `${r.counts.requirements ?? 0} requirements` : r.status}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <div className="hist-toolbar">
        <div className="lens">
          {FILTERS.map((f) => (
            <button key={f.key} className={type === f.key ? "on" : ""} onClick={() => setSearch(f.key ? { type: f.key } : {})}>{f.label}</button>
          ))}
        </div>
        <input className="input search" placeholder="Search your history…" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>

      {h.isLoading && <div className="skeleton" style={{ height: 300 }} />}
      {h.isError && <div className="notice warn">{(h.error as Error).message}</div>}
      {h.data && events.length === 0 && <div className="notice">Nothing here yet. Start an analysis, ask a question or download a file, and it shows up here.</div>}

      {groups.map(([d, list]) => (
        <section key={d} className="hist-day">
          <h3 className="chg-h">{d}</h3>
          <ol className="hist-list">
            {list.map((e) => {
              const x = describe(e);
              return (
                <li key={e.id} data-t={e.type}>
                  <span className="hist-icon" aria-hidden>{x.icon}</span>
                  <div className="hist-text">{x.text}</div>
                  <time className="small muted" dateTime={e.ts}>{new Date(e.ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</time>
                  {x.link && <Link className="btn btn-ghost btn-sm" to={x.link}>Open</Link>}
                </li>
              );
            })}
          </ol>
        </section>
      ))}
      {h.hasNextPage && (
        <button className="btn btn-secondary btn-sm" onClick={() => h.fetchNextPage()} disabled={h.isFetchingNextPage}>
          {h.isFetchingNextPage ? "Loading…" : "Show older"}
        </button>
      )}
    </div>
  );
}
