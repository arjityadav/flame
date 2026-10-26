"""Retrieval quality of pipeline/rag.py on real expert requirements, and its value for paragraph selection.

    uv run python -m eval.rag_eval retrieval      # Recall@k / MRR: does an expert requirement find its source paragraph?
    uv run python -m eval.rag_eval selection      # AUC: does RAG relevance tell expert-listed paragraphs apart?

Queries: the 190 expected requirements of cases/dev-rear-lamp and the 94 expert requirements of subgoal/dev
(UN R175). The answer to each is the paragraph it cites. Search is limited to the query's regulation, as the
pipeline would do. All local: no model API calls.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import numpy as np

from pipeline.graph import Graph
from pipeline.rag import EMBED_MODEL, Rag

ROOT = Path(__file__).resolve().parent.parent
MODELS = (EMBED_MODEL, "BAAI/bge-small-en-v1.5")


def _label(p: str) -> str:
    return re.sub(r"\.?\s*table\s+\S+$", "", p.strip(), flags=re.I).rstrip(".")


def queries(g: Graph) -> list[tuple[str, str, str]]:
    """(query text, regulation, target paragraph id)."""
    base = {}
    for vid, n in g.nodes.items():
        if n["kind"] == "version" and n["role"] == "base":
            base[n["regulation"]] = vid[4:]
    out = []
    reqs = json.loads((ROOT / "cases/dev-rear-lamp/expected.json").read_text())["requirements"]
    reqs += json.loads((ROOT / "subgoal/dev/requirements_old.json").read_text())
    for r in reqs:
        reg = r["source"]["regulation"]
        pid = f"para:{base[reg]}#{_label(r['source'].get('paragraph', ''))}"
        if pid in g.nodes:
            out.append((r["text"], reg, pid))
    return out


def retrieval(modes: tuple[str, ...] = ("bm25", "dense", "hybrid", "rerank"), pool: int = 40) -> None:
    g = Graph.load()
    qs = queries(g)
    print(f"{len(qs)} queries with a resolvable source paragraph\n")
    print(f"{'model':28} {'mode':7} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'s/query':>8}")
    for model in MODELS:
        try:
            rag = Rag(model)
        except FileNotFoundError:
            print(f"{model}: not built (uv run python -m pipeline.rag build --model {model})")
            continue
        for mode in modes:
            if mode in ("bm25", "bm25_rerank") and model != MODELS[0]:
                continue  # same chunks, same BM25
            ranks, t = [], time.time()
            for text, reg, pid in qs:
                hits = rag.search(text, [reg], k=10, mode=mode, pool=pool)
                ids = [h.pid for h in hits]
                ranks.append(ids.index(pid) + 1 if pid in ids else None)
            n = len(ranks)
            r_at = lambda k: sum(1 for r in ranks if r and r <= k) / n
            mrr = sum(1 / r for r in ranks if r) / n
            print(f"{model.split('/')[-1]:28} {mode:7} {r_at(1):6.3f} {r_at(5):6.3f} {r_at(10):6.3f} {mrr:6.3f} "
                  f"{(time.time() - t) / n:8.2f}")


def selection() -> None:
    """For the dev candidate paragraphs (v16 run): score each by RAG relevance to the product's functions."""
    g = Graph.load()
    exp = json.loads((ROOT / "cases/dev-rear-lamp/expected.json").read_text())["requirements"]
    gold = {(x["source"]["regulation"], _label(x["source"]["paragraph"])) for x in exp}
    ranked = json.loads((ROOT / "out/dev-rear-lamp/v16_stages/06_ranked.json").read_text())
    cand = {r["node"]: r["para_score"] for r in ranked}
    y = {p: (g.nodes[p]["regulation"], g.nodes[p]["label"]) in gold for p in cand}
    prof = json.loads((ROOT / "out/dev-rear-lamp/01_profile.json").read_text())
    qs = [f"{f['name']}: requirements the product must meet" for f in prof["functions"]] + \
         [f"{p['name']}: requirements" for p in prof["parts"]]

    def auc(score: dict) -> float:
        pos = [score[p] for p in cand if y[p]]
        neg = [score[p] for p in cand if not y[p]]
        return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))

    rag = Rag(EMBED_MODEL)
    best = {p: -1e9 for p in cand}
    regs = sorted({g.nodes[p]["regulation"] for p in cand})
    for q in qs:  # relevance of every candidate to every product function, by the cross-encoder
        for reg in regs:
            for h in rag.search(q, [reg], k=400, mode="rerank", pool=400):
                if h.pid in best:
                    best[h.pid] = max(best[h.pid], h.score)
    print(f"{len(cand)} candidate paragraphs ({sum(y.values())} expert-listed), {len(qs)} product queries")
    print(f"RAG rerank relevance (max over product queries): AUC {auc(best):.3f}")
    print(f"model ranker (current pipeline):                 AUC {auc(cand):.3f}")
    rk = lambda d: {p: i for i, p in enumerate(sorted(d, key=d.get))}
    a, b = rk(cand), rk(best)
    print(f"ranker + RAG relevance (rank average):           AUC {auc({p: a[p] + b[p] for p in cand}):.3f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("retrieval", "selection"))
    ap.add_argument("--modes", default="bm25,dense,hybrid,rerank", help="comma list; also bm25_rerank")
    ap.add_argument("--pool", type=int, default=40, help="candidates the cross-encoder re-scores")
    args = ap.parse_args(argv)
    retrieval(tuple(args.modes.split(",")), args.pool) if args.what == "retrieval" else selection()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
