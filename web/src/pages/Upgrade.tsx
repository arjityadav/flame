import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { ChangeExplorer } from "./Changes";

// What the newer versions of the cited regulations change for this analysis: the sub-goal analysis run with our own
// requirements as the old ones (base → latest version), each change linked to the drawn parts it concerns.

export function Upgrade() {
  const { runId } = useParams();
  const q = useQuery({ queryKey: ["upgrade", runId], queryFn: () => api.upgrade(runId!), staleTime: Infinity });
  const regs = q.data?.regulations ?? [];
  const withLatest = regs.filter((r) => r.latest);
  const [pick, setPick] = useState<string | null>(null);
  const cur = withLatest.find((r) => r.regulation === pick) ?? withLatest[0];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Next versions</h1>
          <p>
            The requirements cite the base version of each regulation. Where the index also has a newer version, the same
            word-by-word comparison as the regulation-change analysis shows which of our requirements it modifies or
            removes, and what it adds in their chapters. Plain code, no model.
          </p>
        </div>
      </div>
      {q.isLoading && <div className="notice"><span className="status-dot live" /> Comparing the base and latest PDFs of every cited regulation (about a minute the first time)…</div>}
      {q.isError && <div className="notice warn">{(q.error as Error).message}</div>}
      {regs.length > 0 && (
        <div className="upg-regs">
          {regs.map((r) => {
            const total = r.counts ? r.counts.modified + r.counts.removed + r.counts.added : 0;
            return (
              <button key={r.regulation} className={`upg-reg${cur?.regulation === r.regulation ? " on" : ""}`} disabled={!r.latest} onClick={() => setPick(r.regulation)}>
                <b>{r.regulation}</b>
                <span className="mono small">{r.latest ? `${r.base} → ${r.latest}` : `${r.base} · no newer version`}</span>
                {r.counts && (
                  <span className="upg-counts small">
                    <span data-k="modified">{r.counts.modified} modified</span> · <span data-k="removed">{r.counts.removed} removed</span> · <span data-k="added">{r.counts.added} added</span>
                  </span>
                )}
                {r.latest && <span className="small muted">{r.requirements_old} of our requirements checked{total === 0 ? ", nothing changes" : ""}</span>}
              </button>
            );
          })}
        </div>
      )}
      {cur && cur.changes && cur.pdf && runId && (
        <ChangeExplorer key={cur.regulation} runId={runId} pdf={cur.pdf}
          d={{ old_version: cur.base!, new_version: cur.latest!, counts: cur.counts!, requirements_old: cur.requirements_old!, changes: cur.changes }} />
      )}
      {q.data && withLatest.length === 0 && <div className="notice">None of the cited regulations has a newer version in the index.</div>}
    </div>
  );
}
