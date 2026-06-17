"""
Load Transfermarkt market values into `player_market_value`, resolving each TM
player to an Understat `player_id` by the same two-phase fuzzy name match used
for FotMob (pipeline.load_enrich), scoped per (league_key, season).

Safe to run with no scrape file (no-ops). Run after pipeline.scrape_transfermarkt
and the main load:
    python -m pipeline.load_transfermarkt
"""
import sys
import warnings

import duckdb
import pandas as pd
from rapidfuzz import fuzz, process

from pipeline.load_enrich import _norm, _name_compatible, MATCH_THRESHOLD, RECOVER_THRESHOLD

try:
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON

warnings.filterwarnings("ignore")

TM_RAW = RAW_DIR.parent / "transfermarkt"


def load_transfermarkt() -> None:
    files = sorted(TM_RAW.glob("market_values_*.parquet"))
    if not files:
        print("No Transfermarkt files -- run `python -m pipeline.scrape_transfermarkt`. Skipping.")
        return
    tm = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    con = duckdb.connect(str(DB_PATH))
    us = con.execute(
        "SELECT DISTINCT ps.player_id, p.player_name, ps.league_key, ps.season "
        "FROM player_season_stats ps JOIN players p USING (player_id)"
    ).df()
    pools = {
        key: ([int(r.player_id) for r in g.itertuples()],
              [_norm(r.player_name) for r in g.itertuples()])
        for key, g in us.groupby(["league_key", "season"])
    }

    rows, matched, recovered = [], 0, 0
    for (league_key, season), grp in tm.groupby(["league_key", "season"]):
        ids, names = pools.get((league_key, season), ([], []))
        used, leftover = set(), []
        for r in grp.itertuples():
            target = _norm(r.player_name)
            if not target or not names:
                continue
            best = process.extractOne(target, names, scorer=fuzz.token_sort_ratio)
            if not best or best[1] < MATCH_THRESHOLD or ids[best[2]] in used:
                leftover.append((r, target))
                continue
            used.add(ids[best[2]])
            matched += 1
            rows.append((ids[best[2]], season, league_key, int(r.tm_player_id),
                         r.player_name, float(r.market_value_eur), float(best[1])))
        cands = []
        for r, target in leftover:
            for i, ut in enumerate(names):
                if ids[i] in used:
                    continue
                score = fuzz.token_set_ratio(target, ut)
                if score >= RECOVER_THRESHOLD and _name_compatible(target, ut):
                    cands.append((score, id(r), r, ids[i]))
        cands.sort(key=lambda c: -c[0])
        claimed = set()
        for score, rid, r, pid in cands:
            if rid in claimed or pid in used:
                continue
            claimed.add(rid)
            used.add(pid)
            recovered += 1
            rows.append((pid, season, league_key, int(r.tm_player_id),
                         r.player_name, float(r.market_value_eur), float(score)))

    con.execute("DROP TABLE IF EXISTS player_market_value")
    con.execute("""CREATE TABLE player_market_value
        (player_id BIGINT, season VARCHAR, league_key VARCHAR, tm_player_id BIGINT,
         tm_player_name VARCHAR, market_value_eur DOUBLE, match_confidence DOUBLE)""")
    if rows:
        con.executemany("INSERT INTO player_market_value VALUES (?,?,?,?,?,?,?)", rows)
    con.close()
    print(f"player_market_value: matched {matched} + {recovered} recovered "
          f"of {len(tm)} Transfermarkt players.")


if __name__ == "__main__":
    load_transfermarkt()
