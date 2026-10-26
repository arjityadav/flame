"""Agents 2 and 3: which regulations apply to the product.

2. Applicability agents, one per regulation in parallel: does this one regulation apply? Each sees only that
   regulation's brief (scope, introduction, table of contents, what it consolidates and cites) and the profile.
3. Reconciler, one call: sees every candidate together and resolves overlaps. Code applies the hard rule first:
   a regulation consolidated by another applicable one (R148 combines R6, R7, R23, R38) is dropped.
"""

from __future__ import annotations

import collections
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from pipeline.graph import Graph
from pipeline.llm import ask
from pipeline.profile import ProductProfile

ROOT = Path(__file__).resolve().parent.parent
SCOPE_HEADING = re.compile(r"^\d+\.?\s*(scope|purpose|field of application|application(?! for))", re.I)


def briefs(g: Graph) -> dict[str, dict]:
    """A short, factual brief of the base version of every regulation in regulations/index.json."""
    index = json.loads((ROOT / "regulations" / "index.json").read_text())
    out = {}
    for reg in index:
        rid = reg["regulation"]
        base = next(v for v in reg["versions"] if v["role"] == "base")
        vid = f"ver:{rid}@{base['version_label']}/base"
        paras = [g.nodes[p] for p in g.out(vid, "CONTAINS") if g.nodes[p].get("normative", True)]
        scope = [p["text"] for p in paras if not p["annex"] and SCOPE_HEADING.match(p["section"]) and p["text"]]
        if not scope:
            scope = [p["text"] for p in paras if p["shall"]][:3]
        intro = [p["text"] for p in paras if not p["number"] and not p["annex"]
                 and re.search(r"introduction|preamble", p["section"], re.I)]
        sections = list(dict.fromkeys(p["section"] for p in paras if p["section"] and (
            p["annex"] is None and re.match(r"^\d+\.?\s", p["section"]) or p["annex"] and not p["number"])))
        cites = collections.Counter(r[4:] for p in g.out(vid, "CONTAINS") for r in g.out(p, "CITES_REG"))
        out[rid] = {
            "regulation": rid,
            "title": reg["title"],
            "version": base["version_label"],
            "scope": " ".join(scope)[:2500],
            "introduction": " ".join(intro)[:800],
            "sections": [s[:110] for s in sections[:45]],
            "consolidates": sorted(d[4:] for d in g.out(f"reg:{rid}", "CONSOLIDATES")),
            "consolidated_by": sorted(s[4:] for s in g.into(f"reg:{rid}", "CONSOLIDATES")),
            "cites": [f"{r} ({n}x)" for r, n in cites.most_common(6)],
            "scope_labels": [p["label"] for p in paras if not p["annex"] and SCOPE_HEADING.match(p["section"])][:20],
        }
    return out


STOPWORDS = set("""with from that this into which their part parts unit assembly system device module element
elements surface outer inner main other front rear side left right lower upper vehicle function functions
providing provides general typical made using used when where also lamp's""".split())


def keywords(profile: ProductProfile) -> set[str]:
    """Content words naming the product's parts and functions, e.g. {'stop', 'reversing', 'retro-reflector'}."""
    words = set()
    for text in [profile.product_type, profile.product_name] + [f.name for f in profile.functions] + \
            [p.name for p in profile.parts]:
        for w in re.findall(r"[a-z][a-z\-]{3,}", text.lower()):
            if w not in STOPWORDS:
                words.add(w.rstrip("s") if len(w) > 5 else w)
    return words


def evidence(g: Graph, vid: str, words: set[str], limit: int = 10) -> list[str]:
    """Paragraphs of one regulation that mention the most product keywords: shows dedicated sections
    ('6.5 Bumpers') and exclusions ('2.7.4 ... position lamps') that a scope paragraph alone does not."""
    if not words:
        return []
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(words))) + r")", re.I)
    scored = []
    for pid in g.out(vid, "CONTAINS"):
        n = g.nodes[pid]
        hits = {m.lower().rstrip("s") for m in pattern.findall(n["text"])}
        if hits:
            scored.append((len(hits), -len(n["text"]), n["label"], n["text"]))
    scored.sort(reverse=True)
    return [f"{label}: {text[:260]}" for _, _, label, text in scored[:limit]]


class Applicability(BaseModel):
    applies: Literal["yes", "maybe", "no"]
    basis: Literal["device_approval", "installation_on_vehicle", "component_technology", "vehicle_system", "none"] = \
        Field(description="device_approval: the product (or one of its functions) is a device type this regulation "
                          "approves; installation_on_vehicle: it sets requirements for fitting such devices to the "
                          "vehicle; component_technology: it covers a technology the product contains (e.g. "
                          "electrical/electronic sub-assemblies); vehicle_system: it approves a vehicle system the "
                          "product is part of; none: does not apply")
    functions_covered: list[str] = Field(description="the product functions or parts this regulation covers")
    scope_evidence: str = Field(description="short quote from the scope that decides it")
    reason: str = Field(description="one or two sentences")


APPLICABILITY_SYSTEM = """You are a UNECE type-approval expert. You decide whether ONE UN Regulation sets
requirements that the engineers of a given product must fulfil.

A regulation applies when any of these holds:
- the product, or one of its functions or parts, is a device or component type that this regulation approves;
- the regulation sets requirements for installing such a device on the vehicle categories the product is for;
- the product contains a technology this regulation covers as a separately tested component (for example
  electrical/electronic sub-assemblies);
- the regulation approves a vehicle system whose performance this product directly determines.
It does not apply when its scope excludes the product's vehicle category, or it concerns other systems.

A regulation that approves the whole vehicle for a general property (for example the shape of its exterior)
applies to a component only if it has provisions written for that component type (for example a section on
bumpers, or on headlamps); general requirements that cover the whole exterior or every part of it do not count,
and if it explicitly exempts or excludes the component type, it does not apply. A regulation that approves a
vehicle system (for example its protective devices in a low-speed impact) applies to the parts of that system;
a provision that only requires other devices to keep working after that system's test (lamps after a bumper
impact) does not make it apply to those other devices. The <mentions> show the regulation's paragraphs that name the
product's parts and functions: use them to see dedicated provisions and exclusions.

Answer "maybe" when the scope could cover the product but you are not sure. Base the decision on the regulation's
scope and contents shown, not on its number. Facts stated in the specification outrank assumptions in the
profile's uncertainties."""


def _ask_applicability(profile: ProductProfile, brief: dict, mentions: list[str]) -> Applicability:
    b = {k: v for k, v in brief.items() if k != "scope_labels"}
    content = (f"<product_profile>\n{profile.model_dump_json(indent=1)}\n</product_profile>\n\n"
               f"<regulation>\n{json.dumps(b, indent=1, ensure_ascii=False)}\n</regulation>\n\n"
               f"<mentions>\n" + "\n".join(mentions or ["(no paragraph names the product's parts or functions)"])
               + "\n</mentions>\n\n"
               f"Does {brief['regulation']} apply to this product?")
    return ask(Applicability, APPLICABILITY_SYSTEM, content, effort="medium")


class Verdict(BaseModel):
    regulation: str
    include: bool
    reason: str = Field(description="why it is in or out, in terms an engineer can check")


class Reconciliation(BaseModel):
    verdicts: list[Verdict] = Field(description="one per candidate regulation")


RECONCILER_SYSTEM = """You are the lead homologation engineer. Several experts each judged one UN Regulation
in isolation. You now see all their judgements together and make the final list of regulations the product's
requirements must be drawn from.

Resolve overlaps:
- when a consolidated regulation replaces older single-device regulations, cite the consolidated one, not the old
  ones (those already marked superseded are removed for you);
- keep a regulation that governs how the product is installed on the vehicle, if the product is a device that
  regulation lists;
- keep a regulation for a technology the product contains (e.g. EMC for electronic content);
- drop regulations whose link to this product is speculative, or that only concern other vehicle systems;
- drop whole-vehicle regulations that reach the product only through general requirements on the entire
  exterior, with no provisions written for this component type;
- drop a regulation that approves another vehicle system when it touches this product only by requiring it to
  survive or keep working after that system's test;
- do not include a regulation on the condition of an assumption the specification contradicts (if the spec
  says the light sources are LED modules, regulations for replaceable filament bulbs are out).
A missed regulation costs more than an extra one, but every wrong regulation adds many wrong requirements."""


def select(profile: ProductProfile, g: Graph, workers: int = 8) -> dict:
    """Run agents 2 and 3. Returns every decision so the UI and the evaluation can show the reasoning."""
    all_briefs = briefs(g)
    words = keywords(profile)
    mentions = {r: evidence(g, f"ver:{r}@{b['version']}/base", words) for r, b in all_briefs.items()}
    with ThreadPoolExecutor(workers) as pool:
        results = dict(zip(all_briefs, pool.map(lambda b: _ask_applicability(profile, b, mentions[b["regulation"]]),
                                                all_briefs.values())))

    candidates = {r for r, a in results.items() if a.applies in ("yes", "maybe")}
    superseded = {}
    for r in sorted(candidates):  # sorted: the dict goes into the reconciler prompt
        for newer in all_briefs[r]["consolidated_by"]:
            if newer in candidates:
                superseded[r] = newer
    remaining = sorted(candidates - set(superseded))

    final: dict[str, Verdict] = {}
    if remaining:
        listing = [{"regulation": r, "title": all_briefs[r]["title"], **results[r].model_dump()} for r in remaining]
        content = (f"<product_profile>\n{profile.model_dump_json(indent=1)}\n</product_profile>\n\n"
                   f"<candidates>\n{json.dumps(listing, indent=1, ensure_ascii=False)}\n</candidates>\n\n"
                   f"<superseded>\n{json.dumps(superseded)}\n</superseded>\n\n"
                   "Give a verdict for every candidate.")
        rec = ask(Reconciliation, RECONCILER_SYSTEM, content, effort="high")
        final = {v.regulation: v for v in rec.verdicts if v.regulation in remaining}
        for r in remaining:  # a candidate the reconciler skipped stays in: recall first
            final.setdefault(r, Verdict(regulation=r, include=True, reason="kept by default: no verdict given"))

    return {
        "applicability": {r: a.model_dump() for r, a in results.items()},
        "superseded": superseded,
        "verdicts": {r: v.model_dump() for r, v in final.items()},
        "selected": sorted(r for r, v in final.items() if v.include),
    }
