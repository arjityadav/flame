"""Requirement-level score, our stand-in for the platform's hidden judge.

    uv run python -m eval.judge out/dev-rear-lamp/submission.json
    uv run python -m eval.judge out/dev-rear-lamp/submission.json --expected cases/dev-rear-lamp/expected.json

Each expected requirement is matched one to one with a submitted requirement that states the same engineering
obligation (same scope, condition and constraint). Candidates are grouped by regulation and section (the first two
levels of the paragraph number, or the annex part), and one Claude call matches each group. Prints precision,
recall and F2, and writes the matched and missed items next to the submission for error analysis.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline.llm import ask, summary

ROOT = Path(__file__).resolve().parent.parent


def section_key(regulation: str, paragraph: str | None) -> tuple[str, str]:
    label = re.sub(r"\.?\s*table\s+\S+$", "", (paragraph or "").strip(), flags=re.I).rstrip(".")
    m = re.match(r"^(annex\s+\d+[a-z]?(?:\s+appendix\s+\d+)?(?:\s+part\s+\w+)?)", label, re.I)
    if m:
        return regulation, m[1].lower()
    return regulation, ".".join(label.split(".")[:2])


class Pair(BaseModel):
    expected: str = Field(description="E-id")
    submitted: str = Field(description="S-id")


class Matching(BaseModel):
    pairs: list[Pair]


SYSTEM = """You compare two lists of engineering requirements derived from the same section of a UN Regulation:
an expert's expected list (E-ids) and a submitted list (S-ids). Pair an expected requirement with a submitted
one only if all three agree:
- scope: the same subject (the same lamp function, device, part or test object);
- condition: the same circumstances in which it applies; dropping or adding a condition that changes when the
  obligation applies breaks the match;
- constraint: the same limit, value, range, unit or required property; the numbers must agree.
Wording may differ. A submitted requirement that merges several obligations matches at most one of them. A
submitted requirement that only refers to the expected content ("shall comply with paragraph X") does not match.
Each id may appear in at most one pair."""


def _match_group(expected: list[dict], submitted: list[dict]) -> list[tuple[str, str]]:
    if not expected or not submitted:
        return []
    e_lines = "\n".join(f"E{i}: [{x['source'].get('paragraph')}] {x['text']}" for i, x in enumerate(expected))
    s_lines = "\n".join(f"S{i}: [{x['source'].get('paragraph')}] {x['text']}" for i, x in enumerate(submitted))
    result = ask(Matching, SYSTEM, f"<expected>\n{e_lines}\n</expected>\n\n<submitted>\n{s_lines}\n</submitted>\n\n"
                                   "List the matching pairs.", effort="medium")
    pairs, used_e, used_s = [], set(), set()
    for p in result.pairs:  # enforce one-to-one in code
        e, s = p.expected.strip(), p.submitted.strip()
        if not (re.fullmatch(r"E\d+", e) and re.fullmatch(r"S\d+", s)):
            continue
        ei, si = int(e[1:]), int(s[1:])
        if ei < len(expected) and si < len(submitted) and ei not in used_e and si not in used_s:
            used_e.add(ei)
            used_s.add(si)
            pairs.append((expected[ei]["id"], submitted[si]["id"]))
    return pairs


def judge(submission: dict, expected: dict) -> dict:
    groups: dict[tuple, dict[str, list]] = collections.defaultdict(lambda: {"e": [], "s": []})
    for x in expected["requirements"]:
        groups[section_key(x["source"]["regulation"], x["source"].get("paragraph"))]["e"].append(x)
    for x in submission["requirements"]:
        groups[section_key(x["source"]["regulation"], x["source"].get("paragraph"))]["s"].append(x)
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda kv: (kv[0], _match_group(kv[1]["e"], kv[1]["s"])), groups.items()))
    matched = {e: s for _, pairs in results for e, s in pairs}
    n_sub, n_exp, n_match = len(submission["requirements"]), len(expected["requirements"]), len(matched)
    p = n_match / n_sub if n_sub else 0.0
    r = n_match / n_exp if n_exp else 0.0
    f2 = 5 * p * r / (4 * p + r) if p + r else 0.0
    per_section = []
    for key, g in sorted(groups.items()):
        m = sum(1 for x in g["e"] if x["id"] in matched)
        per_section.append({"regulation": key[0], "section": key[1], "expected": len(g["e"]), "submitted": len(g["s"]),
                            "matched": m})
    matched_s = set(matched.values())
    return {
        "precision": p, "recall": r, "f2": f2, "n_submitted": n_sub, "n_expected": n_exp, "n_matched": n_match,
        "per_section": per_section,
        "missed": [x for x in expected["requirements"] if x["id"] not in matched],
        "extra": [x for x in submission["requirements"] if x["id"] not in matched_s],
        "pairs": matched,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("submission", type=Path)
    ap.add_argument("--expected", type=Path, default=ROOT / "cases/dev-rear-lamp/expected.json")
    args = ap.parse_args(argv)
    res = judge(json.loads(args.submission.read_text()), json.loads(args.expected.read_text()))
    report = args.submission.with_name(args.submission.stem + ".judge.json")
    report.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(f"requirement level  precision {res['precision']:.3f}  recall {res['recall']:.3f}  F2 {res['f2']:.3f}   "
          f"{res['n_submitted']} submitted / {res['n_expected']} expected / {res['n_matched']} matched")
    by_reg = collections.defaultdict(lambda: [0, 0, 0])
    for s in res["per_section"]:
        t = by_reg[s["regulation"]]
        t[0] += s["submitted"]; t[1] += s["expected"]; t[2] += s["matched"]
    for reg, (s, e, m) in sorted(by_reg.items()):
        print(f"  {reg:9} submitted {s:4}  expected {e:4}  matched {m:4}")
    print(f"details in {report}; {summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
