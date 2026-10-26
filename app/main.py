"""Flame web app: FastAPI backend.

    uv run uvicorn app.main:app --reload --port 8000        # API; the client dev server proxies /api and /files here
    cd web && npm run build                                  # then the API also serves the built client at /

Every /api and /files route needs a signed-in account (app/auth.py; the login page is /login). FLAME_PASSWORD in .env,
if set, is the access code needed to create an account.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel

from app import accuracy, auth, changes, jobs, parts, runs, trace, upgrade, voice
from app.store import ROOT, store
from pipeline import llm

WEB = ROOT / "web" / "dist"
MAX_UPLOAD = 20 * 1024 * 1024

app = FastAPI(title="Flame")


def error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


@app.exception_handler(HTTPException)
async def _http_error(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse({"error": detail}, status_code=exc.status_code)


auth.init()


@app.middleware("http")
async def _session(request: Request, call_next):
    """The signed-in user on request.state.user; data routes refuse anyone else. The client (HTML, scripts) and
    /api/auth/* stay public so the login page can load."""
    path = request.url.path
    request.state.user = await asyncio.to_thread(auth.user_for, request.cookies.get(auth.COOKIE))
    protected = path.startswith(("/api/", "/files/")) and not path.startswith("/api/auth/")
    if protected and request.state.user is None:
        return JSONResponse({"error": {"code": "unauthenticated", "message": "Sign in to continue."}}, status_code=401)
    return await call_next(request)


def _uid(request: Request) -> int | None:
    user = getattr(request.state, "user", None)
    return user["id"] if user else None


def _meta(run_id: str) -> dict:
    try:
        return runs.load_meta(run_id)
    except KeyError:
        raise error(404, "run_not_found", f"No analysis called {run_id}.") from None


# ---------------------------------------------------------------------------------------------- accounts

class RegisterBody(BaseModel):
    email: str
    password: str
    name: str
    code: str = ""
    remember: bool = True


class LoginBody(BaseModel):
    email: str
    password: str
    remember: bool = True


class ProfileBody(BaseModel):
    name: str | None = None
    organisation: str | None = None
    role: str | None = None
    color: str | None = None


class PasswordBody(BaseModel):
    current: str
    new: str


def _auth_call(fn, *args):
    try:
        return fn(*args)
    except auth.AuthError as exc:
        raise error(exc.status, exc.code, exc.message) from None


def _signed_in(request: Request, user: dict, remember: bool) -> JSONResponse:
    token, max_age = auth.new_session(user["id"], request.headers.get("user-agent", ""),
                                      request.client.host if request.client else "", remember)
    res = JSONResponse({"user": user})
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    res.set_cookie(auth.COOKIE, token, max_age=max_age if remember else None, httponly=True, samesite="lax",
                   secure=secure, path="/")
    return res


def _me(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if user is None:
        raise error(401, "unauthenticated", "Sign in to continue.")
    return user


@app.get("/api/auth/config")
def auth_config():
    return auth.config()


@app.post("/api/auth/register")
def register(body: RegisterBody, request: Request):
    user = _auth_call(auth.register, body.email, body.password, body.name, body.code)
    return _signed_in(request, user, body.remember)


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    user = _auth_call(auth.login, body.email, body.password, request.client.host if request.client else "")
    auth.log(user["id"], "login", agent=request.headers.get("user-agent", "")[:120])
    return _signed_in(request, user, body.remember)


@app.post("/api/auth/logout")
def logout(request: Request):
    if (user := getattr(request.state, "user", None)):
        auth.log(user["id"], "logout")
    auth.end_session(request.cookies.get(auth.COOKIE))
    res = JSONResponse({"ok": True})
    res.delete_cookie(auth.COOKIE, path="/")
    return res


@app.get("/api/auth/me")
def me(request: Request):
    user = _me(request)
    return {"user": user, "stats": auth.stats(user["id"])}


@app.patch("/api/auth/me")
def update_me(body: ProfileBody, request: Request):
    user = _me(request)
    return {"user": _auth_call(auth.update_profile, user["id"], body.model_dump(exclude_none=True))}


@app.post("/api/auth/password")
def change_password(body: PasswordBody, request: Request):
    user = _me(request)
    _auth_call(auth.change_password, user["id"], body.current, body.new, request.cookies.get(auth.COOKIE, ""))
    return {"ok": True}


@app.get("/api/auth/sessions")
def list_sessions(request: Request):
    return auth.sessions(_me(request)["id"], request.cookies.get(auth.COOKIE, ""))


@app.delete("/api/auth/sessions")
def end_other_sessions(request: Request):
    return {"ended": auth.end_other_sessions(_me(request)["id"], request.cookies.get(auth.COOKIE, ""))}


@app.get("/api/me/history")
def my_history(request: Request, type: str = "", before: int | None = None, limit: int = 50):
    user = _me(request)
    types = [t for t in type.split(",") if t in auth.EVENT_TYPES]
    data = auth.history(user["id"], types or None, before, limit)
    titles = {m["id"]: m["title"] for m in runs.list_runs()}
    for e in data["events"]:
        e["run_title"] = titles.get(e["run_id"]) if e["run_id"] else None
    return data


@app.delete("/api/me/history")
def clear_my_history(request: Request):
    return {"deleted": auth.clear_history(_me(request)["id"])}


@app.get("/api/me/runs")
def my_runs(request: Request):
    uid = _me(request)["id"]
    return [m for m in runs.list_runs() if m.get("owner") == uid]


# ---------------------------------------------------------------------------------------------- status

@app.get("/api/status")
def status():
    return {"model": llm.available(), "voice": voice.enabled(), "busy": jobs.busy()}


# ---------------------------------------------------------------------------------------------- runs

@app.get("/api/runs")
def list_runs():
    return runs.list_runs()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "analysis"


@app.post("/api/runs")
async def create_run(
    request: Request,
    title: str = Form(""),
    spec_text: str = Form(""),
    spec: UploadFile | None = File(None),
    drawings: list[UploadFile] = File(default=[]),
):
    if jobs.busy():
        raise error(409, "busy", "Another analysis is still running. Open it from Runs, or wait for it to finish.")
    spec_body = spec_text.strip()
    if spec is not None and spec.filename:
        raw = await spec.read()
        if len(raw) > MAX_UPLOAD:
            raise error(400, "too_large", "The specification is larger than 20 MB.")
        if raw[:4] == b"%PDF":
            with pymupdf.open(stream=raw, filetype="pdf") as doc:
                spec_body = "\n\n".join(page.get_text() for page in doc)
        else:
            try:
                spec_body = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise error(400, "bad_spec", "The specification must be Markdown, plain text or PDF.") from None
    images = []
    for d in drawings:
        raw = await d.read()
        if not raw:
            continue
        if len(raw) > MAX_UPLOAD:
            raise error(400, "too_large", f"{d.filename} is larger than 20 MB.")
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            images.append(("png", raw))
        elif raw[:3] == b"\xff\xd8\xff":
            images.append(("jpg", raw))
        else:
            raise error(400, "bad_drawing", f"{d.filename} is not a PNG or JPEG image.")
    if not spec_body and not images:
        raise error(400, "empty", "Add a specification, a drawing, or both.")

    name = title.strip() or (spec_body.splitlines()[0].lstrip("# ").replace("spec:", "").strip() if spec_body else "") \
        or "New part"
    run_id = f"{_slug(name)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    folder = runs.run_dir(run_id)
    (folder / "input").mkdir(parents=True)
    if spec_body:
        (folder / "input" / "spec.md").write_text(spec_body)
    for i, (ext, raw) in enumerate(images):  # the product analyst reads exploded_view.<ext>; extra drawings are kept
        (folder / "input" / (f"exploded_view.{ext}" if i == 0 else f"drawing_{i + 1}.{ext}")).write_bytes(raw)
    meta = runs.new_meta(run_id, name)
    meta["owner"] = _uid(request)
    runs.save_meta(meta)
    auth.log(_uid(request), "run_created", run_id, title=name)
    if not llm.available():
        meta["status"], meta["error"] = "failed", "No Claude API key (CLAUDE_KEY in .env) is configured on this " \
                                                   "server, so a new analysis can't run. Open a finished one from Runs."
        runs.save_meta(meta)
        return meta
    jobs.start(run_id)
    return runs.load_meta(run_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _meta(run_id)


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request):
    _meta(run_id)

    async def stream():
        sent = 0
        yield f"event: meta\ndata: {json.dumps(runs.load_meta(run_id))}\n\n"
        while not await request.is_disconnected():
            for event in jobs.events(run_id, sent):
                sent += 1
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    return
            meta = runs.load_meta(run_id)
            if meta["status"] in ("done", "failed") and jobs.busy() != run_id:
                yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/runs/{run_id}/requirements")
def get_requirements(run_id: str):
    _meta(run_id)
    return runs.requirements(run_id)


@app.get("/api/runs/{run_id}/profile")
def get_profile(run_id: str):
    _meta(run_id)
    folder = runs.run_dir(run_id)
    inputs = sorted(p.name for p in (folder / "input").iterdir()) if (folder / "input").is_dir() else []
    return {"profile": runs.read_json(run_id, "01_profile.json"), "regulations": runs.regulations(run_id),
            "inputs": inputs}


@app.get("/api/runs/{run_id}/input/{name}")
def get_input(run_id: str, name: str):
    _meta(run_id)
    path = runs.run_dir(run_id) / "input" / Path(name).name
    if not path.is_file():
        raise error(404, "not_found", "No such input file.")
    return FileResponse(path)


@app.get("/api/runs/{run_id}/export")
def export(run_id: str, request: Request, format: str = "submission"):
    auth.log(_uid(request), "export", run_id, format=format)
    _meta(run_id)
    if format == "csv":
        return PlainTextResponse(runs.export_csv(run_id), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'})
    path = runs.run_dir(run_id) / "submission.json"
    if not path.is_file():
        raise error(404, "not_ready", "This analysis has no submission file yet.")
    return FileResponse(path, media_type="application/json", filename=f"{run_id}-submission.json")


# ---------------------------------------------------------------------------------------------- sources

# ---------------------------------------------------------------------------------------------- agents trace

@app.get("/api/runs/{run_id}/trace")
def get_trace(run_id: str):
    """What each agent decided, from the saved stage files (grows while the run is going)."""
    _meta(run_id)
    return trace.trace(run_id)


@app.get("/api/runs/{run_id}/accuracy")
def get_accuracy(run_id: str):
    """The submission against its case's expected answer (worked example only); {"case": null} when there is none."""
    _meta(run_id)
    return accuracy.accuracy(run_id)


@app.get("/api/runs/{run_id}/upgrade")
def get_upgrade(run_id: str):
    """What the latest versions of the cited regulations change for this run's requirements (plain code, cached)."""
    _meta(run_id)
    return upgrade.upgrade(run_id)


@app.get("/api/runs/{run_id}/trace/journeys")
def get_journeys(run_id: str):
    """Every candidate requirement and the agent that passed it on or stopped it."""
    _meta(run_id)
    return trace.journeys(run_id)


# ---------------------------------------------------------------------------------------------- 3D exploded view

@app.get("/api/runs/{run_id}/parts")
def get_parts(run_id: str):
    """Parts of the drawing (boxes, depth order, cut-out crops, requirement ids), or 404 until detected."""
    _meta(run_id)
    data = parts.load(run_id)
    if not data or "crops" not in data:
        has = parts.drawing(run_id) is not None
        raise error(404, "parts_not_detected" if has else "no_drawing",
                    "Parts not detected yet." if has else "This analysis has no exploded-view drawing.")
    return data


@app.post("/api/runs/{run_id}/parts/detect")
def detect_parts(run_id: str, request: Request):
    auth.log(_uid(request), "parts_detect", run_id)
    """Find the parts with Claude vision (two passes, ~$0.07), then cut each part's strokes out of the drawing."""
    _meta(run_id)
    if parts.drawing(run_id) is None:
        raise error(404, "no_drawing", "This analysis has no exploded-view drawing.")
    if not llm.available():
        raise error(503, "no_model", "Part detection needs CLAUDE_KEY.")
    parts.detect(run_id)
    parts.refine(run_id)
    parts.cutouts(run_id)
    return parts.load(run_id)


@app.put("/api/runs/{run_id}/parts")
async def put_parts(run_id: str, request: Request):
    """Save corrected boxes or depths, then cut the parts again."""
    _meta(run_id)
    parts.save(run_id, await request.json())
    parts.cutouts(run_id)
    return parts.load(run_id)


@app.get("/api/runs/{run_id}/parts/{index}.png")
def get_part_png(run_id: str, index: int):
    _meta(run_id)
    path = runs.run_dir(run_id) / "parts" / f"{index}.png"
    if not path.is_file():
        raise error(404, "not_found", "No such part cut-out.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------------------------- regulation change

@app.get("/api/changes")
def list_changes():
    """Sub-goal cases (subgoal/<case>/ with old.pdf, new.pdf, requirements_old.json)."""
    return changes.cases()


@app.get("/api/changes/{case}")
def get_changes(case: str):
    """Which old requirements change, with old/new paragraph texts, pages and the individual edits."""
    try:
        return changes.analysis(case)
    except KeyError:
        raise error(404, "case_not_found", "No such regulation-change case.")


@app.get("/api/changes/{case}/pdf/{which}")
def get_change_pdf(case: str, which: str):
    if which not in ("old", "new"):
        raise error(404, "not_found", "Use old or new.")
    try:
        return FileResponse(changes.case_dir(case) / f"{which}.pdf", media_type="application/pdf")
    except KeyError:
        raise error(404, "case_not_found", "No such regulation-change case.")


@app.get("/api/changes/{case}/export")
def export_changes(case: str, request: Request):
    auth.log(_uid(request), "export", None, format="changes.json", case=case)
    try:
        data = changes.submission(case)
    except KeyError:
        raise error(404, "case_not_found", "No such regulation-change case.")
    return Response(json.dumps(data, indent=1, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{data["case_id"]}-changes.json"'})


@app.get("/api/paragraphs/{node:path}")
def get_paragraph(node: str):
    p = store.paragraph(node)
    if p is None:
        raise error(404, "paragraph_not_found", "That paragraph is not in the parsed regulations.")
    return p


@app.get("/files/{slug}/{role}/{name}")
def get_pdf(slug: str, role: str, name: str):
    path = store.pdf_path(slug, role, name)
    if path is None:
        raise error(404, "pdf_not_found", "That regulation PDF is not on this server.")
    return FileResponse(path, media_type="application/pdf")


# ---------------------------------------------------------------------------------------------- assistant

class AskBody(BaseModel):
    question: str
    focus: str | None = None
    history: list[dict] = []


@app.post("/api/runs/{run_id}/ask")
def ask(run_id: str, body: AskBody, request: Request):
    _meta(run_id)
    if not body.question.strip():
        raise error(400, "empty", "Ask a question first.")
    auth.log(_uid(request), "ask", run_id, question=body.question.strip()[:300])
    from app.ask import stream_answer

    def stream():
        try:
            for kind, value in stream_answer(run_id, body.question.strip(), body.focus, body.history):
                yield f"event: {kind}\ndata: {json.dumps(value)}\n\n"
        except Exception as exc:  # reported to the client as a plain message
            yield f"event: error\ndata: {json.dumps({'message': f'The assistant failed: {exc}'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------------------------- graph

@app.get("/api/runs/{run_id}/graph")
def get_graph(run_id: str, view: str = "structure"):
    _meta(run_id)
    from app import graph_view

    if view == "meaning":
        return graph_view.meaning(run_id)
    return graph_view.structure(run_id)


class GraphChatBody(BaseModel):
    question: str
    selection: str | None = None


@app.post("/api/runs/{run_id}/graph/chat")
def graph_chat(run_id: str, body: GraphChatBody):
    _meta(run_id)
    from app.graph_chat import answer

    try:
        return answer(run_id, body.question, body.selection)
    except RuntimeError as exc:
        raise error(502, "model_failed", str(exc)) from None


# ---------------------------------------------------------------------------------------------- voice

def _keyterms(run_id: str) -> list[str]:
    terms: list[str] = []
    profile = runs.read_json(run_id, "01_profile.json") or {}
    terms += [f["name"].split("(")[0].strip() for f in profile.get("functions", [])]
    terms += [p["name"] for p in profile.get("parts", [])]
    reqs = runs.requirements(run_id)
    terms += list(dict.fromkeys(r["source"]["regulation"] for r in reqs))
    terms += ["retro-reflector", "type approval", "Annex", "ESA", "photometric", "luminous intensity"]
    return [t for t in dict.fromkeys(terms) if 1 < len(t) <= 50][:50]


@app.post("/api/voice/stt")
async def stt(audio: UploadFile = File(...), run_id: str = Form("")):
    if not voice.enabled():
        raise error(503, "voice_off", "Voice is not configured on this server; typing still works.")
    raw = await audio.read()
    if not raw or len(raw) > MAX_UPLOAD:
        raise error(400, "bad_audio", "The recording is empty or too long.")
    terms = _keyterms(run_id) if run_id else []
    try:
        text = await asyncio.to_thread(voice.transcribe, raw, audio.filename or "question.webm",
                                       audio.content_type or "audio/webm", terms)
    except Exception as exc:
        raise error(503, "voice_failed", f"Voice is unavailable right now ({type(exc).__name__}); typing still works.") from None
    return {"text": text}


class SpeakBody(BaseModel):
    text: str


@app.post("/api/voice/tts")
def tts(body: SpeakBody):
    if not voice.enabled():
        raise error(503, "voice_off", "Voice is not configured on this server.")
    try:
        chunks = voice.speak(body.text)
        first = next(chunks)
    except Exception as exc:
        raise error(503, "voice_failed", f"Voice is unavailable right now ({type(exc).__name__}).") from None

    def stream():
        yield first
        yield from chunks

    return StreamingResponse(stream(), media_type="audio/mpeg")


# ---------------------------------------------------------------------------------------------- client

if WEB.is_dir():
    @app.get("/{path:path}")
    def client(path: str):
        if path.startswith(("api/", "files/")):
            raise error(404, "not_found", "No such endpoint.")
        target = (WEB / path).resolve()
        if path and target.is_file() and WEB.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(WEB / "index.html")
