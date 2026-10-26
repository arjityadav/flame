"""Orchestrator: plain code that runs the agents in order and saves every intermediate result.

    uv run python -m pipeline.run cases/dev-rear-lamp                   # all stages built so far
    uv run python -m pipeline.run cases/dev-rear-lamp --stop-after profile
    LLM_OFFLINE=1 uv run python -m pipeline.run cases/dev-rear-lamp --resume   # no API calls: saved stages + cache

Writes out/<case_id>/01_profile.json, 02_regulations.json, ... and, when the case ships an expected.json,
prints how each stage compares with it.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

from pipeline import llm
from pipeline.extract import extract_all
from pipeline.graph import DATA, Graph
from pipeline.profile import ProductProfile, analyse
from pipeline.sections import choose_all
from pipeline.rank import rank
from pipeline.verify import KEEP, dedup, kept, verify
from pipeline.select_regs import select

ROOT = Path(__file__).resolve().parent.parent
RANK_MIN = 20  # paragraph-ranker cut-off: below it the hit rate (6%) is under the F2 break-even (~12%); dev-measured
# Graph rescue (off by default; GRAPH_RESCUE=1 turns it on): a requirement the ranker scored in [RESCUE_FLOOR, RANK_MIN)
# is kept when at least RESCUE_SHARE of its sibling paragraphs (same parent in the graph) are kept. Dev (v17 stages):
# 19 requirements on 13 paragraphs, 3 of them expected (23% hit rate against a ~14% break-even); chosen on 7 positives.
GRAPH_RESCUE = os.environ.get("GRAPH_RESCUE") == "1"
RESCUE_FLOOR, RESCUE_SHARE = 10, 0.5


def graph_rescue(ranked: list[dict], g: Graph) -> list[dict]:
    """The ranker's keepers plus the near-misses whose graph siblings are mostly kept, in the original order."""
    kept_nodes = {r["node"] for r in ranked if r["para_score"] >= RANK_MIN}
    out = []
    for r in ranked:
        if r["para_score"] >= RANK_MIN:
            out.append(r)
        elif r["para_score"] >= RESCUE_FLOOR:
            sibs = {c for p in g.out(r["node"], "CHILD_OF") for c in g.into(p, "CHILD_OF")} - {r["node"]}
            if sibs and len(sibs & kept_nodes) / len(sibs) >= RESCUE_SHARE:
                out.append({**r, "rescued": "graph"})
    return out
STAGES = ("profile", "regulations", "sections", "requirements", "verified")


def _save(out: Path, name: str, data) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(json.dumps(data, indent=1, ensure_ascii=False))


def _stage(out: Path, name: str, resume: bool, compute, valid=None):
    """Reuse out/<case>/<name> when resuming and it exists (and `valid(data)` holds); otherwise compute and save it."""
    path = out / name
    if resume and path.is_file():
        data = json.loads(path.read_text())
        if valid is None or valid(data):
            print(f"  (reused {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path})")
            return data
        print(f"  ({name} refers to paragraphs no longer in data/graph.json, e.g. after a re-ingest: recomputing)")
    data = compute()
    _save(out, name, data)
    return data


def _eval_regulations(case_dir: Path, result: dict) -> None:
    expected = case_dir / "expected.json"
    if not expected.is_file():
        return
    gold = {r["source"]["regulation"] for r in json.loads(expected.read_text())["requirements"]}
    got = set(result["selected"])
    hit = gold & got
    print(f"  regulations vs expected: precision {len(hit)}/{len(got)}, recall {len(hit)}/{len(gold)}")
    for r in sorted(gold - got):
        a = result["applicability"][r]
        why = result["superseded"].get(r) and f"superseded by {result['superseded'][r]}" or \
            result["verdicts"].get(r, {}).get("reason") or f"applicability={a['applies']}: {a['reason']}"
        print(f"    missed {r}: {why}")
    for r in sorted(got - gold):
        print(f"    extra  {r}: {result['verdicts'][r]['reason']}")


def _noop(stage: str, status: str, detail: str = "", counts: dict | None = None) -> None:
    pass


def execute(case_dir: Path, out: Path, *, resume: bool = False, team_id: str = "team-08",
            stop_after: str = STAGES[-1], on_event=_noop, checks: bool = True) -> Path | None:
    """Run the stages in order for one case folder, saving every intermediate result in `out`.

    `on_event(stage, status, detail, counts)` is called as each stage starts ("running") and ends ("done"), so the
    web app can show progress; the CLI ignores it. Returns the submission path, or None when stopped early.
    """
    case_dir = case_dir.resolve()

    print("① product analyst")
    on_event("profile", "running")
    profile = ProductProfile.model_validate(
        _stage(out, "01_profile.json", resume, lambda: analyse(case_dir).model_dump()))
    print(f"  {profile.product_name}: {len(profile.functions)} functions, {len(profile.parts)} parts, "
          f"electronics={profile.electronics}")
    on_event("profile", "done", f"{profile.product_name}: {len(profile.functions)} functions, {len(profile.parts)} parts")
    if stop_after == "profile":
        return None

    print("②③ regulation selection")
    on_event("regulations", "running", "checking all 56 regulations")
    g = Graph.load(DATA / "graph.json")
    result = _stage(out, "02_regulations.json", resume, lambda: select(profile, g))
    print(f"  selected: {result['selected']}")
    print(f"  superseded: {result['superseded']}")
    _eval_regulations(case_dir, result)
    on_event("regulations", "done", ", ".join(result["selected"]), {"regulations": len(result["selected"])})
    if stop_after == "regulations":
        return None

    print("④ section selectors")
    on_event("sections", "running")
    sections = _stage(out, "03_sections.json", resume, lambda: choose_all(profile, g, result),
                      valid=lambda d: all(p in g.nodes for s in d for u in s["units"] for p in u["paragraphs"]))
    nodes_ok = lambda d: all(r["node"] in g.nodes for r in d)
    for s in sections:
        n = sum(len(u["paragraphs"]) for u in s["units"])
        print(f"  {s['regulation']}: {len(s['units'])} units ({n} paragraphs) of {len(s['units']) + len(s['skipped'])}")
    on_event("sections", "done", f"{sum(len(s['units']) for s in sections)} sections to read")
    if stop_after == "sections":
        return None

    print("⑤ extractors")
    on_event("extract", "running")
    reqs = _stage(out, "04_requirements.json", resume, lambda: extract_all(profile, g, sections, case_dir.name),
                  valid=nodes_ok)
    print(f"  {len(reqs)} requirements: {dict(collections.Counter(r['source']['regulation'] for r in reqs))}")
    from pipeline.extract import UNGROUNDED
    if UNGROUNDED:
        print(f"  grounding check: dropped {len(UNGROUNDED)} requirements whose evidence is not a verbatim span "
              f"of their paragraph")
        _save(out, "04_ungrounded.json", UNGROUNDED)
    on_event("extract", "done", f"{len(reqs)} candidate requirements")
    if stop_after == "requirements":
        return None

    print("⑥ verifier and critic")
    on_event("verify", "running")
    reqs = _stage(out, "05_verified.json", resume, lambda: verify(profile, g, reqs), valid=nodes_ok)
    print(f"  kinds: {dict(collections.Counter(r['kind'] for r in reqs))}")
    print(f"  low traceability (<50% shared wording with source): {sum(r['trace'] < 50 for r in reqs)}")
    reqs = kept(reqs)
    print(f"  kept {len(reqs)} ({', '.join(sorted(KEEP))})")
    on_event("verify", "done", f"{len(reqs)} kept after the critic")

    print("⑥b paragraph ranker and de-duplication")
    on_event("rank", "running")
    reqs = _stage(out, "06_ranked.json", resume, lambda: rank(profile, g, reqs, case_dir.name), valid=nodes_ok)
    n = len(reqs)
    if GRAPH_RESCUE:
        reqs = graph_rescue(reqs, g)
        print(f"  ranker + graph rescue: kept {len(reqs)} of {n} ({sum('rescued' in r for r in reqs)} rescued by graph siblings)")
    else:
        reqs = [r for r in reqs if r["para_score"] >= RANK_MIN]
        print(f"  ranker: kept {len(reqs)} of {n} (paragraph score >= {RANK_MIN})")
    n = len(reqs)
    reqs = dedup(reqs)
    print(f"  de-duplication: kept {len(reqs)} of {n}")
    submission = {
        "team_id": team_id,
        "case_id": case_dir.name,
        "requirements": [{"id": f"R-{i + 1:04d}", "text": r["text"], "source": r["source"],
                          **({"test_case": r["test_case"]} if r["test_case"] else {})} for i, r in enumerate(reqs)],
    }
    sub_path = out / "submission.json"
    _save(out, "submission.json", submission)
    print(f"  wrote {sub_path}")
    on_event("rank", "done", f"{len(reqs)} requirements", {"requirements": len(reqs)})
    if checks:
        subprocess.run([sys.executable, str(ROOT / "tools" / "validate.py"), str(sub_path)])
        if (case_dir / "expected.json").is_file():
            subprocess.run([sys.executable, str(ROOT / "tools" / "score_local.py"), str(sub_path),
                            "--expected", str(case_dir / "expected.json")])
    return sub_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--stop-after", choices=STAGES, default=STAGES[-1])
    ap.add_argument("--team-id", default="team-08", help="shown on the platform after registering")
    ap.add_argument("--resume", action="store_true", help="reuse saved stage outputs in out/<case>/ where present")
    args = ap.parse_args(argv)
    case_dir = args.case_dir.resolve()
    execute(case_dir, ROOT / "out" / case_dir.name, resume=args.resume, team_id=args.team_id, stop_after=args.stop_after)
    print(llm.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
