"""Requirement-level relevance: Claude scores each requirement 0-100 for "would an expert list this for this product".

Runs after the paragraph ranker, on the requirements that survived it. Unlike the ranker (one score per paragraph,
shared by all its requirements) this scores each requirement on its own, so a paragraph's central obligation and
its minor side-provisions can be told apart. The score is stored as `relevance`; any cut-off is chosen by
measurement on the dev case, not by the model.
"""

from __future__ import annotations

import collections
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from pipeline.extract import _profile_brief
from pipeline.llm import ask
from pipeline.profile import ProductProfile

GROUP = 40


class Score(BaseModel):
    id: str
    score: int = Field(description="0-100")


class Scores(BaseModel):
    scores: list[Score]


SYSTEM = """You are a senior homologation engineer reviewing a draft list of engineering requirements for one
product. An expert list keeps the obligations the product's engineers must act on: requirements on the product's
own design, performance limits, colours, markings, installation on the vehicle, and the tests whose results it must
meet, for the functions the product has. It leaves out what engineers would not act on for this product:
procedural detail of how a laboratory runs a test, administrative steps, provisions only for variants or optional
equipment the product does not have, generic statements that another requirement already covers, and repetitions.

Score every requirement from 0 to 100 for how likely an expert would keep it in the list for this product. Use the
whole range."""


def _score_group(profile_line: str, reqs: list[dict]) -> dict[int, int]:
    lines = "\n".join(f"Q{i}: [{r['source']['regulation']} {r['source']['paragraph']}] {r['text']}"
                      for i, r in enumerate(reqs))
    result = ask(Scores, SYSTEM, f"<product>{profile_line}</product>\n\n<requirements>\n{lines}\n</requirements>",
                 effort="medium")
    out = {}
    for s in result.scores:
        key = s.id.strip()
        if key.startswith("Q") and key[1:].isdigit():
            out[int(key[1:])] = max(0, min(100, s.score))
    return out


def score(profile: ProductProfile, reqs: list[dict], workers: int = 8) -> list[dict]:
    """Adds `relevance` (0-100) to every requirement; unscored ones get 50."""
    line = _profile_brief(profile)
    by_reg = collections.defaultdict(list)
    for i, r in enumerate(reqs):
        by_reg[r["source"]["regulation"]].append(i)
    groups = [idx[k:k + GROUP] for idx in by_reg.values() for k in range(0, len(idx), GROUP)]
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda idx: (idx, _score_group(line, [reqs[i] for i in idx])), groups))
    for idx, got in results:
        for j, i in enumerate(idx):
            reqs[i]["relevance"] = got.get(j, 50)
    return reqs
