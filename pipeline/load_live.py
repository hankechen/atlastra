"""
Live matches + fixtures feed (SofaScore).

Populates the `live_matches` table with every match in the covered competitions
(top-5 leagues + UCL + World Cup / EURO / Copa America, see
config.SOFASCORE_LIVE_TOURNAMENTS) across a window of [today - LIVE_DAYS_BACK,
today + LIVE_DAYS_AHEAD]. That single window gives all three states the UI needs:

  * recent RESULTS   (status_type = 'finished')
  * LIVE in-play     (status_type = 'inprogress', with a computed clock minute)
  * UPCOMING fixtures(status_type = 'notstarted')

Strategy -- one day-by-day call serves every competition at once:
  1. GET /sport/football/scheduled-events/{date} for each date in the window,
     keep only events whose uniqueTournament id is one we cover.
  2. GET /sport/football/events/live once and overlay the in-play events (freshest
     score + the running clock), since the date feed can lag a minute or two.

Like the other SofaScore scrapers this sends NO extra headers -- a bare browser-TLS
request passes the bot challenge that the usual CORS headers would trip.

The table is rebuilt wholesale each run (event ids are stable, statuses are not),
so it is safe to run on a loop/cron for a near-live feed:

    python -m pipeline.load_live           # one refresh
    while :; do python -m pipeline.load_live; sleep 30; done   # poor-man's live
"""
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import tls_requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (DB_PATH, SOFASCORE_BASE, SOFASCORE_LIVE_TOURNAMENTS,
                    LIVE_DAYS_BACK, LIVE_DAYS_AHEAD)
from analytics.queries import connect_retry
import duckdb

RATE_LIMIT_SEC = 1.2

# SofaScore uniqueTournament id -> (our key, display name, group)
ID_TO_TOURNEY = {tid: (key, name, grp)
                 for key, (tid, name, grp) in SOFASCORE_LIVE_TOURNAMENTS.items()}

DDL = """
CREATE TABLE IF NOT EXISTS live_matches (
    event_id        BIGINT PRIMARY KEY,
    tournament_key  VARCHAR, tournament_name VARCHAR, tournament_group VARCHAR,
    round_name      VARCHAR, start_timestamp BIGINT,
    status_type     VARCHAR, status_desc VARCHAR, minute INTEGER,
    home_team       VARCHAR, home_team_id BIGINT, home_country VARCHAR,
    away_team       VARCHAR, away_team_id BIGINT, away_country VARCHAR,
    home_score      INTEGER, away_score INTEGER,
    winner_code     INTEGER, updated_at TIMESTAMP
);
"""

COLS = ["event_id", "tournament_key", "tournament_name", "tournament_group",
        "round_name", "start_timestamp", "status_type", "status_desc", "minute",
        "home_team", "home_team_id", "home_country",
        "away_team", "away_team_id", "away_country",
        "home_score", "away_score", "winner_code", "updated_at"]


def _get(path: str) -> dict:
    """Bare GET -- NO extra headers (CORS headers trip SofaScore's bot challenge;
    the default browser-TLS fingerprint alone is accepted)."""
    r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _round_name(ev: dict) -> str | None:
    ri = ev.get("roundInfo") or {}
    if ri.get("name"):
        return ri["name"]
    if ri.get("round") is not None:
        return f"Round {ri['round']}"
    return None


def _minute(ev: dict, now: int) -> int | None:
    """The live clock minute for an in-play match, else None. Derived from the
    current period's start + the period's initial offset (0 for H1, 2700 for H2),
    matching SofaScore's own clock. Breaks (halftime/penalties) have no minute."""
    st = ev.get("status") or {}
    if st.get("type") != "inprogress":
        return None
    desc = (st.get("description") or "").lower()
    if any(k in desc for k in ("halftime", "half-time", "penalties", "break", "awaiting")):
        return None
    t = ev.get("time") or {}
    cps = t.get("currentPeriodStartTimestamp")
    if not cps:
        return None
    elapsed = (t.get("initial") or 0) + max(0, now - cps)
    return int(elapsed // 60) + 1


def _row(ev: dict, now: int, updated_at: datetime) -> dict | None:
    ut = (ev.get("tournament") or {}).get("uniqueTournament") or {}
    meta = ID_TO_TOURNEY.get(ut.get("id"))
    if not meta:
        return None
    key, name, grp = meta
    st = ev.get("status") or {}
    hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    home, away = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}

    def country(team):  # ISO alpha-2 for national teams only -> drives the flag
        return ((team.get("country") or {}).get("alpha2") if team.get("national") else None)

    return {
        "event_id": ev["id"],
        "tournament_key": key, "tournament_name": name, "tournament_group": grp,
        "round_name": _round_name(ev), "start_timestamp": ev.get("startTimestamp"),
        "status_type": st.get("type"), "status_desc": st.get("description"),
        "minute": _minute(ev, now),
        "home_team": home.get("name"), "home_team_id": home.get("id"),
        "home_country": country(home),
        "away_team": away.get("name"), "away_team_id": away.get("id"),
        "away_country": country(away),
        "home_score": hs.get("current"), "away_score": as_.get("current"),
        "winner_code": ev.get("winnerCode"), "updated_at": updated_at,
    }


def fetch_rows() -> list[dict]:
    now = int(time.time())
    updated_at = datetime.now()
    rows: dict[int, dict] = {}        # event_id -> row (last write wins)

    today = date.today()
    window = [today + timedelta(days=d)
              for d in range(-LIVE_DAYS_BACK, LIVE_DAYS_AHEAD + 1)]
    for d in window:
        try:
            data = _get(f"/sport/football/scheduled-events/{d.isoformat()}")
        except Exception as e:                       # noqa: BLE001
            print(f"  ! {d}: {e}")
            time.sleep(RATE_LIMIT_SEC)
            continue
        kept = 0
        for ev in data.get("events", []):
            r = _row(ev, now, updated_at)
            if r:
                rows[r["event_id"]] = r
                kept += 1
        print(f"  {d}: {kept} covered match(es)")
        time.sleep(RATE_LIMIT_SEC)

    # overlay the global live feed -- freshest score + running clock for in-play
    try:
        live = _get("/sport/football/events/live")
        overlaid = 0
        for ev in live.get("events", []):
            r = _row(ev, now, updated_at)
            if r:
                rows[r["event_id"]] = r
                overlaid += 1
        print(f"  live overlay: {overlaid} in-play match(es)")
    except Exception as e:                            # noqa: BLE001
        print(f"  ! live feed: {e}")

    return list(rows.values())


def load_live() -> int:
    """Rebuild live_matches; returns the number of in-play matches (so a refresher
    can pace itself -- poll fast while games are live, slow when idle)."""
    rows = fetch_rows()
    con = connect_retry(DB_PATH, read_only=False)
    con.execute(DDL)
    con.execute("DELETE FROM live_matches")
    if rows:
        df = pd.DataFrame(rows)[COLS]                 # exact column order
        con.register("live_df", df)
        con.execute(f"INSERT INTO live_matches ({','.join(COLS)}) "
                    f"SELECT {','.join(COLS)} FROM live_df")
        con.unregister("live_df")
    n_live = sum(r["status_type"] == "inprogress" for r in rows)
    n_fin = sum(r["status_type"] == "finished" for r in rows)
    n_up = sum(r["status_type"] == "notstarted" for r in rows)
    con.close()
    print(f"live_matches: {len(rows)} rows "
          f"({n_live} live, {n_fin} results, {n_up} upcoming).")
    return n_live


def fetch_live_only() -> list[dict]:
    """Just the global live feed -- one fast call (covered tournaments only),
    skipping the slow day-by-day window scrape. For frequent in-play refreshes."""
    now = int(time.time())
    updated_at = datetime.now()
    rows: dict[int, dict] = {}
    live = _get("/sport/football/events/live")
    for ev in live.get("events", []):
        r = _row(ev, now, updated_at)
        if r:
            rows[r["event_id"]] = r
    return list(rows.values())


def update_live_overlay() -> int:
    """Upsert ONLY the currently in-play matches (freshest score + running clock)
    into live_matches, leaving upcoming/finished rows untouched -- the cheap path a
    live refresher runs every ~25s. Returns the in-play count. A full load_live()
    is still needed periodically to catch kickoffs and full-time transitions
    (a match that just ended drops out of the live feed)."""
    rows = fetch_live_only()
    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute(DDL)
        if rows:
            df = pd.DataFrame(rows)[COLS]
            con.register("live_df", df)
            assigns = ", ".join(f"{c}=excluded.{c}" for c in COLS if c != "event_id")
            con.execute(f"INSERT INTO live_matches ({','.join(COLS)}) "
                        f"SELECT {','.join(COLS)} FROM live_df "
                        f"ON CONFLICT (event_id) DO UPDATE SET {assigns}")
            con.unregister("live_df")
    finally:
        con.close()
    return len(rows)


if __name__ == "__main__":
    load_live()
