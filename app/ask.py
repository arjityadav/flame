"""Agent 9, the assistant: short, plain answers about the regulations, from retrieved paragraphs only.

Retrieval (pipeline/retrieve.py) runs first; the model sees the passages as [S1]…[Sn] and cites them. After the
answer has streamed, code keeps only citations that point at a passage it was given; an answer left with none is
replaced by "not covered". Uses Sonnet 5.5 so spoken answers start quickly.
"""

from __future__ import annotations

import re

from app.runs import requirements
from app.store import store
from pipeline import llm

SYSTEM = """You are Flame's assistant. You help design engineers understand the UN Regulations that apply to the
part they are designing. You answer ONLY from the numbered source passages you are given.

How to answer:
- At most 4 short sentences in plain words; a design engineer, not a lawyer, is listening. Lead with the answer.
- Give numbers and limits exactly as the source states them.
- Cite every fact with its passage tag, e.g. [S2]. Use only tags you were given.
- No paragraph numbers or regulation numbers in the sentences themselves (the citations show them), no lists,
  no markdown, no symbols that read badly aloud.
- If the passages do not answer the question, say "The regulations in this analysis don't cover that." and
  suggest a question they can answer.
- If the question is about "this requirement" or "this", it refers to the focused requirement."""

NOT_COVERED = "The regulations in this analysis don't cover that. Try asking about a specific part, test or limit."


def scope(run_id: str) -> tuple[list[str], dict[str, dict]]:
    reqs = requirements(run_id)
    regs = list(dict.fromkeys(r["source"]["regulation"] for r in reqs))
    return regs, {r["node"]: r for r in reqs if r.get("node")}


def prepare(run_id: str, question: str, focus: str | None):
    regs, by_node = scope(run_id)
    focus_node = None
    if focus:  # a requirement id ("R-0042") or a paragraph node id
        focus_node = next((r["node"] for r in by_node.values() if r["id"] == focus), None) or \
            (focus if focus in store.paragraphs else None)
    hits = store.retriever.search(question, regs, focus=focus_node, k=10, boost=set(by_node))
    passages = []
    for i, h in enumerate(hits, 1):
        req = by_node.get(h.id)
        note = f" — requirement in this analysis: {req['summary']}" if req else ""
        passages.append(f"[S{i}] {h.regulation} {h.label} (page {h.page}){note}\n{h.text[:1200]}")
    focus_note = ""
    if focus_node and focus_node in by_node:
        r = by_node[focus_node]
        focus_note = f"\nFocused requirement: {r['text']} ({r['source']['regulation']} {r['source'].get('paragraph')})\n"
    return hits, "\n\n".join(passages), focus_note


def citations(answer: str, hits) -> list[dict]:
    used = []
    for n in dict.fromkeys(int(m) for m in re.findall(r"\[S(\d+)\]", answer)):
        if 1 <= n <= len(hits):
            h = hits[n - 1]
            used.append({"tag": f"S{n}", "node": h.id, "regulation": h.regulation, "version": h.version,
                         "label": h.label, "page": h.page})
    return used


def spoken(answer: str) -> str:
    return re.sub(r"\s*\[S\d+\]", "", answer).strip()


def stream_answer(run_id: str, question: str, focus: str | None, history: list[dict]):
    """Yields ("token", text) while the answer streams, then ("final", payload)."""
    hits, passages, focus_note = prepare(run_id, question, focus)
    if not llm.available():  # no model configured: show the closest passages instead of failing
        lines = [f"{h.regulation} {h.label}: {h.text[:160]}…" for h in hits[:2]]
        text = "No model is configured on this server, so here are the closest passages. " + " ".join(
            f"[S{i}]" for i in range(1, len(lines) + 1))
        yield "token", text
        yield "final", {"answer": text, "spoken": "No model is configured. Showing the closest passages.",
                        "citations": citations(text, hits), "not_in_sources": False, "offline": True}
        return
    messages = []
    for turn in history[-4:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": f"<sources>\n{passages}\n</sources>\n{focus_note}\nQuestion: {question}"})
    answer = ""
    for chunk in llm.stream_text(SYSTEM, messages):
        answer += chunk
        yield "token", chunk
    cited = citations(answer, hits)
    not_covered = "don't cover that" in answer
    if not cited and not not_covered:
        answer, not_covered = NOT_COVERED, True
    yield "final", {"answer": answer, "spoken": spoken(answer), "citations": cited, "not_in_sources": not_covered}
