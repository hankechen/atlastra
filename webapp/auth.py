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
import re
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
    # User comments, keyed by a free-form `target` thread (e.g. 'player:Lionel Messi',
    # 'match:12345', 'team:Arsenal') so the same widget drops onto any page.
    c.execute("CREATE TABLE IF NOT EXISTS comments("
              "id INTEGER PRIMARY KEY, target TEXT, user_id INTEGER, username TEXT, "
              "body TEXT, created REAL)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comments_target ON comments(target, created)")
    c.execute("CREATE TABLE IF NOT EXISTS comment_likes("
              "comment_id INTEGER, user_id INTEGER, PRIMARY KEY(comment_id, user_id))")
    # Google sign-in: link a Google account to a user (no password). Added by
    # migration so existing databases pick up the columns. `pw`/`salt` stay NULL
    # for Google-only accounts (they just can't password-login).
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
    if "email" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "google_sub" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN google_sub TEXT")
    # Admin flag: gates the /admin dashboard. Defaults to 0 for everyone; promote a
    # user with tools/make_admin.py (or set_admin()).
    if "is_admin" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google "
              "ON users(google_sub) WHERE google_sub IS NOT NULL")
    # Lightweight usage log for the admin dashboard: one row per page/api request.
    # `vid` is an anonymous per-browser visitor id (cookie) so we can count uniques
    # without touching PII. Written in batches by admin.record_hit's flush thread.
    c.execute("CREATE TABLE IF NOT EXISTS analytics_hits("
              "ts REAL, path TEXT, kind TEXT, vid TEXT)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_hits_ts ON analytics_hits(ts)")
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
            row = c.execute(
                "SELECT id, pw, salt, username, COALESCE(is_admin,0) FROM users WHERE username = ?",
                [(username or "").strip()]).fetchone()
            # Google-created accounts have NULL pw/salt -> no password login (don't hash None).
            if (not row or row[1] is None or row[2] is None
                    or not secrets.compare_digest(_hash(password, row[2]), row[1])):
                return None, "Wrong username or password."
            tok = _new_session(c, row[0])
            c.commit()
            return {"id": row[0], "username": row[3], "is_admin": bool(row[4])}, tok
        finally:
            c.close()


def _unique_username(c: sqlite3.Connection, base: str) -> str:
    """A free username derived from a Google display name / email local-part."""
    base = re.sub(r"[^A-Za-z0-9_.]", "", base or "")[:20] or "user"
    if len(base) < 3:
        base = (base + "user")[:20]
    name, i = base, 0
    while c.execute("SELECT 1 FROM users WHERE username = ?", [name]).fetchone():
        i += 1
        name = f"{base[:20 - len(str(i))]}{i}"
    return name


def google_login(sub: str, email: str | None, name: str | None):
    """Sign in (or auto-create) via a verified Google account. The caller MUST have
    already verified the Google ID token. -> ({"id","username"}, token) or (None, err)."""
    if not sub:
        return None, "Invalid Google sign-in."
    with _LOCK:
        c = _con()
        try:
            row = c.execute("SELECT id, username FROM users WHERE google_sub = ?", [sub]).fetchone()
            if not row and email:                       # link to an existing local account by email
                row = c.execute("SELECT id, username FROM users WHERE email = ? AND google_sub IS NULL",
                                [email]).fetchone()
                if row:
                    c.execute("UPDATE users SET google_sub = ? WHERE id = ?", [sub, row[0]])
            if not row:                                  # first sign-in -> create an account
                uname = _unique_username(c, name or (email or "").split("@")[0])
                uid = c.execute(
                    "INSERT INTO users(username, email, google_sub, created) VALUES(?,?,?,?)",
                    [uname, email, sub, time.time()]).lastrowid
                row = (uid, uname)
            tok = _new_session(c, row[0])
            c.commit()
            return {"id": row[0], "username": row[1]}, tok
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
            "SELECT u.id, u.username, COALESCE(u.is_admin, 0) FROM sessions s "
            "JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires > ?", [token, time.time()]).fetchone()
        return {"id": row[0], "username": row[1], "is_admin": bool(row[2])} if row else None
    finally:
        c.close()


def set_admin(username: str, is_admin: bool = True) -> bool:
    """Promote/demote a user by username. Returns False if no such user."""
    with _LOCK:
        c = _con()
        try:
            cur = c.execute("UPDATE users SET is_admin = ? WHERE username = ?",
                            [1 if is_admin else 0, (username or "").strip()])
            c.commit()
            return cur.rowcount > 0
        finally:
            c.close()


def ensure_admin(username: str, password: str) -> dict:
    """Bootstrap an admin: create the account if missing (or reset its password),
    then flag it admin. Used by tools/make_admin.py. Returns the user dict."""
    username = (username or "").strip()
    with _LOCK:
        c = _con()
        try:
            salt = secrets.token_hex(16)
            row = c.execute("SELECT id FROM users WHERE username = ?", [username]).fetchone()
            if row:
                c.execute("UPDATE users SET pw = ?, salt = ?, is_admin = 1 WHERE id = ?",
                          [_hash(password, salt), salt, row[0]])
                uid = row[0]
            else:
                uid = c.execute(
                    "INSERT INTO users(username, pw, salt, created, is_admin) VALUES(?,?,?,?,1)",
                    [username, _hash(password, salt), salt, time.time()]).lastrowid
            c.commit()
            return {"id": uid, "username": username, "is_admin": True}
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


# ---- user comments ----
COMMENT_MAX = 1500          # body length cap
COMMENT_MIN_GAP = 5.0       # seconds between a user's posts (anti-spam)


def add_comment(target: str, uid: int, username: str, body: str):
    """Post a comment to a thread. -> (comment_dict, None) or (None, error)."""
    target = (target or "").strip()[:120]
    body = (body or "").strip()
    if not target:
        return None, "Missing target."
    if not body:
        return None, "Comment can't be empty."
    if len(body) > COMMENT_MAX:
        return None, f"Comment is too long (max {COMMENT_MAX} characters)."
    with _LOCK:
        c = _con()
        try:
            last = c.execute("SELECT created, body FROM comments WHERE user_id=? "
                             "ORDER BY created DESC LIMIT 1", [uid]).fetchone()
            now = time.time()
            if last and now - last[0] < COMMENT_MIN_GAP:
                return None, "You're posting too fast — give it a few seconds."
            if last and last[1] == body:
                return None, "Looks like a duplicate of your last comment."
            cid = c.execute(
                "INSERT INTO comments(target, user_id, username, body, created) VALUES(?,?,?,?,?)",
                [target, uid, username, body, now]).lastrowid
            c.commit()
        finally:
            c.close()
    return {"id": cid, "username": username, "body": body, "created": now,
            "likes": 0, "liked": False, "mine": True}, None


def list_comments(target: str, viewer_uid: int | None = None,
                  sort: str = "new", limit: int = 200) -> dict:
    """Comments for a thread + total count. Each carries like count and, for a
    signed-in viewer, whether they liked it / authored it."""
    order = "c.created ASC" if sort == "old" else (
        "likes DESC, c.created DESC" if sort == "top" else "c.created DESC")
    c = _con()
    try:
        rows = c.execute(f"""
            SELECT c.id, c.username, c.body, c.created, c.user_id,
                   (SELECT count(*) FROM comment_likes l WHERE l.comment_id = c.id) AS likes,
                   EXISTS(SELECT 1 FROM comment_likes l
                          WHERE l.comment_id = c.id AND l.user_id = ?) AS liked
            FROM comments c WHERE c.target = ?
            ORDER BY {order} LIMIT ?
        """, [viewer_uid or -1, target, limit]).fetchall()
        total = c.execute("SELECT count(*) FROM comments WHERE target=?", [target]).fetchone()[0]
        items = [{"id": r[0], "username": r[1], "body": r[2], "created": r[3],
                  "likes": r[5], "liked": bool(r[6]), "mine": r[4] == viewer_uid}
                 for r in rows]
        return {"total": total, "comments": items}
    finally:
        c.close()


def delete_comment(cid: int, uid: int) -> bool:
    """Delete a comment (author only). -> True if a row was removed."""
    with _LOCK:
        c = _con()
        try:
            row = c.execute("SELECT user_id FROM comments WHERE id=?", [cid]).fetchone()
            if not row or row[0] != uid:
                return False
            c.execute("DELETE FROM comments WHERE id=?", [cid])
            c.execute("DELETE FROM comment_likes WHERE comment_id=?", [cid])
            c.commit()
            return True
        finally:
            c.close()


def toggle_like(cid: int, uid: int):
    """Like/unlike a comment. -> ({"liked","likes"}, None) or (None, error)."""
    with _LOCK:
        c = _con()
        try:
            if not c.execute("SELECT 1 FROM comments WHERE id=?", [cid]).fetchone():
                return None, "That comment no longer exists."
            has = c.execute("SELECT 1 FROM comment_likes WHERE comment_id=? AND user_id=?",
                            [cid, uid]).fetchone()
            if has:
                c.execute("DELETE FROM comment_likes WHERE comment_id=? AND user_id=?", [cid, uid])
            else:
                c.execute("INSERT OR IGNORE INTO comment_likes(comment_id, user_id) VALUES(?,?)",
                          [cid, uid])
            n = c.execute("SELECT count(*) FROM comment_likes WHERE comment_id=?",
                          [cid]).fetchone()[0]
            c.commit()
            return {"liked": not has, "likes": n}, None
        finally:
            c.close()
