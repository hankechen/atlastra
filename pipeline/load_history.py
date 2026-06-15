"""Load the pre-Understat gap (2008/09-2013/14) into the warehouse.

Reads the football-data.co.uk cache (data/raw/matchhistory/matches.parquet,
produced by pipeline.scrape_history), keeps only the 6 gap seasons, and loads:
  matches_history       -- 1 row per fixture, basic match stats (no xG)
  team_season_history   -- derived standings + shot/discipline aggregates

Name-based: Understat has no team ids for this era, so these tables stand apart
from the Understat-keyed `matches` / `team_match_stats`. Does NOT reset the
warehouse -- it only creates its two tables (IF NOT EXISTS) and replaces their
gap-season rows.

    python -m pipeline.load_history
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config import DB_PATH, SCHEMA_PATH  # noqa: E402

RAW = ROOT / "data" / "raw" / "matchhistory" / "matches.parquet"
GAP_SEASONS = ["0809", "0910", "1011", "1112", "1213", "1314"]

INT_COLS = [
    "home_goals", "away_goals", "home_goals_ht", "away_goals_ht",
    "home_shots", "away_shots", "home_shots_ot", "away_shots_ot",
    "home_fouls", "away_fouls", "home_corners", "away_corners",
    "home_yellows", "away_yellows", "home_reds", "away_reds",
]


def _create_tables(con) -> None:
    # Pull just the two historical CREATE TABLE statements from schema.sql so we
    # never drop/recreate the populated Understat tables.
    ddl = SCHEMA_PATH.read_text()
    for table in ("matches_history", "team_season_history"):
        start = ddl.index(f"CREATE TABLE IF NOT EXISTS {table}")
        stmt = ddl[start: ddl.index(");", start) + 2]
        con.execute(stmt)


def load() -> None:
    if not RAW.exists():
        raise FileNotFoundError(
            f"{RAW} missing -- run `python -m pipeline.scrape_history` first")

    df = pd.read_parquet(RAW)
    df = df[df["season"].isin(GAP_SEASONS)].copy()
    df["match_date"] = pd.to_datetime(
        df["match_date"], dayfirst=True, errors="coerce").dt.date
    for c in INT_COLS:
        df[c] = df[c].astype("Int64")
    if "referee" not in df:
        df["referee"] = pd.NA

    con = duckdb.connect(str(DB_PATH))
    _create_tables(con)

    con.execute("DELETE FROM matches_history WHERE season IN "
                "(SELECT unnest(?))", [GAP_SEASONS])
    con.register("hist_df", df)
    con.execute(
        """
        INSERT INTO matches_history
        (league_key, season, match_date, home_team, away_team,
         home_goals, away_goals, result, home_goals_ht, away_goals_ht, result_ht,
         home_shots, away_shots, home_shots_ot, away_shots_ot,
         home_fouls, away_fouls, home_corners, away_corners,
         home_yellows, away_yellows, home_reds, away_reds, referee)
        SELECT league_key, season, match_date, home_team, away_team,
               home_goals, away_goals, result, home_goals_ht, away_goals_ht, result_ht,
               home_shots, away_shots, home_shots_ot, away_shots_ot,
               home_fouls, away_fouls, home_corners, away_corners,
               home_yellows, away_yellows, home_reds, away_reds, referee
        FROM hist_df
        """
    )
    n = con.execute("SELECT count(*) FROM matches_history").fetchone()[0]
    print(f"  matches_history: {n} fixtures ({len(GAP_SEASONS)} seasons x 5 leagues)")

    _build_standings(con)
    con.close()
    print(f"\nLoad complete -> {DB_PATH}")


def _build_standings(con) -> None:
    con.execute("DELETE FROM team_season_history WHERE season IN "
                "(SELECT unnest(?))", [GAP_SEASONS])
    # Explode each fixture into one row per team (home + away), then aggregate.
    con.execute(
        """
        INSERT INTO team_season_history
        WITH team_rows AS (
            SELECT league_key, season, home_team AS team, home_goals AS gf, away_goals AS ga,
                   home_shots AS sf, home_shots_ot AS sotf, home_corners AS cf,
                   home_yellows AS yc, home_reds AS rc
            FROM matches_history
            UNION ALL
            SELECT league_key, season, away_team, away_goals, home_goals,
                   away_shots, away_shots_ot, away_corners,
                   away_yellows, away_reds
            FROM matches_history
        ), agg AS (
            SELECT league_key, season, team,
                   count(*) AS mp,
                   sum(CASE WHEN gf > ga THEN 1 ELSE 0 END) AS w,
                   sum(CASE WHEN gf = ga THEN 1 ELSE 0 END) AS d,
                   sum(CASE WHEN gf < ga THEN 1 ELSE 0 END) AS l,
                   sum(gf) AS gf, sum(ga) AS ga, sum(gf) - sum(ga) AS gd,
                   sum(CASE WHEN gf > ga THEN 3 WHEN gf = ga THEN 1 ELSE 0 END) AS pts,
                   sum(sf) AS sf, sum(sotf) AS sotf, sum(cf) AS cf,
                   sum(yc) AS yc, sum(rc) AS rc
            FROM team_rows GROUP BY league_key, season, team
        )
        SELECT league_key, season, team, mp, w, d, l, gf, ga, gd, pts,
               sf, sotf, cf, yc, rc,
               row_number() OVER (PARTITION BY league_key, season
                                  ORDER BY pts DESC, gd DESC, gf DESC) AS pos
        FROM agg
        """
    )
    n = con.execute("SELECT count(*) FROM team_season_history").fetchone()[0]
    print(f"  team_season_history (standings): {n} team-seasons")


if __name__ == "__main__":
    load()
