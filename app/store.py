"""Read-side data for the web app: paragraphs (text, pages, line boxes), the graph, regulation PDFs.

Everything here is loaded once and shared; the files come from the pipeline (data/corpus.jsonl, data/graph.json,
regulations/index.json) and are never written by the app.
"""

from __future__ import annotations

import json
import re
import threading
from functools import cached_property
from pathlib import Path

from pipeline.graph import DATA, Graph
from pipeline.index import corpus_rows

ROOT = Path(__file__).resolve().parent.parent
PDF_ROOT = ROOT / "regulations" / "pdf"


def label_key(label: str | None) -> str:
    """Same normalisation as the scorer, plus the dev data's 'Table n' suffixes: '5.1.4.4.1. Table 3' -> '5.1.4.4.1'."""
    text = re.sub(r"\.?\s*table\s+\S+$", "", (label or "").strip(), flags=re.I)
    text = re.sub(r"[,;:()\[\]]", " ", text.casefold())
    text = re.sub(r"(?<![0-9a-z])\.|\.(?![0-9a-z])", " ", text)
    return " ".join(text.split())


class Store:
    _lock = threading.Lock()

    @cached_property
    def graph(self) -> Graph:
        return Graph.load(DATA / "graph.json")

    @cached_property
    def index(self) -> list[dict]:
        return json.loads((ROOT / "regulations" / "index.json").read_text())

    @cached_property
    def titles(self) -> dict[str, str]:
        return {r["regulation"]: r["title"] for r in self.index}

    @cached_property
    def paragraphs(self) -> dict[str, dict]:
        """node id -> corpus row (text, pages, boxes) for every role (base, latest, old, new)."""
        rows: dict[str, dict] = {}
        for role in ("base", "latest", "old", "new"):
            for p in corpus_rows(role):
                rows[p["id"]] = p
        return rows

    @cached_property
    def by_label(self) -> dict[tuple, list[str]]:
        out: dict[tuple, list[str]] = {}
        for pid, p in self.paragraphs.items():
            out.setdefault((p["regulation"], p["version"], label_key(p["label"])), []).append(pid)
        return out

    @cached_property
    def retriever(self):
        from pipeline.retrieve import Retriever
        return Retriever(self.graph)

    # ------------------------------------------------------------------ lookups

    def resolve(self, regulation: str, version: str, label: str | None, page: int | None = None) -> str | None:
        """The paragraph a citation points at. Old runs may carry 'Appendix 1 Appendix 6' (an ingest bug since
        fixed): it is retried as 'Appendix 6'. With several matches, the one on the cited page wins."""
        candidates = self.by_label.get((regulation, version, label_key(label)), [])
        if not candidates and label and (m := re.match(r"^appendix \d+[a-z]? (appendix .*)$", label_key(label))):
            candidates = self.by_label.get((regulation, version, m[1]), [])
        if page is not None and len(candidates) > 1:
            on_page = [c for c in candidates if self.paragraphs[c]["page_start"] <= page <= self.paragraphs[c]["page_end"]]
            candidates = on_page or candidates
        return candidates[0] if candidates else None

    def pdf_url(self, regulation: str, version: str) -> str | None:
        for reg in self.index:
            if reg["regulation"] != regulation:
                continue
            for v in reg["versions"]:
                if v["version_label"] == version:
                    return f"/files/{regulation.replace(' ', '_')}/{v['role']}/{v['files'][0]['name']}"
        return None

    def pdf_sha256(self, regulation: str, version: str) -> str | None:
        for reg in self.index:
            if reg["regulation"] == regulation:
                for v in reg["versions"]:
                    if v["version_label"] == version:
                        return v["files"][0].get("sha256")
        return None

    def pdf_path(self, slug: str, role: str, name: str) -> Path | None:
        """Only files listed in regulations/index.json are served (no path traversal)."""
        for reg in self.index:
            if reg["regulation"].replace(" ", "_") != slug:
                continue
            for v in reg["versions"]:
                if v["role"] == role and any(f["name"] == name for f in v["files"]):
                    path = PDF_ROOT / slug / role / name
                    return path if path.is_file() else None
        return None

    def paragraph(self, pid: str) -> dict | None:
        p = self.paragraphs.get(pid)
        if p is None:
            return None
        g = self.graph
        definitions = []
        for term in g.out(pid, "USES_TERM")[:8]:
            for d in g.into(term, "DEFINES")[:1]:
                if d in self.paragraphs:
                    definitions.append({"term": g.nodes[term].get("term") or g.nodes[term].get("name") or "",
                                        "node": d, "label": self.paragraphs[d]["label"]})
        references = [{"node": t, "label": self.paragraphs[t]["label"], "regulation": self.paragraphs[t]["regulation"]}
                      for t in dict.fromkeys(g.out(pid, "REFERS_TO")) if t in self.paragraphs][:10]
        return {
            "node": pid, "regulation": p["regulation"], "version": p["version"], "role": p["role"],
            "label": p["label"], "section": p["section"], "pages": list(range(p["page_start"], p["page_end"] + 1)),
            "text": p["text"], "boxes": p.get("boxes", []), "pdf": self.pdf_url(p["regulation"], p["version"]),
            "pdf_sha256": self.pdf_sha256(p["regulation"], p["version"]),
            "title": self.titles.get(p["regulation"], ""), "definitions": definitions, "references": references,
        }


store = Store()
