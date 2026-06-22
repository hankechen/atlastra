"""
Per-player per-match log (Understat) for the Big Game Index use case.

For each player-appearance we record the opponent and whether that opponent
finished in the TOP half or BOTTOM half of its league — so a player's output
can be split "vs strong" vs "vs weak" sides (big-game player vs flat-track bully).

Heavy first run (one Understat match page per fixture, ~380 per league), but
soccerdata caches each page so re-runs/resumes are fast. Run per season:

    PYTHONPATH=. python -m pipeline.load_player_match_stats          # FOCUS_SEASON
    PYTHONPATH=. python -m pipeline.load_player_match_stats 2425
"""
import sys
import warnings

import duckdb
import pandas as pd
import soccerdata as sd

try:
    from config import LEAGUES, FOCUS_SEASON, DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import LEAGUES, FOCUS_SEASON, DB_PATH

warnings.filterwarnings("ignore")

DDL = """CREATE TABLE IF NOT EXISTS player_match_log(
    player_id BIGINT, season VARCHAR, game_id BIGINT, match_date DATE,
    team_id BIGINT, opponent_team_id BIGINT, is_home BOOLEAN, league_key VARCHAR,
    minutes INTEGER, goals INTEGER, assists INTEGER, shots INTEGER,
    xg DOUBLE, xa DOUBLE, key_passes INTEGER, opp_position INTEGER, opp_top_half BOOLEAN)"""


def load(season: str = FOCUS_SEASON) -> int:
    con = duckdb.connect(str(DB_PATH))
    con.execute(DDL)
    con.execute("DELETE FROM player_match_log WHERE season = ?", [season])

    pos = {int(r[0]): (r[1], r[2]) for r in con.execute(
        "SELECT team_id, league_key, league_position FROM team_season_stats WHERE season = ?",
        [season]).fetchall()}
    nteams = {r[0]: r[1] for r in con.execute(
        "SELECT league_key, COUNT(*) FROM team_season_stats WHERE season = ? GROUP BY 1",
        [season]).fetchall()}

    total = 0
    for lg in LEAGUES:
        try:
            reader = sd.Understat(leagues=[lg], seasons=[season])
            sched = reader.read_schedule().reset_index()
            sgame = {int(r.game_id): (r.date, int(r.home_team_id), int(r.away_team_id))
                     for r in sched.itertuples() if not pd.isna(r.home_team_id)}
            pm = reader.read_player_match_stats().reset_index()
        except Exception as e:  # noqa: BLE001
            print(f"  {lg}: FAILED ({type(e).__name__}: {e})", flush=True)
            continue
        half = (nteams.get(lg, 20)) / 2
        rows = []
        for r in pm.itertuples():
            g = int(r.game_id)
            info = sgame.get(g)
            if not info:
                continue
            tid = int(r.team_id)
            is_home = tid == info[1]
            opp = info[2] if is_home else info[1]
            opp_pos = pos.get(opp, (None, None))[1]
            top = opp_pos is not None and opp_pos <= half
            rows.append((int(r.player_id), season, g, pd.to_datetime(info[0]).date(), tid, int(opp),
                         is_home, lg, int(r.minutes or 0), int(r.goals or 0), int(r.assists or 0),
                         int(r.shots or 0), float(r.xg or 0), float(r.xa or 0), int(r.key_passes or 0),
                         opp_pos, bool(top)))
        if rows:
            con.executemany("INSERT INTO player_match_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        total += len(rows)
        print(f"  {lg}: {len(rows)} player-match rows (running total {total})", flush=True)
    con.close()
    print(f"player_match_log [{season}]: {total} rows", flush=True)
    return total


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else FOCUS_SEASON)
