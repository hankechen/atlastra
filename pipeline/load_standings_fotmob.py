"""
Live top-5 league standings from FotMob -> table `team_standings_fotmob`.

team_season_stats (analytics/queries.py) only reflects whatever season was
last scraped from Understat by hand, so a real matchday can go unrecorded for
weeks. FotMob's league endpoint gives the full current table in one call per
league and works from the server with no proxy (see pipeline/load_live_fotmob.py
for the same trick already used for the live scores feed), so this refreshes
the CURRENT table independently of the Understat pipeline. analytics/queries.py
overlays it onto team_season_stats for the current-season view.

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


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch_rows() -> list:
    """One row per team per top-5 league, from FotMob's current table."""
    rows, ua = [], datetime.utcnow()
    for league_key, league_id in FOTMOB_LEAGUE_IDS.items():
        try:
            data = _auth.get(f"/api/data/leagues?id={league_id}")
        except Exception as e:                          # noqa: BLE001
            print(f"  ! {league_key}: {type(e).__name__} {str(e)[:80]}", flush=True)
            continue
        blocks = data.get("table") or []
        if not blocks:
            continue
        table = ((blocks[0].get("data") or {}).get("table") or {}).get("all") or []
        for pos, t in enumerate(table, start=1):
            fid = _int(t.get("id"))
            if fid is None:
                continue
            gf, _, ga = str(t.get("scoresStr") or "").partition("-")
            rows.append((league_key, fid, _int(t.get("played")), _int(t.get("wins")),
                         _int(t.get("draws")), _int(t.get("losses")), _int(gf), _int(ga),
                         _int(t.get("goalConDiff")), _int(t.get("pts")),
                         _int(t.get("idx")) or pos, ua))
    return rows


def refresh() -> int:
    """Rebuild team_standings_fotmob wholesale from FotMob. Returns row count."""
    rows = fetch_rows()
    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute(f"""CREATE TABLE IF NOT EXISTS team_standings_fotmob (
            league_key VARCHAR, fotmob_team_id BIGINT, played INTEGER, wins INTEGER,
            draws INTEGER, losses INTEGER, goals_for INTEGER, goals_against INTEGER,
            goal_difference INTEGER, points INTEGER, position INTEGER,
            updated_at TIMESTAMP)""")
        con.execute("DELETE FROM team_standings_fotmob")
        if rows:
            con.executemany(
                f"INSERT INTO team_standings_fotmob ({','.join(COLS)}) "
                f"VALUES ({','.join(['?'] * len(COLS))})", rows)
    finally:
        con.close()
    return len(rows)


if __name__ == "__main__":
    n = refresh()
    print(f"FotMob standings refresh: {n} team-rows across {len(FOTMOB_LEAGUE_IDS)} leagues")
