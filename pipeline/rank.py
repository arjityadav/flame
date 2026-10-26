"""Paragraph ranker: would an expert list requirements from this paragraph for this product?

Runs after the critic on the paragraphs that still carry requirements. Each call sees one section's paragraphs
(with their parent paragraph as context) and retrieved expert examples: similar paragraphs experts listed (with
the requirement they wrote) and similar ones they did not. It returns a 0-100 score per paragraph, stored on each
requirement as `para_score`. The cut-off is chosen by measurement (eval: requirements on paragraphs experts never
used never match), not by the model.
"""

from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from pipeline.examples import retrieve
from pipeline.extract import _profile_brief
from pipeline.graph import Graph
from pipeline.llm import ask
from pipeline.profile import ProductProfile

CHUNK = 25


class Score(BaseModel):
    paragraph: str = Field(description="P-id")
    score: int = Field(description="0-100: likelihood an expert lists requirements from this paragraph")


class Scores(BaseModel):
    scores: list[Score]


SYSTEM = """You are a senior regulatory expert at a company that writes requirement lists for engineers. For a
given product, your experts read the applicable UN Regulations and list the paragraphs whose obligations the
product's engineers must act on. They are selective: they keep the obligations that constrain the product's own
design, performance, markings and installation for the functions it has, and they leave out paragraphs that are
generic, procedural, about other device types or variants, or that only repeat what another listed paragraph
already says.

You see examples of paragraphs experts LISTED (with the requirement they wrote) and paragraphs they did NOT list,
taken from other regulations and products. Then score each target paragraph from 0 to 100 for how likely an
expert would list requirements from it for this product. Use the whole range."""


def _parent(g: Graph, pid: str) -> str:
    par = g.out(pid, "CHILD_OF")
    return f"{g.nodes[par[0]]['label']}: {g.nodes[par[0]]['text'][:160]}" if par else ""


def _rank_chunk(profile: ProductProfile, g: Graph, case: str, regulation: str, pids: list[str]) -> dict[str, int]:
    texts = [g.nodes[p]["text"] for p in pids]
    listed, unlisted = retrieve(g, texts, case, regulation)
    ex = ["LISTED " + f"[{e.regulation} {e.label}] {e.text[:280]}\n   -> expert requirement: {e.requirements[0][:200]}"
          for e in listed]
    ex += ["NOT LISTED " + f"[{e.regulation} {e.label}] {e.text[:280]}" for e in unlisted]
    refs = {f"P{i + 1}": p for i, p in enumerate(pids)}
    body = []
    for ref, p in refs.items():
        n, parent = g.nodes[p], _parent(g, p)
        body.append(f"[{ref}] {n['label']}" + (f" (under {parent})" if parent else "") + f": {n['text'][:500]}")
    content = (f"<product>\n{_profile_brief(profile)}\n</product>\n\n<examples>\n" + "\n".join(ex) +
               f"\n</examples>\n\n<regulation>{regulation}</regulation>\n<paragraphs>\n" + "\n".join(body) +
               "\n</paragraphs>\n\nScore every paragraph.")
    result = ask(Scores, SYSTEM, content, effort="medium")
    return {refs[s.paragraph.strip("[] ")]: max(0, min(100, s.score)) for s in result.scores
            if s.paragraph.strip("[] ") in refs}


def rank(profile: ProductProfile, g: Graph, reqs: list[dict], case: str, workers: int = 8) -> list[dict]:
    by_unit: dict[tuple, list[str]] = collections.defaultdict(list)
    for r in reqs:
        key = (r["source"]["regulation"], r["unit"])
        if r["node"] not in by_unit[key]:
            by_unit[key].append(r["node"])
    jobs = [(reg, pids[i:i + CHUNK]) for (reg, _), pids in by_unit.items() for i in range(0, len(pids), CHUNK)]
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda j: _rank_chunk(profile, g, case, *j), jobs))
    score = {p: s for res in results for p, s in res.items()}
    for r in reqs:
        r["para_score"] = score.get(r["node"], 50)  # unscored: middle, so a cut-off below 50 keeps it
    return reqs
