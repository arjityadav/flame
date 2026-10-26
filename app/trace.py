"""What each agent of a run decided, for the "Agents" page: built from the stage files the pipeline saves.

Each stage file appears in out/runs/<id>/ when its stage finishes, so the trace of a running analysis grows as the
agents work; for a finished run it is the full record. Agents are Claude calls (model and effort as configured);
"code" steps are plain code between them (citations, grounding check, duplicate removal).
"""

from __future__ import annotations

import collections

from rapidfuzz import fuzz

from app import runs
from pipeline.run import RANK_MIN
from pipeline.verify import DEDUP_MIN, KEEP

STAGE_OF = {"profile": "profile", "applicability": "regulations", "reconciler": "regulations", "sections": "sections",
            "extractor": "extract", "grounding": "extract", "critic": "verify", "ranker": "rank", "dedup": "rank",
            "enrich": "enrich"}


def _status(meta: dict, key: str, has_output: bool) -> str:
    if has_output:
        return "done"
    stage = next((s for s in meta.get("stages", []) if s["key"] == STAGE_OF[key]), None)
    return stage["status"] if stage else "pending"


def trace(run_id: str) -> dict:
    meta = runs.load_meta(run_id)
    rd = lambda name: runs.read_json(run_id, name)  # noqa: E731
    profile, regs, sections = rd("01_profile.json"), rd("02_regulations.json"), rd("03_sections.json")
    extracted, ungrounded, verified = rd("04_requirements.json"), rd("04_ungrounded.json"), rd("05_verified.json")
    ranked, submission, enriched = rd("06_ranked.json"), rd("submission.json"), rd("07_enriched.json")
    seconds = {s["key"]: s.get("seconds") for s in meta.get("stages", [])}
    agents = []

    def add(key, name, kind, role, model, out, summary, details, calls=None, handoff=""):
        agents.append({"key": key, "name": name, "kind": kind, "role": role, "model": model, "calls": calls,
                       "status": _status(meta, key, out is not None), "seconds": seconds.get(STAGE_OF[key]),
                       "summary": summary if out is not None else "", "details": details if out is not None else {},
                       "handoff": handoff})

    add("profile", "Product analyst", "agent", "Reads the specification and the drawing (vision) into a product profile",
        "Opus 5.5 · high", profile,
        f"{len(profile['functions'])} functions, {len(profile['parts'])} parts, "
        f"{len(profile['electronics'])} electronic items" if profile else "",
        {"product": profile.get("product_type") if profile else "", "functions": [f["name"] for f in profile["functions"]],
         "parts": [p["name"] for p in profile["parts"]], "categories": profile.get("vehicle_categories", [])}
        if profile else {}, calls=1, handoff="product profile → every later agent")

    app_ = (regs or {}).get("applicability", {})
    counts = collections.Counter(a.get("applies") for a in app_.values())
    add("applicability", "Applicability agents", "agent", "One agent per regulation, in parallel: does this one apply?",
        "Opus 5.5 · medium", regs or None,
        f"{len(app_)} regulations judged: {counts.get('yes', 0)} yes, {counts.get('maybe', 0)} maybe, "
        f"{counts.get('no', 0)} no",
        {"items": [{"regulation": r, "applies": a.get("applies"), "basis": a.get("basis"), "reason": a.get("reason", "")}
                   for r, a in sorted(app_.items(), key=lambda kv: int(''.join(ch for ch in kv[0] if ch.isdigit()) or 0))]},
        calls=len(app_) or 56, handoff="yes/maybe candidates → reconciler")

    sup = (regs or {}).get("superseded", {})
    verdicts = (regs or {}).get("verdicts", {})
    add("reconciler", "Reconciler", "agent", "Code drops regulations replaced by a consolidated one; the agent resolves "
        "the remaining overlaps", "Opus 5.5 · high", regs or None,
        f"selected {', '.join((regs or {}).get('selected', []))}",
        {"superseded": [{"old": o, "new": n} for o, n in sup.items()],
         "verdicts": [{"regulation": r, "include": v.get("include"), "reason": v.get("reason", "")} for r, v in verdicts.items()],
         "selected": (regs or {}).get("selected", [])}, calls=1, handoff="selected regulations → section selectors")

    add("sections", "Section selectors", "agent", "One agent per selected regulation: which chapters to read",
        "Opus 5.5 · high", sections,
        f"{sum(len(s['units']) for s in sections)} sections, {sum(len(u['paragraphs']) for s in sections for u in s['units'])}"
        f" paragraphs to read" if sections else "",
        {"regulations": [{"regulation": s["regulation"], "chosen": len(s["units"]), "total": len(s["units"]) + len(s["skipped"]),
                          "paragraphs": sum(len(u["paragraphs"]) for u in s["units"]),
                          "examples": [f"{u['key']} {u['title'][:60]}" for u in s["units"][:6]]} for s in sections]}
        if sections else {}, calls=len(sections) if sections else None, handoff="sections → extractors")

    by_reg = collections.Counter(r["source"]["regulation"] for r in extracted) if extracted else {}
    add("extractor", "Extractors", "agent", "Turn legal text into one-obligation requirements, each quoting its source",
        "Opus 5.5 · high", extracted,
        f"{len(extracted) + len(ungrounded or [])} candidate requirements" if extracted else "",
        {"by_regulation": dict(by_reg), "example": (extracted[0]["text"][:220], extracted[0].get("evidence", "")[:160],
                                                    f"{extracted[0]['source']['regulation']} {extracted[0]['source']['paragraph']}")
         if extracted else None},
        handoff="requirements → grounding check")

    add("grounding", "Grounding check", "code", "Code: the quoted evidence must occur in the cited paragraph, else drop",
        "code", extracted, f"{len(ungrounded or [])} dropped, {len(extracted) if extracted else 0} kept",
        {"dropped": [{"text": u["text"][:160], "evidence": u.get("evidence", "")[:100]} for u in (ungrounded or [])[:5]]},
        handoff="grounded requirements → critic")

    kinds = collections.Counter(r.get("kind") for r in verified) if verified else {}
    add("critic", "Critic", "agent", "Labels each requirement's kind; code keeps the kinds that experts list",
        "Opus 5.5 · medium", verified,
        f"kept {sum(v for k, v in kinds.items() if k in KEEP)} of {len(verified)}" if verified else "",
        {"kinds": [{"kind": k, "count": v, "kept": k in KEEP} for k, v in kinds.most_common()]},
        handoff="kept requirements → paragraph ranker")

    if ranked:
        scores = [r.get("para_score", 50) for r in ranked]
        hist = [sum(1 for s in scores if lo <= s < lo + 10 or (lo == 90 and s == 100)) for lo in range(0, 100, 10)]
        kept_rank = sum(1 for s in scores if s >= 20)
    add("ranker", "Paragraph ranker", "agent", "Scores 0–100 whether an expert would list each paragraph; code keeps ≥ 20",
        "Opus 5.5 · medium", ranked, f"kept {kept_rank} of {len(ranked)}" if ranked else "",
        {"histogram": hist, "cut": 20} if ranked else {}, handoff="ranked requirements → duplicate removal")

    add("dedup", "Duplicate removal", "code", "Code: near-identical requirements (≥ 93%) within a regulation are merged",
        "code", submission, f"{len(submission['requirements'])} requirements in the submission" if submission else "",
        {"final": len(submission["requirements"]) if submission else 0}, handoff="submission → enrich")

    sev = collections.Counter(r.get("severity") for r in enriched) if enriched else {}
    add("enrich", "Enrich", "agent", "Plain-language summary, severity and project phase for every requirement",
        "Opus 5.5 · low", enriched, f"{len(enriched)} rated" if enriched else "",
        {"severity": {str(k): v for k, v in sorted(sev.items())}} if enriched else {})
    return {"run": run_id, "status": meta.get("status"), "title": meta.get("title"), "cost_usd": meta.get("cost_usd"),
            "agents": agents}


def journeys(run_id: str) -> dict:
    """Every candidate requirement and how far it got: where each agent passed it on or stopped it."""
    rd = lambda name: runs.read_json(run_id, name) or []  # noqa: E731
    sections, verified, ranked = rd("03_sections.json"), rd("05_verified.json"), rd("06_ranked.json")
    ungrounded, enriched = rd("04_ungrounded.json"), rd("07_enriched.json")
    submission = (runs.read_json(run_id, "submission.json") or {}).get("requirements", [])
    units = {(s["regulation"], u["key"]): u for s in sections for u in s["units"]}
    score = {(r["node"], r["text"]): r.get("para_score") for r in ranked}
    final = {(r["source"]["regulation"], r["source"]["paragraph"], r["text"]): r["id"] for r in submission}
    summary = {r["id"]: r.get("summary") for r in enriched}
    norm = lambda t: " ".join(t.lower().split())  # noqa: E731  (as in pipeline.verify.dedup)
    kept_final = [(r["source"]["regulation"], norm(r["text"]), r["id"]) for r in submission]

    items = []
    for r in verified:
        src, key = r["source"], (r["node"], r["text"])
        unit = units.get((src["regulation"], r.get("unit")), {})
        item = {"regulation": src["regulation"], "paragraph": src["paragraph"], "page": src["page"], "text": r["text"],
                "evidence": r.get("evidence", ""), "section": f"{unit.get('key', r.get('unit', ''))} {unit.get('title', '')}".strip(),
                "section_reason": unit.get("reason", ""), "kind": r.get("kind"), "score": score.get(key), "id": None,
                "duplicate_of": None}
        if r.get("kind") not in KEEP:
            item["stopped"] = "critic"
        elif (item["score"] if item["score"] is not None else 50) < RANK_MIN:
            item["stopped"] = "ranker"
        elif (rid := final.get((src["regulation"], src["paragraph"], r["text"]))):
            item["stopped"], item["id"], item["summary"] = None, rid, summary.get(rid)
        else:
            item["stopped"] = "dedup"
            best = max(((fuzz.ratio(norm(r["text"]), t), rid) for reg, t, rid in kept_final if reg == src["regulation"]),
                       default=(0, None))
            item["duplicate_of"] = best[1] if best[0] >= DEDUP_MIN else None
        items.append(item)
    for u in ungrounded:
        reg, _, para = u["node"].removeprefix("para:").partition("#")
        items.append({"regulation": reg.split("@")[0], "paragraph": para, "page": None, "text": u["text"],
                      "evidence": u.get("evidence", ""), "section": "", "section_reason": "", "kind": None, "score": None,
                      "id": None, "duplicate_of": None, "stopped": "grounding"})
    for i, item in enumerate(items):
        item["i"] = i
    return {"run": run_id, "rank_min": RANK_MIN, "items": items}
