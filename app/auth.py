"""Accounts, sessions and each user's activity history, in a SQLite file (data/users.db, git-ignored).

Passwords are hashed with scrypt (standard library). A session is a random token in an HttpOnly cookie; only its
SHA-256 is stored, so the database alone cannot be used to sign in. Signing up needs the access code when
FLAME_PASSWORD is set (it was the shared site password before accounts existed); without it, sign-up is open.
Every /api and /files route except /api/auth/* needs a signed-in user (see the middleware in app/main.py).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

from pathlib import Path

from app.store import ROOT

DB = Path(os.environ.get("FLAME_USERS_DB") or ROOT / "data" / "users.db")  # override for tests
COOKIE = "flame_session"
SESSION_DAYS = 14
SHORT_SESSION_HOURS = 12  # "keep me signed in" unticked
MIN_PASSWORD = 8
COLORS = ["#e8590c", "#3e6ae1", "#2f8f4e", "#8a6d00", "#b3261e", "#7a4fc9", "#0f7c8c", "#5c5e62"]
EVENT_TYPES = {"signup", "login", "logout", "run_created", "ask", "export", "parts_detect", "profile_updated",
               "password_changed", "sessions_revoked"}

_lock = threading.Lock()
_attempts: dict[str, deque] = defaultdict(deque)  # failed sign-ins per ip+email, for rate limiting
MAX_ATTEMPTS, WINDOW = 8, 600


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init() -> None:
    with _lock, _db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, organisation TEXT DEFAULT '',
                role TEXT DEFAULT '', color TEXT NOT NULL, pw_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0,
                created TEXT NOT NULL, last_login TEXT);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created TEXT NOT NULL, expires REAL NOT NULL, last_seen TEXT, agent TEXT DEFAULT '', ip TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                ts TEXT NOT NULL, type TEXT NOT NULL, run_id TEXT, detail TEXT DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS events_user ON events(user_id, id DESC);
        """)


# ---------------------------------------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return secrets.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(400, "weak_password", f"Use at least {MIN_PASSWORD} characters.")
    if password.lower() == password and password.isalpha():
        raise AuthError(400, "weak_password", "Mix in a capital letter, a number or a symbol.")


# ---------------------------------------------------------------------------------------------- users

def _public(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"], "organisation": row["organisation"],
            "role": row["role"], "color": row["color"], "is_admin": bool(row["is_admin"]), "created": row["created"],
            "last_login": row["last_login"]}


def config() -> dict:
    with _db() as con:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return {"code_required": bool(os.environ.get("FLAME_PASSWORD")), "has_users": users > 0,
            "min_password": MIN_PASSWORD, "colors": COLORS}


def register(email: str, password: str, name: str, code: str) -> dict:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AuthError(400, "bad_email", "Enter a valid email address.")
    if not name.strip():
        raise AuthError(400, "bad_name", "Enter your name.")
    required = os.environ.get("FLAME_PASSWORD")
    if required and not secrets.compare_digest(code.strip(), required):
        raise AuthError(403, "bad_code", "That access code is not right. Ask your team for it.")
    _check_new_password(password)
    with _lock, _db() as con:
        first = con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            cur = con.execute(
                "INSERT INTO users (email, name, color, pw_hash, is_admin, created, last_login) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (email, name.strip()[:80], COLORS[secrets.randbelow(len(COLORS))], hash_password(password), int(first), _now(), _now()))
        except sqlite3.IntegrityError:
            raise AuthError(409, "email_taken", "An account with this email already exists. Sign in instead.") from None
        row = con.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    log(row["id"], "signup")
    return _public(row)


def login(email: str, password: str, ip: str) -> dict:
    email = email.strip().lower()
    key = f"{ip}|{email}"
    q = _attempts[key]
    while q and q[0] < time.time() - WINDOW:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        raise AuthError(429, "too_many_attempts", "Too many failed sign-ins. Wait ten minutes and try again.")
    with _db() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    # hash even when the account does not exist, so the response time does not tell which emails have accounts
    ok = check_password(password, row["pw_hash"] if row else hash_password("x" * 12))
    if not row or not ok:
        q.append(time.time())
        raise AuthError(401, "bad_credentials", "Email or password is not right.")
    q.clear()
    with _lock, _db() as con:
        con.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))
    return _public(row)


def update_profile(user_id: int, fields: dict) -> dict:
    allowed = {k: str(v).strip()[:80] for k, v in fields.items() if k in ("name", "organisation", "role", "color") and v is not None}
    if "name" in allowed and not allowed["name"]:
        raise AuthError(400, "bad_name", "Your name can't be empty.")
    if "color" in allowed and allowed["color"] not in COLORS:
        raise AuthError(400, "bad_color", "Pick one of the offered colours.")
    with _lock, _db() as con:
        for k, v in allowed.items():
            con.execute(f"UPDATE users SET {k} = ? WHERE id = ?", (v, user_id))  # k is from the allow-list above
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    log(user_id, "profile_updated", fields=sorted(allowed))
    return _public(row)


def change_password(user_id: int, current: str, new: str, keep_token: str) -> None:
    with _db() as con:
        row = con.execute("SELECT pw_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not check_password(current, row["pw_hash"]):
        raise AuthError(403, "bad_credentials", "Your current password is not right.")
    _check_new_password(new)
    with _lock, _db() as con:
        con.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new), user_id))
        con.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, _h(keep_token)))
    log(user_id, "password_changed")


def stats(user_id: int) -> dict:
    with _db() as con:
        rows = con.execute("SELECT type, COUNT(*) n FROM events WHERE user_id = ? GROUP BY type", (user_id,)).fetchall()
    c = {r["type"]: r["n"] for r in rows}
    return {"analyses": c.get("run_created", 0), "questions": c.get("ask", 0), "exports": c.get("export", 0),
            "sign_ins": c.get("login", 0) + c.get("signup", 0)}


# ---------------------------------------------------------------------------------------------- sessions

def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id: int, agent: str, ip: str, remember: bool) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    max_age = SESSION_DAYS * 86400 if remember else SHORT_SESSION_HOURS * 3600
    with _lock, _db() as con:
        con.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (_h(token), user_id, _now(), time.time() + max_age, _now(), agent[:200], ip[:64]))
    return token, max_age


def user_for(token: str | None) -> dict | None:
    if not token:
        return None
    with _db() as con:
        row = con.execute("SELECT u.*, s.last_seen FROM sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token_hash = ? AND s.expires > ?", (_h(token), time.time())).fetchone()
        if row and (row["last_seen"] or "") < _now()[:16]:  # touch at most once a minute
            con.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (_now(), _h(token)))
    return _public(row) if row else None


def end_session(token: str | None) -> None:
    if token:
        with _lock, _db() as con:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (_h(token),))


def sessions(user_id: int, current: str) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT * FROM sessions WHERE user_id = ? AND expires > ? ORDER BY last_seen DESC",
                           (user_id, time.time())).fetchall()
    return [{"id": r["token_hash"][:12], "created": r["created"], "last_seen": r["last_seen"], "agent": r["agent"],
             "ip": r["ip"], "current": r["token_hash"] == _h(current)} for r in rows]


def end_other_sessions(user_id: int, current: str) -> int:
    with _lock, _db() as con:
        n = con.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, _h(current))).rowcount
    log(user_id, "sessions_revoked", count=n)
    return n


# ---------------------------------------------------------------------------------------------- history

def log(user_id: int | None, type_: str, run_id: str | None = None, **detail) -> None:
    if user_id is None or type_ not in EVENT_TYPES:
        return
    with _lock, _db() as con:
        con.execute("INSERT INTO events (user_id, ts, type, run_id, detail) VALUES (?, ?, ?, ?, ?)",
                    (user_id, _now(), type_, run_id, json.dumps(detail, ensure_ascii=False)[:4000]))


def history(user_id: int, types: list[str] | None, before: int | None, limit: int) -> dict:
    sql, args = "SELECT * FROM events WHERE user_id = ?", [user_id]
    if types:
        sql += f" AND type IN ({','.join('?' * len(types))})"
        args += types
    if before:
        sql += " AND id < ?"
        args.append(before)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(max(limit, 1), 200) + 1)
    with _db() as con:
        rows = con.execute(sql, args).fetchall()
    more = len(rows) > min(max(limit, 1), 200)
    rows = rows[:-1] if more else rows
    return {"events": [{"id": r["id"], "ts": r["ts"], "type": r["type"], "run_id": r["run_id"],
                        "detail": json.loads(r["detail"] or "{}")} for r in rows],
            "next": rows[-1]["id"] if more and rows else None}


def clear_history(user_id: int) -> int:
    with _lock, _db() as con:
        return con.execute("DELETE FROM events WHERE user_id = ?", (user_id,)).rowcount
