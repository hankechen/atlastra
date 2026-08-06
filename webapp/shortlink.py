"""Short share links (/t/<code>).

A Tactics Lab setup encodes the whole build into the URL, which keeps links
working with no server state but makes them long enough to be mangled by chat
apps and impossible to read out. This trades the smallest amount of state for a
short link: the payload is stored once and handed back by /t/<code>.

The long ?s=... link is NOT replaced -- it stays the canonical format and keeps
working, so a shortened link is only ever an alias. If this store is lost, the
site still works; only the aliases die.

Codes are DERIVED from the payload (first N chars of its base64url digest), not
random, which buys three things: the same setup always shortens to the same code
so re-sharing doesn't grow the table, a retry after a failed write is idempotent,
and a pruned row regenerates under its old code. Collisions extend the code by a
character until it is unique for a different payload.

Backed by its own small SQLite file, like the other write stores here -- never
the DuckDB warehouse, which opens read-only per request.
"""
from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
import threading
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "atlastra_links.sqlite"
_LOCK = threading.Lock()                 # ThreadingHTTPServer -> serialize writes

# Where a code sends the browser, and under which query parameter the payload is
# replayed. Only these targets can be created: the redirect location is built
# from this table and never from anything the caller sends, so a stored payload
# can't turn /t/<code> into an open redirect.
TARGETS = {
    "tactics": ("/tactics.html", "s"),
}

CODE_LEN = 6                             # ~57 bits of digest; extends on collision
MAX_PAYLOAD = 6000                       # a full two-sided Lab build is ~1-2k
_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]+$")     # base64url, as encodeShare emits
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _con() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS links("
              "code TEXT PRIMARY KEY, target TEXT, payload TEXT, "
              "created REAL, hits INTEGER DEFAULT 0, last_hit REAL)")
    return c


def _digest_code(target: str, payload: str, n: int) -> str:
    h = hashlib.sha256(f"{target}\0{payload}".encode()).digest()
    return base64.urlsafe_b64encode(h).decode().rstrip("=")[:n]


def shorten(target: str, payload: str) -> dict:
    """Store a payload and return {code, path}. Idempotent: the same setup shared
    twice returns the same code without writing a second row."""
    if target not in TARGETS:
        return {"error": "Unknown share target."}
    payload = (payload or "").strip()
    if not payload or not _PAYLOAD_RE.match(payload):
        return {"error": "Nothing to share."}
    if len(payload) > MAX_PAYLOAD:
        return {"error": "That setup is too large to shorten."}
    with _LOCK, _con() as c:
        # Walk out one character at a time on a genuine collision (a different
        # payload already holding this code). Bounded by the digest's length.
        for n in range(CODE_LEN, 44):
            code = _digest_code(target, payload, n)
            row = c.execute("SELECT target, payload FROM links WHERE code=?", (code,)).fetchone()
            if row is None:
                c.execute("INSERT INTO links(code, target, payload, created, hits) "
                          "VALUES(?,?,?,?,0)", (code, target, payload, time.time()))
                return {"code": code, "path": f"/t/{code}"}
            if row[0] == target and row[1] == payload:
                return {"code": code, "path": f"/t/{code}"}
    return {"error": "Could not shorten that link."}


def resolve(code: str) -> tuple[str, str] | None:
    """Return (path, query) for a code, or None. Counts the hit."""
    code = (code or "").strip()
    if not _CODE_RE.match(code):
        return None
    with _LOCK, _con() as c:
        row = c.execute("SELECT target, payload FROM links WHERE code=?", (code,)).fetchone()
        if not row:
            return None
        c.execute("UPDATE links SET hits=hits+1, last_hit=? WHERE code=?", (time.time(), code))
    target, payload = row
    dest = TARGETS.get(target)
    if not dest:                          # target retired since the link was made
        return None
    path, param = dest
    return path, f"{param}={payload}"


def stats(limit: int = 20) -> dict:
    """Most-followed share links -- for the admin dashboard."""
    with _LOCK, _con() as c:
        rows = c.execute("SELECT code, target, hits, created, last_hit FROM links "
                         "ORDER BY hits DESC, created DESC LIMIT ?", (limit,)).fetchall()
        total, followed = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(hits), 0) FROM links").fetchone()
    return {"total": total, "followed": followed,
            "top": [{"code": r[0], "target": r[1], "hits": r[2],
                     "created": r[3], "last_hit": r[4]} for r in rows]}
