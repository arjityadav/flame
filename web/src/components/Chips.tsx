import { PHASES, SEVERITY } from "../api";

export function SeverityChip({ s, label = true }: { s: number; label?: boolean }) {
  return (
    <span className="chip chip-sev" data-s={s} title={`Severity ${s} of 5: ${SEVERITY[s]}`}>
      {s}
      {label && <span>{SEVERITY[s]}</span>}
    </span>
  );
}

export function PhaseChip({ p }: { p: number }) {
  return (
    <span className="chip chip-phase" title={`Phase ${p}: ${PHASES[p]}`}>
      <b>P{p}</b> {PHASES[p]}
    </span>
  );
}
