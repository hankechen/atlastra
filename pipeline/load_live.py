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
import os
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


# Optional outbound proxy for SofaScore (datacenter IPs are 403-blocked, so a
# residential proxy is needed when running from a cloud host). Set SOFASCORE_PROXY
# to e.g. http://user:pass@host:port.
_PROXY = os.environ.get("SOFASCORE_PROXY") or None


def _get(path: str, retries: int = 1) -> dict:
    """Bare GET -- NO extra headers (CORS headers trip SofaScore's bot challenge;
    the default browser-TLS fingerprint alone is accepted). One light retry for a
    transient blip, but NEVER retry a 403 -- a hard block won't clear and just
    burns through the (single, rate-limited) proxy IP."""
    last = None
    for i in range(retries + 1):
        try:
            r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=30, proxy=_PROXY)
            r.raise_for_status()
            return r.json()
        except Exception as e:                        # noqa: BLE001
            last = e
            if "403" in str(e) or i >= retries:
                break
            time.sleep(0.8)
    raise last


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


# current-season id per uniqueTournament. Changes once a season, so cache it for
# the process (a server restart picks up a new season); avoids a /seasons call per
# full refresh.
_SEASON_CACHE: dict[int, int] = {}


def _season_id(tid: int) -> int | None:
    """Current (latest) SofaScore season id for a uniqueTournament, or None."""
    if tid in _SEASON_CACHE:
        return _SEASON_CACHE[tid]
    try:
        seasons = _get(f"/unique-tournament/{tid}/seasons").get("seasons") or []
    except Exception as e:                            # noqa: BLE001
        print(f"  ! seasons {tid}: {e}")
        time.sleep(RATE_LIMIT_SEC)
        return None
    time.sleep(RATE_LIMIT_SEC)
    sid = seasons[0].get("id") if seasons else None
    if sid:
        _SEASON_CACHE[tid] = sid
    return sid


def fetch_rows() -> list[dict]:
    """Build the live_matches snapshot. SofaScore's date-based scheduled-events
    endpoint is unreliable (returns 404), so pull each covered tournament's
    current-season `next` (upcoming) + `last` (recent results) and keep the events
    inside the [today-LIVE_DAYS_BACK, today+LIVE_DAYS_AHEAD] window, then overlay the
    global live feed for the freshest in-play scores."""
    now = int(time.time())
    updated_at = datetime.now()
    rows: dict[int, dict] = {}        # event_id -> row (last write wins)

    today = date.today()
    lo, hi = today - timedelta(days=LIVE_DAYS_BACK), today + timedelta(days=LIVE_DAYS_AHEAD)

    def in_window(ev) -> bool:
        ts = ev.get("startTimestamp")
        return bool(ts) and lo <= date.fromtimestamp(ts) <= hi

    for key, (tid, name, grp) in SOFASCORE_LIVE_TOURNAMENTS.items():
        sid = _season_id(tid)
        if not sid:
            continue
        kept = 0
        for kind in ("next", "last"):     # upcoming fixtures + recent results
            try:
                data = _get(f"/unique-tournament/{tid}/season/{sid}/events/{kind}/0")
            except Exception as e:                    # noqa: BLE001
                print(f"  ! {name} {kind}: {e}")
                time.sleep(RATE_LIMIT_SEC)
                continue
            for ev in data.get("events", []):
                if not in_window(ev):
                    continue
                r = _row(ev, now, updated_at)
                if r:
                    rows[r["event_id"]] = r
                    kept += 1
            time.sleep(RATE_LIMIT_SEC)
        print(f"  {name}: {kept} match(es) in window")

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
    """Refresh live_matches and return the in-play count (so a refresher can pace
    itself -- poll fast while games are live, slow when idle).

    Non-destructive: UPSERT the scraped rows and only DELETE rows that have aged out
    of the [today-BACK, today+AHEAD] window. A degraded scrape (e.g. an upstream
    endpoint 404s and we get only the live overlay) therefore never blanks the feed
    -- it just doesn't add new rows that cycle, while good in-window rows persist."""
    rows = fetch_rows()
    con = connect_retry(DB_PATH, read_only=False)
    con.execute(DDL)
    if rows:
        df = pd.DataFrame(rows)[COLS]                 # exact column order
        con.register("live_df", df)
        assigns = ", ".join(f"{c}=excluded.{c}" for c in COLS if c != "event_id")
        con.execute(f"INSERT INTO live_matches ({','.join(COLS)}) "
                    f"SELECT {','.join(COLS)} FROM live_df "
                    f"ON CONFLICT (event_id) DO UPDATE SET {assigns}")
        con.unregister("live_df")
    # prune only by time window -- drops old results / far-future fixtures and keeps
    # the table bounded, without ever deleting a row just because one scrape missed it
    now = int(time.time())
    lo = now - (LIVE_DAYS_BACK + 1) * 86400
    hi = now + (LIVE_DAYS_AHEAD + 1) * 86400
    con.execute("DELETE FROM live_matches "
                "WHERE start_timestamp IS NOT NULL "
                "AND (start_timestamp < ? OR start_timestamp > ?)", [lo, hi])
    n_live, n_fin, n_up = con.execute(
        "SELECT count(*) FILTER (WHERE status_type='inprogress'), "
        "       count(*) FILTER (WHERE status_type='finished'), "
        "       count(*) FILTER (WHERE status_type='notstarted') FROM live_matches"
    ).fetchone()
    total = con.execute("SELECT count(*) FROM live_matches").fetchone()[0]
    con.close()
    print(f"live_matches: {total} rows "
          f"({n_live} live, {n_fin} results, {n_up} upcoming) "
          f"[+{len(rows)} scraped].")
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


def ingest_rows(rows: list[dict], prune: bool = False) -> int:
    """Upsert live_matches rows that were scraped ELSEWHERE and pushed in (the cloud
    server can't reach SofaScore, so a non-blocked machine scrapes and POSTs the
    rows here). Server stamps updated_at so the feed's freshness reflects ingest
    time. With prune=True also drops rows outside the live window (use on the full
    push, not the in-play overlay push). Returns the in-play count."""
    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute(DDL)
        if rows:
            ua = datetime.now()
            clean = []
            for r in rows:
                r = {k: r.get(k) for k in COLS}
                r["updated_at"] = ua
                clean.append(r)
            df = pd.DataFrame(clean)[COLS]
            con.register("live_df", df)
            assigns = ", ".join(f"{c}=excluded.{c}" for c in COLS if c != "event_id")
            con.execute(f"INSERT INTO live_matches ({','.join(COLS)}) "
                        f"SELECT {','.join(COLS)} FROM live_df "
                        f"ON CONFLICT (event_id) DO UPDATE SET {assigns}")
            con.unregister("live_df")
        if prune:
            now = int(time.time())
            lo = now - (LIVE_DAYS_BACK + 1) * 86400
            hi = now + (LIVE_DAYS_AHEAD + 1) * 86400
            con.execute("DELETE FROM live_matches WHERE start_timestamp IS NOT NULL "
                        "AND (start_timestamp < ? OR start_timestamp > ?)", [lo, hi])
        n_live = con.execute("SELECT count(*) FILTER (WHERE status_type='inprogress') "
                             "FROM live_matches").fetchone()[0]
    finally:
        con.close()
    return n_live


if __name__ == "__main__":
    load_live()
