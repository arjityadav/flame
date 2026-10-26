"""What the newer versions of the cited regulations change for a run's own requirements.

The submission cites the base versions. For every cited regulation that also has a latest version in the index, the
sub-goal analysis (pipeline/subgoal.py, plain code) runs with the run's requirements on that regulation as the old
requirements: which of them the new version modifies or removes, and what it adds in the chapters they come from.
Each change is linked to the drawn parts its requirement concerns. Saved to out/runs/<id>/09_upgrade.json.
"""

from __future__ import annotations

import json

from app import changes, parts, runs, store

INDEX = store.ROOT / "regulations" / "index.json"


def _pdf(reg: str, role: str):
    d = store.ROOT / "regulations" / "pdf" / reg.replace(" ", "_") / role
    return next(iter(sorted(d.glob("*.pdf"))), None) if d.is_dir() else None


def upgrade(run_id: str) -> dict:
    cached = runs.read_json(run_id, "09_upgrade.json")
    if cached is not None:
        return cached
    sub = runs.read_json(run_id, "submission.json") or {"requirements": []}
    index = json.loads(INDEX.read_text())
    versions = {r["regulation"]: {v["role"]: v["version_label"] for v in r["versions"]}
                for r in (index if isinstance(index, list) else index["regulations"])}
    try:
        drawn = parts.load(run_id)["parts"]
    except Exception:  # noqa: BLE001  (no drawing or parts not separated: changes are shown without parts)
        drawn = []
    part_of = {}
    for p in drawn:
        for rid in p.get("requirements", []):
            part_of.setdefault(rid, []).append(p["name"])

    out = []
    for reg in sorted({r["source"]["regulation"] for r in sub["requirements"]}):
        v = versions.get(reg, {})
        old, new = _pdf(reg, "base"), _pdf(reg, "latest")
        if "latest" not in v or old is None or new is None:
            out.append({"regulation": reg, "base": v.get("base"), "latest": None})
            continue
        d = runs.run_dir(run_id) / "upgrade" / reg.replace(" ", "_")
        d.mkdir(parents=True, exist_ok=True)
        for name, src in (("old.pdf", old), ("new.pdf", new)):
            if not (d / name).exists():
                (d / name).symlink_to(src.resolve())
        (d / "files.json").write_text(json.dumps([{"name": "old.pdf"}, {"name": "new.pdf"}]))
        (d / "requirements_old.json").write_text(json.dumps([r for r in sub["requirements"] if r["source"]["regulation"] == reg]))
        a = changes.analyse_dir(d, reg)
        for c in a["changes"]:
            c["parts"] = part_of.get(c.get("requirement_id") or "", [])
        out.append({"regulation": reg, "base": v["base"], "latest": v["latest"],
                    "pdf": {"old": f"/files/{reg.replace(' ', '_')}/base/{old.name}",
                            "new": f"/files/{reg.replace(' ', '_')}/latest/{new.name}"},
                    **{k: a[k] for k in ("counts", "requirements_old", "changes")}})
    result = {"run": run_id, "regulations": out}
    (runs.run_dir(run_id) / "09_upgrade.json").write_text(json.dumps(result, ensure_ascii=False))
    return result
