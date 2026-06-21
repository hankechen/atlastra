"""
Map the scraped SofaScore heatmaps to our Understat player_id -> `player_heatmap`.

SofaScore player ids are GLOBAL (same across competitions), so `ucl_understat_xwalk`
(sofascore_player_id -> player_id) already links every UCL player directly; the
domestic-only rest are matched by (first-initial, surname) key + team, reusing the
same helpers as load_sofa_domestic. Stores one binned density grid per player_id.

Run after pipeline.scrape_sofa_heatmaps:
    python -m pipeline.load_sofa_heatmaps
"""
import sys
import warnings
from collections import defaultdict

import duckdb
import pandas as pd
from rapidfuzz import fuzz

try:
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON
    from pipeline.load_sofa_domestic import _key, _team
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON
    from pipeline.load_sofa_domestic import _key, _team

warnings.filterwarnings("ignore")
SOFA_RAW = RAW_DIR.parent / "sofascore"


def load_sofa_heatmaps(season: str = FOCUS_SEASON) -> None:
    src = SOFA_RAW / f"heatmaps_{season}.parquet"
    if not src.exists():
        print(f"No {src.name} -- run `python -m pipeline.scrape_sofa_heatmaps` first. Skipping.")
        return
    df = pd.read_parquet(src)
    con = duckdb.connect(str(DB_PATH))

    # 1. direct: SofaScore global id -> Understat player_id (from the UCL crosswalk)
    xw = dict(con.execute(
        "SELECT DISTINCT sofascore_player_id, player_id FROM ucl_understat_xwalk "
        "WHERE player_id IS NOT NULL").fetchall())

    # 2. fuzzy fallback pool: our domestic players (name + main team) this season
    us = con.execute(
        "SELECT ps.player_id, any_value(p.player_name) AS name, "
        "arg_max(t.team_name, ps.minutes) AS team "
        "FROM player_season_stats ps JOIN players p USING(player_id) "
        "JOIN teams t USING(team_id) WHERE ps.season = ? GROUP BY ps.player_id",
        [season]).df()
    by_key = defaultdict(list)
    for r in us.itertuples():
        by_key[_key(r.name)].append((int(r.player_id), r.name, r.team))

    rows, used, direct, fuzzy = [], set(), 0, 0
    for r in df.itertuples():
        pid = xw.get(int(r.sofascore_player_id))
        if pid is not None:
            direct += 1
        else:
            cands = by_key.get(_key(r.player_name), [])
            if len(cands) == 1:
                pid = cands[0][0]
            elif len(cands) > 1:
                best = max(cands, key=lambda c: fuzz.token_set_ratio(_team(c[2]), _team(r.team_name)))
                if fuzz.token_set_ratio(_team(best[2]), _team(r.team_name)) >= 60:
                    pid = best[0]
            if pid is not None:
                fuzzy += 1
        if pid is None or pid in used:
            continue
        used.add(pid)
        rows.append((pid, season, r.grid))

    out = pd.DataFrame(rows, columns=["player_id", "season", "grid"])
    # append-safe per season (so past-season heatmaps accumulate instead of
    # overwriting the current one): create if needed, replace just this season.
    con.execute("CREATE TABLE IF NOT EXISTS player_heatmap "
                "(player_id BIGINT, season VARCHAR, grid VARCHAR)")
    con.execute("DELETE FROM player_heatmap WHERE season = ?", [season])
    con.register("out_df", out)
    con.execute("INSERT INTO player_heatmap SELECT * FROM out_df")
    con.unregister("out_df")
    con.execute("CREATE INDEX IF NOT EXISTS idx_hm ON player_heatmap(player_id)")
    con.close()
    print(f"player_heatmap: {len(rows)} players "
          f"({direct} via UCL xwalk, {fuzzy} via name+team) of {len(df)} scraped.")


if __name__ == "__main__":
    load_sofa_heatmaps(sys.argv[1] if len(sys.argv) > 1 else FOCUS_SEASON)
