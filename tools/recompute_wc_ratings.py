"""
Re-rate the World Cup off the cached raw stats -- no SofaScore scrape.

Use after tweaking weights/calibration in pipeline.rate_wc: it reads the raw
per-player stats saved by the last full `python -m pipeline.load_wc`
(wc_raw_player_stats.json), recomputes atlas_rating / atlas_class, and UPDATEs
wc_player_stats in place. Then push to prod with the usual /api/ingest/wc payload.

    python -m tools.recompute_wc_ratings
"""
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import duckdb

from config import DB_PATH
from pipeline import rate_wc
from pipeline.load_wc import RAW_STATS_CACHE


def main() -> None:
    if not Path(RAW_STATS_CACHE).exists():
        sys.exit(f"no raw-stats cache at {RAW_STATS_CACHE} -- run `python -m pipeline.load_wc` first")
    with open(RAW_STATS_CACHE) as fh:
        player_stats = json.load(fh)
    rated = rate_wc.compute(player_stats)
    print(f"rated {len(rated)}/{len(player_stats)} players (>= {rate_wc.MIN_MINUTES} min)")

    con = duckdb.connect(str(DB_PATH))
    con.execute("UPDATE wc_player_stats SET atlas_rating = NULL, atlas_class = NULL")
    rows = [(v["rating"], v["classification"], season, pid) for (season, pid), v in rated.items()]
    con.executemany(
        "UPDATE wc_player_stats SET atlas_rating = ?, atlas_class = ? "
        "WHERE season = ? AND player_id = ?", rows)
    n = con.execute("SELECT count(*) FROM wc_player_stats WHERE atlas_rating IS NOT NULL").fetchone()[0]
    con.close()
    print(f"updated wc_player_stats: {n} rows now carry a rating")


if __name__ == "__main__":
    main()
