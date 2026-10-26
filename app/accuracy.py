"""How a run's submission compares with its case's expected answer, for the "Accuracy" page. Plain code, no model.

A run is matched to a case of cases/ by its input files (same spec.md or drawing), so only the worked example has an
answer to compare with. Regulation and page level use the organisers' tools/score_local.py; on top of that, each
expected requirement counts as found when we cite its paragraph (normalised as the platform does), with the wording
similarity of our closest requirement on that paragraph as a hint. Requirement-level matching on the platform is done
by a language model, so this is a guide, not the platform score.
"""

from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from app import runs
from app.store import ROOT

_spec = importlib.util.spec_from_file_location("score_local", ROOT / "tools" / "score_local.py")
score_local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score_local)  # type: ignore[union-attr]


def find_case(run_id: str) -> Path | None:
    """The case under cases/ whose inputs this run was made from, if it has an expected answer."""
    inp = runs.run_dir(run_id) / "input"
    for case in sorted((ROOT / "cases").glob("*/expected.json")):
        d = case.parent
        for name in ("spec.md", "exploded_view.png"):
            if (inp / name).is_file() and (d / name).is_file() and (inp / name).read_bytes() == (d / name).read_bytes():
                return d
    return None


def _f2(p: float, r: float) -> float:
    return 0.0 if p + r == 0 else 5 * p * r / (4 * p + r)


def _with_f2(prf: dict) -> dict:
    return {**prf, "f2": _f2(prf["precision"], prf["recall"])}


def accuracy(run_id: str) -> dict:
    case = find_case(run_id)
    sub = runs.read_json(run_id, "submission.json")
    if case is None or sub is None:
        return {"run": run_id, "case": None}
    exp = json.loads((case / "expected.json").read_text())
    sc = score_local.score(sub, exp)
    key = lambda r: (r["source"]["regulation"], score_local._paragraph_key(r["source"].get("paragraph")))  # noqa: E731
    enriched = {r["id"]: r for r in runs.read_json(run_id, "07_enriched.json") or []}

    ours_by_para: dict[tuple, list[dict]] = defaultdict(list)
    for r in sub["requirements"]:
        ours_by_para[key(r)].append(r)
    gold_paras = {key(g) for g in exp["requirements"]}

    expected = []
    for g in exp["requirements"]:
        same = ours_by_para.get(key(g), [])
        best = max(((fuzz.token_set_ratio(g["text"], o["text"]), o["id"]) for o in same), default=(0, None))
        expected.append({"id": g["id"], "text": g["text"], "source": g["source"], "found": bool(same),
                         "similarity": round(best[0]) if same else None, "closest": best[1],
                         "ours": [o["id"] for o in same]})
    ours = [{"id": r["id"], "text": r["text"], "summary": enriched.get(r["id"], {}).get("summary"), "source": r["source"],
             "on_expected": key(r) in gold_paras} for r in sub["requirements"]]

    regs = sorted({r["source"]["regulation"] for r in sub["requirements"]} | {g["source"]["regulation"] for g in exp["requirements"]})
    per_reg = [{"regulation": reg,
                "expected": sum(e["source"]["regulation"] == reg for e in expected),
                "found": sum(e["source"]["regulation"] == reg and e["found"] for e in expected),
                "ours": sum(o["source"]["regulation"] == reg for o in ours),
                "ours_on_expected": sum(o["source"]["regulation"] == reg and o["on_expected"] for o in ours)} for reg in regs]

    our_paras = set(ours_by_para)
    p_par = len(our_paras & gold_paras) / len(our_paras) if our_paras else 0.0
    r_par = len(our_paras & gold_paras) / len(gold_paras) if gold_paras else 0.0
    return {
        "run": run_id, "case": case.name,
        "regulation_level": _with_f2(sc["regulation_level"]),
        "page_level": _with_f2(sc["page_level"]),
        "paragraph_level": {"precision": p_par, "recall": r_par, "f2": _f2(p_par, r_par),
                            "matched": len(our_paras & gold_paras), "submitted": len(our_paras), "expected": len(gold_paras)},
        "found": sum(e["found"] for e in expected), "n_expected": len(expected), "n_ours": len(ours),
        "per_regulation": per_reg, "expected": expected, "ours": ours,
    }
