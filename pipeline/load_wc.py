"""Snapshot FIFA World Cup matches + group standings into wc_matches / wc_standings.

SofaScore (uniqueTournament id 16) is our only World Cup source. Unlike the rolling
`live_matches` window (which only keeps a ~10-day slice and rolls earlier rounds
off), this PERSISTS the whole tournament — finished AND upcoming matches plus the
group tables — so the World Cup hub can show the full event as it unfolds. Re-run to
refresh the current tournament (new results, updated standings, the bracket as it
fills); it rebuilds both tables wholesale each run (event ids are stable, statuses
and standings are not).

National teams carry their ISO alpha-2 country code (for flags). We keep the last
few World Cups (2010+) so the hub has a season picker like the Champions League one.

Like the other SofaScore scrapers this sends NO extra headers — the bare browser-TLS
fingerprint passes the bot challenge the usual CORS headers would trip.

Run:  python -m pipeline.load_wc
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import tls_requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, SOFASCORE_BASE  # noqa: E402
from analytics.queries import connect_retry  # noqa: E402
import duckdb  # noqa: E402

WC = 16                  # SofaScore uniqueTournament id for the FIFA World Cup
MAX_PAGES = 12           # safety cap per feed per season
MIN_YEAR = 2010          # earliest World Cup to snapshot

MATCH_DDL = """
CREATE TABLE IF NOT EXISTS wc_matches (
    event_id    BIGINT PRIMARY KEY,
    season      VARCHAR,           -- World Cup year, e.g. '2026'
    match_date  TIMESTAMP,
    round       VARCHAR,           -- SofaScore round name ('Round 2', 'Quarterfinals', …)
    home_name   VARCHAR, home_cc VARCHAR,   -- cc = ISO alpha-2 for the flag
    away_name   VARCHAR, away_cc VARCHAR,
    home_goals  INTEGER, away_goals INTEGER,   -- regulation/ET score (excludes shootout)
    home_pens   INTEGER, away_pens INTEGER,    -- penalty shootout goals, if any
    winner_code INTEGER,           -- 1 home / 2 away / 3 draw
    status      VARCHAR            -- finished / notstarted / inprogress
);
"""
STAND_DDL = """
CREATE TABLE IF NOT EXISTS wc_standings (
    season    VARCHAR, group_name VARCHAR, position INTEGER,
    team      VARCHAR, cc VARCHAR,
    played INTEGER, w INTEGER, d INTEGER, l INTEGER,
    gf INTEGER, ga INTEGER, pts INTEGER
);
"""
LEADERS_DDL = """
CREATE TABLE IF NOT EXISTS wc_leaders (
    season    VARCHAR, stat_key VARCHAR, rank INTEGER,
    player    VARCHAR, player_id BIGINT, team VARCHAR,
    value     DOUBLE, appearances INTEGER
);
"""
# SofaScore top-players categories we surface (the stat value lives under the same
# key in each entry's `statistics`). Labels/formatting are applied in queries.py.
LEADER_KEYS = ["rating", "goals", "assists", "goalsAssistsSum", "expectedGoals",
               "expectedAssists", "bigChancesCreated", "totalShots", "shotsOnTarget",
               "keyPasses", "successfulDribbles", "tackles", "interceptions",
               "clearances", "saves"]
TOP_N = 5


def _get(path: str) -> dict:
    for attempt in range(3):
        try:
            r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return {}
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5 * (attempt + 1))
    return {}


def _round(ev: dict) -> str | None:
    ri = ev.get("roundInfo") or {}
    if ri.get("name"):
        return ri["name"]
    if ri.get("round") is not None:
        return f"Round {ri['round']}"
    return None


def _cc(team: dict) -> str | None:
    return ((team.get("country") or {}).get("alpha2") if team.get("national") else None)


def fetch_wc_rows(only_season: str | None = None) -> dict:
    """Scrape the World Cup matches/standings/leaders into row lists (no DB writes),
    so a non-blocked machine can fetch and push them to a WAF-blocked server. Pass
    only_season (e.g. '2026') to refresh just the current edition -- historical World
    Cups don't change, so a periodic push only needs the live one."""
    seasons = (_get(f"/unique-tournament/{WC}/seasons") or {}).get("seasons") or []
    wanted = []
    for s in seasons:
        yr = (s.get("year") or "").strip()
        if yr.isdigit() and int(yr) >= MIN_YEAR and (only_season is None or yr == only_season):
            wanted.append((yr, s["id"]))
    wanted.sort()

    match_rows, stand_rows, leader_rows = [], [], []
    for season, sid in wanted:
        events: dict[int, dict] = {}
        for feed in ("last", "next"):        # finished + upcoming
            for page in range(MAX_PAGES):
                evs = (_get(f"/unique-tournament/{WC}/season/{sid}/events/{feed}/{page}")
                       or {}).get("events") or []
                if not evs:
                    break
                for e in evs:
                    events[e["id"]] = e
        for e in events.values():
            st = e.get("status") or {}
            hs, as_ = e.get("homeScore") or {}, e.get("awayScore") or {}
            h, a = e.get("homeTeam") or {}, e.get("awayTeam") or {}
            # `display` is the goals score (incl. ET) WITHOUT the shootout; `current`
            # folds penalties in (so a 3-3 final reads as 7-5). Prefer display.
            hg = hs.get("display") if hs.get("display") is not None else hs.get("current")
            ag = as_.get("display") if as_.get("display") is not None else as_.get("current")
            match_rows.append(
                (e["id"], season, e.get("startTimestamp"), _round(e),
                 h.get("name"), _cc(h), a.get("name"), _cc(a),
                 hg, ag, hs.get("penalties"), as_.get("penalties"),
                 e.get("winnerCode"), st.get("type")))

        std = (_get(f"/unique-tournament/{WC}/season/{sid}/standings/total")
               or {}).get("standings") or []
        srows = 0
        for grp in std:
            gname = grp.get("name") or ""
            for row in grp.get("rows") or []:
                t = row.get("team") or {}
                stand_rows.append(
                    (season, gname, row.get("position"), t.get("name"),
                     (t.get("country") or {}).get("alpha2"),
                     row.get("matches"), row.get("wins"), row.get("draws"),
                     row.get("losses"), row.get("scoresFor"), row.get("scoresAgainst"),
                     row.get("points")))
                srows += 1

        tp = (_get(f"/unique-tournament/{WC}/season/{sid}/top-players/overall")
              or {}).get("topPlayers") or {}
        lrows = 0
        for key in LEADER_KEYS:
            for rank, e in enumerate(tp.get(key) or [], 1):
                if rank > TOP_N:
                    break
                st = e.get("statistics") or {}
                pl, tm = e.get("player") or {}, e.get("team") or {}
                leader_rows.append((season, key, rank, pl.get("name"), pl.get("id"),
                                    tm.get("name"), st.get(key), st.get("appearances")))
                lrows += 1
        print(f"  {season}: {len(events)} matches, {srows} standings, {lrows} leader rows")
    return {"matches": match_rows, "standings": stand_rows, "leaders": leader_rows}


def write_wc_rows(data: dict) -> dict:
    """Write pushed/scraped WC rows. Season-scoped: replaces only the seasons present
    in `data` (so pushing just 2026 leaves historical World Cups intact)."""
    matches = data.get("matches") or []
    standings = data.get("standings") or []
    leaders = data.get("leaders") or []
    seasons = ({str(r[1]) for r in matches} | {str(r[0]) for r in standings}
               | {str(r[0]) for r in leaders})
    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute(MATCH_DDL)
        con.execute(STAND_DDL)
        con.execute(LEADERS_DDL)
        for s in seasons:
            con.execute("DELETE FROM wc_matches WHERE season = ?", [s])
            con.execute("DELETE FROM wc_standings WHERE season = ?", [s])
            con.execute("DELETE FROM wc_leaders WHERE season = ?", [s])
        if matches:
            con.executemany(
                "INSERT OR REPLACE INTO wc_matches VALUES (?,?,to_timestamp(?),?,?,?,?,?,?,?,?,?,?,?)",
                matches)
        if standings:
            con.executemany("INSERT INTO wc_standings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", standings)
        if leaders:
            con.executemany("INSERT INTO wc_leaders VALUES (?,?,?,?,?,?,?,?)", leaders)
    finally:
        con.close()
    return {"matches": len(matches), "standings": len(standings),
            "leaders": len(leaders), "seasons": sorted(seasons)}


def main() -> None:
    data = fetch_wc_rows()
    write_wc_rows(data)
    print(f"\nwc: {len(data['matches'])} matches, {len(data['standings'])} standings, "
          f"{len(data['leaders'])} leader rows")


if __name__ == "__main__":
    main()
