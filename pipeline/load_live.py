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

# How long after kickoff a match can still be in play -- 90' + halftime + stoppage,
# plus room for extra time and a penalty shootout, plus a safety buffer. A match
# whose kickoff is within this window but that isn't `finished` yet should be live
# right now, so we direct-poll its /event/{id} (see _should_be_live / fetch_events).
LIVE_MATCH_MAX_MIN = int(os.environ.get("LIVE_MATCH_MAX_MIN", "210"))   # 3.5h

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
    home_pens       INTEGER, away_pens INTEGER,
    winner_code     INTEGER, updated_at TIMESTAMP
);
"""

COLS = ["event_id", "tournament_key", "tournament_name", "tournament_group",
        "round_name", "start_timestamp", "status_type", "status_desc", "minute",
        "home_team", "home_team_id", "home_country",
        "away_team", "away_team_id", "away_country",
        "home_score", "away_score", "home_pens", "away_pens", "winner_code", "updated_at"]


def _setup(con) -> None:
    """Create the table and backfill the penalty columns on a pre-existing one."""
    con.execute(DDL)
    con.execute("ALTER TABLE live_matches ADD COLUMN IF NOT EXISTS home_pens INTEGER")
    con.execute("ALTER TABLE live_matches ADD COLUMN IF NOT EXISTS away_pens INTEGER")


# Optional outbound proxy for SofaScore (datacenter IPs are 403-blocked, so a
# residential proxy is needed when running from a cloud host). Set SOFASCORE_PROXY
# to e.g. http://user:pass@host:port.
_PROXY = os.environ.get("SOFASCORE_PROXY") or None

# A machine resuming from sleep (or a brief Wi-Fi drop) can't resolve
# api.sofascore.com for several seconds -- the fetch fails with a DNS/connection
# error, NOT an HTTP status. That's a transient blip, not a block, so ride it out
# with a bounded backoff instead of failing the whole cycle and leaving the site
# with stale / "match not found" data until the next poll. Set to 0 to disable.
NET_RETRY_MAX_SEC = float(os.environ.get("SOFA_NET_RETRY_SEC", "45"))

# Substrings that only appear in connectivity/DNS failures (no HTTP response) -- an
# HTTP status error (403/404/5xx) reads like "404 Client Error: Not Found ..." and
# matches none of these, so it is never mistaken for a network blip.
_NET_ERR_MARKERS = ("dial tcp", "failed to do request", "no such host",
                    "temporary failure in name resolution", "connection refused",
                    "connection reset", "network is unreachable", "timed out",
                    "timeout")


def _is_network_error(e: Exception) -> bool:
    """True for a transient connectivity/DNS failure worth waiting out, as opposed
    to an HTTP status error (403/404/5xx) that won't clear on retry."""
    s = str(e).lower()
    return any(m in s for m in _NET_ERR_MARKERS)


def _get(path: str, retries: int = 1) -> dict:
    """Bare GET -- NO extra headers (CORS headers trip SofaScore's bot challenge;
    the default browser-TLS fingerprint alone is accepted). One light retry for a
    transient blip, but NEVER retry a 403 -- a hard block won't clear and just
    burns through the (single, rate-limited) proxy IP. A DNS/connection failure
    (e.g. just resumed from sleep) is retried with backoff up to NET_RETRY_MAX_SEC,
    so a few seconds of no network doesn't fail the cycle."""
    last = None
    i = 0
    backoff = 0.8
    net_deadline = None                               # set on the first network blip
    while True:
        try:
            r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=30, proxy=_PROXY)
            r.raise_for_status()
            return r.json()
        except Exception as e:                        # noqa: BLE001
            last = e
            if "403" in str(e):
                break
            if _is_network_error(e) and NET_RETRY_MAX_SEC > 0:
                now = time.monotonic()
                if net_deadline is None:
                    net_deadline = now + NET_RETRY_MAX_SEC
                if now < net_deadline:
                    time.sleep(min(backoff, 5.0))
                    backoff *= 1.7
                    continue
                break
            if i >= retries:
                break
            i += 1
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
        # `display` is the regulation/ET goals score; `current` folds the penalty
        # shootout in (a 1-1 game decided on pens reads as 4-5). Show goals + pens apart.
        "home_score": hs.get("display") if hs.get("display") is not None else hs.get("current"),
        "away_score": as_.get("display") if as_.get("display") is not None else as_.get("current"),
        "home_pens": hs.get("penalties"), "away_pens": as_.get("penalties"),
        "winner_code": ev.get("winnerCode"), "updated_at": updated_at,
    }


def _should_be_live(row: dict, now: int) -> bool:
    """True for a row that ought to be in play right now: its kickoff has passed but
    it isn't `finished`. Such a match sits in a blind spot -- it has left the `next`
    (upcoming) scrape list, hasn't reached `last` (finished), and SofaScore's global
    live feed intermittently omits it -- so its own /event/{id} is the reliable
    source. Bounded by LIVE_MATCH_MAX_MIN so we don't poll long-over games forever."""
    ts = row.get("start_timestamp")
    if not ts or row.get("status_type") == "finished":
        return False
    return ts <= now <= ts + LIVE_MATCH_MAX_MIN * 60


# Event ids that should be in play now, learned from the last full scrape. The
# global live feed is flaky, so between full scrapes the fast in-play path ALSO
# direct-polls these. Populated by fetch_rows(); an id drops once it reads finished.
_LIVE_CANDIDATES: set[int] = set()


def fetch_events(event_ids, now: int | None = None,
                 updated_at: datetime | None = None) -> list[dict]:
    """Direct-poll /event/{id} for each id -> fresh rows (covered tournaments only).
    Unlike the global live feed, an event's own endpoint always carries its current
    status/score/clock, so this reliably refreshes a match the feed dropped. Rows for
    tournaments we don't cover (or failed fetches) are simply skipped."""
    now = now if now is not None else int(time.time())
    updated_at = updated_at if updated_at is not None else datetime.now()
    rows = []
    for eid in event_ids:
        try:
            ev = (_get(f"/event/{eid}") or {}).get("event")
        except Exception as e:                            # noqa: BLE001
            print(f"  ! event {eid}: {e}")
            continue
        r = _row(ev, now, updated_at) if ev else None
        if r:
            rows.append(r)
    return rows


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

    # A match that has kicked off but isn't finished is in neither scrape list, and
    # the live feed may have missed it -- direct-poll every such dead-zone match so
    # its score/clock is fresh. Also remember the live set so the fast in-play path
    # (fetch_live_only) can keep polling them between these full scrapes.
    global _LIVE_CANDIDATES
    now = int(time.time())
    # rebind (not clear()+update()) so a concurrent reader in another thread -- the
    # pusher polls this from both its main loop and its warm thread -- always sees a
    # complete set, never one mid-rebuild.
    _LIVE_CANDIDATES = {r["event_id"] for r in rows.values() if _should_be_live(r, now)}
    missed = [eid for eid in _LIVE_CANDIDATES
              if rows[eid].get("status_type") != "inprogress"]
    if missed:
        for r in fetch_events(missed, now, updated_at):
            rows[r["event_id"]] = r
        print(f"  direct-polled {len(missed)} dead-zone match(es)")

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
    _setup(con)
    if rows:
        df = pd.DataFrame(rows).reindex(columns=COLS)   # exact column order; missing -> NULL
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


def fetch_live_only(extra_ids=None) -> list[dict]:
    """Fast in-play refresh, skipping the slow day-by-day window scrape. Combines the
    global live feed with a direct poll of the known live candidates (from the last
    full scrape's _LIVE_CANDIDATES, plus any `extra_ids`), so a match the flaky feed
    omits still gets a fresh score/clock. A candidate that now reads finished is
    returned (catching the full-time transition) and dropped from the candidate set."""
    now = int(time.time())
    updated_at = datetime.now()
    rows: dict[int, dict] = {}
    try:
        live = _get("/sport/football/events/live")
        for ev in live.get("events", []):
            r = _row(ev, now, updated_at)
            if r:
                rows[r["event_id"]] = r
                if r.get("status_type") == "inprogress":
                    _LIVE_CANDIDATES.add(r["event_id"])   # lock on -- keep polling even if the feed later drops it
    except Exception as e:                                # noqa: BLE001
        print(f"  ! live feed: {e}")

    ids = set(_LIVE_CANDIDATES)
    if extra_ids:
        ids.update(extra_ids)
    for r in fetch_events(ids, now, updated_at):          # direct poll -- freshest per match
        rows[r["event_id"]] = r
        if r.get("status_type") == "finished":
            _LIVE_CANDIDATES.discard(r["event_id"])
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
        _setup(con)
        if rows:
            df = pd.DataFrame(rows).reindex(columns=COLS)
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
        _setup(con)
        if rows:
            ua = datetime.now()
            clean = []
            for r in rows:
                r = {k: r.get(k) for k in COLS}
                r["updated_at"] = ua
                clean.append(r)
            df = pd.DataFrame(clean).reindex(columns=COLS)
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
