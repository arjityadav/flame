"""Background pipeline runs with live progress.

One run at a time (a run costs ~$6 and hits rate limits; two in parallel would only slow both). Each stage event
updates out/runs/<id>/run.json and is appended to an in-memory event list that the SSE endpoint streams.
"""

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from app.runs import finish_meta, load_meta, run_dir, save_meta

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flame-run")
_events: dict[str, list[dict]] = {}
_lock = threading.Lock()
_active: str | None = None


def busy() -> str | None:
    return _active


def events(run_id: str, since: int = 0) -> list[dict]:
    with _lock:
        return list(_events.get(run_id, [])[since:])


def _emit(meta: dict, event: dict) -> None:
    event = {**event, "at": time.time()}
    with _lock:
        _events.setdefault(meta["id"], []).append(event)
    save_meta(meta)


def start(run_id: str) -> None:
    global _active
    with _lock:
        if _active is not None:
            raise RuntimeError(f"run {_active} is still in progress")
        _active = run_id
        _events[run_id] = []
    _pool.submit(_run, run_id)


def _run(run_id: str) -> None:
    global _active
    from pipeline import llm
    from pipeline.enrich import enrich
    from pipeline.run import execute

    meta = load_meta(run_id)
    folder = run_dir(run_id)
    started_usd = llm.spent["usd"]
    started = {}

    def on_event(stage: str, status: str, detail: str = "", counts: dict | None = None) -> None:
        for s in meta["stages"]:
            if s["key"] == stage:
                s["status"] = status
                if detail:
                    s["detail"] = detail
                if status == "running":
                    started[stage] = time.time()
                elif stage in started:
                    s["seconds"] = round(time.time() - started[stage])
        meta["stage"] = stage if status == "running" else meta["stage"]
        meta["status"] = "running"
        meta["counts"].update(counts or {})
        meta["cost_usd"] = round(llm.spent["usd"] - started_usd, 2)
        _emit(meta, {"type": "stage", "stage": stage, "status": status, "detail": detail,
                     "counts": meta["counts"], "cost_usd": meta["cost_usd"]})

    try:
        execute(folder / "input", folder, resume=True, checks=False, on_event=on_event)
        on_event("enrich", "running", "rating severity and phase")
        reqs = enrich(folder)
        on_event("enrich", "done", f"{len(reqs)} requirements ordered by priority")
        try:  # build both graph views now, so opening them in a demo never waits (meaning takes ~30 s)
            from app import graph_view
            graph_view.structure(run_id)
            graph_view.meaning(run_id)
        except Exception:  # a graph problem must not fail the run; the view rebuilds on first open
            traceback.print_exc()
        finish_meta(meta, folder)
        meta["cost_usd"] = round(llm.spent["usd"] - started_usd, 2)
        _emit(meta, {"type": "done", "counts": meta["counts"], "cost_usd": meta["cost_usd"]})
    except Exception as exc:  # the run fails, the server stays up
        meta["status"], meta["error"] = "failed", f"{type(exc).__name__}: {exc}"
        for s in meta["stages"]:
            if s["status"] == "running":
                s["status"] = "failed"
        traceback.print_exc()
        _emit(meta, {"type": "error", "message": meta["error"]})
    finally:
        with _lock:
            _active = None
