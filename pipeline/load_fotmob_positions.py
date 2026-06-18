"""
Derive each player's detailed position by combining datamb's fine group with the
left/right side from FotMob's positionIds (pipeline.scrape_fotmob_positions).

Only the wide groups gain detail: datamb W -> LW/RW, datamb FB -> LB/RB; the
others (ST/AM/CM/DM/CB/GK) keep their datamb fine group. Side is read from the
player's primary FotMob positionId. Writes `player_position_detail`.

Safe with no scrape file (no-ops). Run after scrape_fotmob_positions + profiles:
    python -m pipeline.load_fotmob_positions
"""
import sys

import duckdb
import pandas as pd

try:
    from config import DB_PATH, FOCUS_SEASON, RAW_DIR
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON, RAW_DIR

FOTMOB_RAW = RAW_DIR.parent / "fotmob"
# FotMob positionId sets that denote a side (verified vs known players). Note 87
# is a GENERIC wide-forward id used by both wings (Rodrygo RW but also Díaz/
# Rashford LW), so it is deliberately excluded -- those stay unsplit, not wrong.
WING_R, WING_L = {83, 88, 92}, {107, 106, 105}   # right / left attacking-wide
FB_R, FB_L = {32, 33, 71}, {37, 38, 79}          # right / left full/wing-back


def _side(ids, right, left):
    for x in ids:
        if x in right:
            return "R"
        if x in left:
            return "L"
    return None


def load_fotmob_positions(season: str = FOCUS_SEASON) -> None:
    path = FOTMOB_RAW / f"positions_{season}.parquet"
    if not path.exists():
        print("No FotMob positions file -- run scrape_fotmob_positions. Skipping.")
        return
    pos = pd.read_parquet(path)
    con = duckdb.connect(str(DB_PATH))
    emap = con.execute(
        "SELECT DISTINCT fotmob_player_id, player_id FROM player_enrichment "
        "WHERE source='fotmob' AND season=? AND fotmob_player_id IS NOT NULL", [season]).df()
    grp = con.execute(
        "SELECT DISTINCT player_id, position_group FROM player_profile_metrics").df()
    df = pos.merge(emap, on="fotmob_player_id").merge(grp, on="player_id")

    rows = []
    for r in df.itertuples():
        ids = [int(x) for x in str(r.position_ids).split(",") if x]
        g = r.position_group
        if g == "W":
            s = _side(ids, WING_R, WING_L)
            det = {"R": "RW", "L": "LW"}.get(s, "W")
        elif g == "FB":
            s = _side(ids, FB_R, FB_L)
            det = {"R": "RB", "L": "LB"}.get(s, "FB")
        else:
            det = g
        rows.append((int(r.player_id), season, det, str(r.position_ids)))
    out = (pd.DataFrame(rows, columns=["player_id", "season", "detailed_position",
                                       "fotmob_position_ids"])
           .drop_duplicates("player_id"))
    con.execute("DROP TABLE IF EXISTS player_position_detail")
    con.execute("CREATE TABLE player_position_detail AS SELECT * FROM out")
    counts = out["detailed_position"].value_counts().to_dict()
    con.close()
    print(f"player_position_detail: {len(out)} players. {counts}")


if __name__ == "__main__":
    load_fotmob_positions()
