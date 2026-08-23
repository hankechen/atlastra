"""
Live top-5 league standings + stat leaders from FotMob -> tables
`team_standings_fotmob` / `player_stat_leaders_fotmob`.

team_season_stats / v_player_season_stats (analytics/queries.py) only reflect
whatever season was last scraped from Understat by hand, so a real matchday
can go unrecorded for weeks. FotMob's league endpoint gives the full current
table AND a "Top Stats" leaderboard (top scorer/assists/xG/etc, already
ranked) in the SAME call per league, and works from the server with no proxy
(see pipeline/load_live_fotmob.py for the same trick already used for the live
scores feed) -- so this refreshes both independently of the Understat
pipeline, with no extra API calls for the leaders. analytics/queries.py
overlays both onto the current-season view (league_standings/web_spotlight).

    python -m pipeline.load_standings_fotmob        # one refresh
"""
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, FOTMOB_LEAGUE_IDS
from analytics.queries import connect_retry
from pipeline.fotmob_auth import FotmobAuth

_auth = FotmobAuth()

COLS = ["league_key", "fotmob_team_id", "played", "wins", "draws", "losses",
        "goals_for", "goals_against", "goal_difference", "points", "position",
        "updated_at"]

# FotMob "Top Stats" widget name -> our spotlight stat key. Values marked
# per-90 are genuinely per-90 rates (FotMob doesn't expose season TOTAL
# dribbles at league level) -- an honest redefinition of "Most Dribbles",
# not a bug: see SoccerDB.web_spotlight.
LEADER_STATS = {
    "goals": "top_scorer", "goal_assist": "top_assists",
    "expected_goals": "most_xg", "total_att_assist": "most_chances",
    "won_contest": "most_dribbles",           # per-90 rate
}
LEADER_COLS = ["league_key", "stat_key", "fotmob_player_id", "player_name",
               "fotmob_team_id", "value", "updated_at"]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _fetch() -> tuple[list, list]:
    """(standings_rows, leader_rows) across the 5 top-5 leagues, one FotMob
    call per league."""
    standings, leaders, ua = [], [], datetime.utcnow()
    for league_key, league_id in FOTMOB_LEAGUE_IDS.items():
        try:
            data = _auth.get(f"/api/data/leagues?id={league_id}")
        except Exception as e:                          # noqa: BLE001
            print(f"  ! {league_key}: {type(e).__name__} {str(e)[:80]}", flush=True)
            continue
        blocks = data.get("table") or []
        if blocks:
            table = ((blocks[0].get("data") or {}).get("table") or {}).get("all") or []
            for pos, t in enumerate(table, start=1):
                fid = _int(t.get("id"))
                if fid is None:
                    continue
                gf, _, ga = str(t.get("scoresStr") or "").partition("-")
                standings.append((league_key, fid, _int(t.get("played")), _int(t.get("wins")),
                                  _int(t.get("draws")), _int(t.get("losses")), _int(gf), _int(ga),
                                  _int(t.get("goalConDiff")), _int(t.get("pts")),
                                  _int(t.get("idx")) or pos, ua))
        for p in ((data.get("stats") or {}).get("players") or []):
            key = LEADER_STATS.get(p.get("name"))
            part = p.get("participant") or {}
            if not key or part.get("id") is None or part.get("value") is None:
                continue
            # teamId (FotMob's own id) rather than teamName -- matching by name
            # is brittle ("Brighton & Hove Albion" vs the stat widget's
            # "Brighton and Hove Albion"); the crest URL just needs the id.
            leaders.append((league_key, key, int(part["id"]), part.get("name"),
                            _int(part.get("teamId")), float(part["value"]), ua))
    return standings, leaders


def refresh() -> int:
    """Rebuild team_standings_fotmob + player_stat_leaders_fotmob wholesale
    from FotMob. Returns the standings row count."""
    standings, leaders = _fetch()
    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute(f"""CREATE TABLE IF NOT EXISTS team_standings_fotmob (
            league_key VARCHAR, fotmob_team_id BIGINT, played INTEGER, wins INTEGER,
            draws INTEGER, losses INTEGER, goals_for INTEGER, goals_against INTEGER,
            goal_difference INTEGER, points INTEGER, position INTEGER,
            updated_at TIMESTAMP)""")
        con.execute("DELETE FROM team_standings_fotmob")
        if standings:
            con.executemany(
                f"INSERT INTO team_standings_fotmob ({','.join(COLS)}) "
                f"VALUES ({','.join(['?'] * len(COLS))})", standings)

        con.execute("DROP TABLE IF EXISTS player_stat_leaders_fotmob")
        con.execute("""CREATE TABLE player_stat_leaders_fotmob (
            league_key VARCHAR, stat_key VARCHAR, fotmob_player_id BIGINT,
            player_name VARCHAR, fotmob_team_id BIGINT, value DOUBLE,
            updated_at TIMESTAMP)""")
        if leaders:
            con.executemany(
                f"INSERT INTO player_stat_leaders_fotmob ({','.join(LEADER_COLS)}) "
                f"VALUES ({','.join(['?'] * len(LEADER_COLS))})", leaders)
    finally:
        con.close()
    return len(standings)


if __name__ == "__main__":
    n = refresh()
    print(f"FotMob standings refresh: {n} team-rows across {len(FOTMOB_LEAGUE_IDS)} leagues")
