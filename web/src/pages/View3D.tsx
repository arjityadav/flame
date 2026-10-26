import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { Exploded3D } from "../components/Exploded3D";

export function View3D() {
  const { runId } = useParams();
  const qc = useQueryClient();
  const parts = useQuery({ queryKey: ["parts", runId], queryFn: () => api.parts(runId!), retry: false });
  const reqs = useQuery({ queryKey: ["requirements", runId], queryFn: () => api.requirements(runId!) });
  const detect = useMutation({
    mutationFn: () => api.detectParts(runId!),
    onSuccess: (data) => qc.setQueryData(["parts", runId], data),
  });
  const [search] = useSearchParams();
  const [exploded, setExploded] = useState(search.get("mode") !== "assembled");
  const [autoRotate, setAutoRotate] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [resetKey, setResetKey] = useState(0);

  const data = parts.data;
  const code = parts.error instanceof ApiError ? parts.error.code : undefined;
  const byId = useMemo(() => new Map((reqs.data ?? []).map((r) => [r.id, r])), [reqs.data]);
  const focus = selected ?? hovered;
  const part = data && focus !== null ? data.parts[focus] : null;
  const partReqs = part ? part.requirements.map((id) => byId.get(id)).filter((r) => r !== undefined) : [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>3D view</h1>
          <p>The drawing's own parts as layers in 3D. Drag to orbit, scroll to zoom, click a part to see its requirements.</p>
        </div>
        {data && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn btn-primary btn-sm" onClick={() => setExploded((e) => !e)}>
              {exploded ? "Assemble" : "Explode"}
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => setAutoRotate((a) => !a)} aria-pressed={autoRotate}>
              {autoRotate ? "Stop rotating" : "Rotate"}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setResetKey((k) => k + 1)}>Reset view</button>
          </div>
        )}
      </div>

      {parts.isLoading && <div className="skeleton viewer3d-skeleton" />}
      {code === "no_drawing" && <div className="notice">This analysis has no exploded-view drawing, so there is nothing to show in 3D.</div>}
      {code === "parts_not_detected" && (
        <div className="card" style={{ display: "grid", gap: 12, justifyItems: "start" }}>
          <div>The parts of this drawing have not been separated yet.</div>
          <div className="small muted">Claude finds each part and its order along the assembly axis (about $0.07, 30 seconds); then each part's lines are cut out of the drawing.</div>
          <button className="btn btn-primary" onClick={() => detect.mutate()} disabled={detect.isPending}>
            {detect.isPending ? "Separating parts…" : "Separate the parts"}
          </button>
          {detect.isError && <div className="notice warn">{(detect.error as Error).message}</div>}
        </div>
      )}
      {parts.isError && !code && <div className="notice warn">{(parts.error as Error).message}</div>}

      {data && runId && (
        <div className="view3d-layout">
          <div className="card" style={{ padding: 0, overflow: "hidden" }}>
            <Exploded3D runId={runId} data={data} exploded={exploded} autoRotate={autoRotate && selected === null}
              selected={selected} onSelect={setSelected} onHover={setHovered} resetKey={resetKey} />
          </div>
          <aside className="card view3d-side">
            <div className="small muted">Parts, front to back</div>
            <ol className="view3d-parts">
              {data.parts.map((p, i) => (
                <li key={i}>
                  <button className={`view3d-part${selected === i ? " active" : ""}`} onClick={() => setSelected(selected === i ? null : i)}
                    onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)}>
                    <span>{p.name}</span>
                    <span className="small muted">{p.requirements.length}</span>
                  </button>
                </li>
              ))}
            </ol>
            {part ? (
              <div style={{ marginTop: 16 }}>
                <h2 style={{ fontSize: "var(--t-20)" }}>{part.name}</h2>
                {part.callout && <p className="small muted" style={{ marginTop: 4 }}>Drawing note: {part.callout}</p>}
                <div className="small muted" style={{ marginTop: 12 }}>{partReqs.length} requirements concern this part</div>
                <ul className="view3d-reqs">
                  {partReqs.slice(0, 40).map((r) => (
                    <li key={r!.id}>
                      <Link to={`/runs/${runId}?r=${r!.id}`}>{r!.summary || r!.text}</Link>
                      <div className="small muted">{r!.source.regulation} {r!.source.paragraph} · p.{r!.source.page}</div>
                    </li>
                  ))}
                </ul>
                {partReqs.length > 40 && <div className="small muted">…and {partReqs.length - 40} more in Requirements.</div>}
              </div>
            ) : (
              <p className="small muted" style={{ marginTop: 16 }}>Hover or click a part to see the requirements that concern it.</p>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
