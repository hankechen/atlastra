"""
Resolve each player's position from FotMob (the preferred position source --
datamb only has coarse buckets and mis-splits CM/DM/AM). Maps FotMob's primary
positionId to a rating group + side, joined to the Understat player_id, into the
`player_position` table that the rating engines read.

FotMob is authoritative for GK/CB/FB(LB/RB)/ST/AM/W(LW/RW); for the central-mid
band ('CMID') it returns CMID and the rating engine defers the DM-vs-CM call to
datamb (which does separate holders from box-to-box, if imperfectly). See
[[positions-use-fotmob]].

Runs BEFORE rate (needs only player_enrichment + the FotMob positions parquet +
the datamb<->Understat name crosswalk). Safe with no scrape file (no-ops).
    python -m pipeline.load_fotmob_positions
"""
import sys

import duckdb
import pandas as pd

from pipeline.profile import _datamb_to_understat   # datamb-name -> Understat player_id

try:
    from config import DB_PATH, FOCUS_SEASON, RAW_DIR
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON, RAW_DIR

FOTMOB_RAW = RAW_DIR.parent / "fotmob"

# FotMob primary positionId -> (rating group, side). Side only for W / FB.
GK = {11}
CB = {31, 34, 35, 36, 39}
RB, LB = {32, 33}, {37, 38}
CMID = {64, 65, 66, 71, 72, 73, 74, 75, 76, 77, 78, 79}   # central mid (DM vs CM -> datamb)
AM = {82, 84, 85, 86, 103}
RW, LW = {83, 88, 92}, {96, 105, 106, 107}
GENERIC_W = {87}
ST = {104, 114, 115}


def _fm_group_side(posids):
    if not posids:
        return None, None
    p = posids[0]
    if p in GK:   return "GK", None
    if p in CB:   return "CB", None
    if p in RB:   return "FB", "R"
    if p in LB:   return "FB", "L"
    if p in CMID: return "CMID", None
    if p in AM:   return "AM", None
    if p in RW:   return "W", "R"
    if p in LW:   return "W", "L"
    if p in GENERIC_W: return "W", None
    if p in ST:   return "ST", None
    return None, None


def load_fotmob_positions(season: str = FOCUS_SEASON) -> None:
    path = FOTMOB_RAW / f"positions_{season}.parquet"
    if not path.exists():
        print("No FotMob positions file -- run scrape_fotmob_positions. Skipping.")
        return
    pos = pd.read_parquet(path)
    con = duckdb.connect(str(DB_PATH))

    # fotmob_player_id -> Understat player_id (via the FotMob enrichment join)
    emap = con.execute(
        "SELECT DISTINCT fotmob_player_id, player_id FROM player_enrichment "
        "WHERE source='fotmob' AND season=? AND fotmob_player_id IS NOT NULL", [season]).df()
    fm = pos.merge(emap, on="fotmob_player_id")
    gs = fm["position_ids"].apply(
        lambda s: _fm_group_side([int(x) for x in str(s).split(",") if x]))
    fm["fotmob_group"] = [g for g, _ in gs]
    fm["side"] = [s for _, s in gs]
    fm = fm.dropna(subset=["fotmob_group"]).drop_duplicates("player_id")

    # datamb name -> player_id, so the (name-keyed) rating engine can join by name
    datamb = con.execute(
        "SELECT DISTINCT player FROM player_wyscout WHERE season=?", [season]).df()
    xwalk = _datamb_to_understat(con, datamb["player"].tolist(), season)  # name -> player_id
    pid_to_name = {}
    for name, pid in xwalk.items():
        pid_to_name.setdefault(pid, name)
    fm["datamb_player"] = fm["player_id"].map(pid_to_name)

    out = fm[["player_id", "datamb_player", "fotmob_group", "side", "position_ids"]].rename(
        columns={"position_ids": "fotmob_position_ids"})
    con.execute("DROP TABLE IF EXISTS player_position")
    con.execute("CREATE TABLE player_position AS SELECT * FROM out")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pp ON player_position(player_id)")
    matched_names = out["datamb_player"].notna().sum()
    con.close()
    print(f"player_position: {len(out)} players ({matched_names} linked to a datamb name). "
          f"groups: {out['fotmob_group'].value_counts().to_dict()}")


if __name__ == "__main__":
    load_fotmob_positions()
