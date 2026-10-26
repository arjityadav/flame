"""Agent 4, section selector: one call per selected regulation, picks the parts of it to read.

A regulation is cut into units: main-text sections two levels deep ("6.10", "5.3") and annexes or annex parts
("Annex 6 Part 4"). The selector sees the unit list with titles and sizes, never the full text, and keeps the units
that hold obligations for this product. Extraction (agent 5) then reads only those units.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from pipeline.graph import Graph
from pipeline.llm import ask
from pipeline.profile import ProductProfile


@dataclass
class Unit:
    uid: str
    key: str  # "6.10", "Annex 6 Part 4"
    title: str
    paragraphs: list[str] = field(default_factory=list)  # paragraph node ids, in document order


def units(g: Graph, vid: str) -> list[Unit]:
    out: dict[str, Unit] = {}
    for pid in g.out(vid, "CONTAINS"):
        n = g.nodes[pid]
        if not n.get("normative", True):
            continue  # GTR rationale before the regulation text
        if n["annex"]:
            key = " ".join(x for x in (n["annex"], n["part"]) if x)
        elif n["number"]:
            key = ".".join(n["number"].split(".")[:2])
        else:
            continue  # unnumbered introduction text: no obligations
        if key not in out:
            out[key] = Unit(f"U{len(out) + 1}", key, "")
        u = out[key]
        u.paragraphs.append(pid)
        if not u.title:
            if n["annex"]:
                u.title = n["section"]
            elif n["label"] == key:
                u.title = n["text"] or n["section"]
            else:
                u.title = f"(under {n['section']})"
    return list(out.values())


class Pick(BaseModel):
    unit: str = Field(description="unit id, e.g. 'U12'")
    reason: str


class SectionChoice(BaseModel):
    selected: list[Pick]


SYSTEM = """You are a homologation engineer preparing the requirement list for one product under one UN
Regulation. You see the regulation's table of contents, cut into units. Pick every unit that contains
obligations the product (as a device, and as installed on the vehicle where the regulation covers installation)
must fulfil.

Pick:
- markings and approval-mark provisions that the device itself must carry;
- general technical requirements that apply to all devices the regulation covers;
- the specific sections for each function or part the product has;
- conformity-of-production provisions that set requirements on the product;
- annexes and annex parts with test methods or limits whose results the product must meet.
Do not pick:
- sections only about functions, devices or vehicle categories the product does not have (a section that covers
  the product's functions or category together with others is picked);
- application documents, communication forms, approval procedures for authorities, transitional provisions;
- definitions (they are given to the extractor separately).
When unsure, pick the unit: a later agent drops paragraphs that do not apply."""


def choose(profile: ProductProfile, g: Graph, regulation: str, version: str, why: str) -> dict:
    us = units(g, f"ver:{regulation}@{version}/base")
    lines = []
    for u in us:
        shall = sum(g.nodes[p]["shall"] for p in u.paragraphs)
        lines.append(f"{u.uid} | {u.key} | {u.title[:110]} | {len(u.paragraphs)} paragraphs, {shall} with shall/must")
    content = (f"<product_profile>\n{profile.model_dump_json(indent=1)}\n</product_profile>\n\n"
               f"<regulation>{regulation} (version {version}). Why it applies: {why}</regulation>\n\n"
               f"<units>\n" + "\n".join(lines) + "\n</units>\n\nPick the units to read.")
    picked = ask(SectionChoice, SYSTEM, content, effort="high")
    by_id = {u.uid: u for u in us}
    chosen = [(by_id[p.unit], p.reason) for p in picked.selected if p.unit in by_id]
    return {
        "regulation": regulation,
        "version": version,
        "units": [{"uid": u.uid, "key": u.key, "title": u.title[:110], "reason": r, "paragraphs": u.paragraphs}
                  for u, r in chosen],
        "skipped": [{"uid": u.uid, "key": u.key, "title": u.title[:110]} for u in us if u not in [c[0] for c in chosen]],
    }


def choose_all(profile: ProductProfile, g: Graph, regs: dict) -> list[dict]:
    """regs: the 02_regulations.json result. One selector per selected regulation, in parallel."""
    def one(r: str) -> dict:
        version = next(g.nodes[v]["version"] for v in g.out(f"reg:{r}", "HAS_VERSION") if v.endswith("/base"))
        return choose(profile, g, r, version, regs["verdicts"][r]["reason"])
    with ThreadPoolExecutor(4) as pool:
        return list(pool.map(one, regs["selected"]))


if __name__ == "__main__":
    print(json.dumps([u.__dict__ for u in units(Graph.load(), "ver:UN R48@9.3.0/base")][:5], indent=1)[:1500])
