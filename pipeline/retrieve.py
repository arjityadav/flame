"""Hybrid retrieval for the assistant and graph chat: graph scope + BM25 + embeddings, fused by reciprocal rank.

Regulation questions mix exact tokens ("S3", "Annex 8", "6.7.4.1") that BM25 finds with paraphrases ("how far from
the edge") that embeddings find; the graph then adds what neither finds: the definitions a paragraph uses, the
paragraphs it refers to and its parent.

    uv run python -m pipeline.retrieve "how high must the stop lamp be mounted" --regs "UN R48,UN R148"
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from pipeline.graph import DATA, Graph
from pipeline.index import EMBED_MODEL, INDEX, QUERY_PREFIX, corpus_rows, tokenize

RRF_K = 60
BM25_WEIGHT = 0.3  # measured on 16 plain-English dev questions: meaning finds most, exact tokens help a little
TOP_DIRECT = 8     # fused matches kept before graph expansion
MAX_EXPANDED = 2   # definitions / references / parents added after them


@dataclass
class Hit:
    id: str
    regulation: str
    version: str
    label: str
    page: int
    section: str
    text: str
    score: float
    why: list[str] = field(default_factory=list)  # "bm25", "meaning", "focus", "definition", "reference", "parent"

    def brief(self, chars: int = 900) -> str:
        return f"{self.regulation} {self.label} (p.{self.page}): {self.text[:chars]}"


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avg = float(self.len.mean()) if len(docs) else 1.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, d in enumerate(self.tf):
            for t in d:
                self.postings[t].append(i)

    def scores(self, query: list[str], allowed: np.ndarray) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                if not allowed[i]:
                    continue
                f = self.tf[i][t]
                out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out


class Retriever:
    def __init__(self, graph: Graph | None = None):
        ids: list[str] = json.loads((INDEX / "ids.json").read_text())
        rows = {p["id"]: p for p in corpus_rows()}
        # an index built before a re-ingest can name paragraphs that no longer exist: skip them instead of failing
        keep = [i for i, pid in enumerate(ids) if pid in rows]
        if len(keep) < len(ids):
            print(f"retrieve: {len(ids) - len(keep)} indexed paragraphs are no longer in the corpus; "
                  f"rebuild with `uv run python -m pipeline.index`")
        self.ids = [ids[i] for i in keep]
        self.pos = {pid: i for i, pid in enumerate(self.ids)}
        self.rows = [rows[pid] for pid in self.ids]
        tokens = json.loads((INDEX / "tokens.json").read_text())
        self.bm25 = BM25([tokens[i] for i in keep])
        self.vectors = np.load(INDEX / "embeddings.npy").astype(np.float32)[keep]
        self.graph = graph or Graph.load(DATA / "graph.json")
        self._model = None

    # -------------------------------------------------------------- pieces

    def embed(self, text: str) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(EMBED_MODEL)
        return self._model.encode([QUERY_PREFIX + text], normalize_embeddings=True)[0]

    def scope_mask(self, regulations: list[str] | None) -> np.ndarray:
        if not regulations:
            return np.ones(len(self.ids), dtype=bool)
        wanted = set(regulations)
        for reg in list(wanted):  # plus the regulations they cite directly
            wanted.update(r[len("reg:"):] for r in self.graph.out(f"reg:{reg}", "CITES_REG"))
        return np.array([r["regulation"] in wanted for r in self.rows])

    def hit(self, pid: str, score: float, why: str) -> Hit | None:
        node = self.graph.nodes.get(pid)
        if not node or node.get("kind") != "paragraph":
            return None
        return Hit(pid, node["regulation"], node["version"], node["label"], node["page_start"], node["section"],
                   node["text"], score, [why])

    def expand(self, pid: str) -> list[tuple[str, str]]:
        """Definitions the paragraph uses, paragraphs it refers to, and its parent."""
        out = []
        for term in self.graph.out(pid, "USES_TERM")[:4]:
            out += [(d, "definition") for d in self.graph.into(term, "DEFINES")[:1]]
        out += [(t, "reference") for t in self.graph.out(pid, "REFERS_TO") if t.startswith("para:")][:3]
        out += [(p, "parent") for p in self.graph.out(pid, "CHILD_OF")[:1]]
        return out

    # -------------------------------------------------------------- search

    def search(self, question: str, regulations: list[str] | None = None, focus: str | None = None,
               k: int = 10, boost: set[str] | None = None, max_chars: int = 6000) -> list[Hit]:
        allowed = self.scope_mask(regulations)
        lexical = self.bm25.scores(tokenize(question), allowed)
        lex_rank = sorted(lexical, key=lambda i: -lexical[i])[:30]
        sims = self.vectors @ self.embed(question)
        sims[~allowed] = -1
        sem_rank = list(np.argsort(-sims)[:30])

        fused: dict[int, float] = defaultdict(float)
        why: dict[int, list[str]] = defaultdict(list)
        for rank, i in enumerate(lex_rank):
            fused[i] += BM25_WEIGHT / (RRF_K + rank)
            why[i].append("bm25")
        for rank, i in enumerate(sem_rank):
            fused[int(i)] += 1 / (RRF_K + rank)
            why[int(i)].append("meaning")
        for i in list(fused):
            if boost and self.ids[i] in boost:
                fused[i] += 0.005
        order = sorted(fused, key=lambda i: -fused[i])

        hits: list[Hit] = []
        seen: set[str] = set()
        if focus and (h := self.hit(focus, 1.0, "focus")):
            hits.append(h)
            seen.add(focus)
        for i in order:
            pid = self.ids[i]
            if pid in seen:
                continue
            h = self.hit(pid, fused[i], "+".join(why[i]))
            if h:
                hits.append(h)
                seen.add(pid)
            if len(hits) >= TOP_DIRECT:
                break
        added = 0
        for seed in [h.id for h in hits[:2]]:  # graph expansion after the strongest matches, capped
            for pid, reason in self.expand(seed):
                if added >= MAX_EXPANDED:
                    break
                if pid not in seen and (h := self.hit(pid, 0.0, reason)):
                    hits.append(h)
                    seen.add(pid)
                    added += 1
        for i in order:  # fill up with the rest of the fused list
            if len(hits) >= k:
                break
            pid = self.ids[i]
            if pid not in seen and (h := self.hit(pid, fused[i], "+".join(why[i]))):
                hits.append(h)
                seen.add(pid)

        out, used = [], 0
        for h in hits[:k]:
            size = min(len(h.text), 300 if h.why == ["definition"] else 900)
            if out and used + size > max_chars:
                break
            out.append(h)
            used += size
        return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("question")
    ap.add_argument("--regs", default="")
    args = ap.parse_args(argv)
    r = Retriever()
    for h in r.search(args.question, [x.strip() for x in args.regs.split(",") if x.strip()]):
        print(f"{h.why} {h.regulation} {h.label} p.{h.page}: {h.text[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
