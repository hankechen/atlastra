"""
Load SofaScore UEFA Champions League player stats into `ucl_player_stats`.

Kept in its own table (different provider, different competition, SofaScore ids
rather than Understat ones, and many clubs outside the Top-5) so the domestic
Understat-sourced facts stay clean -- same separation as `player_enrichment`.

The table is materialised directly from the combined parquet via CREATE TABLE AS
SELECT: the SofaScore stat set is ~77 wide and grows over time, so column-by-
column DDL in schema.sql would be brittle. schema.sql documents the table; this
loader (re)creates and fills it. Camel-cased SofaScore field names are converted
to the project's snake_case convention.

Run after pipeline.scrape_ucl (idempotent -- drops & rebuilds the table):
    python -m pipeline.load_ucl
"""
import re
import sys
import warnings

import duckdb
import pandas as pd

try:
    from config import DB_PATH, RAW_DIR
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, RAW_DIR

warnings.filterwarnings("ignore")

SOFA_RAW = RAW_DIR.parent / "sofascore"
COMBINED = SOFA_RAW / "ucl_player_stats_all.parquet"

# Columns already named the way we want (don't camel->snake these).
_PASSTHROUGH = {"season", "competition", "sofascore_player_id", "player_name",
                "sofascore_team_id", "team_name"}


def _snake(name: str) -> str:
    """camelCase -> snake_case (e.g. 'expectedGoals' -> 'expected_goals')."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    return s.lower()


def load_ucl() -> None:
    if not COMBINED.exists():
        print(f"No {COMBINED.name} -- run `python -m pipeline.scrape_ucl` first. Skipping.")
        return

    df = pd.read_parquet(COMBINED)
    rename = {c: (c if c in _PASSTHROUGH else _snake(c)) for c in df.columns}

    con = duckdb.connect(str(DB_PATH))
    # Build the column-rename projection over read_parquet so we never load the
    # frame into the DB twice; DuckDB infers types straight from the parquet.
    select_cols = ",\n    ".join(f'"{src}" AS {dst}' for src, dst in rename.items())
    con.execute("DROP TABLE IF EXISTS ucl_player_stats")
    con.execute(f"""
        CREATE TABLE ucl_player_stats AS
        SELECT
    {select_cols}
        FROM read_parquet('{COMBINED.as_posix()}')
    """)
    # Helpful indexes mirroring the rest of the warehouse.
    con.execute("CREATE INDEX IF NOT EXISTS idx_ucl_season ON ucl_player_stats(season)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ucl_player ON ucl_player_stats(sofascore_player_id)")

    n, ncol = con.execute(
        "SELECT count(*), (SELECT count(*) FROM pragma_table_info('ucl_player_stats')) "
        "FROM ucl_player_stats"
    ).fetchone()
    seasons = con.execute(
        "SELECT count(DISTINCT season), min(season), max(season) FROM ucl_player_stats"
    ).fetchone()
    con.close()
    print(f"ucl_player_stats: {n} player-seasons, {ncol} columns, "
          f"{seasons[0]} seasons ({seasons[1]}->{seasons[2]}).")


if __name__ == "__main__":
    load_ucl()
