"""Agent 5, extractors: legal text -> one-obligation requirements, one call per chunk of a selected unit.

The model sees numbered paragraphs ([P1], [P2], ...) and the definitions they use, and returns requirements that
each point back to one paragraph. Regulation, version, page and paragraph label are then filled in by code from
the corpus: the model never writes a citation.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from pipeline.examples import retrieve
from pipeline.graph import Graph
from pipeline.llm import ask
from pipeline.profile import ProductProfile

CHUNK_CHARS = 12000
# v4 (retrieved expert examples + parent context) matched 141 vs v1's 143-146 on dev: no gain, so off by default
STYLE_EXAMPLES = False
UNGROUNDED: list[dict] = []  # requirements dropped by the grounding check in this process (for reporting)
EFFORT = "high"  # extraction thinking effort; dev: medium 113-122, high 127-132, xhigh 122 matches


EXAMPLES_RULE = """- <expert_examples> shows how the experts wrote requirements from similar paragraphs of other regulations:
  match their granularity (how many requirements per paragraph) and their wording.
"""


class Extracted(BaseModel):
    paragraph: str = Field(description="the [P..] reference the obligation comes from, e.g. 'P3'")
    text: str = Field(description="one engineering obligation, close to the regulation's wording")
    evidence: str = Field(description="the exact words of the paragraph that state this obligation: one contiguous "
                                      "span copied verbatim, no ellipsis, no paraphrase")
    applies_to: str = Field(description="which of the product's functions or parts it touches; a label only, it "
                                        "does not narrow the text")
    test_case: str = Field(description="how to verify it: method or annex, condition, pass criterion")


class Extraction(BaseModel):
    requirements: list[Extracted]


SYSTEM = """You turn UN Regulation text into the engineering requirements a specific product must fulfil, in the
style of an expert regulatory requirements list.

Rules:
- One obligation per requirement. Separate obligations give separate requirements: two "shall" statements, one
  limit per band, direction or class, one case per item of an enumerated list of cases. A list that is the
  subject or the condition of a single obligation stays in that one requirement.
- Stay close to the regulation's wording. Make each requirement stand alone: name its subject (taken from the
  paragraph or its heading, not "It shall ..."), and keep the condition it depends on (the test method, the
  configuration, the class).
- The product decides which obligations you write, never what they say. Write each obligation with the
  regulation's own scope, even where the product falls under only part of it: its subject, the vehicle
  categories, device classes or variants it names, its lists of values, and its exceptions, alternatives and
  permissions for other configurations or optional equipment. A sentence that relaxes or extends an obligation
  for another case belongs to that obligation: keep it with it, or write it as its own requirement.
- Keep every number, unit, tolerance and referenced paragraph or annex exactly.
- Include obligations on the device, its markings, its performance and tests, and its installation on the
  vehicle where the text covers installation.
- Skip: obligations whose whole scope lies outside the product (only about functions, devices or vehicle
  categories it does not have); when in doubt, write it. Also skip duties of type approval authorities or
  technical services; application paperwork; definitions; notes and explanations with no obligation; pure
  cross-references with no content of their own.
- Every requirement must come from the paragraph it points to. In `evidence`, copy the words of that paragraph
  that state the obligation: one contiguous span, verbatim. A requirement you cannot back with such a span must
  not be written.
- Paragraphs may give no requirement at all; return an empty list if none apply.

Examples of the expected style (from a different regulation; match the form, not the content):
- "The approval mark shall be clearly legible and be indelible."
- "When a collision is not prevented, the ACPE shall ensure the collision speed is not greater than 70% of that
  speed the vehicle would have had in the same position and under the same circumstances but without any ACPE
  intervention."
- "Notwithstanding paragraph 5.1.2., in the case of systems that implement force-based triggering of the ACPE, the
  system shall consider an accelerator control force greater than the triggering threshold as accelerator
  control misapplication."
- "When a vehicle is equipped with a means to manually deactivate the ACPE, the ACPE shall be automatically
  reinstated at the initiation of any new engine start (or run cycle, as relevant), except that this requirement
  does not apply when a new engine start (or run cycle, as relevant) is performed automatically, e.g., the
  operation of a stop/start system."
"""


def _chunks(g: Graph, paragraphs: list[str]) -> list[list[str]]:
    out, cur, size = [], [], 0
    for pid in paragraphs:
        n = len(g.nodes[pid]["text"])
        if cur and size + n > CHUNK_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(pid)
        size += n
    if cur:
        out.append(cur)
    return out


def _definitions(g: Graph, pids: list[str], limit: int = 15) -> list[str]:
    terms = list(dict.fromkeys(t for p in pids for t in g.out(p, "USES_TERM")))
    return [g.nodes[t]["definition"][:350] for t in terms[:limit]]


def _profile_brief(profile: ProductProfile) -> str:
    funcs = "; ".join(f.name for f in profile.functions)
    parts = "; ".join(p.name for p in profile.parts)
    scope = f" ({profile.regulatory_scope})" if profile.regulatory_scope else ""
    return (f"{profile.product_name}: {profile.product_type}. Vehicle categories: {', '.join(profile.vehicle_categories)}"
            f"{scope}. Mounting: {profile.mounting_location}.\nFunctions: {funcs}\nParts: {parts}\n"
            f"Electronics: {'; '.join(profile.electronics) or 'none'}")


def _line(g: Graph, ref: str, pid: str, in_chunk: set[str]) -> str:
    """One paragraph, with its parent's text when the parent is not in the chunk: the subject and condition of
    table rows and sub-items ("6.10.4.2 Where optional lamps are installed ...") often sit in the parent."""
    n = g.nodes[pid]
    par = [p for p in g.out(pid, "CHILD_OF") if p not in in_chunk]
    ctx = f" (under {g.nodes[par[0]]['label']}: {g.nodes[par[0]]['text'][:200]})" if par else ""
    return f"[{ref}] {n['label']}{ctx}: {n['text']}"


def _style_examples(g: Graph, texts: list[str], case: str, regulation: str) -> list[str]:
    listed, _ = retrieve(g, texts, case, regulation, k_listed=5, k_unlisted=0)
    return [f"Paragraph [{e.regulation} {e.label}]: {e.text[:300]}\n" + "\n".join(f"  -> {t[:300]}" for t in e.requirements[:4])
            for e in listed]


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-–—]", "-", text)
    text = re.sub(r"[^\w%°±.,-]+", " ", text)  # drop quotes, brackets and other punctuation; keep numbers whole
    text = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", text)  # sentence punctuation, but not 1,500 or 0.2
    return " ".join(text.split())


def grounded(evidence: str, paragraph: str) -> bool:
    """The evidence span really occurs in the paragraph (ignoring case, whitespace and punctuation)."""
    ev = _norm(evidence)
    return len(ev) >= 12 and "..." not in evidence and "…" not in evidence and ev in _norm(paragraph)


def extract_chunk(profile: ProductProfile, g: Graph, case: str, regulation: str, unit: dict, pids: list[str]
                  ) -> list[dict]:
    refs = {f"P{i + 1}": pid for i, pid in enumerate(pids)}
    if STYLE_EXAMPLES:
        body = "\n".join(_line(g, ref, pid, set(pids)) for ref, pid in refs.items())
        examples = _style_examples(g, [g.nodes[p]["text"] for p in pids], case, regulation)
        system = SYSTEM.replace("Examples of the expected style:", EXAMPLES_RULE + "\nExamples of the expected style:")
    else:
        body = "\n".join(f"[{ref}] {g.nodes[pid]['label']}: {g.nodes[pid]['text']}" for ref, pid in refs.items())
        examples, system = [], SYSTEM
    defs = _definitions(g, pids)
    content = (f"<product>\n{_profile_brief(profile)}\n</product>\n\n"
               + (f"<expert_examples>\n" + "\n".join(examples) + "\n</expert_examples>\n\n" if examples else "")
               + f"<regulation>{regulation}, section {unit['key']}: {unit['title']}</regulation>\n\n"
               + (f"<definitions>\n" + "\n".join(defs) + "\n</definitions>\n\n" if defs else "")
               + f"<paragraphs>\n{body}\n</paragraphs>\n\nExtract the requirements.")
    result = ask(Extraction, system, content, effort=EFFORT)
    out = []
    for r in result.requirements:
        pid = refs.get(r.paragraph.strip("[] "))
        if not pid:
            continue  # model pointed at no paragraph we gave it: drop rather than guess a citation
        n = g.nodes[pid]
        if not grounded(r.evidence, n["text"]):
            UNGROUNDED.append({"node": pid, "text": r.text, "evidence": r.evidence})
            continue  # not backed by a verbatim span of its paragraph: dropped
        out.append({"text": r.text.strip(), "evidence": r.evidence.strip(), "applies_to": r.applies_to,
                    "test_case": r.test_case, "node": pid,
                    "source": {"regulation": n["regulation"], "version": n["version"], "page": n["page_start"],
                               "paragraph": n["label"]}, "unit": unit["key"]})
    return out


def extract_all(profile: ProductProfile, g: Graph, sections: list[dict], case: str, workers: int = 8) -> list[dict]:
    jobs = [(s["regulation"], u, chunk) for s in sections for u in s["units"] for chunk in _chunks(g, u["paragraphs"])]
    with ThreadPoolExecutor(workers) as pool:
        results = pool.map(lambda j: extract_chunk(profile, g, case, *j), jobs)
    return [r for batch in results for r in batch]
