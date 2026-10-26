import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Run } from "../api";
import * as I from "../components/Icons";

export function RunProgress({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [run, setRun] = useState<Run | null>(null);

  useEffect(() => {
    const es = new EventSource(`/api/runs/${runId}/events`);
    const refresh = () => api.run(runId).then(setRun);
    es.addEventListener("meta", (e) => setRun(JSON.parse((e as MessageEvent).data)));
    es.addEventListener("stage", refresh);
    es.addEventListener("done", () => {
      refresh();
      qc.invalidateQueries({ queryKey: ["runs"] });
      es.close();
    });
    es.addEventListener("error", () => refresh());
    return () => es.close();
  }, [runId, qc]);

  if (!run) return <div className="skeleton" style={{ height: 320 }} />;
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="progress" aria-live="polite">
        {run.stages.map((s, i) => (
          <div key={s.key} className={`step ${s.status}`}>
            <span className="idx">{s.status === "done" ? "✓" : i + 1}</span>
            <div>
              <div style={{ fontWeight: 500 }}>{s.label}</div>
              {s.detail && <div className="detail">{s.detail}</div>}
            </div>
            <span className="small muted">{s.seconds != null ? `${s.seconds}s` : s.status === "running" ? "working…" : ""}</span>
          </div>
        ))}
      </div>
      {run.status === "failed" && <div className="notice warn">{run.error}</div>}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        {run.status === "done" ? (
          <button className="btn btn-primary" onClick={() => navigate(`/runs/${run.id}`)}>
            See the requirements
          </button>
        ) : (
          <span className="muted">This takes about 4–5 minutes. You can leave this page; the run continues.</span>
        )}
        {run.cost_usd > 0 && <span className="small muted">${run.cost_usd.toFixed(2)} so far</span>}
      </div>
    </div>
  );
}

export function NewAnalysis() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });
  const [spec, setSpec] = useState<File | null>(null);
  const [drawings, setDrawings] = useState<File[]>([]);
  const [specText, setSpecText] = useState("");
  const [title, setTitle] = useState("");
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function take(files: FileList | null) {
    if (!files) return;
    for (const f of Array.from(files)) {
      if (/\.(png|jpe?g)$/i.test(f.name)) setDrawings((d) => [...d, f]);
      else if (/\.(md|txt|pdf|markdown)$/i.test(f.name)) setSpec(f);
      else setError(`${f.name}: use a .md, .txt or .pdf spec and .png or .jpg drawings.`);
    }
  }

  async function submit() {
    setError(null);
    setSending(true);
    const form = new FormData();
    form.append("title", title);
    form.append("spec_text", specText);
    if (spec) form.append("spec", spec);
    drawings.forEach((d) => form.append("drawings", d));
    try {
      const run = await api.createRun(form);
      if (run.status === "failed") setError(run.error);
      else navigate(`/new/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The upload failed.");
    } finally {
      setSending(false);
    }
  }

  if (runId)
    return (
      <div className="page page-narrow" style={{ maxWidth: 820 }}>
        <div className="page-head">
          <div>
            <h1>Analysing your part</h1>
            <p>Seven agents read the specification and drawing, choose the regulations and extract every requirement with its source.</p>
          </div>
        </div>
        <RunProgress runId={runId} />
      </div>
    );

  return (
    <div className="page page-narrow" style={{ maxWidth: 820 }}>
      <div className="page-head">
        <div>
          <h1>New analysis</h1>
          <p>Add the part's specification and its exploded-view drawing. Flame returns every UN Regulation requirement it must meet, cited to the page.</p>
        </div>
      </div>
      {status.data && !status.data.model && (
        <div className="notice warn">No model is configured on this server, so new analyses can't run here. Finished analyses are under Runs.</div>
      )}
      {status.data?.busy && <div className="notice">Another analysis is running. It must finish before a new one starts.</div>}
      <div className="upload">
        <label className="small muted" htmlFor="title">
          Name (optional)
        </label>
        <input id="title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Front bumper, 2027 hatchback" />
        <div
          className={`drop${over ? " over" : ""}`}
          role="button"
          tabIndex={0}
          aria-label="Add files: specification and drawings"
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            take(e.dataTransfer.files);
          }}
        >
          <I.Upload size={28} />
          <div>
            <b>Drop the spec and drawing here</b>
            <div className="small muted">Spec: .md, .txt or .pdf · Drawing: .png or .jpg · up to 20 MB each</div>
          </div>
          <div className="files">
            {spec && <span className="chip">{spec.name}</span>}
            {drawings.map((d) => (
              <span key={d.name} className="chip">
                {d.name}
              </span>
            ))}
          </div>
          <input ref={input} type="file" multiple hidden accept=".md,.txt,.pdf,.markdown,.png,.jpg,.jpeg" onChange={(e) => take(e.target.files)} />
        </div>
        <label className="small muted" htmlFor="spectext">
          …or paste the specification
        </label>
        <textarea id="spectext" value={specText} onChange={(e) => setSpecText(e.target.value)} placeholder="# spec: Front bumper …" />
        {error && <div className="notice warn">{error}</div>}
        <div>
          <button className="btn btn-primary" disabled={sending || (!spec && !specText.trim() && !drawings.length) || !!status.data?.busy} onClick={submit}>
            {sending ? "Uploading…" : "Analyse"}
          </button>
        </div>
      </div>
    </div>
  );
}
