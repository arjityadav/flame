"""Offline search index over the base-version paragraphs, for the assistant, graph chat and the meaning view.

    uv run python -m pipeline.index            # ~28k paragraphs; embeddings take a few minutes on a laptop CPU

Writes data/index/:
  ids.json           paragraph node ids, in the same form as data/graph.json ("para:UN R48@9.3.0/base#6.7.4.1")
  tokens.json        BM25 tokens per paragraph (built into a BM25 scorer at load time)
  embeddings.npy     float16, one row per id, from a local open-source model (no text leaves the machine)
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = DATA / "index"
EMBED_MODEL = "Snowflake/snowflake-arctic-embed-m-v1.5"  # dense R@1 0.908 vs 0.863 for bge-small (eval/rag_eval.py)
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

TOKEN = re.compile(r"\d+(?:\.\d+)+|[a-z]+\d*|\d+")  # keeps "6.7.4.1", "s3", "r148", plain words
STOP = set("the a an of to in and or for on by with be shall is are as at that this which from it its not any "
           "than such these those each all may if when where".split())


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


def corpus_rows(role: str = "base") -> list[dict]:
    """Corpus paragraphs with the node ids graph.build gives them (same per-version duplicate numbering)."""
    seen: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    rows = []
    with (DATA / "corpus.jsonl").open() as fh:
        for line in fh:
            p = json.loads(line)
            vid = f"{p['regulation']}@{p['version']}/{p['role']}"
            seen[vid][p["label"]] += 1
            n = seen[vid][p["label"]]
            p["id"] = f"para:{vid}#{p['label']}" + (f"~{n}" if n > 1 else "")
            if p["role"] == role:
                rows.append(p)
    return rows


def heading_path(graph, pid: str) -> str:
    """'Stop lamp › Position › In height' from the CHILD_OF chain: lead-in paragraphs rely on their parents' titles."""
    titles, current = [], pid
    for _ in range(6):
        parents = graph.out(current, "CHILD_OF")
        if not parents:
            break
        current = parents[0]
        titles.append(" ".join(graph.nodes[current]["text"].split()[:8]))
    return " › ".join(reversed(titles))


def doc_text(p: dict, graph=None) -> str:
    """What gets indexed for one paragraph: regulation, label, heading path, own text, and for a short lead-in
    ("In height:") the text of its sub-paragraphs, which carry its content."""
    path = heading_path(graph, p["id"]) if graph else ""
    text = p["text"]
    if graph and len(text) < 80:
        children = graph.into(p["id"], "CHILD_OF")[:4]
        text += " " + " ".join(graph.nodes[c]["text"][:300] for c in children)
    return f"{p['regulation']} {p['label']} {p['section']} › {path}: {text}"


def build() -> None:
    from pipeline.graph import Graph

    INDEX.mkdir(parents=True, exist_ok=True)
    graph = Graph.load(DATA / "graph.json")
    rows = [p for p in corpus_rows() if p["text"]]
    texts = [doc_text(p, graph) for p in rows]
    (INDEX / "ids.json").write_text(json.dumps([p["id"] for p in rows]))
    (INDEX / "tokens.json").write_text(json.dumps([tokenize(t) for t in texts]))
    print(f"BM25 tokens for {len(rows)} paragraphs")

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBED_MODEL)
    start = time.time()
    vectors = model.encode([t[:2000] for t in texts], batch_size=64, normalize_embeddings=True,
                           show_progress_bar=True)
    np.save(INDEX / "embeddings.npy", vectors.astype(np.float16))
    print(f"embedded {len(rows)} paragraphs with {EMBED_MODEL} in {time.time() - start:.0f}s")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
