"""Targeted split pass: re-extract paragraphs where we wrote fewer requirements than they hold.

On v17 half of the remaining misses (16 of 31) were paragraphs the experts split into more obligations than we
did (one requirement per band, per class, per list item). After the ranker, code counts what each kept paragraph
appears to oblige ("shall"/"must" clauses, numeric ranges, enumerated items); a paragraph with fewer requirements
than that is sent again with the count and the earlier requirements, under the same grounded prompt, and the
longer answer is kept. New requirements go through the critic like the rest.
"""

from __future__ import annotations

import collections
import re
from concurrent.futures import ThreadPoolExecutor

from pipeline.extract import SYSTEM, Extraction, _definitions, _profile_brief, grounded
from pipeline.graph import Graph
from pipeline.llm import ask
from pipeline.profile import ProductProfile
from pipeline.verify import KEEP, verify

UNIT = r"(?:MHz|kHz|GHz|Hz|cd|mm|cm|m|V|°|%|per cent|lx|dB|s|ms|min|h|km/h|N|kg|mcd)"
RANGE = re.compile(rf"\d+(?:[.,]\d+)?\s*(?:to|-|–)\s*\d+(?:[.,]\d+)?\s*{UNIT}(?![a-z])")
ITEM = re.compile(r"(?:^|\s)\((?:[a-z]|i{1,3}|iv|vi{0,3}|ix|x)\)\s")
OBLIGE = re.compile(r"\b(shall|must)\b", re.I)
BATCH = 6
EFFORT = "high"

HINT = """
This is a second, closer reading of paragraphs where the first pass may have merged separate obligations. For each
paragraph you get an estimate of how many obligations it holds and the requirements written before. Return the
complete list for every paragraph: keep the earlier requirements that are right, and write each separate
obligation, limit, band, class, direction, colour or list item that has its own constraint as its own requirement.
Do not split a list that is only the subject or condition of one obligation, and do not invent obligations: the
count is an estimate."""


def expected(text: str) -> int:
    if not OBLIGE.search(text):
        return 0
    return max(len(OBLIGE.findall(text)), len(RANGE.findall(text)), len(ITEM.findall(text)))


def _redo(profile: ProductProfile, g: Graph, regulation: str, unit: str, pids: list[str], before: dict) -> list[dict]:
    refs = {f"P{i + 1}": pid for i, pid in enumerate(pids)}
    body = []
    for ref, pid in refs.items():
        n = g.nodes[pid]
        prev = "\n".join(f"    - {r['text']}" for r in before[pid])
        body.append(f"[{ref}] {n['label']} (about {expected(n['text'])} obligations; written before: "
                    f"{len(before[pid])}): {n['text']}\n  Written before:\n{prev}")
    defs = _definitions(g, pids)
    content = (f"<product>\n{_profile_brief(profile)}\n</product>\n\n<regulation>{regulation}, section {unit}"
               f"</regulation>\n\n" + (f"<definitions>\n" + "\n".join(defs) + "\n</definitions>\n\n" if defs else "")
               + "<paragraphs>\n" + "\n".join(body) + "\n</paragraphs>\n\nReturn the complete requirements.")
    result = ask(Extraction, SYSTEM + HINT, content, effort=EFFORT)
    out = []
    for r in result.requirements:
        pid = refs.get(r.paragraph.strip("[] "))
        if not pid or not grounded(r.evidence, g.nodes[pid]["text"]):
            continue
        n = g.nodes[pid]
        old = before[pid][0]
        out.append({"text": r.text.strip(), "evidence": r.evidence.strip(), "applies_to": r.applies_to,
                    "test_case": r.test_case, "node": pid, "unit": old["unit"],
                    "source": {"regulation": n["regulation"], "version": n["version"], "page": n["page_start"],
                               "paragraph": n["label"]},
                    "para_score": old.get("para_score", 50), "split_pass": True})
    return out


def split_pass(profile: ProductProfile, g: Graph, reqs: list[dict], workers: int = 8) -> tuple[list[dict], dict]:
    """reqs: requirements kept after the critic and the ranker. Returns (requirements, report)."""
    by_node = collections.defaultdict(list)
    for r in reqs:
        by_node[r["node"]].append(r)
    short = [p for p, rs in by_node.items() if expected(g.nodes[p]["text"]) > len(rs)]
    by_unit = collections.defaultdict(list)
    for p in short:
        r0 = by_node[p][0]
        by_unit[(r0["source"]["regulation"], r0["unit"])].append(p)
    jobs = [(reg, unit, pids[i:i + BATCH]) for (reg, unit), pids in by_unit.items() for i in range(0, len(pids), BATCH)]
    with ThreadPoolExecutor(workers) as pool:
        redone = [r for batch in pool.map(lambda j: _redo(profile, g, *j, by_node), jobs) for r in batch]
    if redone:
        redone = [r for r in verify(profile, g, redone) if r["kind"] in KEEP]  # same critic as the first pass
    new_by_node = collections.defaultdict(list)
    for r in redone:
        new_by_node[r["node"]].append(r)
    replaced = {p for p in short if len(new_by_node[p]) > len(by_node[p])}
    out, done = [], set()
    for r in reqs:
        if r["node"] in replaced:
            if r["node"] not in done:
                out.extend(new_by_node[r["node"]])
                done.add(r["node"])
        else:
            out.append(r)
    return out, {"short_paragraphs": len(short), "calls": len(jobs), "replaced": len(replaced),
                 "added": len(out) - len(reqs)}
