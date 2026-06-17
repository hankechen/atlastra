"""
Load datamb.football (Wyscout) per-position stats into `player_wyscout`.

This is the source that drives the position-weighted player rating engine
(pipeline.rate): it carries the progressive / carrying / shot-creation metrics
the spec's vectors need, which no other source has. Current season only.

Kept in its own table (Wyscout provider, its own 6 position buckets, no player
ids, many clubs beyond the Top-5) like `player_enrichment` / `ucl_player_stats`.
The ~143-field set is wide, so the table is materialised via CREATE TABLE AS
SELECT from the combined parquet, with datamb's column names slug-cased to the
project's snake_case convention. schema.sql documents it.

Run after pipeline.scrape_datamb (idempotent -- drops & rebuilds):
    python -m pipeline.load_datamb
"""
import re
import sys
import warnings

import duckdb
import pandas as pd

try:
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON, DATAMB_NON_TOP5_TEAMS
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON, DATAMB_NON_TOP5_TEAMS

warnings.filterwarnings("ignore")

DATAMB_RAW = RAW_DIR.parent / "datamb"


def _slug(col: str) -> str:
    """datamb column -> snake_case slug.
    'Aerial duels won %' -> 'aerial_duels_won_pct';
    'Progressive passes per 90' -> 'progressive_passes_per_90'."""
    s = col.lower().replace("%", "pct").replace("+", "_plus_").replace("/", "_per_")
    s = re.sub(r"[(),.]", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return re.sub(r"_+", "_", s)


def load_datamb(season: str = FOCUS_SEASON) -> None:
    src = DATAMB_RAW / f"player_wyscout_{season}.parquet"
    if not src.exists():
        print(f"No {src.name} -- run `python -m pipeline.scrape_datamb` first. Skipping.")
        return

    df = pd.read_parquet(src)
    rename = {c: _slug(c) for c in df.columns}

    con = duckdb.connect(str(DB_PATH))
    select_cols = ",\n    ".join(f'"{src_c}" AS {dst}' for src_c, dst in rename.items())
    con.execute("DROP TABLE IF EXISTS player_wyscout")
    con.execute(f"""
        CREATE TABLE player_wyscout AS
        SELECT
    {select_cols}
        FROM read_parquet('{src.as_posix()}')
    """)
    # Tag each row with the player's MAIN position (from datamb's index): a
    # player listed in several buckets has identical season minutes in each, so
    # only the index disambiguates which is their primary role. The rating engine
    # keeps just the row where datamb_position == main_position.
    con.execute("ALTER TABLE player_wyscout ADD COLUMN main_position VARCHAR")
    pos_path = DATAMB_RAW / f"player_positions_{season}.parquet"
    if pos_path.exists():
        con.execute(f"""
            UPDATE player_wyscout AS w
            SET main_position = p.main_position
            FROM read_parquet('{pos_path.as_posix()}') AS p
            WHERE w.player = p.player
        """)
        # Fallback for any player not in the index: their own bucket is primary.
        con.execute("UPDATE player_wyscout SET main_position = datamb_position "
                    "WHERE main_position IS NULL")
    else:
        print(f"  (no {pos_path.name}; main_position = each row's own bucket)")
        con.execute("UPDATE player_wyscout SET main_position = datamb_position")

    # Flag Top-5 membership by excluding the two non-Top-5 leagues in datamb's
    # "TOP7" (Eredivisie + Primeira); the rating engine pools over Top-5 only.
    con.execute("ALTER TABLE player_wyscout ADD COLUMN in_top5 BOOLEAN")
    excl = ", ".join("?" * len(DATAMB_NON_TOP5_TEAMS))
    con.execute(
        f"UPDATE player_wyscout SET in_top5 = "
        f"trim(team_within_selected_timeframe) NOT IN ({excl})",
        list(DATAMB_NON_TOP5_TEAMS),
    )

    con.execute("CREATE INDEX IF NOT EXISTS idx_wyscout_pos ON player_wyscout(datamb_position)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_wyscout_season ON player_wyscout(season)")

    n, ncol = con.execute(
        "SELECT count(*), (SELECT count(*) FROM pragma_table_info('player_wyscout')) "
        "FROM player_wyscout"
    ).fetchone()
    buckets = con.execute(
        "SELECT datamb_position, count(*) FROM player_wyscout GROUP BY 1 ORDER BY 1"
    ).fetchall()
    con.close()
    print(f"player_wyscout: {n} players, {ncol} columns. "
          f"buckets: {dict(buckets)}")


if __name__ == "__main__":
    load_datamb()
