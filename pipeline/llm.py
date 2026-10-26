"""One way to call Claude for every agent: structured output, on-disk cache, cost tally.

Every call is cached under data/llm_cache/ by a hash of (model, system, content, schema, effort), so a rerun of the
pipeline costs nothing and gives the same answer. Delete the folder, pass fresh=True, or set LLM_FRESH=1 to force new calls
(the new answer then replaces the cached one).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import TypeVar

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "llm_cache"
MODEL = "claude-opus-5-5"
FAST_MODEL = "claude-sonnet-5-5"  # the voice/text assistant, where the first word must come quickly
PRICES = {  # $ per 1M tokens
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
}
PRICE = PRICES[MODEL]

load_dotenv(ROOT / ".env")
if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("CLAUDE_KEY"):
    os.environ["ANTHROPIC_API_KEY"] = os.environ["CLAUDE_KEY"].strip().strip('"')

T = TypeVar("T", bound=BaseModel)
_client: anthropic.Anthropic | None = None
_lock = threading.Lock()
spent = {"calls": 0, "cached": 0, "usd": 0.0}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _cost(usage, model: str = MODEL) -> float:
    price = PRICES.get(model, PRICE)
    return (usage.input_tokens * price["input"] + usage.output_tokens * price["output"]
            + (usage.cache_read_input_tokens or 0) * price["cache_read"]
            + (usage.cache_creation_input_tokens or 0) * price["cache_write"]) / 1e6


def available() -> bool:
    """Whether model calls can be made (a key is configured and offline mode is off)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and not os.environ.get("LLM_OFFLINE")


def ask(schema: type[T], system: str, content: str | list, *, effort: str = "medium", max_tokens: int = 16000,
        fresh: bool = False, model: str = MODEL) -> T:
    """Send one request and return the response parsed into `schema`."""
    messages = [{"role": "user", "content": content}]
    key = hashlib.sha256(json.dumps([model, system, messages, schema.model_json_schema(), effort],
                                    sort_keys=True, default=str).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.is_file() and not fresh and not os.environ.get("LLM_FRESH"):
        with _lock:
            spent["cached"] += 1
        return schema.model_validate_json(json.loads(path.read_text())["output"])
    if os.environ.get("LLM_OFFLINE"):
        raise RuntimeError(f"LLM_OFFLINE is set and this {schema.__name__} request is not cached ({key[:12]})")

    response = _get_client().beta.messages.parse(
        model=model,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # on a safety decline, the API re-runs the request on a fallback model
        system=system,
        messages=messages,
        output_config={"effort": effort},
        output_format=schema,
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"model stopped with {response.stop_reason} (request {response._request_id})")
    parsed = response.parsed_output
    cost = _cost(response.usage, model)
    with _lock:
        spent["calls"] += 1
        spent["usd"] += cost
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"output": parsed.model_dump_json(), "usd": cost, "request_id": response._request_id}))
    return parsed


def stream_text(system: str, messages: list[dict], *, model: str = FAST_MODEL, effort: str = "low",
                max_tokens: int = 1024):
    """Yield the text of one response as it is generated (not cached: questions rarely repeat word for word)."""
    if not available():
        raise RuntimeError("no ANTHROPIC_API_KEY configured (or LLM_OFFLINE is set)")
    with _get_client().beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=messages,
        output_config={"effort": effort},
    ) as stream:
        for text in stream.text_stream:
            yield text
        final = stream.get_final_message()
    with _lock:
        spent["calls"] += 1
        spent["usd"] += _cost(final.usage, model)


def tool_loop(system: str, messages: list[dict], tools: list[dict], run_tool, *, model: str = MODEL,
              effort: str = "low", max_rounds: int = 6, max_tokens: int = 4096):
    """A plain tool-use loop: call the model, run the tools it asks for, repeat until it answers.

    `run_tool(name, input) -> str` executes one tool. Returns (final_text, messages).
    """
    if not available():
        raise RuntimeError("no ANTHROPIC_API_KEY configured (or LLM_OFFLINE is set)")
    for _ in range(max_rounds):
        response = _get_client().beta.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", output_config={"effort": effort},
        )
        with _lock:
            spent["calls"] += 1
            spent["usd"] += _cost(response.usage, model)
        if response.stop_reason == "refusal":
            raise RuntimeError(f"model declined (request {response._request_id})")
        messages = messages + [{"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in response.content]}]
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            return "".join(b.text for b in response.content if b.type == "text"), messages
        results = []
        for call in calls:
            try:
                out, error = run_tool(call.name, call.input), False
            except Exception as exc:  # report the failure to the model rather than crash the loop
                out, error = f"tool failed: {exc}", True
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": out, "is_error": error})
        messages = messages + [{"role": "user", "content": results}]
    return "I could not finish answering within the tool budget; try a narrower question.", messages


def summary() -> str:
    return f"{spent['calls']} model calls (${spent['usd']:.2f}), {spent['cached']} served from cache"
