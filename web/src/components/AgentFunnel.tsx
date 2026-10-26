import type { Journey } from "../api";

// The requirements' path through the agents as a stream: the band that goes on to the next agent, and the band each
// one stops, which can be clicked to list those requirements.

export type Stop = "grounding" | "critic" | "ranker" | "dedup";
export const STOPS: { key: Stop; agent: string; label: string }[] = [
  { key: "grounding", agent: "grounding", label: "quote not in paragraph" },
  { key: "critic", agent: "critic", label: "not a product obligation" },
  { key: "ranker", agent: "ranker", label: "paragraph scored below cut" },
  { key: "dedup", agent: "dedup", label: "near-duplicate" },
];
const COLS = ["extractor", "grounding", "critic", "ranker", "dedup"];
const NAMES = ["Extracted", "Grounded", "Critic kept", "Ranker kept", "Submitted"];

type Props = {
  items: Journey[];
  revealed: (agentKey: string) => boolean;
  active: string | null;
  filter: Stop | "submitted" | null;
  onFilter: (f: Stop | "submitted" | null) => void;
  onAgent: (agentKey: string) => void;
};

export function AgentFunnel({ items, revealed, active, filter, onFilter, onAgent }: Props) {
  const order: (Stop | null)[] = ["grounding", "critic", "ranker", "dedup", null];
  // counts[k] = requirements still in the stream at column k
  const counts = COLS.map((_, k) => items.filter((it) => order.indexOf(it.stopped) >= k).length);
  const max = Math.max(1, counts[0]);
  const W = 1000, TOP = 58, H = 150, X0 = 40, STEP = 225, BAR = 16;
  const h = (n: number) => Math.max(n ? 3 : 0, (H * n) / max);
  const x = (k: number) => X0 + k * STEP;

  return (
    <svg className="funnel" viewBox={`0 0 ${W} 312`} role="img" aria-label="Requirements kept and stopped by each agent">
      {COLS.slice(0, -1).map((_, k) => {
        const on = revealed(COLS[k + 1]);
        const x1 = x(k) + BAR, x2 = x(k + 1), hk = h(counts[k + 1]);
        const drop = counts[k] - counts[k + 1];
        const hd = h(counts[k]) - hk;
        const stop = STOPS[k];
        const mid = (x1 + x2) / 2;
        const sel = filter === stop.key;
        return (
          <g key={k} data-on={on}>
            <path className="funnel-band" d={`M${x1},${TOP} L${x2},${TOP} L${x2},${TOP + hk} L${x1},${TOP + hk} Z`} />
            {on && <path className="funnel-flow" d={`M${x1},${TOP + hk / 2} L${x2},${TOP + hk / 2}`} />}
            {drop > 0 && (
              <g className={`funnel-drop${sel ? " sel" : ""}`} onClick={() => on && onFilter(sel ? null : stop.key)}
                role="button" tabIndex={on ? 0 : -1} aria-label={`${drop} stopped by ${stop.agent}`}
                onKeyDown={(e) => e.key === "Enter" && on && onFilter(sel ? null : stop.key)}>
                <path d={`M${x1},${TOP + hk} C${mid},${TOP + hk} ${mid - 40},${250} ${mid},${250} L${mid},${250 + Math.max(4, hd * 0.25)}
                  C${mid - 40},${250 + Math.max(4, hd * 0.25)} ${mid},${TOP + hk + hd} ${x1},${TOP + hk + hd} Z`} />
                <rect className="funnel-hit" x={mid - 8} y={238} width={STEP - 20} height={58} />
                <text x={mid + 10} y={262} className="funnel-drop-n">−{drop}</text>
                <text x={mid + 10} y={286} className="funnel-drop-l">{stop.label}</text>
              </g>
            )}
          </g>
        );
      })}
      {COLS.map((key, k) => {
        const on = revealed(key);
        const last = k === COLS.length - 1;
        const sel = last && filter === "submitted";
        return (
          <g key={key} className={`funnel-col${active === key ? " active" : ""}${sel ? " sel" : ""}`} data-on={on}
            onClick={() => (last ? onFilter(sel ? null : "submitted") : onAgent(key))} role="button" tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && (last ? onFilter(sel ? null : "submitted") : onAgent(key))}>
            <rect x={x(k)} y={TOP} width={BAR} height={h(counts[k])} rx={3} />
            <text x={x(k) + BAR / 2} y={TOP - 32} textAnchor="middle" className="funnel-n">{on ? counts[k] : "…"}</text>
            <text x={x(k) + BAR / 2} y={TOP - 7} textAnchor="middle" className="funnel-l">{NAMES[k]}</text>
          </g>
        );
      })}
    </svg>
  );
}
