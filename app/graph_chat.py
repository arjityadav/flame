"""Agent 10, graph chat: answers questions about the requirement graph with read-only graph tools and tells the
client which nodes to highlight.
"""

from __future__ import annotations

import json

from app.runs import requirements
from app.store import store
from pipeline import llm

SYSTEM = """You are Flame's graph assistant. The user is looking at a graph of the UN Regulations that apply to
their part: regulations, their sections, the paragraphs that hold the part's requirements, and the references
between them. Answer questions about it with the tools, then:
- call `highlight` once with the node ids the answer is about (requirement paragraphs, sections or regulations),
  and the one to focus the camera on;
- answer in at most 3 short plain sentences. Mention regulation and paragraph labels only where they help.
Never invent node ids; use ids returned by the tools."""

TOOLS = [
    {"name": "search_paragraphs", "description": "Find paragraphs in this analysis's regulations about a topic.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "requirements", "description": "List requirements of this analysis, optionally filtered.",
     "input_schema": {"type": "object", "properties": {
         "regulation": {"type": "string"}, "phase": {"type": "integer"}, "severity_min": {"type": "integer"},
         "text": {"type": "string", "description": "words that must appear in the requirement or its summary"}}}},
    {"name": "get_node", "description": "Details of one paragraph node: text, page, requirements on it.",
     "input_schema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]}},
    {"name": "neighbors", "description": "Paragraphs a node refers to, is referred to by, its parent and children.",
     "input_schema": {"type": "object", "properties": {"node_id": {"type": "string"}}, "required": ["node_id"]}},
    {"name": "highlight", "description": "Highlight these node ids in the graph and focus the camera on one.",
     "input_schema": {"type": "object", "properties": {"node_ids": {"type": "array", "items": {"type": "string"}},
                                                       "focus": {"type": "string"}}, "required": ["node_ids"]}},
]


def answer(run_id: str, question: str, selection: str | None = None) -> dict:
    reqs = requirements(run_id)
    regs = list(dict.fromkeys(r["source"]["regulation"] for r in reqs))
    by_node: dict[str, list[dict]] = {}
    for r in reqs:
        if r.get("node"):
            by_node.setdefault(r["node"], []).append(r)
    action = {"highlight": [], "focus": None}

    def brief(pid: str) -> dict:
        p = store.paragraphs.get(pid, {})
        return {"node_id": pid, "regulation": p.get("regulation"), "label": p.get("label"), "page": p.get("page_start"),
                "requirements": [r["summary"] for r in by_node.get(pid, [])][:3]}

    def run_tool(name: str, args: dict) -> str:
        if name == "search_paragraphs":
            hits = store.retriever.search(args["query"], regs, k=min(int(args.get("k", 8)), 15), boost=set(by_node))
            return json.dumps([{**brief(h.id), "text": h.text[:300]} for h in hits])
        if name == "requirements":
            out = []
            for r in reqs:
                if args.get("regulation") and r["source"]["regulation"] != args["regulation"]:
                    continue
                if args.get("phase") and r["phase"] != args["phase"]:
                    continue
                if args.get("severity_min") and r["severity"] < args["severity_min"]:
                    continue
                if args.get("text") and args["text"].lower() not in (r["text"] + r["summary"]).lower():
                    continue
                out.append({"id": r["id"], "node_id": r.get("node"), "summary": r["summary"], "severity": r["severity"],
                            "phase": r["phase_label"], "paragraph": f"{r['source']['regulation']} {r['source'].get('paragraph')}"})
            return json.dumps(out[:40])
        if name == "get_node":
            pid = args["node_id"]
            if pid not in store.paragraphs:
                return "unknown node id"
            return json.dumps({**brief(pid), "text": store.paragraphs[pid]["text"][:800]})
        if name == "neighbors":
            g, pid = store.graph, args["node_id"]
            return json.dumps({
                "refers_to": [brief(t) for t in g.out(pid, "REFERS_TO") if t in store.paragraphs][:8],
                "referred_by": [brief(s) for s in g.into(pid, "REFERS_TO") if s in store.paragraphs][:8],
                "parent": [brief(t) for t in g.out(pid, "CHILD_OF")][:1],
                "children": [brief(s) for s in g.into(pid, "CHILD_OF")][:8],
            })
        if name == "highlight":
            action["highlight"] = [n for n in args.get("node_ids", []) if isinstance(n, str)][:60]
            action["focus"] = args.get("focus")
            return "highlighted"
        raise ValueError(f"unknown tool {name}")

    if not llm.available():  # no model: highlight the closest paragraphs and say so
        hits = store.retriever.search(question, regs, k=8, boost=set(by_node))
        ids = [h.id for h in hits]
        return {"answer": "No model is configured on this server; highlighting the closest paragraphs.",
                "highlight": ids, "focus": ids[0] if ids else None, "offline": True}

    context = f"Regulations in this analysis: {', '.join(regs)}. {len(reqs)} requirements."
    if selection and selection in store.paragraphs:
        context += f" The user has selected node {selection} ({json.dumps(brief(selection))})."
    text, _ = llm.tool_loop(SYSTEM, [{"role": "user", "content": f"{context}\n\nQuestion: {question}"}], TOOLS, run_tool)
    return {"answer": text, **action}
