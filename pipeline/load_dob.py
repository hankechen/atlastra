"""
Load scraped FotMob birth dates -> `player_dob`, keyed by Understat player_id.

`pipeline/scrape_dob.py` fetches birth dates by FotMob player id;
`player_enrichment` already carries the FotMob id <-> Understat id mapping the
enrichment fuzzy-matcher resolved, so this just joins the two and keeps the
highest-confidence mapping per player.

Run:  python -m pipeline.load_dob
"""
import sys

import duckdb
import pandas as pd

try:
    from config import RAW_DIR, DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import RAW_DIR, DB_PATH

DOB_PARQUET = RAW_DIR.parent / "fotmob" / "player_dob.parquet"


def load() -> None:
    if not DOB_PARQUET.exists():
        print(f"No {DOB_PARQUET} -- run `python -m pipeline.scrape_dob` first.")
        return

    dob = pd.read_parquet(DOB_PARQUET)
    dob["date_of_birth"] = pd.to_datetime(dob["date_of_birth"], errors="coerce")
    dob = dob.dropna(subset=["date_of_birth"]).drop_duplicates("fotmob_player_id")

    con = duckdb.connect(str(DB_PATH))
    con.register("dob_raw", dob)
    con.execute("""
        CREATE OR REPLACE TABLE player_dob AS
        WITH best AS (            -- one FotMob id per Understat player: the match
            SELECT player_id,     -- the enrichment matcher was most confident in
                   ARG_MAX(CAST(fotmob_player_id AS BIGINT), match_confidence) AS fid
            FROM player_enrichment
            WHERE fotmob_player_id IS NOT NULL
            GROUP BY 1
        )
        SELECT b.player_id, CAST(d.date_of_birth AS DATE) AS date_of_birth, b.fid AS fotmob_player_id
        FROM best b
        JOIN dob_raw d ON CAST(d.fotmob_player_id AS BIGINT) = b.fid
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_dob ON player_dob(player_id)")
    n = con.execute("SELECT count(*) FROM player_dob").fetchone()[0]

    # how much of the historical panel this actually unlocks, reported honestly
    cov = con.execute("""
        SELECT r.season,
               count(*) AS n,
               -- player_bio stores the date as text, player_dob as a DATE
               round(100.0 * sum(CASE WHEN COALESCE(CAST(d.date_of_birth AS VARCHAR),
                                                    CAST(b.date_of_birth AS VARCHAR))
                                      IS NOT NULL THEN 1 ELSE 0 END) / count(*), 1) AS pct_with_age
        FROM player_ratings_combined r
        LEFT JOIN player_dob d ON d.player_id = r.player_id
        LEFT JOIN player_bio b ON b.player_id = r.player_id
        WHERE r.scope = 'league'
        GROUP BY 1 ORDER BY 1
    """).df()
    con.close()

    print(f"[load_dob] player_dob: {n} players with a birth date")
    print("[load_dob] age coverage of the rating panel, by season:")
    print(cov.to_string(index=False))


if __name__ == "__main__":
    load()
