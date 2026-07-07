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
# raw per-player tournament stats from the last full scrape (for offline re-rating)
RAW_STATS_CACHE = str(Path(DB_PATH).parent / "wc_raw_player_stats.json")

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
# Full per-player tournament ratings (the leaders snapshot above is only the top
# 5 per stat). SofaScore's season /statistics feed returns every player's average
# match rating; filtering by position group (G/D/M/F) lets us pull the top-rated
# players per line directly -- enough to build a best XI by World Cup match rating.
PLAYERS_DDL = """
CREATE TABLE IF NOT EXISTS wc_player_stats (
    season       VARCHAR, position VARCHAR,   -- position: G / D / M / F
    player_id    BIGINT, player VARCHAR, team VARCHAR,
    rating       DOUBLE, appearances INTEGER, minutes INTEGER,  -- SofaScore avg rating
    atlas_rating INTEGER, atlas_class VARCHAR,  -- our stats-based 0-99 WC rating
    goals        INTEGER, assists INTEGER,      -- tournament totals (for the match modal)
    -- per-tournament totals for the profile's "World Cup" stat scope (Total + Per-90
    -- tiles). xg/shots absent pre-2018 (SofaScore didn't track them then) -> NULL.
    xg           DOUBLE, shots INTEGER, chances_created INTEGER,
    big_chances_created INTEGER, dribbles_completed INTEGER,
    tackles      INTEGER, interceptions INTEGER, passes_completed INTEGER,
    pass_accuracy_pct DOUBLE, duels_won_pct DOUBLE, duels_won INTEGER
);
"""
# expected column set of wc_player_stats; a prod table with an older schema is
# transparently migrated (drop + recreate, then the full push repopulates it).
_PLAYERS_COLS = ["season", "position", "player_id", "player", "team", "rating",
                 "appearances", "minutes", "atlas_rating", "atlas_class", "goals", "assists",
                 "xg", "shots", "chances_created", "big_chances_created",
                 "dribbles_completed", "tackles", "interceptions", "passes_completed",
                 "pass_accuracy_pct", "duels_won_pct", "duels_won"]
# SofaScore statistics fields we pull per player to grade the tournament rating.
PLAYER_FIELDS = ("rating,appearances,minutesPlayed,goals,assists,expectedGoals,"
                 "totalShots,keyPasses,bigChancesCreated,successfulDribbles,tackles,"
                 "interceptions,accuratePasses,accuratePassesPercentage,"
                 "totalDuelsWon,totalDuelsWonPercentage,saves,cleanSheet,"
                 "goalsConceded,goalsPrevented")
POSITIONS = ("G", "D", "M", "F")     # SofaScore position-group filter codes
PLAYER_PAGE = 100                    # page size for the per-player stats feed
PLAYER_MAX_PAGES = 5                 # up to 500 players per position -> full coverage
# Knockout bracket VISUAL order. SofaScore's `cuptrees` gives the true tree (each
# block's participants carry a `sourceBlockId` -> the feeder block's `blockId`).
# Walking it from the Final yields the top-to-bottom leaf order with the halving
# property the UI bracket needs -- which team-name inference can't recover while
# most knockout slots are still 'W##' placeholders.
WC_BRACKET_DDL = """
CREATE TABLE IF NOT EXISTS wc_bracket (
    season VARCHAR, round_order INTEGER, seq INTEGER, event_id BIGINT
);
"""


def _wc_bracket_order(sid: int, season: str) -> list:
    """(season, round_order, seq, event_id) rows in visual bracket order, via the
    SofaScore cup tree. Empty if the edition has no published tree yet."""
    d = _get(f"/unique-tournament/{WC}/season/{sid}/cuptrees") or {}
    trees = d.get("cupTrees") or []
    if not trees:
        return []
    t = trees[0]
    by_blk, round_of = {}, {}
    for r in t.get("rounds") or []:
        for b in r.get("blocks") or []:
            by_blk[b.get("blockId")] = b
            round_of[b.get("blockId")] = r.get("order")
    # root = the real Final (not the third-place block)
    final_id = (t.get("finalMatchCupBlock") or {}).get("id")
    root = next((b.get("blockId") for r in t.get("rounds") or []
                 for b in r.get("blocks") or [] if b.get("id") == final_id), None)
    if root is None:
        return []
    rows, counter = [], {}
    seen = set()

    def dfs(blk):
        b = by_blk.get(blk)
        if not b or blk in seen:
            return
        seen.add(blk)
        ro = round_of.get(blk)
        ev = (b.get("events") or [None])[0]
        seq = counter.get(ro, 0)
        counter[ro] = seq + 1
        if ev is not None:
            rows.append((season, ro, seq, ev))
        for p in sorted(b.get("participants") or [], key=lambda p: p.get("order", 0)):
            if p.get("sourceBlockId"):
                dfs(p["sourceBlockId"])

    dfs(root)
    return rows
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

    match_rows, stand_rows, leader_rows, bracket_rows = [], [], [], []
    player_stats: list[dict] = []            # rich per-player stats -> rated below
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

        prows = 0
        for pos in POSITIONS:               # ALL players per position (for the WC rating)
            for page in range(PLAYER_MAX_PAGES):
                res = (_get(f"/unique-tournament/{WC}/season/{sid}/statistics"
                            f"?accumulation=total&group=ALL&order=-rating"
                            f"&fields={PLAYER_FIELDS}"
                            f"&limit={PLAYER_PAGE}&offset={page * PLAYER_PAGE}"
                            f"&filters=position.in.{pos}") or {}).get("results") or []
                if not res:
                    break
                for e in res:
                    if e.get("rating") is None:
                        continue
                    pl, tm = e.get("player") or {}, e.get("team") or {}
                    player_stats.append({           # canonical metric names for rate_wc
                        "season": season, "position": pos,
                        "player_id": pl.get("id"), "player": pl.get("name"),
                        "team": tm.get("name"), "rating": e.get("rating"),
                        "appearances": e.get("appearances"), "minutes": e.get("minutesPlayed"),
                        "goals": e.get("goals"), "assists": e.get("assists"),
                        "xg": e.get("expectedGoals"), "shots": e.get("totalShots"),
                        "chances_created": e.get("keyPasses"),
                        "big_chances_created": e.get("bigChancesCreated"),
                        "dribbles_completed": e.get("successfulDribbles"),
                        "tackles": e.get("tackles"), "interceptions": e.get("interceptions"),
                        "passes_completed": e.get("accuratePasses"),
                        "pass_accuracy_pct": e.get("accuratePassesPercentage"),
                        "duels_won_pct": e.get("totalDuelsWonPercentage"),
                        "duels_won": e.get("totalDuelsWon"),
                        "saves": e.get("saves"), "clean_sheets": e.get("cleanSheet"),
                        "goals_conceded": e.get("goalsConceded"),
                        "goals_prevented": e.get("goalsPrevented")})
                    prows += 1
        brows = _wc_bracket_order(sid, season)
        bracket_rows.extend(brows)
        print(f"  {season}: {len(events)} matches, {srows} standings, "
              f"{lrows} leader rows, {prows} player rows, {len(brows)} bracket rows")

    # Cache the raw per-player stats of a FULL scrape so the rating can be
    # re-tuned (weight changes) without re-hitting SofaScore -- see
    # tools/recompute_wc_ratings.py. Skipped on single-season refreshes (the pusher).
    if only_season is None and player_stats:
        import json
        with open(RAW_STATS_CACHE, "w") as fh:
            json.dump(player_stats, fh)
        print(f"  cached {len(player_stats)} raw player-stat rows -> {RAW_STATS_CACHE}")

    # Grade the tournament rating from the stats just scraped (position-relative,
    # per edition) and fold it into the pushed/stored player rows.
    from pipeline import rate_wc
    rated = rate_wc.compute(player_stats)
    player_rows = []
    for p in player_stats:
        r = rated.get((p["season"], int(p["player_id"]))) if p.get("player_id") else None
        player_rows.append((p["season"], p["position"], p["player_id"], p["player"],
                            p["team"], p["rating"], p["appearances"], p["minutes"],
                            (r or {}).get("rating"), (r or {}).get("classification"),
                            p.get("goals"), p.get("assists"),
                            p.get("xg"), p.get("shots"), p.get("chances_created"),
                            p.get("big_chances_created"), p.get("dribbles_completed"),
                            p.get("tackles"), p.get("interceptions"), p.get("passes_completed"),
                            p.get("pass_accuracy_pct"), p.get("duels_won_pct"), p.get("duels_won")))
    print(f"  rated {len(rated)}/{len(player_stats)} players (>= {rate_wc.MIN_MINUTES} min)")
    return {"matches": match_rows, "standings": stand_rows, "leaders": leader_rows,
            "players": player_rows, "bracket": bracket_rows}


def write_wc_rows(data: dict) -> dict:
    """Write pushed/scraped WC rows. Season-scoped: replaces only the seasons present
    in `data` (so pushing just 2026 leaves historical World Cups intact)."""
    matches = data.get("matches") or []
    standings = data.get("standings") or []
    leaders = data.get("leaders") or []
    players = data.get("players") or []
    bracket = data.get("bracket") or []
    seasons = ({str(r[1]) for r in matches} | {str(r[0]) for r in standings}
               | {str(r[0]) for r in leaders} | {str(r[0]) for r in players}
               | {str(r[0]) for r in bracket})
    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute(MATCH_DDL)
        con.execute(STAND_DDL)
        con.execute(LEADERS_DDL)
        con.execute(PLAYERS_DDL)
        con.execute(WC_BRACKET_DDL)
        # migrate an older wc_player_stats (pre atlas_rating) -> new schema. The full
        # all-editions push that follows repopulates it, so a drop loses nothing.
        cols = [r[1] for r in con.execute("PRAGMA table_info('wc_player_stats')").fetchall()]
        if cols != _PLAYERS_COLS:
            con.execute("DROP TABLE IF EXISTS wc_player_stats")
            con.execute(PLAYERS_DDL)
        for s in seasons:
            con.execute("DELETE FROM wc_matches WHERE season = ?", [s])
            con.execute("DELETE FROM wc_standings WHERE season = ?", [s])
            con.execute("DELETE FROM wc_leaders WHERE season = ?", [s])
            con.execute("DELETE FROM wc_player_stats WHERE season = ?", [s])
            con.execute("DELETE FROM wc_bracket WHERE season = ?", [s])
        if matches:
            con.executemany(
                "INSERT OR REPLACE INTO wc_matches VALUES (?,?,to_timestamp(?),?,?,?,?,?,?,?,?,?,?,?)",
                matches)
        if standings:
            con.executemany("INSERT INTO wc_standings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", standings)
        if leaders:
            con.executemany("INSERT INTO wc_leaders VALUES (?,?,?,?,?,?,?,?)", leaders)
        if players:
            con.executemany(
                f"INSERT INTO wc_player_stats VALUES ({','.join(['?'] * len(_PLAYERS_COLS))})",
                players)
        if bracket:
            con.executemany("INSERT INTO wc_bracket VALUES (?,?,?,?)", bracket)
    finally:
        con.close()
    return {"matches": len(matches), "standings": len(standings), "leaders": len(leaders),
            "players": len(players), "bracket": len(bracket), "seasons": sorted(seasons)}


def main() -> None:
    data = fetch_wc_rows()
    write_wc_rows(data)
    print(f"\nwc: {len(data['matches'])} matches, {len(data['standings'])} standings, "
          f"{len(data['leaders'])} leader rows")


if __name__ == "__main__":
    main()
