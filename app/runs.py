"""Runs: one folder per analysis under out/runs/<run_id>/, holding the uploaded inputs and every stage file.

    out/runs/<id>/run.json          status, stages, counts, cost (written by the job runner)
    out/runs/<id>/input/            spec.md + exploded_view.<ext>, laid out like a kit case folder
    out/runs/<id>/01_profile.json … 06_ranked.json, submission.json   (pipeline, unchanged)
    out/runs/<id>/07_enriched.json  (agent ⑧)

A finished pipeline output elsewhere (e.g. Arjit's out/heldout-front-bumper) is brought in with
    uv run python -m app.runs import out/heldout-front-bumper --title "Front bumper"
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
from datetime import datetime
from pathlib import Path

from app.store import ROOT, store

RUNS = ROOT / "out" / "runs"
STAGES = [
    ("profile", "Product profile"),
    ("regulations", "Regulation selection"),
    ("sections", "Section selection"),
    ("extract", "Requirement extraction"),
    ("verify", "Verification"),
    ("rank", "Ranking and de-duplication"),
    ("enrich", "Plain language and priorities"),
]


def run_dir(run_id: str) -> Path:
    path = (RUNS / run_id).resolve()
    if path.parent != RUNS.resolve():
        raise KeyError(run_id)
    return path


def new_meta(run_id: str, title: str, preloaded: bool = False) -> dict:
    return {
        "id": run_id, "title": title, "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "queued", "stage": None, "preloaded": preloaded, "error": None, "cost_usd": 0.0,
        "counts": {}, "stages": [{"key": k, "label": label, "status": "pending", "detail": "", "seconds": None}
                                 for k, label in STAGES],
    }


def load_meta(run_id: str) -> dict:
    path = run_dir(run_id) / "run.json"
    if path.is_file():
        return json.loads(path.read_text())
    folder = run_dir(run_id)
    if not (folder / "submission.json").is_file():
        raise KeyError(run_id)
    meta = new_meta(run_id, run_id.replace("-", " ").capitalize(), preloaded=True)  # a folder made by hand
    finish_meta(meta, folder)
    save_meta(meta)
    return meta


def save_meta(meta: dict) -> None:
    folder = run_dir(meta["id"])
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "run.json.tmp"
    tmp.write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    tmp.replace(folder / "run.json")


def finish_meta(meta: dict, folder: Path) -> None:
    meta["status"], meta["stage"] = "done", None
    for s in meta["stages"]:
        s["status"] = "done"
    subs = json.loads((folder / "submission.json").read_text())["requirements"]
    meta["counts"] = {"requirements": len(subs), "regulations": len({r["source"]["regulation"] for r in subs})}


def list_runs() -> list[dict]:
    RUNS.mkdir(parents=True, exist_ok=True)
    out = []
    for folder in RUNS.iterdir():
        if folder.is_dir():
            try:
                out.append(load_meta(folder.name))
            except (KeyError, json.JSONDecodeError):
                continue
    return sorted(out, key=lambda m: m["created"], reverse=True)


def read_json(run_id: str, name: str):
    path = run_dir(run_id) / name
    return json.loads(path.read_text()) if path.is_file() else None


def requirements(run_id: str) -> list[dict]:
    """Enriched requirements in priority order; enrichment by rules is made on the fly if ⑧ has not run."""
    folder = run_dir(run_id)
    if not (folder / "07_enriched.json").is_file():
        if not (folder / "submission.json").is_file():
            return []
        from pipeline.enrich import enrich
        enrich(folder, use_model=False)
    reqs = json.loads((folder / "07_enriched.json").read_text())
    for r in reqs:  # attach the paragraph node the viewer opens
        src = r["source"]
        if not r.get("node") or r["node"] not in store.paragraphs:
            r["node"] = store.resolve(src["regulation"], src["version"], src.get("paragraph"), src.get("page"))
        r["regulation_title"] = store.titles.get(src["regulation"], "")
    return reqs


def regulations(run_id: str) -> dict:
    """Which regulations were chosen and why (from 02_regulations.json), for the profile screen."""
    data = read_json(run_id, "02_regulations.json") or {}
    selected = data.get("selected", [])
    reasons = {}
    for reg, verdict in (data.get("verdicts") or {}).items():
        reasons[reg] = verdict.get("reason") if isinstance(verdict, dict) else str(verdict)
    for reg, a in (data.get("applicability") or {}).items():
        reasons.setdefault(reg, a.get("reason") if isinstance(a, dict) else str(a))
    app_ = data.get("applicability") or {}
    rows = [{"regulation": reg, "title": store.titles.get(reg, ""), "selected": reg in selected,
             "superseded_by": (data.get("superseded") or {}).get(reg), "reason": reasons.get(reg, ""),
             "applies": (app_.get(reg) or {}).get("applies"), "basis": (app_.get(reg) or {}).get("basis"),
             "functions_covered": (app_.get(reg) or {}).get("functions_covered", []),
             "scope_evidence": (app_.get(reg) or {}).get("scope_evidence", "")}
            for reg in sorted(reasons, key=lambda r: (r not in selected, r))]
    return {"selected": selected, "rows": rows}


def export_csv(run_id: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["priority", "id", "phase", "severity", "summary", "requirement", "regulation", "version", "page",
                "paragraph", "severity_reason", "phase_reason", "test_case"])
    for r in requirements(run_id):
        s = r["source"]
        w.writerow([r["priority"], r["id"], r["phase_label"], r["severity"], r["summary"], r["text"], s["regulation"],
                    s["version"], s["page"], s.get("paragraph", ""), r["severity_reason"], r["phase_reason"],
                    r.get("test_case") or ""])
    return buf.getvalue()


def import_folder(src: Path, run_id: str | None = None, title: str | None = None) -> str:
    """Copy a finished pipeline folder (with submission.json) and its case inputs into out/runs/."""
    if not (src / "submission.json").is_file():
        raise SystemExit(f"{src} has no submission.json")
    run_id = run_id or src.name
    dst = run_dir(run_id)
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("0*.json"):
        shutil.copy(f, dst / f.name)
    shutil.copy(src / "submission.json", dst / "submission.json")
    case = ROOT / "cases" / src.name
    if case.is_dir():
        (dst / "input").mkdir(exist_ok=True)
        for f in case.iterdir():
            if f.name != "expected.json" and f.is_file():
                shutil.copy(f, dst / "input" / f.name)
    meta = new_meta(run_id, title or src.name.replace("-", " ").capitalize(), preloaded=True)
    finish_meta(meta, dst)
    save_meta(meta)
    (dst / "07_enriched.json").unlink(missing_ok=True)
    return run_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bring a finished pipeline folder into the web app's run list.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("folder", type=Path)
    imp.add_argument("--id")
    imp.add_argument("--title")
    args = ap.parse_args(argv)
    run_id = import_folder(args.folder.resolve(), args.id, args.title)
    print(f"imported as out/runs/{run_id}; enrich with: uv run python -m pipeline.enrich out/runs/{run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
