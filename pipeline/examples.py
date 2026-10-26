"""Retrieval of expert examples: which paragraphs experts list, and how they write the requirements.

Sources, both written by Certivity's experts:
  * cases/*/expected.json                -> paragraphs listed for a product, with their requirements
  * subgoal/*/requirements_old.json      -> requirements derived from a regulation (UN R175 for dev)
Paragraphs of the same regulations that experts did not cite are the "not listed" examples.

Leave-one-out: examples from the same case AND the same regulation as the target are never used, so the dev
case is not scored with its own answer. For a held-out case everything from the dev data is usable.
"""

from __future__ import annotations

import collections
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from pipeline.graph import Graph

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Example:
    case: str
    regulation: str
    label: str
    text: str
    listed: bool
    requirements: list[str] = field(default_factory=list)


def _key(label: str) -> str:
    return re.sub(r"\.?\s*table\s+\S+$", "", label.strip(), flags=re.I).rstrip(".")


_POOL: list[Example] | None = None


def pool(g: Graph) -> list[Example]:
    global _POOL
    if _POOL is not None:
        return _POOL
    out: list[Example] = []
    sources = []
    for exp in sorted((ROOT / "cases").glob("*/expected.json")):
        reqs = json.loads(exp.read_text())["requirements"]
        sources.append((exp.parent.name, "base", reqs))
    for old in sorted((ROOT / "subgoal").glob("*/requirements_old.json")):
        sources.append((f"subgoal-{old.parent.name}", "old", json.loads(old.read_text())))
    for case, role, reqs in sources:
        by_para = collections.defaultdict(list)
        for r in reqs:
            by_para[(r["source"]["regulation"], _key(r["source"].get("paragraph", "")))].append(r["text"])
        regs = {reg for reg, _ in by_para}
        for nid, n in g.nodes.items():
            if n["kind"] != "paragraph" or n["role"] != role or n["regulation"] not in regs or not n["text"]:
                continue
            got = by_para.get((n["regulation"], n["label"]))
            if got:
                out.append(Example(case, n["regulation"], n["label"], n["text"], True, got))
            elif n["shall"] and n["label"].split(".")[0] != "2":  # definitions are never listed
                out.append(Example(case, n["regulation"], n["label"], n["text"], False))
    # one example per (case, regulation, label)
    seen, uniq = set(), []
    for e in out:
        k = (e.case, e.regulation, e.label)
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    _POOL = uniq
    return uniq


def retrieve(g: Graph, texts: list[str], case: str, regulation: str, k_listed: int = 6, k_unlisted: int = 6
             ) -> tuple[list[Example], list[Example]]:
    """The listed and not-listed examples most similar to any of `texts`, leaving out (case, regulation)."""
    cands = [e for e in pool(g) if not (e.case == case and e.regulation == regulation)]
    def top(items: list[Example], k: int) -> list[Example]:
        if not items:
            return []
        score = collections.defaultdict(float)
        choices = [e.text[:600] for e in items]
        for t in texts:
            for _, s, i in process.extract(t[:600], choices, scorer=fuzz.token_set_ratio, limit=k):
                score[i] = max(score[i], s)
        return [items[i] for i, _ in sorted(score.items(), key=lambda x: (-x[1], x[0]))[:k]]
    return top([e for e in cands if e.listed], k_listed), top([e for e in cands if not e.listed], k_unlisted)
