"""Admin analytics for Atlastra.

Two jobs:
  * record_hit() — a cheap, buffered request logger. Handlers call it on every
    page/API hit; a daemon thread flushes the buffer to SQLite in batches so we
    never do a synchronous write on the request path.
  * overview() — aggregates the usage log + accounts tables into the numbers the
    /admin dashboard renders (traffic rates, signups, top pages, content counts).

Everything lives in the same standalone SQLite file as accounts (auth.DB), so the
read-only DuckDB warehouse is never touched.
"""
from __future__ import annotations

import threading
import time

from webapp import auth

# --- buffered writer -------------------------------------------------------- #
_BUF: list[tuple] = []
_BUF_LOCK = threading.Lock()
_FLUSH_EVERY = 5.0            # seconds between batch writes
_RETAIN_DAYS = 120           # prune hits older than this
_started = False


def record_hit(path: str, kind: str, vid: str | None) -> None:
    """Queue a single request for logging (non-blocking). kind is 'page' or 'api'."""
    with _BUF_LOCK:
        _BUF.append((time.time(), path, kind, vid or ""))


def _flush() -> None:
    with _BUF_LOCK:
        if not _BUF:
            return
        batch, _BUF[:] = _BUF[:], []
    with auth._LOCK:
        c = auth._con()
        try:
            c.executemany("INSERT INTO analytics_hits(ts, path, kind, vid) VALUES(?,?,?,?)", batch)
            c.commit()
        finally:
            c.close()


def _prune() -> None:
    cutoff = time.time() - _RETAIN_DAYS * 86400
    with auth._LOCK:
        c = auth._con()
        try:
            c.execute("DELETE FROM analytics_hits WHERE ts < ?", [cutoff])
            c.commit()
        finally:
            c.close()


def start_writer() -> None:
    """Launch the background flush thread once (idempotent)."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        last_prune = 0.0
        while True:
            time.sleep(_FLUSH_EVERY)
            try:
                _flush()
                if time.time() - last_prune > 3600:      # prune hourly
                    _prune()
                    last_prune = time.time()
            except Exception as e:                        # noqa: BLE001 -- never kill the thread
                print(f"admin writer: {type(e).__name__}: {str(e)[:120]}", flush=True)

    threading.Thread(target=loop, daemon=True).start()


# --- dashboard aggregation -------------------------------------------------- #
def overview() -> dict:
    """Everything the /admin dashboard needs, in one round trip."""
    _flush()                                             # surface the newest hits immediately
    now = time.time()
    day, week, month = now - 86400, now - 7 * 86400, now - 30 * 86400
    c = auth._con()
    try:
        def scalar(sql, params=()):
            return c.execute(sql, params).fetchone()[0]

        # ---- accounts ----
        users_total = scalar("SELECT count(*) FROM users")
        users_google = scalar("SELECT count(*) FROM users WHERE google_sub IS NOT NULL")
        users_pw = scalar("SELECT count(*) FROM users WHERE pw IS NOT NULL")
        admins = scalar("SELECT count(*) FROM users WHERE COALESCE(is_admin,0)=1")
        new_1d = scalar("SELECT count(*) FROM users WHERE created >= ?", [day])
        new_7d = scalar("SELECT count(*) FROM users WHERE created >= ?", [week])
        new_30d = scalar("SELECT count(*) FROM users WHERE created >= ?", [month])
        active_sessions = scalar("SELECT count(*) FROM sessions WHERE expires > ?", [now])

        # daily signups, last 30 days (dense, zero-filled)
        rows = c.execute(
            "SELECT CAST((? - created) / 86400 AS INT) d, count(*) FROM users "
            "WHERE created >= ? GROUP BY d", [now, month]).fetchall()
        signup_by_day = {int(d): n for d, n in rows}
        signups_series = [{"days_ago": 29 - i, "count": signup_by_day.get(29 - i, 0)}
                          for i in range(30)]

        recent_users = [{"username": u, "created": cr, "google": g is not None,
                         "admin": bool(a)}
                        for u, cr, g, a in c.execute(
            "SELECT username, created, google_sub, COALESCE(is_admin,0) FROM users "
            "ORDER BY created DESC LIMIT 15").fetchall()]

        # ---- traffic ----
        hits_1d = scalar("SELECT count(*) FROM analytics_hits WHERE ts >= ?", [day])
        hits_7d = scalar("SELECT count(*) FROM analytics_hits WHERE ts >= ?", [week])
        hits_total = scalar("SELECT count(*) FROM analytics_hits")
        uniq_1d = scalar("SELECT count(DISTINCT vid) FROM analytics_hits "
                         "WHERE ts >= ? AND vid <> ''", [day])
        uniq_7d = scalar("SELECT count(DISTINCT vid) FROM analytics_hits "
                         "WHERE ts >= ? AND vid <> ''", [week])
        page_1d = scalar("SELECT count(*) FROM analytics_hits WHERE ts >= ? AND kind='page'", [day])
        api_1d = scalar("SELECT count(*) FROM analytics_hits WHERE ts >= ? AND kind='api'", [day])

        # hits per hour, last 24h (dense)
        hrows = c.execute(
            "SELECT CAST((? - ts) / 3600 AS INT) h, count(*) FROM analytics_hits "
            "WHERE ts >= ? GROUP BY h", [now, day]).fetchall()
        by_hour = {int(h): n for h, n in hrows}
        hourly_series = [{"hours_ago": 23 - i, "count": by_hour.get(23 - i, 0)}
                         for i in range(24)]

        # hits per day, last 30 days (dense)
        drows = c.execute(
            "SELECT CAST((? - ts) / 86400 AS INT) d, count(*) FROM analytics_hits "
            "WHERE ts >= ? GROUP BY d", [now, month]).fetchall()
        by_day = {int(d): n for d, n in drows}
        daily_series = [{"days_ago": 29 - i, "count": by_day.get(29 - i, 0)}
                        for i in range(30)]

        top_pages = [{"path": p, "count": n} for p, n in c.execute(
            "SELECT path, count(*) n FROM analytics_hits WHERE kind='page' AND ts >= ? "
            "GROUP BY path ORDER BY n DESC LIMIT 12", [month]).fetchall()]
        top_api = [{"path": p, "count": n} for p, n in c.execute(
            "SELECT path, count(*) n FROM analytics_hits WHERE kind='api' AND ts >= ? "
            "GROUP BY path ORDER BY n DESC LIMIT 12", [month]).fetchall()]

        # ---- engagement / content ----
        comments = scalar("SELECT count(*) FROM comments")
        comments_7d = scalar("SELECT count(*) FROM comments WHERE created >= ?", [week])
        scores_total = scalar("SELECT count(*) FROM scores")
        games = [{"game": g, "plays": n, "players": u} for g, n, u in c.execute(
            "SELECT game, count(*) n, count(DISTINCT user_id) u FROM scores "
            "GROUP BY game ORDER BY n DESC").fetchall()]

        return {
            "generated": now,
            "users": {"total": users_total, "google": users_google, "password": users_pw,
                      "admins": admins, "new_1d": new_1d, "new_7d": new_7d, "new_30d": new_30d,
                      "active_sessions": active_sessions, "signups_series": signups_series,
                      "recent": recent_users},
            "traffic": {"hits_1d": hits_1d, "hits_7d": hits_7d, "hits_total": hits_total,
                        "uniq_1d": uniq_1d, "uniq_7d": uniq_7d, "page_1d": page_1d, "api_1d": api_1d,
                        "hourly_series": hourly_series, "daily_series": daily_series,
                        "top_pages": top_pages, "top_api": top_api},
            "content": {"comments": comments, "comments_7d": comments_7d,
                        "scores": scores_total, "games": games},
        }
    finally:
        c.close()
