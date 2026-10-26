"""RAG over the regulation corpus: contextual chunks, a strong open embedding model, hybrid search, cross-encoder rerank.

    uv run python -m pipeline.rag build                       # data/rag/<model>/ (chunks, embeddings); ~15 min on an M2
    uv run python -m pipeline.rag search "stop lamp luminous intensity" --regs "UN R148"

Ingestion: one chunk per normative base-version paragraph from data/corpus.jsonl (GTR rationale left out), with its
context written in front of the text, so a sub-item embeds with its meaning ("UN R48 > 6.10 Rear position lamp >
6.10.4.2: Where optional lamps are installed ..."). Paragraphs longer than CHUNK_CHARS are split on sentence
boundaries with overlap; every chunk keeps its paragraph id, and search results are per paragraph (best chunk).

Search: BM25 (exact tokens: "S3", "Annex 8", "6.7.4.1") and dense vectors (paraphrase), fused by reciprocal rank;
the top candidates are then re-scored by a cross-encoder that reads query and paragraph together.
Everything runs locally: no text leaves the machine.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from pipeline.graph import DATA, Graph
from pipeline.index import tokenize
from pipeline.retrieve import BM25

EMBED_MODEL = "Snowflake/snowflake-arctic-embed-m-v1.5"  # Qwen3-Embedding-0.6B: >1 h on an M2, too slow
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
CHUNK_CHARS = 1400
OVERLAP_CHARS = 250
MAX_TOKENS = 320  # covers 95% of chunks in full (p95 ~1,340 characters); the rest are truncated
RRF_K = 60
QUERY_TASK = "Given a requirement or a question about a vehicle component, retrieve the UN Regulation paragraph that states it"
RAG = DATA / "rag"


def _device() -> str:
    import torch
    return "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"


def _slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def _context(g: Graph, pid: str) -> str:
    """'UN R48 > Annex 3 > 6.10 Rear position lamp > 6.10.4' from the paragraph's ancestors."""
    n = g.nodes[pid]
    chain, cur = [], pid
    for _ in range(6):
        par = g.out(cur, "CHILD_OF")
        if not par:
            break
        cur = par[0]
        p = g.nodes[cur]
        chain.append(f"{p['label']} {p['text'][:70]}".strip())
    section = n["section"][:90] if n["section"] else ""
    top = re.match(r"^(\S+?)\.?\s", section)
    chain = [c for c in chain if not (top and c.split(" ", 1)[0].rstrip(".") == top[1].rstrip("."))]  # = the section
    parts = [n["regulation"]] + ([section] if section else []) + chain[::-1]
    return " > ".join(dict.fromkeys(parts))


def _split(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    sents = re.split(r"(?<=[.;:])\s+(?=[A-Z(])", text)
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + len(s) > CHUNK_CHARS:
            out.append(cur)
            cur = cur[-OVERLAP_CHARS:] + " " + s  # overlap keeps the lead-in of a list with its items
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    hard = []  # tables and lists without sentence ends: fixed windows with overlap
    for piece in out:
        while len(piece) > CHUNK_CHARS * 1.5:
            hard.append(piece[:CHUNK_CHARS])
            piece = piece[CHUNK_CHARS - OVERLAP_CHARS:]
        hard.append(piece)
    return hard


def chunks(g: Graph) -> list[dict]:
    out = []
    for pid, n in g.nodes.items():
        if n["kind"] != "paragraph" or n["role"] != "base" or not n["text"] or not n.get("normative", True):
            continue
        ctx = _context(g, pid)
        for i, piece in enumerate(_split(n["text"])):
            out.append({"pid": pid, "regulation": n["regulation"], "label": n["label"], "page": n["page_start"],
                        "part": i, "text": f"{ctx} > {n['label']}: {piece}"})
    return out


def build(model: str = EMBED_MODEL, batch: int = 32) -> Path:
    from sentence_transformers import SentenceTransformer
    g = Graph.load()
    cs = chunks(g)
    folder = RAG / _slug(model)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "chunks.jsonl").open("w") as fh:
        for c in cs:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    dev = _device()
    kwargs = {"model_kwargs": {"torch_dtype": "float16"}} if dev in ("mps", "cuda") else {}
    m = SentenceTransformer(model, device=dev, **kwargs)  # half precision on a GPU: ~2x faster, same ranking
    m.max_seq_length = MAX_TOKENS
    t = time.time()
    order = np.argsort([len(c["text"]) for c in cs])  # similar lengths per batch: much faster padding
    emb = m.encode([cs[i]["text"] for i in order], batch_size=batch, normalize_embeddings=True,
                   show_progress_bar=True, convert_to_numpy=True)
    out = np.empty_like(emb)
    out[order] = emb
    np.save(folder / "embeddings.npy", out.astype(np.float16))
    (folder / "meta.json").write_text(json.dumps({"model": model, "chunks": len(cs), "seconds": round(time.time() - t)}))
    print(f"embedded {len(cs)} chunks with {model} in {time.time() - t:.0f}s -> {folder}")
    return folder


@dataclass
class Hit:
    pid: str
    regulation: str
    label: str
    page: int
    text: str
    score: float


class Rag:
    def __init__(self, model: str = EMBED_MODEL, reranker: str | None = RERANK_MODEL):
        folder = RAG / _slug(model)
        self.model_name = model
        self.chunks = [json.loads(line) for line in (folder / "chunks.jsonl").open()]
        self.emb = np.load(folder / "embeddings.npy").astype(np.float32)
        self.bm25 = BM25([tokenize(c["text"]) for c in self.chunks])
        self.reg = np.array([c["regulation"] for c in self.chunks])
        self._embedder = None
        self._reranker_name = reranker
        self._reranker = None

    def _embed_query(self, q: str) -> np.ndarray:
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(self.model_name, device=_device())
        prompt = f"Instruct: {QUERY_TASK}\nQuery: " if "qwen" in self.model_name.lower() else \
            "Represent this sentence for searching relevant passages: " if ("bge" in self.model_name.lower()
                                                                          or "arctic" in self.model_name.lower()) else ""
        return self._embedder.encode([prompt + q], normalize_embeddings=True)[0]

    def _rerank(self, query: str, cands: list[int]) -> list[float]:
        if self._reranker is None:
            from sentence_transformers import CrossEncoder
            self._reranker = CrossEncoder(self._reranker_name, device=_device(), max_length=512)
        return list(self._reranker.predict([(query, self.chunks[i]["text"]) for i in cands], batch_size=16))

    def search(self, query: str, regs: list[str] | None = None, k: int = 10, mode: str = "rerank",
               pool: int = 40) -> list[Hit]:
        """mode: 'bm25', 'dense', 'hybrid' (RRF of both), 'rerank' (hybrid, then cross-encoder on the top `pool`)
        or 'bm25_rerank' (BM25 only, then cross-encoder on its top `pool`: no embedding model needed)."""
        allowed = np.isin(self.reg, regs) if regs else np.ones(len(self.chunks), bool)
        idx = np.flatnonzero(allowed)
        ranked: dict[str, list[int]] = {}
        if mode in ("bm25", "hybrid", "rerank", "bm25_rerank"):
            s = self.bm25.scores(tokenize(query), allowed)
            ranked["bm25"] = sorted(s, key=lambda i: -s[i])[:200]
        if mode in ("dense", "hybrid", "rerank"):
            sims = self.emb[idx] @ self._embed_query(query)
            ranked["dense"] = [int(idx[i]) for i in np.argsort(-sims)[:200]]
        if mode == "bm25_rerank":
            top = ranked["bm25"][:pool]
            scores = dict(zip(top, self._rerank(query, top)))
            order = sorted(top, key=lambda i: -scores[i])
        elif mode in ("bm25", "dense"):
            order = ranked[mode]
            scores = {i: 1.0 / (r + 1) for r, i in enumerate(order)}
        else:
            fused: dict[int, float] = {}
            for lst in ranked.values():
                for r, i in enumerate(lst):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + r + 1)
            order = sorted(fused, key=lambda i: -fused[i])
            scores = fused
            if mode == "rerank":
                top = order[:pool]
                rs = self._rerank(query, top)
                scores = dict(zip(top, rs))
                order = sorted(top, key=lambda i: -scores[i])
        hits, seen = [], set()
        for i in order:  # best chunk per paragraph
            c = self.chunks[i]
            if c["pid"] in seen:
                continue
            seen.add(c["pid"])
            hits.append(Hit(c["pid"], c["regulation"], c["label"], c["page"], c["text"], float(scores[i])))
            if len(hits) >= k:
                break
        return hits


@lru_cache(maxsize=2)
def get(model: str = EMBED_MODEL) -> Rag:
    return Rag(model)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--model", default=EMBED_MODEL)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--regs", default="")
    s.add_argument("--mode", default="rerank", choices=("bm25", "dense", "hybrid", "rerank", "bm25_rerank"))
    s.add_argument("--model", default=EMBED_MODEL)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.model)
    else:
        rag = Rag(args.model)
        for h in rag.search(args.query, [r.strip() for r in args.regs.split(",") if r.strip()] or None, mode=args.mode):
            print(f"{h.score:8.3f}  {h.regulation} {h.label} p{h.page}: {h.text[:140]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
