"""Score a sub-goal changes file against an expected one, offline and without a model.

    uv run python -m eval.subgoal_score out/subgoal-dev/changes.json
    uv run python -m eval.subgoal_score out/subgoal-dev/changes.json --expected subgoal/dev/expected.json

The platform scores recall of the requirements flagged for change and the accuracy of the change stated (with a
model). Here: modified/removed are matched by requirement id (change type must agree for "type correct"); added are
matched one to one by new paragraph and wording. Wording similarity of new_text is reported as a rough proxy for
accuracy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent


def score(sub: dict, exp: dict) -> dict:
    mine = {c["requirement_id"]: c for c in sub["changes"] if c["requirement_id"]}
    gold = {c["requirement_id"]: c for c in exp["changes"] if c["requirement_id"]}
    hit = [r for r in gold if r in mine]
    type_ok = [r for r in hit if mine[r]["change"] == gold[r]["change"]]
    text_sim = [fuzz.ratio(mine[r].get("new_text") or "", gold[r].get("new_text") or "") for r in type_ok
                if gold[r]["change"] == "modified"]

    gold_add = [c for c in exp["changes"] if c["change"] == "added"]
    my_add = [c for c in sub["changes"] if c["change"] == "added"]
    used, add_hits, add_sim = set(), [], []
    for g in gold_add:
        best, best_s = None, 0.0
        for i, m in enumerate(my_add):
            if i in used or (m.get("source") or {}).get("paragraph") != (g.get("source") or {}).get("paragraph"):
                continue
            s = fuzz.token_set_ratio(m["new_text"], g["new_text"])
            if s > best_s:
                best, best_s = i, s
        if best is not None and best_s >= 60:
            used.add(best)
            add_hits.append(g)
            add_sim.append(best_s)

    n_exp = len(gold) + len(gold_add)
    n_sub = len(mine) + len(my_add)
    found = len(type_ok) + len(add_hits)
    return {
        "flagged_recall": found / n_exp if n_exp else 0.0,
        "flagged_precision": found / n_sub if n_sub else 0.0,
        "modified_removed": {"expected": len(gold), "flagged": len(hit), "type_correct": len(type_ok),
                             "submitted": len(mine), "missed": sorted(set(gold) - set(mine)),
                             "extra": sorted(set(mine) - set(gold))},
        "added": {"expected": len(gold_add), "found": len(add_hits), "submitted": len(my_add),
                  "missed": [g["source"]["paragraph"] + ": " + g["new_text"][:80] for g in gold_add
                             if g not in add_hits]},
        "new_text_similarity_modified": round(sum(text_sim) / len(text_sim), 1) if text_sim else None,
        "new_text_similarity_added": round(sum(add_sim) / len(add_sim), 1) if add_sim else None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("changes", type=Path)
    ap.add_argument("--expected", type=Path, default=ROOT / "subgoal/dev/expected.json")
    args = ap.parse_args(argv)
    res = score(json.loads(args.changes.read_text()), json.loads(args.expected.read_text()))
    print(json.dumps(res, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
