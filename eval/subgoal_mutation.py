"""Mutation test for the sub-goal (regulation change): known edits, known answers.

    uv run python -m eval.subgoal_mutation                     # R175 (dev pair, old version) and R48 (base)

A "new version" is made from a real old version by applying known edits to the paragraphs that old requirements
cite; pipeline/subgoal.py then compares old and new as usual. Each edit has a correct verdict, so detection can be
scored per kind of edit without an answer file. Code only, no model calls.
"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path

import pipeline.subgoal as sg
from pipeline.ingest import parse_pdf

ROOT = Path(__file__).resolve().parent.parent
PER_KIND = 6


def _sentence_with(text: str, pattern: str) -> str | None:
    for s in sg.sentences(text):
        if re.search(pattern, s) and len(s) > 40:
            return s
    return None


def mutate(old: list, reqs: list[dict], seed: int = 7):
    """Returns (new paragraphs, expected {requirement id: verdict}, number of added paragraphs)."""
    rnd = random.Random(seed)
    new = copy.deepcopy(old)
    by_label = {p.label: p for p in new}
    cited = {}
    for r in reqs:
        cited.setdefault(r["source"].get("paragraph", ""), []).append(r)
    labels = [lab for lab in cited if lab in by_label and by_label[lab].text]
    rnd.shuffle(labels)
    expected, used = {}, set()

    def take(pattern: str):
        for lab in labels:
            if lab in used or len(cited[lab]) != 1:
                continue  # one requirement per paragraph keeps the expected verdict unambiguous
            spans = [sg.Span(lab, 0, t, False) for t in sg.sentences(by_label[lab].text)]
            if not spans:
                continue
            best, _ = sg._best(cited[lab][0]["text"], spans)  # mutate the sentence the requirement came from
            if len(best.text) > 40 and re.search(pattern, best.text):
                used.add(lab)
                return lab, best.text
        return None, None

    def expect(lab: str, verdict: str):
        for r in cited[lab]:
            expected[r["id"]] = verdict

    for _ in range(PER_KIND):  # change a number
        lab, s = take(r"\b\d+(?:[.,]\d+)?\s*(?:mm|m|°|%|per cent|km/h|s|V|cd|kg)\b")
        if lab:
            m = re.search(r"\b(\d+)(?=(?:[.,]\d+)?\s*(?:mm|m|°|%|per cent|km/h|s|V|cd|kg)\b)", s)
            s2 = s[:m.start()] + str(int(m[1]) + 7) + s[m.end():]
            by_label[lab].text = by_label[lab].text.replace(s, s2)
            expect(lab, "modified")
    for _ in range(PER_KIND):  # add a condition
        lab, s = take(r"\bshall\b")
        if lab:
            by_label[lab].text = by_label[lab].text.replace(s, s.replace(" shall ", " shall, under all operating "
                                                                         "conditions, ", 1))
            expect(lab, "modified")
    for _ in range(PER_KIND):  # delete the sentence (and its paragraph when it was the only one)
        lab, s = take(r"\bshall\b")
        if lab:
            by_label[lab].text = by_label[lab].text.replace(s, "").strip()
            expect(lab, "removed")
    for _ in range(PER_KIND):  # renumber, same text
        lab, s = take(r"\bshall\b")
        if lab:
            by_label[lab].label = by_label[lab].label + ".9"
            by_label[lab].number = (by_label[lab].number or "") + ".9"
            expect(lab, "unchanged")
    for _ in range(PER_KIND):  # editorial only
        lab, s = take(r"\b(seconds?|per cent|%)\b")
        if lab:
            s2 = re.sub(r"\bseconds?\b", "s", s) if re.search(r"\bseconds?\b", s) else \
                s.replace("per cent", "%") if "per cent" in s else s.replace("%", " per cent")
            by_label[lab].text = by_label[lab].text.replace(s, s2)
            expect(lab, "unchanged")
    added = [
        "The device shall withstand a salt spray test of 96 hours without loss of function.",
        "The manufacturer shall provide a durable label stating the rated voltage of the device.",
        "Each lamp shall be tested at an ambient temperature of 23 °C ± 5 °C before the photometric measurements.",
    ]
    chapter = sorted(lab.split(".")[0] for lab in cited if lab[:1].isdigit())[0]  # a chapter old requirements cover
    last = next(p for p in reversed(new) if p.number and p.number.split(".")[0] == chapter)
    for i, text in enumerate(added):
        p = copy.deepcopy(last)
        p.annex, p.part, p.number = None, None, f"{chapter}.99.{i + 1}"
        p.label, p.text = p.number, text
        new.insert(new.index(last) + 1 + i, p)
    # requirements on untouched paragraphs must stay unflagged
    for lab, rs in cited.items():
        if lab not in used:
            for r in rs:
                expected.setdefault(r["id"], "unchanged")
    return new, expected, len(added)


def run(old_pdf: Path, reqs: list[dict], name: str) -> dict:
    old = parse_pdf(old_pdf.resolve(), "old")
    new, expected, n_added = mutate(old, reqs)
    case = ROOT / "out" / "subgoal-mutation" / name
    case.mkdir(parents=True, exist_ok=True)
    (case / "files.json").write_text(json.dumps([{"name": "old.pdf"}, {"name": "new.pdf"}]))
    (case / "requirements_old.json").write_text(json.dumps(reqs))
    real = sg.parse_pdf
    sg.parse_pdf = lambda path, role: copy.deepcopy(old) if role == "old" else copy.deepcopy(new)
    try:
        (case / "old.pdf").touch()
        (case / "new.pdf").touch()
        res = sg.analyse(case)
    finally:
        sg.parse_pdf = real
    got = {c["requirement_id"]: c["change"] for c in res["changes"] if c["requirement_id"]}
    added_found = sum(1 for c in res["changes"] if c["change"] == "added" and ".99." in c["source"]["paragraph"])
    added_other = sum(1 for c in res["changes"] if c["change"] == "added" and not ".99." in c["source"]["paragraph"])
    rows = {}
    for rid, want in expected.items():
        have = got.get(rid, "unchanged")
        rows.setdefault(want, [0, 0, []])
        rows[want][0] += 1
        rows[want][1] += have == want
        if have != want:
            rows[want][2].append((rid, have))
    (case / "result.json").write_text(json.dumps({"expected": expected, "got": got, "changes": res["changes"]},
                                                 indent=1, ensure_ascii=False))
    return {"rows": rows, "added": (added_found, n_added), "spurious_added": added_other}


def main() -> int:
    cases = [("R175", ROOT / "subgoal/dev/old.pdf", json.loads((ROOT / "subgoal/dev/requirements_old.json").read_text()))]
    v17 = json.loads((ROOT / "out/uploads/dev-rear-lamp_v17_F2-0.716.json").read_text())["requirements"]
    r48 = [{"id": r["id"], "text": r["text"], "source": {"regulation": "UN R48", "page": r["source"]["page"],
                                                          "paragraph": r["source"]["paragraph"]}}
           for r in v17 if r["source"]["regulation"] == "UN R48"]
    cases.append(("R48", ROOT / "regulations/pdf/UN_R48/base/UN_R48_base.pdf", r48))
    for name, pdf, reqs in cases:
        res = run(pdf, reqs, name)
        print(f"\n{name}: {len(reqs)} old requirements")
        print(f"  {'correct verdict':16} {'cases':>5} {'right':>6}")
        for want, (n, ok, wrong) in sorted(res["rows"].items()):
            print(f"  {want:16} {n:5} {ok:6}" + (f"   wrong: {wrong[:4]}" if wrong else ""))
        print(f"  {'added (new ¶)':16} {res['added'][1]:5} {res['added'][0]:6}   spurious 'added' elsewhere: "
              f"{res['spurious_added']}")
    print("\nfiles: out/subgoal-mutation/<case>/result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
