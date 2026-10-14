"""Reliability signals for the admin dashboard's "System Health" card.

Separate from webapp.admin, which stays DuckDB-free by design (see its own
docstring) -- these checks need the warehouse itself. Both are cheap, bounded
reads (a MAX() on an indexed-by-nature small table, a local file read), not a
scan, so calling this on every dashboard poll (30s) is fine.

Built after the FotMob signing break (2026-10-13, see memory
fotmob-signing-break-oct13): live_matches went to zero rows and stayed there
for days with nothing in the UI showing anything was wrong -- the feed just
looked like "nothing live right now" instead of "broken". This exists so
that state is visible to a human glancing at /admin instead of requiring
someone to notice the site itself feels off and ask about it.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from analytics.queries import SoccerDB

# How stale a signal can get before it's flagged red. The live refresher polls
# every <=300s even when nothing's live (ATLASTRA_IDLE_POLL) and every 45s
# when something is -- so anything past this is the refresher itself stuck or
# erroring, not just "no new data yet". Generous on purpose to avoid false
# alarms from a brief restart window.
_LIVE_STALE_AFTER = 20 * 60

_DEPLOY_SHA_PATH = Path(__file__).resolve().parent.parent / ".deployed_sha"


def _live_feed_status(read_only: bool) -> dict:
    # Must match whatever mode server.py's own pooled connection already uses
    # (its DB_READ_ONLY) -- DuckDB refuses to open a second connection to the
    # same file in a different mode, and analytics.queries._pool_acquire falls
    # back to an UNPOOLED connect_retry() on a mode mismatch, which hits that
    # refusal directly: "Can't open a connection to same database file with a
    # different configuration than existing connections." Hardcoding True here
    # broke on prod (FOTMOB=1 -> DB_READ_ONLY=False), so the caller passes it in.
    try:
        with SoccerDB(read_only=read_only) as d:
            row = d.con.execute("SELECT max(updated_at) FROM live_matches").fetchone()
        updated_at = row[0] if row else None
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:150]}"}
    if updated_at is None:
        return {"ok": False, "detail": "live_matches has no rows at all"}
    # updated_at is written with datetime.utcnow() (pipeline/load_live_fotmob.py)
    # -- a NAIVE datetime that already represents UTC. .timestamp() on a naive
    # datetime assumes the system's LOCAL timezone instead, which would silently
    # skew "age" by the server's UTC offset on any box not itself set to UTC.
    # Subtracting two naive-but-both-UTC datetimes sidesteps that entirely.
    age = (datetime.utcnow() - updated_at).total_seconds()
    epoch = updated_at.replace(tzinfo=timezone.utc).timestamp()
    return {
        "ok": age <= _LIVE_STALE_AFTER,
        "updated_at": epoch,
        "age_seconds": round(age),
        "detail": None if age <= _LIVE_STALE_AFTER
                  else f"last update {round(age / 60)} min ago -- refresher likely stuck or erroring",
    }


def _deploy_status() -> dict:
    if not _DEPLOY_SHA_PATH.exists():
        return {"available": False, "detail": "no .deployed_sha -- predates deploy.sh"}
    try:
        sha, deployed_at = _DEPLOY_SHA_PATH.read_text().strip().split("\n")
    except ValueError:
        return {"available": False, "detail": ".deployed_sha is malformed"}
    return {"available": True, "sha": sha[:10], "deployed_at": deployed_at}


def overview(db_read_only: bool) -> dict:
    """Everything the System Health card needs, in one call. `db_read_only`
    must be the caller's own DB_READ_ONLY -- see _live_feed_status."""
    return {
        "checked_at": time.time(),
        "live_feed": _live_feed_status(db_read_only),
        "deploy": _deploy_status(),
    }
