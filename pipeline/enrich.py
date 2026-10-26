"""Agent 8, enrich: make each submitted requirement readable for an engineer who is not a regulation expert.

Adds a plain-language summary, a severity (1-5) and the development phase by which the requirement must be met,
each with a one-line reason, then orders the list by priority: earlier phase first, then higher severity. The
priority is computed here in code, never by the model. The scored submission.json is not touched; the result is
written to out/<run>/07_enriched.json.

    uv run python -m pipeline.enrich out/dev-rear-lamp            # rate with Claude (cached), fall back to rules

Without an API key (or for requirements the model skipped) a keyword rule set rates the requirement instead, and
says so in `rated_by`.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline import llm

SEVERITY = {
    5: "Safety-critical performance",
    4: "Blocks type approval if failed",
    3: "Installation or integration constraint",
    2: "Marking or documentation",
    1: "Optional or advisory",
}
PHASES = {
    1: "Concept and architecture",
    2: "Detailed design",
    3: "Design verification",
    4: "Type approval",
    5: "Series production",
}
BATCH = 30

RUBRIC = """Severity (how bad it is if the product does not meet it):
5 Safety-critical performance: light output, visibility angles, impact or injury limits, anything that makes the
  vehicle unsafe for its users or other road users if missed.
4 Blocks type approval if failed: colour limits, EMC limits, required test results, durability tests.
3 Installation or integration constraint: mounting height, spacing, orientation, electrical connections, switching.
2 Marking or documentation: approval mark, trade name, space for markings, information to supply.
1 Optional or advisory: "may", permissions, recommendations.

Phase (by when it must be met; earlier is more urgent because it is more expensive to fix later):
1 Concept and architecture: which functions or devices are mandatory, device categories, positions on the vehicle
  that fix packaging.
2 Detailed design: geometry, materials, optics, colour, electrical design, space for markings; must be right before
  design freeze.
3 Design verification: photometric, colorimetric, EMC and environmental tests the part must pass before it is
  submitted for approval.
4 Type approval: approval marking, documents, communication with the authority.
5 Series production: conformity of production."""

SYSTEM = f"""You help design engineers who are not regulation experts understand the UN Regulation requirements their
product must meet. For each requirement you get, write:
- summary: what the engineer has to do, in at most 25 plain words. Name the part and the number or limit if there
  is one. No paragraph numbers, no legal phrasing.
- severity (1-5) and a reason of at most 20 words;
- phase (1-5) and a reason of at most 20 words.

{RUBRIC}

Rate every requirement id you are given."""


class Rated(BaseModel):
    id: str = Field(description="the Q-id of the requirement")
    summary: str
    severity: int = Field(ge=1, le=5)
    severity_reason: str
    phase: int = Field(ge=1, le=5)
    phase_reason: str


class RatedBatch(BaseModel):
    items: list[Rated]


# ------------------------------------------------------------------ rules (fallback without a model)

_RULES_SEVERITY = [
    (5, r"luminous intensit|\bcd\b|candela|geometric visibility|visib|head injury|\bhic\b|impact|legform|headform"),
    (4, r"colou?r|trichromatic|chromaticity|\bdb\b|v/m|emission|immunity|disturbance|shall not exceed|test|withstand"
        r"|resistance|corrosion|heat|water|dust|photometric|reflect"),
    (3, r"mounted|installed|installation|height above the ground|above the ground|distance|spacing|position"
        r"|electrical connection|switched|switch(?:ed)? on|wiring|aim|orientation|width"),
    (2, r"mark(?:ing)?s?\b|approval mark|trade name|legible|indelible|label|documents?|information"),
]
_RULES_PHASE = [
    (5, r"conformity of production|series production|mass-produced|production sampling|manufacturer shall ensure"),
    (4, r"approval mark|approval number|type-approval mark|communication|application for"),
    (3, r"when (?:tested|measured)|test|measured|measurement|luminous intensit|photometric|\bcd\b|trichromatic|\bdb\b"
        r"|v/m|emission|immunity|shall withstand|annex \d+.*(?:method|procedure)"),
    (1, r"mandatory|optional|shall be fitted|number\b|category|categories|presence|position on the vehicle"
        r"|height above the ground|width|length"),
]


def rate_by_rules(req: dict) -> dict:
    text = req["text"].lower()
    kind = req.get("kind") or ""
    severity, severity_reason = 3, "General design obligation."
    if kind == "optional_or_advisory" or (re.search(r"\bmay\b", text) and "shall" not in text):
        severity, severity_reason = 1, "Optional: the regulation allows it rather than requires it."
    else:
        for level, pattern in _RULES_SEVERITY:
            if m := re.search(pattern, text):
                severity, severity_reason = level, f"Mentions “{m.group(0)}”: {SEVERITY[level].lower()}."
                break
    phase, phase_reason = 2, "Shapes the detailed design."
    if kind == "manufacturer_process":
        phase, phase_reason = 5, "A production-control duty."
    else:
        for level, pattern in _RULES_PHASE:
            if m := re.search(pattern, text):
                phase, phase_reason = level, f"Mentions “{m.group(0)}”: {PHASES[level].lower()}."
                break
    first = re.split(r"(?<=[.;])\s", req["text"].strip())[0]
    summary = first if len(first.split()) <= 30 else " ".join(first.split()[:28]) + " …"
    return {"summary": summary, "severity": severity, "severity_reason": severity_reason,
            "phase": phase, "phase_reason": phase_reason, "rated_by": "rules"}


# ------------------------------------------------------------------ model rating

def _profile_line(out: Path) -> str:
    path = out / "01_profile.json"
    if not path.is_file():
        return "(no product profile)"
    p = json.loads(path.read_text())
    return (f"{p.get('product_name')}: {p.get('product_type')}. Vehicle categories: {', '.join(p.get('vehicle_categories', []))}. "
            f"Functions: {'; '.join(f['name'] for f in p.get('functions', []))}.")


def _rate_batch(profile: str, batch: list[dict]) -> dict[int, dict]:
    lines = []
    for i, r in enumerate(batch):
        src = r["source"]
        lines.append(f"Q{i}: [{src['regulation']} {src.get('paragraph', '')}] {r['text']}"
                     + (f"\n    critic kind: {r['kind']}" if r.get("kind") else "")
                     + (f"; applies to: {r['applies_to']}" if r.get("applies_to") else ""))
    content = f"<product>{profile}</product>\n\n<requirements>\n" + "\n".join(lines) + "\n</requirements>"
    result = llm.ask(RatedBatch, SYSTEM, content, effort="low")
    out = {}
    for item in result.items:
        key = item.id.strip().lstrip("Q")
        if key.isdigit() and int(key) < len(batch):
            out[int(key)] = {**item.model_dump(exclude={"id"}), "rated_by": "model"}
    return out


def _guardrails(req: dict, rating: dict) -> dict:
    """Rules that hold whatever the model says."""
    kind = req.get("kind") or ""
    if kind == "optional_or_advisory":
        rating["severity"] = min(rating["severity"], 1)
    if kind == "manufacturer_process":
        rating["phase"] = 5
    return rating


def details(out: Path) -> dict[tuple, dict]:
    """Extra fields the pipeline saved per requirement (node, applies_to, kind, scores), keyed by (text, reg, para)."""
    extra = {}
    for name in ("06_ranked.json", "05_verified.json", "04_requirements.json"):
        path = out / name
        if path.is_file():
            for r in json.loads(path.read_text()):
                extra.setdefault((r["text"], r["source"]["regulation"], r["source"].get("paragraph")), r)
    return extra


def enrich(out: Path, *, use_model: bool | None = None, workers: int = 8) -> list[dict]:
    """Rate every requirement in out/submission.json and write 07_enriched.json in priority order."""
    submission = json.loads((out / "submission.json").read_text())["requirements"]
    extra = details(out)
    reqs = []
    for order, r in enumerate(submission):
        e = extra.get((r["text"], r["source"]["regulation"], r["source"].get("paragraph")), {})
        reqs.append({
            "id": r["id"], "text": r["text"], "source": r["source"], "order": order,
            "node": e.get("node"), "applies_to": e.get("applies_to"), "test_case": r.get("test_case") or e.get("test_case"),
            "kind": e.get("kind"), "para_score": e.get("para_score"), "unit": e.get("unit"),
            "evidence": e.get("evidence"),  # verbatim span of the source paragraph (grounding check, v17+)
        })

    use_model = llm.available() if use_model is None else use_model
    ratings: dict[str, dict] = {}
    if use_model:
        groups = collections.defaultdict(list)
        for r in reqs:
            groups[(r["source"]["regulation"], r.get("unit") or r["source"].get("paragraph", "").split(".")[0])].append(r)
        batches = [g[i:i + BATCH] for g in groups.values() for i in range(0, len(g), BATCH)]
        profile = _profile_line(out)
        with ThreadPoolExecutor(workers) as pool:
            for batch, result in zip(batches, pool.map(lambda b: _rate_batch(profile, b), batches)):
                for i, rating in result.items():
                    ratings[batch[i]["id"]] = rating

    for r in reqs:
        r.update(_guardrails(r, ratings.get(r["id"]) or rate_by_rules(r)))
        r["severity_label"] = SEVERITY[r["severity"]]
        r["phase_label"] = PHASES[r["phase"]]

    reg_order = {reg: i for i, reg in enumerate(dict.fromkeys(r["source"]["regulation"] for r in reqs))}
    reqs.sort(key=lambda r: (r["phase"], -r["severity"], reg_order[r["source"]["regulation"]], r["order"]))
    for i, r in enumerate(reqs, 1):
        r["priority"] = i
    (out / "07_enriched.json").write_text(json.dumps(reqs, indent=1, ensure_ascii=False))
    return reqs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path, help="run folder holding submission.json")
    ap.add_argument("--rules", action="store_true", help="rate with the keyword rules only (no model calls)")
    args = ap.parse_args(argv)
    reqs = enrich(args.out, use_model=False if args.rules else None)
    by = collections.Counter(r["rated_by"] for r in reqs)
    print(f"rated {len(reqs)} requirements ({dict(by)}); phases {dict(collections.Counter(r['phase'] for r in reqs))}, "
          f"severity {dict(collections.Counter(r['severity'] for r in reqs))}; {llm.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
