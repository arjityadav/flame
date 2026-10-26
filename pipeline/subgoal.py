"""Agent 7, change analyst (sub-goal), in plain code: which old requirements must change in a newer version.

    uv run python -m pipeline.subgoal subgoal/dev                 # writes out/subgoal-dev/changes.json
    uv run python -m pipeline.subgoal subgoal/dev --team-id team-08

Both PDFs are parsed with the Stage 0 parser and cut into spans: sentences, list items "(a) ...", and whole
paragraphs. Each old requirement is located in the old text (the span its wording matches best within its cited
paragraph), then its counterpart is searched in the whole new text, so renumbered and split paragraphs are found
(6.5 -> 6.5.2, 6.6, 6.6.1.2). A word diff of the two spans decides:
  identical            -> unchanged (not reported)
  similar, with edits  -> modified; the reason lists the edits ("'a' became 'b'; 'c' was added")
  no counterpart       -> removed
New spans with no old counterpart, in the chapters the old requirements cover, are reported as added.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

from pipeline.ingest import Paragraph, parse_pdf

ROOT = Path(__file__).resolve().parent.parent
MODIFIED_MIN = 56  # fuzz.ratio of old span vs best new span: below this the provision is gone (tuned on subgoal/dev)
ADDED_MAX = 75  # a new span whose best old match is below this is new text
OBLIGATION = re.compile(r"\b(shall|must|may|required|not more than|at least)\b", re.I)
SENTENCE_END = re.compile(r"(?<=[.;:])\s+(?=[A-Z(])")
ABBREV_END = re.compile(r"\b(e\.g|i\.e|No|Nos|para|paras|Fig|approx|max|min|cf)\.$", re.I)


HEADING = re.compile(r"^((?:[A-Z][\w\-/()]*)(?:\s+[\w\-/(),]+){0,7}?),?\s+(?=(?:The|A|An|Each|When|If|For|In|Where|While|Any|"
                     r"All|No|This|These|Vehicles?|ACPE)\b)")
EDITORIAL = [(r"\bper cent\b", "%"), (r"\s+%", "%"), (r"\bseconds?\b", "s"), (r"\bminutes?\b", "min"),
             (r"[“”\"]", "'"), (r"[‐–—]", "-"), (r"\s+", " ")]
CONDITION_START = re.compile(r"^(The|A|An|This|These|For|In|When|If|Where|While|During|After|Before|On|At|With|Without|Each|All|Any|Unless)\b")  # starts a sentence or a condition, not a heading
NOT_HEADING = re.compile(r"\b(shall|must|may|is|are|be|not)\b", re.I)


@dataclass
class Span:
    label: str  # paragraph label
    page: int
    text: str
    whole: bool  # the whole paragraph rather than one sentence or item
    lead: str | None = None  # for a list item "(a) ...": the sentence introducing the list


def _norm(text: str) -> str:
    return " ".join(text.split()).strip()


def sentences(text: str) -> list[str]:
    """Split paragraph text into sentences and list items, keeping abbreviations and numbers together."""
    out: list[str] = []
    for piece in SENTENCE_END.split(text):
        if out and (ABBREV_END.search(out[-1]) or re.search(r"\d\.$", out[-1]) and not piece.startswith("(")):
            out[-1] += " " + piece
        else:
            out.append(piece)
    return [_norm(s) for s in out if _norm(s)]


def _core(text: str) -> str:
    """Text without a leading heading ("Accelerator control misapplication test The lateral offset ..."): the
    parser joins a heading line to the paragraph body, and a new or dropped heading is not a change in substance."""
    m = HEADING.match(text)
    if m and not NOT_HEADING.search(m[1]) and not CONDITION_START.match(m[1]):
        return text[m.end():]
    return text


def _editorial(text: str) -> str:
    text = text.lower()
    for pat, rep in EDITORIAL:
        text = re.sub(pat, rep, text)
    return text.strip(" .;")


def spans(paras: list[Paragraph]) -> list[Span]:
    out = []
    for p in paras:
        if not p.text:
            continue
        parts = sentences(p.text)
        out.append(Span(p.label, p.page_start, _norm(p.text), True))
        if len(parts) > 1:
            lead = None
            for part in parts:
                item = bool(re.match(r"^\(([a-z]|[ivx]+)\)\s", part))
                out.append(Span(p.label, p.page_start, part, False, lead if item else None))
                if part.endswith(":"):
                    lead = part
                elif not item:
                    lead = None
    return out


def diff_ops(old: str, new: str) -> list[str]:
    """Word-level edits from old to new, phrased as the expected answers phrase them."""
    a, b = old.split(), new.split()
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        x, y = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if tag == "replace":
            ops.append(f"'{x}' became '{y}'")
        elif tag == "insert":
            ops.append(f"'{y}' was added")
        elif tag == "delete":
            ops.append(f"'{x}' was removed")
    return ops


def substantive(old: str, new: str) -> bool:
    """Whether the change from old to new is more than spelling of units, quotes, dashes or a heading."""
    return _editorial(_core(old)) != _editorial(_core(new))


def _cap(text: str, n: int = 2000) -> str:
    if len(text) <= n:
        return text
    cut = text[:n]
    return cut[: max(cut.rfind(". "), cut.rfind("; ")) + 1] or cut


def _item_text(lead: str, item: str) -> str:
    """'... shall be as follows:' + '(a) Position the vehicle ...' -> '... shall be to position the vehicle ...'."""
    body = re.sub(r"^\(([a-z]|[ivx]+)\)\s*", "", item)
    first = body.split(" ", 1)[0]
    if re.search(r"\bshall be as follows:$", lead) and first[:1].isupper() and first not in (
            "In", "If", "When", "Where", "The", "A", "An", "For", "While", "Each"):
        return lead[: -len("as follows:")] + "to " + body[0].lower() + body[1:]
    return f"{lead} {item}"


def _split_modal(text: str) -> list[str]:
    """'For tests without a target the ACPE shall be deactivated if necessary, and may be combined where feasible.'
    -> ['... the ACPE shall be deactivated if necessary.', 'Tests without a target may be combined where feasible.']"""
    m = re.search(r",?\s+and\s+(?=(?:shall|must|may)\b)", text)
    if not m:
        return [text]
    first, second = text[: m.start()].rstrip(" ,") + ".", text[m.end():]
    cond = re.match(r"^(?:For|In the case of|In case of)\s+(.+?)\s+(?:the|an?)\s+[\w\-]+(?:\s+[\w\-]+)?\s+(?:shall|must)\b",
                    first)
    subject = cond[1] if cond else re.split(r"\s+(?:shall|must|may)\b", first)[0]
    return [first, subject[0].upper() + subject[1:] + " " + second]


def _snip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _best(target: str, pool: list[Span], prefer_label: str | None = None) -> tuple[Span | None, float]:
    best, score = None, -1.0
    for s in pool:
        r = fuzz.ratio(_core(target), _core(s.text)) + (0.5 if s.label == prefer_label else 0)
        if r > score:
            best, score = s, r
    return best, score


def analyse(case_dir: Path) -> dict:
    files = {f["name"]: f for f in json.loads((case_dir / "files.json").read_text())}
    old_paras = parse_pdf((case_dir / "old.pdf").resolve(), "old")
    new_paras = parse_pdf((case_dir / "new.pdf").resolve(), "new")
    reg, new_version = new_paras[0].regulation, new_paras[0].version
    old_spans, new_spans = spans(old_paras), spans(new_paras)
    old_by_label: dict[str, list[Span]] = {}
    for s in old_spans:
        old_by_label.setdefault(s.label, []).append(s)
    requirements = json.loads((case_dir / "requirements_old.json").read_text())
    new_wholes = [s for s in new_spans if s.whole]
    # a sentence can move into a paragraph of its own (6.5 -> 6.5.2), which is stored as a whole paragraph only
    new_parts = [s for s in new_spans if not s.whole] + [s for s in new_wholes if len(sentences(s.text)) == 1]

    changes, used_new = [], set()
    for req in requirements:
        label = req["source"].get("paragraph", "")
        local = old_by_label.get(label) or old_spans
        old_span, _ = _best(req["text"], local)  # the regulation text this requirement was written from
        pool = new_parts if not old_span.whole else new_wholes
        new_span, score = _best(old_span.text, pool, prefer_label=old_span.label)
        if new_span and not substantive(old_span.text, new_span.text):
            used_new.add((new_span.label, new_span.text))
            continue  # unchanged in substance, wherever it now sits
        if new_span and score >= MODIFIED_MIN:
            used_new.add((new_span.label, new_span.text))
            ops = diff_ops(old_span.text, new_span.text)
            moved = f" (formerly {old_span.label})" if new_span.label != old_span.label else ""
            changes.append({
                "requirement_id": req["id"], "change": "modified", "new_text": _cap(_core(new_span.text)),
                "reason": f"Paragraph {new_span.label}{moved} of {reg} was amended in version {new_version}: "
                          + "; ".join(ops) + ".",
                "source": {"page": new_span.page, "paragraph": new_span.label},
                "similarity": round(score, 1),
            })
        else:
            changes.append({
                "requirement_id": req["id"], "change": "removed",
                "reason": f"Paragraph {old_span.label} of {reg} was amended in version {new_version}; the provision "
                          f"'{_snip(old_span.text, 90)}' no longer appears.",
                "similarity": round(score, 1),
            })

    chapters = {req["source"].get("paragraph", "").split(".")[0] for req in requirements}
    old_texts = [s for s in old_spans if not s.whole] + [s for s in old_spans if s.whole]
    old_wholes = [s for s in old_spans if s.whole]
    new_paragraph = {s.label for s in new_spans if s.whole and _best(s.text, old_wholes)[1] < ADDED_MAX}
    whole_with_items = {(s.label) for s in new_spans if s.lead and not re.search(r"\b(shall|must)\b", s.lead)}
    for s in new_spans:
        if s.label.split(".")[0] not in chapters or (s.label, s.text) in used_new or len(s.text) < 30:
            continue
        if s.lead:  # list item: an obligation if its lead-in obliges ("... shall be as follows: (a) ...")
            if not re.search(r"\b(shall|must)\b", s.lead):
                continue
            new_text = _item_text(s.lead, s.text)
        elif s.whole:  # one-sentence paragraph, or a list whose lead-in does not oblige ("not required when:")
            if not (len(sentences(s.text)) == 1 or s.label in whole_with_items) or not OBLIGATION.search(s.text):
                continue
            new_text = s.text
        else:
            if s.text.endswith(":") or not OBLIGATION.search(s.text) or s.label in whole_with_items:
                continue
            new_text = s.text
        _, score = _best(s.text, old_texts)
        if score < ADDED_MAX or (s.lead and s.label in new_paragraph):  # items of a new paragraph are new
            for text in _split_modal(_core(new_text)):  # one obligation per change; no heading in new_text
                changes.append({
                    "requirement_id": None, "change": "added", "new_text": _cap(text),
                    "reason": f"Paragraph {s.label} of {reg} is new in version {new_version}: '{_snip(s.text, 100)}'.",
                    "source": {"page": s.page, "paragraph": s.label},
                    "similarity": round(score, 1),
                })
    return {"regulation": reg, "old_version": old_paras[0].version, "new_version": new_version,
            "files": sorted(files), "changes": changes}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--team-id", default="team-08")
    args = ap.parse_args(argv)
    case_dir = args.case_dir.resolve()
    case_id = f"subgoal-{case_dir.name}"
    result = analyse(case_dir)
    out = ROOT / "out" / case_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "analysis.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    submission = {"team_id": args.team_id, "case_id": case_id,
                  "changes": [{k: v for k, v in c.items() if k != "similarity"} for c in result["changes"]]}
    path = out / "changes.json"
    path.write_text(json.dumps(submission, indent=1, ensure_ascii=False))
    kinds = {k: sum(c["change"] == k for c in result["changes"]) for k in ("modified", "removed", "added")}
    print(f"{result['regulation']} {result['old_version']} -> {result['new_version']}: {kinds}; wrote "
          f"{path.relative_to(ROOT)}")
    subprocess.run([sys.executable, str(ROOT / "tools" / "validate.py"), str(path),
                    "--requirements-old", str(case_dir / "requirements_old.json")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
