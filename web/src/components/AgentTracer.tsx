import { Link } from "react-router-dom";
import type { AgentStep, Journey } from "../api";
import { STOPS, type Stop } from "./AgentFunnel";

// One requirement's path through the agents: what each one said about it, and where it was stopped.

const STOP_AT: Record<Stop, string> = { grounding: "grounding", critic: "critic", ranker: "ranker", dedup: "dedup" };
export const PATH = ["applicability", "reconciler", "sections", "extractor", "grounding", "critic", "ranker", "dedup", "enrich"];

/** pass / stop / after for each agent on this requirement's path */
export function pathState(j: Journey): Record<string, "pass" | "stop" | "after"> {
  const at = j.stopped ? PATH.indexOf(STOP_AT[j.stopped]) : PATH.length;
  return Object.fromEntries(PATH.map((k, i) => [k, i < at ? "pass" : i === at ? "stop" : "after"]));
}

export function outcome(j: Journey) {
  if (!j.stopped) return { label: j.id ?? "submitted", cls: "yes" };
  return { label: `stopped: ${STOPS.find((s) => s.key === j.stopped)!.agent}`, cls: "no" };
}

type Props = {
  runId: string;
  journey: Journey;
  agents: AgentStep[];
  rankMin: number;
  onPick: (id: string) => void;
  onClose: () => void;
};

export function AgentTracer({ runId, journey: j, agents, rankMin, onPick, onClose }: Props) {
  const state = pathState(j);
  const verdict = agents.find((a) => a.key === "applicability")?.details.items?.find((x: any) => x.regulation === j.regulation);
  const kindKept = j.stopped !== "critic" && j.stopped !== "grounding";
  const steps: { key: string; name: string; body: React.ReactNode }[] = [
    { key: "applicability", name: "Applicability agent", body: <>{j.regulation}: <b>{verdict?.applies ?? "yes"}</b>{verdict?.reason ? <div className="small muted">{verdict.reason}</div> : null}</> },
    { key: "reconciler", name: "Reconciler", body: <>{j.regulation} selected</> },
    { key: "sections", name: "Section selector", body: j.section ? <>chose <b>{j.section}</b>{j.section_reason && <div className="small muted">{j.section_reason}</div>}</> : <span className="muted">—</span> },
    { key: "extractor", name: "Extractor", body: <>{j.text}<div className="small muted">{j.regulation} ¶ {j.paragraph}{j.page ? ` · p.${j.page}` : ""}</div></> },
    { key: "grounding", name: "Grounding check", body: j.stopped === "grounding" ? <>quote <i>“{j.evidence}”</i> is not in the paragraph</> : <blockquote className="tracer-quote">“{j.evidence}”</blockquote> },
    { key: "critic", name: "Critic", body: j.kind ? <>kind <b>{j.kind.replaceAll("_", " ")}</b> → {kindKept ? "kept" : "dropped"}</> : <span className="muted">—</span> },
    { key: "ranker", name: "Paragraph ranker", body: j.score !== null ? (
      <div className="tracer-score"><span className="tracer-score-track"><span style={{ width: `${j.score}%` }} /><i style={{ left: `${rankMin}%` }} /></span>
        <span className="mono small">{j.score}/100 {j.score >= rankMin ? "≥" : "<"} {rankMin}</span></div>) : <span className="muted">—</span> },
    { key: "dedup", name: "Duplicate removal", body: j.stopped === "dedup" ? (
      <>near-identical to {j.duplicate_of ? <button className="chip chip-cite" onClick={() => onPick(j.duplicate_of!)}>{j.duplicate_of}</button> : "another requirement"}, which was kept</>
    ) : <>unique, kept</> },
    { key: "enrich", name: "In the submission", body: j.id ? <><Link to={`/runs/${runId}?r=${j.id}`} className="chip chip-cite">{j.id}</Link> {j.summary}</> : <span className="muted">—</span> },
  ];

  return (
    <div className="tracer">
      <div className="tracer-head">
        <span className={`chip`} data-a={outcome(j).cls}>{outcome(j).label}</span>
        <span className="spacer" />
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Back to list</button>
      </div>
      <ol className="tracer-steps">
        {steps.map((s) => (
          <li key={s.key} data-s={state[s.key]}>
            <span className="tracer-dot" aria-hidden>{state[s.key] === "pass" ? "✓" : state[s.key] === "stop" ? "✕" : ""}</span>
            <div><div className="tracer-name">{s.name}</div>{state[s.key] !== "after" && <div className="tracer-body">{s.body}</div>}</div>
          </li>
        ))}
      </ol>
    </div>
  );
}
