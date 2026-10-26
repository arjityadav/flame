import { useQuery } from "@tanstack/react-query";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Sigma from "sigma";
import { api, type GraphNode, type GraphPayload } from "../api";
import { PhaseChip, SeverityChip } from "../components/Chips";
import * as I from "../components/Icons";
import { SourcePanel } from "../components/SourcePanel";

const SEV = { 5: "#ff6b5e", 4: "#ff9a5c", 3: "#e8c547", 2: "#b4b6b9", 1: "#8e8e8e" } as Record<number, string>;
const REG_TINTS = ["#6f8fe8", "#9aa7c7", "#7fb7a8", "#c79ad1", "#d1b07f", "#8fb3d9", "#b5b5b5"];
const EDGE = { child: "#2a2d33", refers: "#ff7a2f", cites_reg: "#a2a3a5", similar: "#4a5570" } as Record<string, string>;

type Hover = { node: GraphNode; x: number; y: number } | null;

function build(payload: GraphPayload, showOther: boolean, hiddenRegs: Set<string>) {
  const g = new Graph({ multi: false, type: "directed" });
  const tint = Object.fromEntries(payload.regulations.map((r, i) => [r, REG_TINTS[i % REG_TINTS.length]]));
  for (const n of payload.nodes) {
    if (hiddenRegs.has(n.regulation)) continue;
    const isReq = !!n.requirement_ids?.length;
    if (n.kind === "paragraph" && !isReq && !showOther) continue;
    const size = n.kind === "regulation" ? 14 : n.kind === "section" ? 5 : isReq ? 4 + 1.5 * Math.min(n.requirement_ids!.length, 4) : 2.2;
    const color = n.kind === "regulation" ? "#ffffff" : n.kind === "section" ? tint[n.regulation] : isReq ? SEV[n.severity ?? 3] : "#3b3f47";
    g.addNode(n.id, {
      x: n.x,
      y: n.y,
      size,
      color,
      label: n.kind === "paragraph" ? (isReq ? `${n.label}` : "") : n.kind === "regulation" ? `${n.label}` : n.label,
      forceLabel: n.kind === "regulation",
      zIndex: n.kind === "regulation" ? 3 : isReq ? 2 : 0,
      data: n,
    });
  }
  // A hidden parent paragraph would cut its children loose; climb to the nearest node that is shown instead.
  const parent = new Map(payload.edges.filter((e) => e.kind === "child").map((e) => [e.source, e.target]));
  const visibleAncestor = (id: string) => {
    let cur: string | undefined = id;
    for (let i = 0; i < 12 && cur && !g.hasNode(cur); i++) cur = parent.get(cur);
    return cur && g.hasNode(cur) ? cur : undefined;
  };
  for (const raw of payload.edges) {
    const e = raw.kind === "child" ? { ...raw, target: visibleAncestor(raw.target) ?? raw.target } : raw;
    if (!g.hasNode(e.source) || !g.hasNode(e.target) || e.source === e.target || g.hasEdge(e.source, e.target)) continue;
    g.addEdge(e.source, e.target, {
      size: e.kind === "cites_reg" ? Math.min(6, 1 + Math.log2(1 + (e.weight ?? 1))) : e.kind === "refers" ? 0.8 : 0.5,
      color: EDGE[e.kind],
      kind: e.kind,
    });
  }
  return g;
}

export function GraphPage({ view }: { view: "structure" | "meaning" }) {
  const { runId } = useParams();
  const navigate = useNavigate();
  const payload = useQuery({ queryKey: ["graph", runId, view], queryFn: () => api.graph(runId!, view), staleTime: Infinity });
  const container = useRef<HTMLDivElement>(null);
  const sigma = useRef<Sigma | null>(null);
  const [hover, setHover] = useState<Hover>(null);
  const [open, setOpen] = useState<GraphNode | null>(null);
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [showOther, setShowOther] = useState(view === "meaning");
  const [hiddenRegs, setHiddenRegs] = useState<Set<string>>(new Set());
  const [chat, setChat] = useState<{ q: string; a?: string }[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const highlightRef = useRef(highlight);
  highlightRef.current = highlight;

  const graph = useMemo(() => {
    if (!payload.data) return null;
    const g = build(payload.data, showOther, hiddenRegs);
    if (view === "structure" && g.order > 1) {
      forceAtlas2.assign(g, { iterations: 250, settings: { ...forceAtlas2.inferSettings(g), gravity: 1, scalingRatio: 8, barnesHutOptimize: g.order > 800 } });
    }
    return g;
  }, [payload.data, showOther, hiddenRegs, view]);

  useEffect(() => {
    if (!graph || !container.current) return;
    const s = new Sigma(graph, container.current, {
      labelColor: { color: "#d0d1d2" },
      labelFont: "Inter, system-ui, sans-serif",
      labelSize: 12,
      labelWeight: "500",
      labelRenderedSizeThreshold: 7,
      defaultEdgeType: "line",
      zIndex: true,
      nodeReducer: (node, attrs) => {
        const hl = highlightRef.current;
        if (hl.size && !hl.has(node)) return { ...attrs, color: "#2a2d33", label: "", zIndex: 0 };
        if (hl.has(node)) return { ...attrs, highlighted: true, forceLabel: true, zIndex: 5, size: (attrs.size as number) * 1.4 };
        return attrs;
      },
      edgeReducer: (edge, attrs) => {
        const hl = highlightRef.current;
        if (hl.size) {
          const [a, b] = graph.extremities(edge);
          if (!(hl.has(a) && hl.has(b))) return { ...attrs, hidden: true };
        }
        return attrs;
      },
    });
    s.on("enterNode", ({ node }) => {
      const data = graph.getNodeAttribute(node, "data") as GraphNode;
      const pos = s.graphToViewport(graph.getNodeAttributes(node) as { x: number; y: number });
      setHover({ node: data, x: pos.x, y: pos.y });
      container.current!.style.cursor = data.kind === "paragraph" ? "pointer" : "default";
    });
    s.on("leaveNode", () => {
      setHover(null);
      container.current!.style.cursor = "default";
    });
    s.on("clickNode", ({ node }) => {
      const data = graph.getNodeAttribute(node, "data") as GraphNode;
      if (data.kind === "paragraph") setOpen(data);
    });
    s.on("clickStage", () => setOpen(null));
    sigma.current = s;
    return () => {
      s.kill();
      sigma.current = null;
    };
  }, [graph]);

  useEffect(() => {
    sigma.current?.refresh();
  }, [highlight]);

  function focusOn(id: string | null, ids: string[]) {
    const s = sigma.current;
    if (!s || !graph) return;
    const target = id && graph.hasNode(id) ? id : ids.find((x) => graph.hasNode(x));
    if (!target) return;
    const display = s.getNodeDisplayData(target);
    if (display) s.getCamera().animate({ x: display.x, y: display.y, ratio: 0.35 }, { duration: 600 });
  }

  async function askGraph(q: string) {
    if (!q.trim() || asking) return;
    setQuestion("");
    setAsking(true);
    setChat((c) => [...c, { q }]);
    try {
      const res = await api.graphChat(runId!, q, open?.id ?? null);
      setChat((c) => c.map((m, i) => (i === c.length - 1 ? { ...m, a: res.answer } : m)));
      const ids = res.highlight.filter((x) => graph?.hasNode(x));
      if (!ids.length && res.highlight.length && !showOther) setShowOther(true); // highlighted nodes may be hidden ones
      setHighlight(new Set(res.highlight));
      setTimeout(() => focusOn(res.focus, res.highlight), 50);
    } catch (e) {
      setChat((c) => c.map((m, i) => (i === c.length - 1 ? { ...m, a: e instanceof Error ? e.message : "The graph assistant failed." } : m)));
    } finally {
      setAsking(false);
    }
  }

  const regs = payload.data?.regulations ?? [];
  const reqCount = payload.data?.nodes.filter((n) => n.requirement_ids?.length).length ?? 0;

  return (
    <div className="graph-wrap" data-theme="dark">
      <div className="graph-canvas">
        <div ref={container} className="sigma" role="img" aria-label={`Knowledge graph, ${view} view, ${reqCount} paragraphs with requirements`} />
        {payload.isLoading && <div className="graph-loading">{view === "meaning" ? "Placing paragraphs by meaning… (first time takes ~20 s)" : "Building the graph…"}</div>}
        {payload.isError && <div className="graph-loading">{(payload.error as Error).message}</div>}
        <div className="graph-toolbar">
          <div className="seg" role="group" aria-label="View">
            <button aria-pressed={view === "structure"} onClick={() => navigate(`/runs/${runId}/graph`)}>
              Structure
            </button>
            <button aria-pressed={view === "meaning"} onClick={() => navigate(`/runs/${runId}/graph/meaning`)}>
              Meaning
            </button>
          </div>
          <div className="seg" role="group" aria-label="Regulations">
            {regs.map((r) => (
              <button key={r} aria-pressed={!hiddenRegs.has(r)} onClick={() => setHiddenRegs((h) => { const n = new Set(h); n.has(r) ? n.delete(r) : n.add(r); return n; })}>
                {r}
              </button>
            ))}
          </div>
          <div className="seg">
            <button aria-pressed={showOther} onClick={() => setShowOther(!showOther)}>
              Other paragraphs
            </button>
            {highlight.size > 0 && <button onClick={() => setHighlight(new Set())}>Clear highlight</button>}
          </div>
        </div>
        <div className="legend" aria-label="Legend">
          {[5, 4, 3, 2, 1].map((s) => (
            <div className="row" key={s}>
              <span className="dot" style={{ background: SEV[s] }} /> Severity {s}
            </div>
          ))}
          <div className="row">
            <span className="dot" style={{ background: "#3b3f47" }} /> Other paragraph
          </div>
          {view === "structure" ? (
            <div className="row">
              <span style={{ width: 14, height: 2, background: EDGE.refers, display: "inline-block" }} /> Refers to
            </div>
          ) : (
            <div className="row">
              <span style={{ width: 14, height: 2, background: "#6f7a96", display: "inline-block" }} /> Says something similar
            </div>
          )}
        </div>
        {hover && hover.node.kind !== "section" && (
          <div className="popover" style={{ left: Math.min(hover.x + 16, (container.current?.clientWidth ?? 800) - 316), top: Math.max(8, hover.y - 20) }}>
            {hover.node.kind === "regulation" ? (
              <>
                <b>{hover.node.label}</b>
                <div className="muted small">{hover.node.title}</div>
              </>
            ) : (
              <>
                <div className="where">
                  {hover.node.regulation} §{hover.node.label} · p.{hover.node.page}
                </div>
                {hover.node.summaries ? (
                  <ul>
                    {hover.node.summaries.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="small muted" style={{ marginTop: 6 }}>
                    {hover.node.excerpt}
                  </p>
                )}
                {hover.node.severity && (
                  <div className="chips">
                    <SeverityChip s={hover.node.severity} />
                    <PhaseChip p={hover.node.phase!} />
                  </div>
                )}
                <div className="small muted" style={{ marginTop: 8 }}>
                  Click to open the source
                </div>
              </>
            )}
          </div>
        )}
        {open && (
          <div className="drawer">
            <SourcePanel node={open.id} page={open.page} onClose={() => setOpen(null)} />
          </div>
        )}
      </div>
      <aside className="graph-side" aria-label="Graph assistant">
        <header>
          <h2>{view === "structure" ? "How your requirements connect" : "Requirements by meaning"}</h2>
          <p>
            {view === "structure"
              ? "Each cluster is a regulation. Coloured dots are paragraphs your part must meet; lines show references between them."
              : "Paragraphs that say similar things sit close together, across regulations. Lines join each requirement to its nearest neighbours."}
          </p>
        </header>
        <div className="chat-log" aria-live="polite">
          {chat.length === 0 && (
            <div className="suggestions" style={{ marginTop: 0 }}>
              {["Show everything about the retro-reflector", "Which requirements are due at concept stage?", `What does ${regs[0] ?? "the first regulation"} require most?`].map((s) => (
                <button key={s} className="chip chip-btn" onClick={() => askGraph(s)}>
                  {s}
                </button>
              ))}
            </div>
          )}
          {chat.map((m, i) => (
            <div key={i} style={{ display: "grid", gap: 8 }}>
              <div className="q">{m.q}</div>
              <div className="a">{m.a ?? "Looking through the graph…"}</div>
            </div>
          ))}
        </div>
        <form
          className="chat-form"
          onSubmit={(e) => {
            e.preventDefault();
            askGraph(question);
          }}
        >
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={open ? `Ask about §${open.label}…` : "Ask about the graph…"} aria-label="Question about the graph" />
          <button className="btn btn-primary btn-icon" disabled={asking || !question.trim()} aria-label="Ask">
            <I.Send />
          </button>
        </form>
      </aside>
    </div>
  );
}
