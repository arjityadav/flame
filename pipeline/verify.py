"""Agent 6, verifier and critic: an independent second pass over the extracted requirements.

Code check: the requirement must be traceable to its cited paragraph (shared wording); a requirement whose text
is unlike its source paragraph is flagged, since it may be invented or mis-attributed.
Critic: one call per section labels each requirement with what kind of statement it is. Which kinds go into the
submission is decided in KEEP, measured on the dev case (see eval/judge.py), not by the model.
"""

from __future__ import annotations

import collections
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from pipeline.extract import _profile_brief
from pipeline.graph import Graph
from pipeline.llm import ask
from pipeline.profile import ProductProfile

Kind = Literal["product_requirement", "limit_defining_test_condition", "lab_procedure", "manufacturer_process",
               "optional_or_advisory", "condition_not_met", "not_applicable"]
# measured on dev-rear-lamp (eval/judge.py cross-tab): lab_procedure and manufacturer_process are almost never in the
# expected list; optional_or_advisory and not_applicable items rarely are but are kept for recall
KEEP: set[str] = {"product_requirement", "limit_defining_test_condition", "optional_or_advisory", "not_applicable"}


class Label(BaseModel):
    id: str
    kind: Kind


class Labels(BaseModel):
    labels: list[Label]


SYSTEM = """You review engineering requirements extracted from a UN Regulation for one product, and label what
kind of statement each one is:

- product_requirement: an obligation the product itself must meet: design, construction, performance limit,
  colour, marking it must carry, how it must be installed on the vehicle, behaviour in a test it must pass.
- limit_defining_test_condition: a test condition without which a limit has no meaning (supply voltage for
  photometry, frequency range, modulation, test severity, the time after switch-on when values must hold).
- lab_procedure: how the laboratory sets up or carries out a measurement (positioning, masking, distances,
  equipment, calibration, what to do if results are challenged).
- manufacturer_process: the manufacturer's or approval holder's procedures (conformity-of-production sampling,
  records, quality system, documents, application).
- optional_or_advisory: not mandatory: "should", "may", "at the request of the applicant", permissions.
- condition_not_met: holds only if the product has a feature the product description rules out (for example
  "if the lamp uses replaceable light sources" when it uses LED modules). If the description is silent about the
  feature, do not use this label.
- not_applicable: concerns only functions, devices or vehicle categories this product does not have. A
  requirement whose scope covers the product together with others is not this.

Label every id."""


def _label_group(profile_line: str, reqs: list[dict]) -> dict[int, str]:
    lines = "\n".join(f"Q{i}: [{r['source']['regulation']} {r['source']['paragraph']}] {r['text']}"
                      for i, r in enumerate(reqs))
    result = ask(Labels, SYSTEM, f"<product>{profile_line}</product>\n\n<requirements>\n{lines}\n</requirements>",
                 effort="medium")
    out = {}
    for lab in result.labels:
        if lab.id.strip().startswith("Q") and lab.id.strip()[1:].isdigit():
            out[int(lab.id.strip()[1:])] = lab.kind
    return out


def verify(profile: ProductProfile, g: Graph, reqs: list[dict], group_size: int = 40) -> list[dict]:
    """Adds 'trace' (similarity to the source paragraph) and 'kind' to every requirement."""
    for r in reqs:
        r["trace"] = round(fuzz.partial_ratio(r["text"].lower(), g.nodes[r["node"]]["text"].lower()), 1)
    profile_line = _profile_brief(profile)
    by_unit = collections.defaultdict(list)
    for i, r in enumerate(reqs):
        by_unit[(r["source"]["regulation"], r["unit"])].append(i)
    groups = [idx[k:k + group_size] for idx in by_unit.values() for k in range(0, len(idx), group_size)]
    with ThreadPoolExecutor(8) as pool:
        labelled = list(pool.map(lambda idx: (idx, _label_group(profile_line, [reqs[i] for i in idx])), groups))
    for idx, labels in labelled:
        for j, i in enumerate(idx):
            reqs[i]["kind"] = labels.get(j, "product_requirement")  # unlabelled: keep, recall first
    return reqs


def kept(reqs: list[dict]) -> list[dict]:
    return [r for r in reqs if r.get("kind", "product_requirement") in KEEP]


DEDUP_MIN = 93  # same requirement twice within one regulation; platform: 93 -> F2 0.630 vs 97 -> 0.625 (dev)


def dedup(reqs: list[dict]) -> list[dict]:
    """Drop near-identical requirements, keeping the one on the higher-ranked paragraph; document order kept.
    Within one regulation, across paragraphs: this rule scored 0.641 (v10) and 0.644 (v16) on the platform. A stricter
    rule that also required the same numbers (keeping per-band EMC limits) scored 0.610, with recall no higher.
    A same-paragraph-only variant was never scored on its own."""
    norm = [" ".join(r["text"].lower().split()) for r in reqs]
    order = sorted(range(len(reqs)), key=lambda i: (-reqs[i].get("para_score", 50), i))
    kept: list[int] = []
    for i in order:
        reg = reqs[i]["source"]["regulation"]
        if not any(reqs[j]["source"]["regulation"] == reg and fuzz.ratio(norm[i], norm[j]) >= DEDUP_MIN for j in kept):
            kept.append(i)
    return [reqs[i] for i in sorted(kept)]
