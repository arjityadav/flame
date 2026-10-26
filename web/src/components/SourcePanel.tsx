import { useQuery } from "@tanstack/react-query";
import { api, type Requirement } from "../api";
import { PhaseChip, SeverityChip } from "./Chips";
import * as I from "./Icons";
import { PdfViewer } from "./PdfViewer";

type Props = {
  node: string | null;
  requirement?: Requirement | null;
  page?: number; // the cited page, when it differs from the paragraph's
  onClose?: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  onOpenNode?: (node: string) => void;
};

/** The "receipt" for a requirement: the regulation PDF at the cited page with its paragraph highlighted. */
export function SourcePanel({ node, requirement, page, onClose, onPrev, onNext, onOpenNode }: Props) {
  const para = useQuery({ queryKey: ["paragraph", node], queryFn: () => api.paragraph(node!), enabled: !!node });
  const p = para.data;
  const pages = p ? Array.from(new Set([...(page && !p.pages.includes(page) ? [page] : []), ...p.pages])).slice(0, 3) : [];
  const src = requirement?.source;

  return (
    <section className="source" data-theme="dark" aria-label="Source document">
      <div className="source-head">
        <div className="where">
          {p ? (
            <>
              <b>{p.regulation}</b> <span className="mono">§{p.label}</span> · page {pages[0]}
              <div className="sub">
                version {p.version} · {p.title}
              </div>
            </>
          ) : src ? (
            <>
              <b>{src.regulation}</b> <span className="mono">§{src.paragraph}</span> · page {src.page}
            </>
          ) : (
            <span className="muted">Pick a requirement to see its source</span>
          )}
        </div>
        {onPrev && (
          <button className="btn btn-ghost btn-icon" onClick={onPrev} aria-label="Previous requirement">
            <I.ChevronLeft />
          </button>
        )}
        {onNext && (
          <button className="btn btn-ghost btn-icon" onClick={onNext} aria-label="Next requirement">
            <I.Chevron />
          </button>
        )}
        {onClose && (
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close source">
            <I.Close />
          </button>
        )}
      </div>
      <div className="source-body">
        {!node && src && <div className="notice warn">Paragraph position not available; the regulation is cited on page {src.page}.</div>}
        {para.isError && <div className="notice warn">{(para.error as Error).message}</div>}
        {para.isLoading && <div className="skeleton" style={{ width: "90%", height: 600 }} />}
        {p && p.pdf && <PdfViewer url={p.pdf} pages={pages} boxes={p.boxes} />}
        {p && !p.boxes.length && <div className="notice warn">Paragraph position not available; showing page {pages[0]}.</div>}
      </div>
      {(requirement || p) && (
        <div className="source-foot">
          {requirement && (
            <>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
                <SeverityChip s={requirement.severity} />
                <PhaseChip p={requirement.phase} />
              </div>
              <div style={{ color: "var(--ink)" }}>{requirement.summary}</div>
            </>
          )}
          {p && p.definitions.length > 0 && (
            <>
              <h4>Definitions used</h4>
              <div className="links">
                {p.definitions.map((d) => (
                  <button key={d.node} className="chip chip-btn" onClick={() => onOpenNode?.(d.node)}>
                    {d.term} <span className="mono">§{d.label}</span>
                  </button>
                ))}
              </div>
            </>
          )}
          {p && p.references.length > 0 && (
            <>
              <h4>Refers to</h4>
              <div className="links">
                {p.references.map((r) => (
                  <button key={r.node} className="chip chip-btn" onClick={() => onOpenNode?.(r.node)}>
                    <span className="mono">
                      {r.regulation} §{r.label}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
