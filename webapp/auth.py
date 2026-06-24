"""Optional accounts for Atlastra.

Login is never required — the app works fully as a guest (everything in the
browser's localStorage). Signing in just lets a user sync their profile, follows
and saved comparisons across devices: the client pushes its data blob here and
pulls it back on the next login.

Backed by a small standalone SQLite file (NOT the DuckDB warehouse, which opens
read-only per request) so writes never contend with the analytics queries.
Passwords are salted + PBKDF2-hashed; sessions are random opaque tokens stored
server-side and handed to the browser as an HttpOnly cookie.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "atlastra_users.sqlite"
SESSION_DAYS = 30
_LOCK = threading.Lock()                 # ThreadingHTTPServer -> serialize writes


def _con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS users("
              "id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, "
              "pw TEXT, salt TEXT, created REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS sessions("
              "token TEXT PRIMARY KEY, user_id INTEGER, expires REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS user_data("
              "user_id INTEGER PRIMARY KEY, data TEXT)")
    # Global game leaderboards. One best (max) score per user per (game, period);
    # `period` is e.g. a date for daily challenges or 'alltime' for endless modes.
    c.execute("CREATE TABLE IF NOT EXISTS scores("
              "game TEXT, period TEXT, user_id INTEGER, username TEXT, "
              "score REAL, updated REAL, PRIMARY KEY(game, period, user_id))")
    return c


def _hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", (pw or "").encode(), bytes.fromhex(salt), 200_000).hex()


def _new_session(c: sqlite3.Connection, uid: int) -> str:
    tok = secrets.token_urlsafe(32)
    c.execute("DELETE FROM sessions WHERE expires < ?", [time.time()])
    c.execute("INSERT INTO sessions(token, user_id, expires) VALUES(?,?,?)",
              [tok, uid, time.time() + SESSION_DAYS * 86400])
    return tok


def signup(username: str, password: str):
    """-> ({"id","username"}, token) on success, else (None, error_message)."""
    username = (username or "").strip()
    if not (3 <= len(username) <= 24) or not username.replace("_", "").replace(".", "").isalnum():
        return None, "Username must be 3–24 letters, numbers, '_' or '.'."
    if len(password or "") < 6:
        return None, "Password must be at least 6 characters."
    with _LOCK:
        c = _con()
        try:
            if c.execute("SELECT 1 FROM users WHERE username = ?", [username]).fetchone():
                return None, "That username is taken."
            salt = secrets.token_hex(16)
            uid = c.execute("INSERT INTO users(username, pw, salt, created) VALUES(?,?,?,?)",
                            [username, _hash(password, salt), salt, time.time()]).lastrowid
            tok = _new_session(c, uid)
            c.commit()
            return {"id": uid, "username": username}, tok
        finally:
            c.close()


def login(username: str, password: str):
    with _LOCK:
        c = _con()
        try:
            row = c.execute("SELECT id, pw, salt, username FROM users WHERE username = ?",
                            [(username or "").strip()]).fetchone()
            if not row or not secrets.compare_digest(_hash(password, row[2]), row[1]):
                return None, "Wrong username or password."
            tok = _new_session(c, row[0])
            c.commit()
            return {"id": row[0], "username": row[3]}, tok
        finally:
            c.close()


def logout(token: str) -> None:
    if not token:
        return
    with _LOCK:
        c = _con()
        try:
            c.execute("DELETE FROM sessions WHERE token = ?", [token])
            c.commit()
        finally:
            c.close()


def user_for_token(token: str):
    if not token:
        return None
    c = _con()
    try:
        row = c.execute(
            "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires > ?", [token, time.time()]).fetchone()
        return {"id": row[0], "username": row[1]} if row else None
    finally:
        c.close()


def get_data(uid: int):
    c = _con()
    try:
        row = c.execute("SELECT data FROM user_data WHERE user_id = ?", [uid]).fetchone()
        return row[0] if row else None
    finally:
        c.close()


def set_data(uid: int, data: str) -> None:
    with _LOCK:
        c = _con()
        try:
            c.execute("INSERT OR REPLACE INTO user_data(user_id, data) VALUES(?,?)", [uid, data])
            c.commit()
        finally:
            c.close()


# ---- game leaderboards ----
def submit_score(game: str, period: str, uid: int, username: str, score: float) -> dict:
    """Record a score, keeping only the user's best for this (game, period).
    Returns the user's stored best + their rank (1-based) on the board."""
    score = float(score)
    with _LOCK:
        c = _con()
        try:
            row = c.execute(
                "SELECT score FROM scores WHERE game=? AND period=? AND user_id=?",
                [game, period, uid]).fetchone()
            best = max(score, row[0]) if row else score
            c.execute("INSERT OR REPLACE INTO scores(game, period, user_id, username, score, updated) "
                      "VALUES(?,?,?,?,?,?)", [game, period, uid, username, best, time.time()])
            c.commit()
        finally:
            c.close()
    return {"best": best, "improved": (not row) or score > row[0],
            "rank": _rank(game, period, best), "leaderboard": leaderboard(game, period)}


def _rank(game: str, period: str, score: float) -> int:
    c = _con()
    try:
        n = c.execute("SELECT count(*) FROM scores WHERE game=? AND period=? AND score > ?",
                      [game, period, score]).fetchone()[0]
        return int(n) + 1
    finally:
        c.close()


def leaderboard(game: str, period: str, limit: int = 25) -> list[dict]:
    c = _con()
    try:
        rows = c.execute(
            "SELECT username, score, updated FROM scores WHERE game=? AND period=? "
            "ORDER BY score DESC, updated ASC LIMIT ?", [game, period, limit]).fetchall()
        return [{"rank": i + 1, "username": r[0], "score": r[1], "updated": r[2]}
                for i, r in enumerate(rows)]
    finally:
        c.close()
