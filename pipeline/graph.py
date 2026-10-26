"""Deterministic knowledge graph over the parsed regulations (no model calls).

    uv run python -m pipeline.graph             # build data/graph.json from data/corpus.jsonl
    uv run python -m pipeline.graph --eval      # also measure how far references reach on the dev case

Node ids:
    reg:UN R148                         regulation
    ver:UN R148@1.6.0/base              one version (role: base | latest | old | new)
    annex:UN R148@1.6.0/base#Annex 3    annex (or appendix) of a version
    para:UN R148@1.6.0/base#Annex 3 1.2 paragraph ("~2" suffix when a label repeats)
    term:UN R148@1.6.0/base#reference axis

Edges (src, type, dst):
    HAS_VERSION   reg -> ver            NEXT_VERSION  ver(base) -> ver(latest), ver(old) -> ver(new)
    CONTAINS      ver -> para            IN_ANNEX      para -> annex
    CHILD_OF      para -> parent para    REFERS_TO     para -> para | annex (same version)
    CITES_REG     para -> reg            CONSOLIDATES  reg -> reg (R148 combines R6, R7, R23, ...)
    DEFINES       para -> term           USES_TERM     para -> term
    ALIGNS        para(older) -> para(newer), attrs: status same|changed|moved, similarity
Paragraphs of an older version with no ALIGNS edge are removed; of a newer version with none, added.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

NUM = r"\d+(?:\.\d+)*"
PARA_REF = re.compile(
    rf"\b(?:paragraphs?|paras?\.)\s+((?:{NUM}\.?(?:\s*(?:,|and|or|to)\s*(?=\d))?)+)"
    r"(?:\s*(?:,\s*)?(?:of|in)\s+(this annex|this regulation|annex\s+(\d+[a-z]?)|(?:un\s+)?regulation\s+no\.?\s*\d+"
    r"|[a-z]{2,}\b))?",
    re.I,
)
ANNEX_REF = re.compile(r"\bAnnex(?:es)?\s+(\d+[A-Z]?)\b(?!\s*(?:to|of)\s+(?:UN\s+)?Regulation\s+No)")
REG_REF = re.compile(r"\b(?:UN\s+)?Regulations?\s+Nos?\.?\s*((?:\d+(?:-H)?(?:\s*(?:,|and)\s*(?=\d))?)+)")
CONSOLIDATES = re.compile(r"combines the provisions of (?:the )?individual UN Regulations Nos\.?\s*([\d,\sand]+)", re.I)
DEFINITION = re.compile(r"^[\"“]([^\"”]{2,80})[\"”]\s*(?:\([^)]*\)\s*)?(?:,\s*)?(?:means|is|shall mean|refers to|denotes)\b")
NORMATIVE_START = re.compile(r"^1\.?\s*(purpose|scope|application|field of application)", re.I)
RATIONALE = re.compile(r"rationale|justification|procedural background|\bphase\s+\d|\bphase\b", re.I)
CATEGORY = re.compile(r"\b(?:categor(?:y|ies)\s+)?([MNLOT])\s?([1-7])?\b(?=[\s,.;)]|$)")


@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[tuple[str, str, str, dict]] = field(default_factory=list)
    _out: dict[str, list[tuple[str, str, dict]]] = field(default_factory=lambda: collections.defaultdict(list), repr=False)
    _in: dict[str, list[tuple[str, str, dict]]] = field(default_factory=lambda: collections.defaultdict(list), repr=False)

    def add_node(self, nid: str, kind: str, **attrs) -> str:
        self.nodes.setdefault(nid, {"kind": kind, **attrs})
        return nid

    def add_edge(self, src: str, etype: str, dst: str, **attrs) -> None:
        self.edges.append((src, etype, dst, attrs))
        self._out[src].append((etype, dst, attrs))
        self._in[dst].append((etype, src, attrs))

    def out(self, nid: str, etype: str | None = None) -> list[str]:
        return [d for t, d, _ in self._out.get(nid, []) if etype is None or t == etype]

    def into(self, nid: str, etype: str | None = None) -> list[str]:
        return [s for t, s, _ in self._in.get(nid, []) if etype is None or t == etype]

    def expand(self, seeds: set[str], hops: int = 1, etypes: tuple[str, ...] = ("REFERS_TO",)) -> set[str]:
        """Paragraphs reachable from `seeds` within `hops` steps; an annex target expands to all its paragraphs."""
        seen, frontier = set(seeds), set(seeds)
        for _ in range(hops):
            nxt = set()
            for nid in frontier:
                for t, dst, _ in self._out.get(nid, []):
                    if t not in etypes:
                        continue
                    targets = self.into(dst, "IN_ANNEX") if dst.startswith("annex:") else [dst]
                    nxt.update(x for x in targets if x not in seen)
            seen |= nxt
            frontier = nxt
        return seen

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"nodes": self.nodes, "edges": self.edges}, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path = DATA / "graph.json") -> "Graph":
        raw = json.loads(path.read_text())
        g = cls(nodes=raw["nodes"])
        for src, t, dst, attrs in raw["edges"]:
            g.add_edge(src, t, dst, **attrs)
        return g


def _reg_id(number: str, known: set[str]) -> str | None:
    rid = f"UN R{number.replace('-', '')}"
    return f"reg:{rid}" if rid in known else None


def _numbers(spec: str) -> list[tuple[str, str]]:
    """'5.1, 5.3 and 5.4 to 5.6' -> [('5.1', ''), ('5.3', ''), ('5.4', 'to'), ('5.6', '')] (sep = separator after)."""
    parts = re.split(r"\s*(,|\band\b|\bor\b|\bto\b)\s*", spec.strip())
    out, i = [], 0
    while i < len(parts):
        num = parts[i].rstrip(".")
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        if re.fullmatch(NUM, num):
            out.append((num, sep))
        i += 2
    return out


def build() -> Graph:
    rows = [json.loads(line) for line in (DATA / "corpus.jsonl").open()]
    index = json.loads((ROOT / "regulations" / "index.json").read_text())
    known = {r["regulation"] for r in index}
    g = Graph()
    for r in index:
        g.add_node(f"reg:{r['regulation']}", "regulation", regulation=r["regulation"], title=r["title"])

    by_ver: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_ver[f"ver:{row['regulation']}@{row['version']}/{row['role']}"].append(row)

    for vid, paras in by_ver.items():
        first = paras[0]
        reg = f"reg:{first['regulation']}"
        g.add_node(reg, "regulation", regulation=first["regulation"], title="")
        g.add_node(vid, "version", regulation=first["regulation"], version=first["version"], role=first["role"],
                   file=first["file"], pages=max(p["page_end"] for p in paras))
        g.add_edge(reg, "HAS_VERSION", vid)

        # GTRs open with a long "statement of technical rationale" whose numbering restarts before the normative
        # text ("1. PURPOSE" on page 71 of GTR 9). Paragraphs before that restart are marked non-normative and are
        # left out of label lookups, section units and briefs.
        start = 0
        for i, p in enumerate(paras):
            if i and p["number"] == "1" and not p["annex"] and NORMATIVE_START.match(p["section"]) and any(
                    RATIONALE.search(q["section"]) for q in paras[:i]):
                start = i
        # paragraphs, annexes, hierarchy
        label_to_id: dict[str, str] = {}
        order: list[str] = []
        seen = collections.Counter()
        for i, p in enumerate(paras):
            seen[p["label"]] += 1
            pid = f"para:{vid[4:]}#{p['label']}" + (f"~{seen[p['label']]}" if seen[p["label"]] > 1 else "")
            p["id"] = pid
            g.add_node(pid, "paragraph", regulation=p["regulation"], version=p["version"], role=p["role"],
                       label=p["label"], number=p["number"], annex=p["annex"], part=p["part"],
                       page_start=p["page_start"], page_end=p["page_end"], section=p["section"], text=p["text"],
                       shall=bool(re.search(r"\b(shall|must)\b", p["text"], re.I)),
                       normative=i >= start)
            g.add_edge(vid, "CONTAINS", pid)
            if i >= start:
                label_to_id.setdefault(p["label"], pid)
            order.append(pid)
            if p["annex"]:
                aid = f"annex:{vid[4:]}#{p['annex']}"
                g.add_node(aid, "annex", label=p["annex"], title=p["section"] if not p["number"] else "")
                g.add_edge(pid, "IN_ANNEX", aid)
            if p["number"] and "." in p["number"]:
                prefix = " ".join(x for x in (p["annex"], p["part"]) if x)
                num = p["number"]
                while "." in num:
                    num = num.rsplit(".", 1)[0]
                    parent = label_to_id.get(f"{prefix} {num}".strip())
                    if parent:
                        g.add_edge(pid, "CHILD_OF", parent)
                        break
        position = {pid: i for i, pid in enumerate(order)}

        # scope: vehicle categories mentioned
        scope = " ".join(p["text"] for p in paras if not p["annex"] and "scope" in p["section"].lower())[:3000]
        cats = sorted({m[1] + (m[2] or "") for m in CATEGORY.finditer(scope) if m[2] or "categor" in m[0].lower()})
        g.nodes[vid]["categories"] = cats

        # definitions
        terms: dict[str, str] = {}
        for p in paras:
            if "definition" in p["section"].lower() and (m := DEFINITION.match(p["text"])):
                term = m[1].strip().rstrip(",").lower()
                tid = g.add_node(f"term:{vid[4:]}#{term}", "term", term=term, definition=p["text"][:600])
                g.add_edge(p["id"], "DEFINES", tid)
                terms[term] = tid
        usable = sorted((t for t in terms if len(t) >= 4), key=len, reverse=True)
        term_re = re.compile(r"\b(" + "|".join(map(re.escape, usable)) + r")\b", re.I) if usable else None

        # references
        for p in paras:
            text, here = p["text"], p["annex"]
            if term_re:
                for t in sorted({m.lower() for m in term_re.findall(text)}):  # sorted: set order changes per run
                    if t in terms and terms[t] != p["id"] and not any(
                            d == terms[t] for d in g.out(p["id"], "DEFINES")):
                        g.add_edge(p["id"], "USES_TERM", terms[t])
            for m in PARA_REF.finditer(text):
                scope_word = (m[2] or "").lower()
                if scope_word and not (scope_word.startswith("this ") or m[3]):
                    if (rm := re.search(r"regulation\s+no\.?\s*(\d+)", scope_word)) and (rid := _reg_id(rm[1], known)):
                        g.add_edge(p["id"], "CITES_REG", rid)
                    continue  # paragraph of another document (ISO, IEC, other regulation)
                if m[3]:
                    prefixes = [f"Annex {m[3].upper()}"]
                elif scope_word == "this annex":
                    prefixes = [here] if here else [""]
                elif scope_word == "this regulation":
                    prefixes = [""]
                else:
                    prefixes = ([here] if here else []) + [""]
                nums = _numbers(m[1])
                targets = []
                for i, (num, sep) in enumerate(nums):
                    hit = next((label_to_id[f"{pre} {num}".strip()] for pre in prefixes
                                if f"{pre} {num}".strip() in label_to_id), None)
                    if not hit:
                        continue
                    targets.append(hit)
                    if sep == "to" and i + 1 < len(nums):  # a range: every paragraph in between
                        end = next((label_to_id[f"{pre} {nums[i + 1][0]}".strip()] for pre in prefixes
                                    if f"{pre} {nums[i + 1][0]}".strip() in label_to_id), None)
                        if end and position[end] > position[hit]:
                            targets += order[position[hit] + 1: position[end]]
                for t in dict.fromkeys(targets):
                    if t != p["id"]:
                        g.add_edge(p["id"], "REFERS_TO", t, via="paragraph")
            for m in ANNEX_REF.finditer(text):
                aid = f"annex:{vid[4:]}#Annex {m[1].upper()}"
                if aid in g.nodes and aid != (f"annex:{vid[4:]}#{here}" if here else None):
                    g.add_edge(p["id"], "REFERS_TO", aid, via="annex")
            for m in REG_REF.finditer(text):
                for n in re.findall(r"\d+(?:-H)?", m[1]):
                    if (rid := _reg_id(n, known)) and rid != reg:
                        g.add_edge(p["id"], "CITES_REG", rid)
            if (m := CONSOLIDATES.search(text)) and not p["number"]:
                for n in re.findall(r"\d+", m[1]):
                    if (rid := _reg_id(n, known)) and rid != reg and rid not in g.out(reg, "CONSOLIDATES"):
                        g.add_edge(reg, "CONSOLIDATES", rid)

    # version alignment: base -> latest, old -> new
    for older_role, newer_role in (("base", "latest"), ("old", "new")):
        for vid, paras in by_ver.items():
            if not vid.endswith(f"/{older_role}"):
                continue
            reg = paras[0]["regulation"]
            newer = [v for v in by_ver if v.startswith(f"ver:{reg}@") and v.endswith(f"/{newer_role}")]
            if not newer:
                continue
            g.add_edge(vid, "NEXT_VERSION", newer[0])
            _align(g, paras, by_ver[newer[0]])
    return g


def _align(g: Graph, old: list[dict], new: list[dict]) -> None:
    by_label = collections.defaultdict(list)
    for p in new:
        by_label[p["label"]].append(p)
    unmatched_old, used = [], set()
    for p in old:
        cands = [q for q in by_label.get(p["label"], []) if q["id"] not in used]
        if cands:
            q = cands[0]
            sim = fuzz.ratio(p["text"], q["text"])
            if sim >= 60 or not p["text"]:
                used.add(q["id"])
                g.add_edge(p["id"], "ALIGNS", q["id"], status="same" if p["text"] == q["text"] else "changed",
                           similarity=round(sim, 1))
                continue
        unmatched_old.append(p)
    pool = [q for q in new if q["id"] not in used and q["text"]]
    for p in unmatched_old:  # renumbered or moved paragraphs
        if not p["text"] or not pool:
            continue
        best = process.extractOne(p["text"], [q["text"] for q in pool], scorer=fuzz.ratio, score_cutoff=85)
        if best:
            q = pool.pop(best[2])
            g.add_edge(p["id"], "ALIGNS", q["id"], status="moved", similarity=round(best[1], 1))
            continue
        # split: the old text now lives in sub-paragraphs (6.5 -> 6.5.1 ... 6.5.5); link each new paragraph that
        # continues the old numbering and quotes part of the old text
        kids = [q for q in pool if p["number"] and q["number"] and q["annex"] == p["annex"]
                and q["number"].startswith(p["number"] + ".")]
        for q in kids:
            sim = fuzz.partial_ratio(q["text"], p["text"])
            if sim >= 60:
                g.add_edge(p["id"], "ALIGNS", q["id"], status="split", similarity=round(sim, 1))


def evaluate(g: Graph) -> None:
    """How many golden paragraphs of the dev case are reachable by references from the other golden paragraphs?"""
    gold = json.loads((ROOT / "cases/dev-rear-lamp/expected.json").read_text())["requirements"]
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    from score_local import _paragraph_key  # pyright: ignore[reportMissingImports]

    lookup = {}
    for nid, n in g.nodes.items():
        if n["kind"] == "paragraph" and n["role"] == "base":
            lookup.setdefault((n["regulation"], _paragraph_key(n["label"])), nid)
    gold_ids = {lookup.get((x["source"]["regulation"], re.sub(r"\s+table\s+\S+$", "", _paragraph_key(x["source"]["paragraph"]))))
                for x in gold} - {None}
    body = {nid for nid in gold_ids if not g.nodes[nid]["annex"]}
    annex = gold_ids - body
    for hops in (1, 2):
        reach = g.expand(body, hops)
        print(f"golden annex paragraphs reachable from golden main-body paragraphs in {hops} hop(s): "
              f"{len(annex & reach)}/{len(annex)}  (expansion adds {len(reach - body)} paragraphs)")
    print("consolidation edges:", sorted((s[4:], d[4:]) for s, t, d, _ in g.edges if t == "CONSOLIDATES"))
    print("regulations cited by golden paragraphs:", collections.Counter(
        r[4:] for nid in gold_ids for r in g.out(nid, "CITES_REG")).most_common(8))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--eval", action="store_true", help="measure reference reach on the dev case")
    args = ap.parse_args(argv)
    g = build()
    g.save(DATA / "graph.json")
    kinds = collections.Counter(n["kind"] for n in g.nodes.values())
    etypes = collections.Counter(t for _, t, _, _ in g.edges)
    status = collections.Counter(a["status"] for _, t, _, a in g.edges if t == "ALIGNS")
    print(f"wrote data/graph.json: {dict(kinds)}")
    print(f"edges: {dict(etypes)}")
    print(f"alignment: {dict(status)}")
    if args.eval:
        evaluate(g)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
