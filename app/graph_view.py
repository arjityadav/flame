"""Graph payloads for the two views of a run, cached in the run folder.

Structure: regulations → sections → the requirement paragraphs (with their parent paragraphs), plus the paragraphs
they refer to and the links between regulations. Laid out on the client with ForceAtlas2 from seeded positions,
so each regulation forms its own cluster.

Meaning: the paragraphs of the run's regulations placed by their text embeddings (UMAP to 2-D), with lines from
each requirement paragraph to its 3 nearest neighbours, so similar obligations sit together across regulations.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import defaultdict

import numpy as np

from app.runs import read_json, requirements, run_dir
from app.store import store

ADMIN = re.compile(r"application for|approval\b|communication|conformity of production|transitional|names and addresses"
                   r"|modification|penalties|production definitively|arrangements? of|information document|model of",
                   re.I)


def _req_index(run_id: str) -> tuple[list[str], dict[str, list[dict]]]:
    reqs = requirements(run_id)
    regs = (read_json(run_id, "02_regulations.json") or {}).get("selected") or \
        list(dict.fromkeys(r["source"]["regulation"] for r in reqs))
    by_node: dict[str, list[dict]] = defaultdict(list)
    for r in reqs:
        if r.get("node"):
            by_node[r["node"]].append(r)
    return regs, by_node


def _paragraph_node(pid: str, reqs: list[dict]) -> dict:
    p = store.paragraphs[pid]
    node = {"id": pid, "kind": "paragraph", "label": p["label"], "regulation": p["regulation"], "page": p["page_start"]}
    if reqs:
        node.update({
            "requirement_ids": [r["id"] for r in reqs],
            "severity": max(r["severity"] for r in reqs),
            "phase": min(r["phase"] for r in reqs),
            "summaries": [r["summary"] for r in reqs[:3]],
            "priority": min(r["priority"] for r in reqs),
        })
    else:
        node["excerpt"] = p["text"][:140] + ("…" if len(p["text"]) > 140 else "")
    return node


def _section_key(p: dict) -> str:
    if p["annex"]:
        return p["annex"]
    return (p["number"] or "0").split(".")[0]


def structure(run_id: str) -> dict:
    cache = run_dir(run_id) / "graph_structure.json"
    if cache.is_file():
        return json.loads(cache.read_text())
    regs, by_node = _req_index(run_id)
    g = store.graph
    wanted: set[str] = set(by_node)
    for pid in list(by_node):  # parent chain and referenced paragraphs
        current = pid
        for _ in range(8):
            parents = g.out(current, "CHILD_OF")
            if not parents:
                break
            current = parents[0]
            wanted.add(current)
        for target in g.out(pid, "REFERS_TO"):
            tp = store.paragraphs.get(target)
            if tp and tp["role"] == "base" and tp["regulation"] in regs:
                wanted.add(target)

    rng = random.Random(7)
    nodes, edges = {}, []
    centre = {reg: (math.cos(2 * math.pi * i / max(1, len(regs))) * 100, math.sin(2 * math.pi * i / max(1, len(regs))) * 100)
              for i, reg in enumerate(regs)}
    for reg in regs:
        x, y = centre[reg]
        nodes[f"reg:{reg}"] = {"id": f"reg:{reg}", "kind": "regulation", "label": reg, "regulation": reg,
                               "title": store.titles.get(reg, ""), "x": x, "y": y}
    for pid in wanted:
        p = store.paragraphs.get(pid)
        if not p or p["regulation"] not in centre:
            continue
        sec_id = f"sec:{p['regulation']}#{_section_key(p)}"
        if sec_id not in nodes:
            cx, cy = centre[p["regulation"]]
            angle = rng.random() * 2 * math.pi
            nodes[sec_id] = {"id": sec_id, "kind": "section", "label": _section_key(p) if p["annex"] else f"§{_section_key(p)}",
                             "regulation": p["regulation"], "title": p["section"][:80],
                             "x": cx + 30 * math.cos(angle), "y": cy + 30 * math.sin(angle)}
            edges.append({"source": sec_id, "target": f"reg:{p['regulation']}", "kind": "child"})
        node = _paragraph_node(pid, by_node.get(pid, []))
        sx, sy = nodes[sec_id]["x"], nodes[sec_id]["y"]
        node.update({"x": sx + rng.uniform(-12, 12), "y": sy + rng.uniform(-12, 12)})
        nodes[pid] = node
        parents = [q for q in g.out(pid, "CHILD_OF") if q in wanted]
        edges.append({"source": pid, "target": parents[0] if parents else sec_id, "kind": "child"})
    for pid in by_node:
        for target in g.out(pid, "REFERS_TO"):
            if target in nodes and target != pid:
                edges.append({"source": pid, "target": target, "kind": "refers"})
    cites = defaultdict(int)  # regulation-to-regulation links, from paragraphs that name another selected regulation
    for pid, p in store.paragraphs.items():
        if p["role"] == "base" and p["regulation"] in centre:
            for target in g.out(pid, "CITES_REG"):
                reg = target[len("reg:"):]
                if reg in centre and reg != p["regulation"]:
                    cites[(p["regulation"], reg)] += 1
    for (a, b), n in cites.items():
        edges.append({"source": f"reg:{a}", "target": f"reg:{b}", "kind": "cites_reg", "weight": n})
    payload = {"view": "structure", "regulations": regs, "nodes": list(nodes.values()), "edges": edges}
    cache.write_text(json.dumps(payload, ensure_ascii=False))
    return payload


def meaning(run_id: str) -> dict:
    cache = run_dir(run_id) / "graph_meaning.json"
    if cache.is_file():
        return json.loads(cache.read_text())
    regs, by_node = _req_index(run_id)
    r = store.retriever
    keep = []
    for i, row in enumerate(r.rows):
        if row["regulation"] not in regs:
            continue
        is_req = row["id"] in by_node
        title = row["section"] or ""
        if is_req or (len(row["text"]) >= 60 and not ADMIN.search(title)):
            keep.append(i)
    vectors = r.vectors[keep]
    import umap  # imported late: numba compiles on first use

    xy = umap.UMAP(n_neighbors=15, min_dist=0.1, metric="cosine", random_state=7).fit_transform(vectors)
    xy = (xy - xy.mean(0)) / (xy.std(0) + 1e-9) * 60
    ids = [r.ids[i] for i in keep]
    nodes = []
    for pid, (x, y) in zip(ids, xy):
        node = _paragraph_node(pid, by_node.get(pid, []))
        node.update({"x": float(x), "y": float(y)})
        nodes.append(node)
    edges = []
    sims = vectors @ vectors.T
    for a, pid in enumerate(ids):
        if pid not in by_node:
            continue
        sims[a, a] = -1
        for b in np.argsort(-sims[a])[:3]:
            edges.append({"source": pid, "target": ids[int(b)], "kind": "similar", "weight": round(float(sims[a, b]), 3)})
    centroids = {}
    for reg in regs:
        pts = [(n["x"], n["y"]) for n in nodes if n["regulation"] == reg]
        if pts:
            centroids[reg] = [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]
    payload = {"view": "meaning", "regulations": regs, "nodes": nodes, "edges": edges, "centroids": centroids}
    cache.write_text(json.dumps(payload, ensure_ascii=False))
    return payload
