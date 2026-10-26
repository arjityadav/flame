"""Regulation change (sub-goal) for the web app: which old requirements change between two versions, and why.

A case is a folder subgoal/<name>/ with old.pdf, new.pdf, files.json and requirements_old.json (the kit layout).
The analysis is pipeline/subgoal.py, plain code and deterministic, cached per case in memory. Each change comes
with what a side-by-side view needs: the old requirement, the old and new paragraph texts and pages, and the
individual edits ("'a' became 'b'") parsed from the reason.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.store import ROOT

SUBGOAL = ROOT / "subgoal"


def cases() -> list[dict]:
    out = []
    for d in sorted(SUBGOAL.glob("*/requirements_old.json")):
        case = d.parent
        if (case / "old.pdf").is_file() and (case / "new.pdf").is_file():
            out.append({"case": case.name, "case_id": f"subgoal-{case.name}",
                        "requirements": len(json.loads(d.read_text())),
                        "has_expected": (case / "expected.json").is_file()})
    return out


def case_dir(case: str) -> Path:
    path = (SUBGOAL / case).resolve()
    if path.parent != SUBGOAL.resolve() or not (path / "requirements_old.json").is_file():
        raise KeyError(case)
    return path


def _edits(reason: str) -> list[dict]:
    """"…amended in version 1.0.0: 'a' became 'b'; 'c' was added." -> [{"from": "a", "to": "b"}, {"added": "c"}]."""
    body = reason.split(": ", 1)[1] if ": " in reason and "amended" in reason.split(": ", 1)[0] else ""
    out = []
    for op in re.split(r"; (?=')", body.rstrip(".")):  # one edit per "; " that starts a new quoted fragment
        if m := re.fullmatch(r"'(.*)' became '(.*)'", op, re.S):
            out.append({"from": m[1], "to": m[2]})
        elif m := re.fullmatch(r"'(.*)' was added", op, re.S):
            out.append({"added": m[1]})
        elif m := re.fullmatch(r"'(.*)' was removed", op, re.S):
            out.append({"removed": m[1]})
    return out


@lru_cache(maxsize=8)
def analysis(case: str) -> dict:
    return analyse_dir(case_dir(case), case)


def analyse_dir(d: Path, case: str) -> dict:
    """The sub-goal analysis of a case folder (old.pdf, new.pdf, files.json, requirements_old.json) with what the
    change view needs per change."""
    from pipeline import subgoal
    from pipeline.ingest import parse_pdf

    res = subgoal.analyse(d)
    old = {p.label: p for p in parse_pdf((d / "old.pdf").resolve(), "old")}
    new = {p.label: p for p in parse_pdf((d / "new.pdf").resolve(), "new")}
    reqs = {r["id"]: r for r in json.loads((d / "requirements_old.json").read_text())}
    rows = []
    for c in res["changes"]:
        req = reqs.get(c.get("requirement_id") or "")
        old_label = (req or {}).get("source", {}).get("paragraph")
        new_label = (c.get("source") or {}).get("paragraph")
        o, n = old.get(old_label or ""), new.get(new_label or "")
        rows.append({
            "change": c["change"], "requirement_id": c.get("requirement_id"),
            "old_requirement": (req or {}).get("text"), "new_text": c.get("new_text"), "reason": c["reason"],
            "edits": _edits(c["reason"]),
            "old": {"paragraph": old_label, "page": o.page_start if o else (req or {}).get("source", {}).get("page"),
                    "text": o.text if o else None},
            "new": {"paragraph": new_label, "page": n.page_start if n else (c.get("source") or {}).get("page"),
                    "text": n.text if n else None},
        })
    counts = {k: sum(r["change"] == k for r in rows) for k in ("modified", "removed", "added")}
    out = {"case": case, "case_id": f"subgoal-{case}", "regulation": res["regulation"],
           "old_version": res["old_version"], "new_version": res["new_version"], "counts": counts,
           "requirements_old": len(reqs), "changes": rows, "check": None}
    if (d / "expected.json").is_file():
        out["check"] = _check(rows, res["changes"], json.loads((d / "expected.json").read_text()), reqs, new)
    return out


def _check(rows: list[dict], raw: list[dict], expected: dict, reqs: dict, new: dict) -> dict:
    """Mark each change against the case's expected answer (matching as in eval/subgoal_score.py) and list the
    expected changes we missed."""
    from rapidfuzz import fuzz

    from eval.subgoal_score import score

    gold = {c["requirement_id"]: c for c in expected["changes"] if c["requirement_id"]}
    gold_add = [c for c in expected["changes"] if c["change"] == "added"]
    for r in rows:
        if r["change"] != "added":
            g = gold.get(r["requirement_id"])
            r["verdict"] = "extra" if g is None else "correct" if g["change"] == r["change"] else "wrong_type"
            r["expected_text"] = g.get("new_text") if g else None
    mine = [i for i, c in enumerate(raw) if c["change"] == "added"]
    used: set[int] = set()
    missed = []
    for g in gold_add:
        para = (g.get("source") or {}).get("paragraph")
        cands = [(fuzz.token_set_ratio(raw[i]["new_text"], g["new_text"]), i) for i in mine
                 if i not in used and (raw[i].get("source") or {}).get("paragraph") == para]
        best = max(cands, default=(0, None))
        if best[1] is not None and best[0] >= 60:
            used.add(best[1])
            rows[best[1]]["verdict"], rows[best[1]]["expected_text"] = "correct", g["new_text"]
        else:
            n = new.get(para or "")
            missed.append({"change": "added", "requirement_id": None, "old_requirement": None, "new_text": g["new_text"],
                           "reason": g.get("reason", ""), "edits": [], "old": None, "new": {"paragraph": para, "page": n.page_start if n else None,
                                                                  "text": n.text if n else None}})
    for i in mine:
        rows[i].setdefault("verdict", "extra")
    for rid, g in gold.items():
        if not any(r["requirement_id"] == rid for r in rows):
            req = reqs.get(rid, {})
            missed.append({"change": g["change"], "requirement_id": rid, "old_requirement": req.get("text"),
                           "new_text": g.get("new_text"), "reason": g.get("reason", ""), "edits": [], "new": None,
                           "old": {"paragraph": req.get("source", {}).get("paragraph"),
                                   "page": req.get("source", {}).get("page"), "text": None}})
    sc = score({"changes": raw}, expected)
    return {"recall": round(sc["flagged_recall"], 3), "precision": round(sc["flagged_precision"], 3),
            "type_correct": sc["modified_removed"]["type_correct"], "flagged": sc["modified_removed"]["flagged"],
            "expected": len(gold) + len(gold_add), "missed": missed}


def submission(case: str) -> dict:
    """The changes file in the platform's sub-goal format (what pipeline.subgoal writes)."""
    a = analysis(case)
    from pipeline import subgoal
    raw = subgoal.analyse(case_dir(case))["changes"]
    return {"team_id": "team-08", "case_id": a["case_id"],
            "changes": [{k: v for k, v in c.items() if k != "similarity"} for c in raw]}
