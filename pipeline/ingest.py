"""Stage 0: parse the Certivity-rendered regulation PDFs into paragraph records.

    uv run python -m pipeline.ingest            # all regulations (base + latest) and subgoal/*/{old,new}.pdf
    uv run python -m pipeline.ingest --check    # also check every citation in the worked examples resolves

Writes data/corpus.jsonl (one paragraph per line) and data/cards.json (title, scope, top-level sections per
regulation version). Page numbers come from the printed "page N of M" header, never from a model.

Layout facts the parser relies on (true for every hosted PDF):
  * section and annex headings are Helvetica-Bold; body text is Times at one dominant size
  * "Part N - ..." inside an annex starts with a Times-Bold span; numbering restarts in each annex and part
  * figure notes are set smaller than body text, so numbered lines there are not paragraphs
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PAGE_HEADER = re.compile(r"^(UN .+?), version (\S+) \| page (\d+) of (\d+)$")
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?(?:\s+(.*)|$)")
ANNEX = re.compile(r"^annex\s+(\d+[a-z]?)\b(?:\s*[-–]?\s*appendix\s+(\d+[a-z]?))?", re.I)
APPENDIX = re.compile(r"^appendix\s+(\d+[a-z]?)\b", re.I)
PART = re.compile(r"^part\s+(\d+|[IVX]+)\b", re.I)


@dataclass
class Paragraph:
    regulation: str
    version: str
    role: str  # base | latest | old | new
    file: str
    number: str | None  # "6.2", None for unnumbered text under a heading
    annex: str | None  # "Annex 3", "Annex 1 Appendix 1", "Appendix 2"
    part: str | None  # "Part 1"
    label: str  # golden-style paragraph label: "6.2", "Annex 3 1.2", "Annex 4 Part 1 1.2"
    page_start: int
    page_end: int
    section: str  # nearest heading title, e.g. "Annex 3 Standard light distributions"
    text: str = ""
    boxes: list[dict] = field(default_factory=list, repr=False)  # [{"page": 46, "bbox": [x0, y0, x1, y1]}] per line, PDF points
    lines: list[str] = field(default_factory=list, repr=False)


def _is_successor(prev: tuple[int, ...] | None, new: tuple[int, ...]) -> bool:
    """Whether paragraph number `new` can follow `prev`: first child, next sibling, or next sibling of an ancestor.

    Guards against wrapped body lines that happen to start with a number ("10 m from the antenna").
    """
    if prev is None:
        return True
    if new == prev + (1,):
        return True
    for depth in range(len(prev), 0, -1):
        head = prev[: depth - 1]
        if new[:-1] == head and new[-1] in (prev[depth - 1] + 1, prev[depth - 1] + 2):
            return True
    return False


def _body_size(doc: pymupdf.Document) -> float:
    sizes: collections.Counter[float] = collections.Counter()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["font"].startswith("Times"):
                        sizes[round(span["size"], 1)] += len(span["text"])
    return sizes.most_common(1)[0][0] if sizes else 10.5


def parse_pdf(path: Path, role: str) -> list[Paragraph]:
    doc = pymupdf.open(path)
    body = _body_size(doc)
    out: list[Paragraph] = []
    regulation = version = ""
    annex = part = None
    section = ""
    prev_num: tuple[int, ...] | None = None
    cur: Paragraph | None = None
    in_heading = False

    def start(number: str | None, page: int, first_line: str) -> Paragraph:
        nonlocal cur
        flush()
        label = " ".join(x for x in (annex, part, number) if x) or (section or "preamble")
        cur = Paragraph(regulation, version, role, str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path), number, annex, part, label,
                        page, page, section, lines=[first_line] if first_line else [])
        return cur

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            cur.text = re.sub(r"\s+", " ", " ".join(cur.lines)).strip()
            if cur.text or cur.number:
                out.append(cur)
        cur = None

    for page in doc:
        page_no = None
        for raw in page.get_text().splitlines():  # the header is printed at the bottom, so find it first
            if m := PAGE_HEADER.match(raw.strip()):
                regulation, version, page_no = m[1], m[2], int(m[3])
        if page_no is None:
            continue
        for block in page.get_text("dict", sort=True)["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = " ".join("".join(s["text"] for s in spans).split())
                font, size = spans[0]["font"], spans[0]["size"]
                box = {"page": page_no, "bbox": [round(v, 1) for v in line["bbox"]]}

                if PAGE_HEADER.match(text):
                    continue
                if font.startswith("Helvetica") and not font.startswith("Helvetica-Bold"):
                    continue  # Certivity's rendering notes on page 1
                if font.startswith("Helvetica-Bold") and size > 15:
                    continue  # document title "UN R148 (version 1.6.0)"

                if font.startswith("Helvetica-Bold"):
                    if in_heading and cur is not None and not ANNEX.match(text) and not NUMBERED.match(text) \
                            and not APPENDIX.match(text):
                        section += " " + text  # wrapped heading title
                        cur.section = section
                        cur.boxes.append(box)
                        continue
                    in_heading = True
                    if m := ANNEX.match(text):
                        annex = f"Annex {m[1].upper()}" + (f" Appendix {m[2]}" if m[2] else "")
                        part, prev_num, section = None, None, text
                        start(None, page_no, "")
                    elif m := APPENDIX.match(text):
                        # an appendix inside an annex keeps the annex; one after a top-level appendix does not
                        base = annex.split(" Appendix")[0] if annex and not annex.startswith("Appendix") else None
                        annex = f"{base} Appendix {m[1]}" if base else f"Appendix {m[1]}"
                        part, prev_num, section = None, None, text
                        start(None, page_no, "")
                    elif (m := NUMBERED.match(text)) and "." not in m[1]:
                        section, prev_num = text, (int(m[1]),)
                        start(m[1], page_no, m[2] or "")
                    else:
                        section = text
                        start(None, page_no, "")
                    cur.boxes.append(box)
                    continue
                in_heading = False

                if font.startswith("Times") and "Bold" in font and (m := PART.match(text)) and annex:
                    part, prev_num, section = f"Part {m[1]}", None, f"{annex} {text}"
                    start(None, page_no, text)
                    cur.boxes.append(box)
                    continue

                m = NUMBERED.match(text)
                if m and re.match(r"^(and|or|to)\s+\d", m[2] or ""):
                    m = None  # a wrapped cross-reference ("paragraphs 5. and 6. below"), not a new paragraph
                if m and size >= body - 0.3:
                    num = tuple(int(x) for x in m[1].split("."))
                    if _is_successor(prev_num, num):
                        prev_num = num
                        start(m[1], page_no, m[2] or "")
                        cur.boxes.append(box)
                        continue

                if cur is None:
                    start(None, page_no, text)
                else:
                    cur.lines.append(text)
                    cur.page_end = page_no
                cur.boxes.append(box)
    flush()
    return out


def _sources() -> list[tuple[Path, str]]:
    index = json.loads((ROOT / "regulations" / "index.json").read_text())
    found = []
    for reg in index:
        for v in reg["versions"]:
            for f in v["files"]:
                p = ROOT / "regulations" / "pdf" / reg["regulation"].replace(" ", "_") / v["role"] / f["name"]
                found.append((p, v["role"]))
    for files in sorted((ROOT / "subgoal").glob("*/files.json")):
        for f in json.loads(files.read_text()):
            found.append((files.parent / f["name"], Path(f["name"]).stem))
    return found


def card(paras: list[Paragraph]) -> dict:
    """Short summary of one regulation version for regulation selection: scope text and top-level sections."""
    first = paras[0]
    scope = next((p.text for p in paras if not p.annex and p.section.lower().lstrip("0123456789. ").startswith("scope")
                  and p.text), "")
    sections = list(dict.fromkeys(p.section for p in paras if p.section))
    return {"regulation": first.regulation, "version": first.version, "role": first.role, "file": first.file,
            "pages": max(p.page_end for p in paras), "paragraphs": len(paras), "scope": scope[:1500],
            "sections": sections}


def _norm(label: str | None) -> str:
    sys.path.insert(0, str(ROOT / "tools"))
    from score_local import _paragraph_key  # pyright: ignore[reportMissingImports]  # same normalisation as the scorer
    return _paragraph_key(label) or ""


def check(corpus: list[Paragraph]) -> int:
    """Every citation in the worked examples must resolve to a corpus paragraph whose pages contain the cited page."""
    by_key = collections.defaultdict(list)
    for p in corpus:
        by_key[(p.regulation, p.version, _norm(p.label))].append(p)
    old_version = {p.regulation: p.version for p in corpus if p.role == "old"}
    cites = [(x["source"], "case") for x in json.loads((ROOT / "cases/dev-rear-lamp/expected.json").read_text())["requirements"]]
    cites += [({**x["source"], "version": old_version.get(x["source"]["regulation"], "")}, "subgoal-old")
              for x in json.loads((ROOT / "subgoal/dev/requirements_old.json").read_text())]
    bad = collections.Counter()
    for src, kind in cites:
        key = re.sub(r"\s+table\s+\S+$", "", _norm(src.get("paragraph")))  # "5.1.4.4.1. Table 3" -> its paragraph
        hits = by_key.get((src["regulation"], src["version"], key), [])
        if not hits:
            bad[kind, "label-missing"] += 1
            print(f"  missing  {kind:11} {src['regulation']} {src['version']} p{src['page']} {src.get('paragraph')!r}")
        elif not any(h.page_start - 1 <= src["page"] <= h.page_end + 1 for h in hits):
            bad[kind, "page-off"] += 1
            print(f"  page-off {kind:11} {src['regulation']} {src.get('paragraph')!r} cited p{src['page']}, "
                  f"parsed p{[(h.page_start, h.page_end) for h in hits]}")
    print(f"checked {len(cites)} citations: {len(cites) - sum(bad.values())} resolve; problems: {dict(bad) or 'none'}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="check worked-example citations resolve")
    args = ap.parse_args(argv)

    corpus: list[Paragraph] = []
    cards = []
    for path, role in _sources():
        if not path.is_file():
            print(f"  skip (not downloaded): {path.relative_to(ROOT)}", file=sys.stderr)
            continue
        paras = parse_pdf(path, role)
        corpus += paras
        cards.append(card(paras))
    DATA.mkdir(exist_ok=True)
    with (DATA / "corpus.jsonl").open("w") as fh:
        for p in corpus:
            row = asdict(p)
            del row["lines"]
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    (DATA / "cards.json").write_text(json.dumps(cards, indent=1, ensure_ascii=False))
    print(f"wrote {len(corpus)} paragraphs from {len(cards)} PDFs to data/corpus.jsonl")
    return check(corpus) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
