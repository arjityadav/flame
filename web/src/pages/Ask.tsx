import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, ask, sse, type AskFinal, type Citation } from "../api";
import * as I from "../components/Icons";
import { SourcePanel } from "../components/SourcePanel";

type Turn = { q: string; answer: string; final?: AskFinal; error?: string };

function clean(text: string) {
  return text.replace(/\s*\[S\d+\]/g, "");
}

export function Ask() {
  const { runId } = useParams();
  const [params] = useSearchParams();
  const focus = params.get("focus");
  const reqs = useQuery({ queryKey: ["requirements", runId], queryFn: () => api.requirements(runId!) });
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState<Citation | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const focusReq = reqs.data?.find((r) => r.id === focus);

  const suggestions = useMemo(() => {
    const top = reqs.data?.[0];
    const sev5 = reqs.data?.find((r) => r.severity === 5);
    return [
      focusReq ? `Explain requirement ${focusReq.id} in plain words` : null,
      "Which requirements must be settled at concept stage?",
      sev5 ? `What does "${sev5.summary.split(" ").slice(0, 8).join(" ")}…" mean for my design?` : null,
      top ? `Why is ${top.source.regulation} paragraph ${top.source.paragraph} first in the list?` : null,
      "What tests does the part have to pass before approval?",
    ].filter(Boolean) as string[];
  }, [reqs.data, focusReq]);

  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [turns]);

  async function send(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const history = turns.flatMap((t) => [
      { role: "user", content: t.q },
      { role: "assistant", content: clean(t.final?.answer ?? t.answer) },
    ]);
    setTurns((all) => [...all, { q, answer: "" }]);
    const update = (patch: Partial<Turn>) => setTurns((all) => all.map((t, i) => (i === all.length - 1 ? { ...t, ...patch } : t)));
    try {
      let text = "";
      for await (const { event, data } of sse(await ask(runId!, q, focus, history))) {
        if (event === "token") {
          text += data;
          update({ answer: text });
        } else if (event === "final") {
          update({ answer: data.answer, final: data });
        } else if (event === "error") {
          update({ error: data.message });
        }
      }
    } catch (e) {
      update({ error: e instanceof Error ? e.message : "The assistant is unavailable." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={source ? "req-layout" : ""} style={source ? { padding: "0 24px 0 0", alignItems: "start" } : undefined}>
      <div className="ask">
        <div className="ask-thread" aria-live="polite">
          {turns.length === 0 && (
            <div className="ask-intro">
              <h1>Ask about the regulations.</h1>
              <p>
                Type a question. Answers come only from the regulations in this analysis, in plain words, with the paragraph they rely on.
              </p>
              {focusReq && (
                <div className="answer" style={{ marginTop: 20 }}>
                  <div className="small muted">Asking about requirement #{focusReq.priority}</div>
                  <p style={{ fontSize: "var(--t-14)", marginTop: 4 }}>{focusReq.summary}</p>
                </div>
              )}
              <div className="suggestions">
                {suggestions.map((s) => (
                  <button key={s} className="chip chip-btn" style={{ height: 32, padding: "0 12px" }} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} style={{ display: "grid", gap: 10 }}>
              <div className="bubble-q">{t.q}</div>
              <div className={`answer${!t.answer && !t.error ? " thinking" : ""}`}>
                {t.error ? (
                  <p className="muted">{t.error}</p>
                ) : (
                  <p>{t.answer ? clean(t.answer) : "Looking it up in the regulations…"}</p>
                )}
                {t.final && t.final.citations.length > 0 && (
                  <div className="cites">
                    {t.final.citations.map((c) => (
                      <button key={c.tag + c.node} className="chip chip-cite" onClick={() => setSource(c)}>
                        {c.regulation} §{c.label} · p.{c.page}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={end} />
        </div>
        <div className="promptbar">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask, e.g. how high must the stop lamp be mounted?"
              aria-label="Your question"
            />
            <button type="submit" className="btn btn-primary send" disabled={busy || !input.trim()} aria-label="Send">
              <I.Send />
            </button>
          </form>
        </div>
      </div>
      {source && (
        <SourcePanel node={source.node} page={source.page} onClose={() => setSource(null)} onOpenNode={(n) => setSource({ ...source, node: n, page: 0 })} />
      )}
    </div>
  );
}
