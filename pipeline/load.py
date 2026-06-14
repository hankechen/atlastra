"""
Load scraped Understat Parquet into the DuckDB warehouse and build the derived
tables (standings + player ratings/classification).

Run after pipeline.scrape and pipeline.init_db:
    python -m pipeline.load
"""
import sys
import warnings

import duckdb
import pandas as pd

try:
    from config import (
        DB_PATH, RAW_DIR, LEAGUES, FOCUS_SEASON, POSITION_GROUP,
        MIN_MINUTES_FOR_RATING, FULL_SEASON_MINUTES, season_label,
    )
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import (
        DB_PATH, RAW_DIR, LEAGUES, FOCUS_SEASON, POSITION_GROUP,
        MIN_MINUTES_FOR_RATING, FULL_SEASON_MINUTES, season_label,
    )

warnings.filterwarnings("ignore")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _position_group(raw: str) -> str | None:
    """Understat encodes positions like 'F M S'; map the primary code to a group."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    for token in raw.split():
        if token in ("S", "Sub"):
            continue
        return POSITION_GROUP.get(token)
    return None


def _per90(value, minutes):
    return (value / minutes * 90) if minutes and minutes > 0 else None


def _read(name: str) -> pd.DataFrame:
    path = RAW_DIR / f"{name}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"missing raw file {path}; run `python -m pipeline.scrape` first")
    return pd.read_parquet(path)


# --------------------------------------------------------------------------- #
# dimensions
# --------------------------------------------------------------------------- #
def load_leagues(con) -> None:
    rows = [(k, v["name"], v["country"]) for k, v in LEAGUES.items()]
    con.executemany(
        "INSERT OR REPLACE INTO leagues (league_key, league_name, country) VALUES (?,?,?)",
        rows,
    )
    print(f"  leagues: {len(rows)}")


def load_teams(con, players: pd.DataFrame, schedule: pd.DataFrame) -> None:
    # Teams from player rows (team_id + team name + league).
    t1 = players[["team_id", "team", "league"]].rename(
        columns={"team": "team_name", "league": "league_key"}
    )
    # Teams from schedule (gives us the short code).
    home = schedule[["home_team_id", "home_team", "home_team_code", "league"]].rename(
        columns={"home_team_id": "team_id", "home_team": "team_name",
                 "home_team_code": "team_code", "league": "league_key"}
    )
    away = schedule[["away_team_id", "away_team", "away_team_code", "league"]].rename(
        columns={"away_team_id": "team_id", "away_team": "team_name",
                 "away_team_code": "team_code", "league": "league_key"}
    )
    codes = pd.concat([home, away], ignore_index=True)

    teams = t1.merge(
        codes[["team_id", "team_code"]].drop_duplicates("team_id"),
        on="team_id", how="left",
    ).drop_duplicates("team_id")

    rows = [
        (int(r.team_id), r.team_name, (None if pd.isna(r.team_code) else r.team_code), r.league_key)
        for r in teams.itertuples()
    ]
    con.executemany(
        "INSERT OR REPLACE INTO teams (team_id, team_name, team_code, league_key) VALUES (?,?,?,?)",
        rows,
    )
    print(f"  teams: {len(rows)}")


def load_players(con, players: pd.DataFrame) -> None:
    # One identity row per player; prefer the most recent season's position.
    p = players.sort_values("season").drop_duplicates("player_id", keep="last")
    rows = [
        (int(r.player_id), r.player, r.position, _position_group(r.position))
        for r in p.itertuples()
    ]
    con.executemany(
        "INSERT OR REPLACE INTO players (player_id, player_name, primary_position, position_group) "
        "VALUES (?,?,?,?)",
        rows,
    )
    print(f"  players: {len(rows)}")


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #
def load_matches(con, schedule: pd.DataFrame) -> None:
    rows = []
    for r in schedule.itertuples():
        rows.append((
            int(r.game_id), r.league, r.season,
            pd.to_datetime(r.date).to_pydatetime() if pd.notna(r.date) else None,
            int(r.home_team_id), int(r.away_team_id),
            None if pd.isna(r.home_goals) else int(r.home_goals),
            None if pd.isna(r.away_goals) else int(r.away_goals),
            None if pd.isna(r.home_xg) else float(r.home_xg),
            None if pd.isna(r.away_xg) else float(r.away_xg),
            bool(r.is_result),
        ))
    con.executemany(
        "INSERT OR REPLACE INTO matches "
        "(game_id, league_key, season, match_date, home_team_id, away_team_id, "
        " home_goals, away_goals, home_xg, away_xg, is_result) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    print(f"  matches: {len(rows)}")


def load_team_match_stats(con, tms: pd.DataFrame) -> None:
    """Explode each fixture row into two team rows (home + away)."""
    rows = []

    def points(gf, ga):
        if gf is None or ga is None:
            return None
        return 3 if gf > ga else (1 if gf == ga else 0)

    def f(v):  # float or None  (older seasons lack ppda/xpoints/deep_completions)
        return None if pd.isna(v) else float(v)

    def i(v):  # int or None
        return None if pd.isna(v) else int(v)

    for r in tms.itertuples():
        d = pd.to_datetime(r.date).to_pydatetime() if pd.notna(r.date) else None
        hg, ag = i(r.home_goals), i(r.away_goals)
        # home perspective
        rows.append((
            int(r.game_id), int(r.home_team_id), r.league, r.season, d, True,
            int(r.away_team_id), hg, ag,
            f(r.home_xg), f(r.away_xg), f(r.home_np_xg),
            points(hg, ag),
            f(r.home_expected_points), f(r.home_ppda), i(r.home_deep_completions),
        ))
        # away perspective
        rows.append((
            int(r.game_id), int(r.away_team_id), r.league, r.season, d, False,
            int(r.home_team_id), ag, hg,
            f(r.away_xg), f(r.home_xg), f(r.away_np_xg),
            points(ag, hg),
            f(r.away_expected_points), f(r.away_ppda), i(r.away_deep_completions),
        ))
    con.executemany(
        "INSERT OR REPLACE INTO team_match_stats "
        "(game_id, team_id, league_key, season, match_date, is_home, opponent_team_id, "
        " goals_for, goals_against, xg_for, xg_against, np_xg_for, points, expected_points, "
        " ppda, deep_completions) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    print(f"  team_match_stats: {len(rows)}")


def load_player_season_stats(con, players: pd.DataFrame) -> None:
    rows = []
    for r in players.itertuples():
        mins = None if pd.isna(r.minutes) else int(r.minutes)
        goals = None if pd.isna(r.goals) else int(r.goals)
        assists = None if pd.isna(r.assists) else int(r.assists)
        ga = (goals or 0) + (assists or 0)
        rows.append((
            int(r.player_id), int(r.team_id), r.league, r.season,
            r.position, _position_group(r.position),
            None if pd.isna(r.matches) else int(r.matches),
            mins, goals, assists,
            None if pd.isna(r.shots) else int(r.shots),
            None if pd.isna(r.key_passes) else int(r.key_passes),
            None if pd.isna(r.xg) else float(r.xg),
            None if pd.isna(r.np_goals) else int(r.np_goals),
            None if pd.isna(r.np_xg) else float(r.np_xg),
            None if pd.isna(r.xa) else float(r.xa),
            None if pd.isna(r.xg_chain) else float(r.xg_chain),
            None if pd.isna(r.xg_buildup) else float(r.xg_buildup),
            None if pd.isna(r.yellow_cards) else int(r.yellow_cards),
            None if pd.isna(r.red_cards) else int(r.red_cards),
            _per90(goals, mins), _per90(assists, mins), _per90(ga, mins),
            _per90(None if pd.isna(r.xg) else float(r.xg), mins),
            _per90(None if pd.isna(r.xa) else float(r.xa), mins),
            _per90(None if pd.isna(r.np_xg) else float(r.np_xg), mins),
            _per90(None if pd.isna(r.shots) else float(r.shots), mins),
            _per90(None if pd.isna(r.key_passes) else float(r.key_passes), mins),
        ))
    con.executemany(
        "INSERT OR REPLACE INTO player_season_stats "
        "(player_id, team_id, league_key, season, position, position_group, matches, minutes, "
        " goals, assists, shots, key_passes, xg, np_goals, np_xg, xa, xg_chain, xg_buildup, "
        " yellow_cards, red_cards, goals_per90, assists_per90, ga_per90, xg_per90, xa_per90, "
        " npxg_per90, shots_per90, key_passes_per90) "
        "VALUES (" + ",".join(["?"] * 28) + ")",
        rows,
    )
    print(f"  player_season_stats: {len(rows)}")


# --------------------------------------------------------------------------- #
# derived: standings
# --------------------------------------------------------------------------- #
def build_standings(con) -> None:
    con.execute("DELETE FROM team_season_stats")
    con.execute(
        """
        INSERT INTO team_season_stats
        SELECT
            team_id, league_key, season,
            count(*)                              AS matches_played,
            sum(CASE WHEN goals_for > goals_against THEN 1 ELSE 0 END) AS wins,
            sum(CASE WHEN goals_for = goals_against THEN 1 ELSE 0 END) AS draws,
            sum(CASE WHEN goals_for < goals_against THEN 1 ELSE 0 END) AS losses,
            sum(goals_for)                        AS goals_for,
            sum(goals_against)                    AS goals_against,
            sum(goals_for) - sum(goals_against)   AS goal_difference,
            round(sum(xg_for), 2)                 AS xg_for,
            round(sum(xg_against), 2)             AS xg_against,
            sum(points)                           AS points,
            round(sum(expected_points), 2)        AS expected_points,
            row_number() OVER (
                PARTITION BY league_key, season
                ORDER BY sum(points) DESC,
                         sum(goals_for) - sum(goals_against) DESC,
                         sum(goals_for) DESC
            )                                     AS league_position
        FROM team_match_stats
        WHERE goals_for IS NOT NULL
        GROUP BY team_id, league_key, season
        """
    )
    n = con.execute("SELECT count(*) FROM team_season_stats").fetchone()[0]
    print(f"  team_season_stats (standings): {n}")


# --------------------------------------------------------------------------- #
# derived: ratings & classification (README use case #2)
# --------------------------------------------------------------------------- #
# Per-90 metrics that feed the composite score for each position group.
# Understat is attack/possession oriented, so DEF/GK ratings lean on
# involvement metrics (xg_buildup/xg_chain) -- documented in NOTES.md.
RATING_WEIGHTS = {
    "FWD": {"goals_per90": 0.30, "npxg_per90": 0.20, "assists_per90": 0.15,
            "xa_per90": 0.15, "shots_per90": 0.10, "key_passes_per90": 0.10},
    "MID": {"assists_per90": 0.20, "xa_per90": 0.20, "key_passes_per90": 0.20,
            "goals_per90": 0.15, "npxg_per90": 0.15, "xgchain_per90": 0.10},
    "DEF": {"xgbuildup_per90": 0.40, "xgchain_per90": 0.25, "key_passes_per90": 0.15,
            "assists_per90": 0.10, "goals_per90": 0.10},
    "GK":  {"xgbuildup_per90": 1.0},
}


def build_ratings(con) -> None:
    con.execute("DELETE FROM player_ratings WHERE rating_version = 'v1'")

    df = con.execute(
        """
        SELECT player_id, league_key, season, position_group, minutes,
               goals_per90, assists_per90, npxg_per90, xa_per90,
               shots_per90, key_passes_per90,
               xg_chain  / NULLIF(minutes,0) * 90 AS xgchain_per90,
               xg_buildup/ NULLIF(minutes,0) * 90 AS xgbuildup_per90
        FROM player_season_stats
        WHERE minutes >= ? AND position_group IS NOT NULL
        """,
        [MIN_MINUTES_FOR_RATING],
    ).df()

    if df.empty:
        print("  player_ratings: 0 (no eligible players)")
        return

    out = []
    # Percentile-rank within (league, season, position group).
    for (league, season, grp), g in df.groupby(["league_key", "season", "position_group"]):
        weights = RATING_WEIGHTS.get(grp, {})
        g = g.copy()
        # z-score-free approach: percentile-rank each metric, then weight.
        score = pd.Series(0.0, index=g.index)
        for metric, w in weights.items():
            if metric in g and g[metric].notna().any():
                pct = g[metric].rank(pct=True)
                score = score + w * pct.fillna(0)
        # Scale quality by availability so small samples don't top the chart.
        reliability = (g["minutes"] / FULL_SEASON_MINUTES).clip(upper=1.0)
        g["composite"] = score * reliability
        g["percentile_in_group"] = g["composite"].rank(pct=True)
        g["rating"] = (g["percentile_in_group"] * 100).round(1)
        g = g.sort_values("rating", ascending=False).reset_index(drop=True)
        g["rank_in_group"] = g.index + 1

        for r in g.itertuples():
            out.append((
                int(r.player_id), league, season, grp, int(r.minutes),
                float(r.rating), float(r.percentile_in_group),
                _classify(r.rank_in_group, r.percentile_in_group),
                int(r.rank_in_group),
            ))

    con.executemany(
        "INSERT OR REPLACE INTO player_ratings "
        "(player_id, league_key, season, position_group, minutes, rating, "
        " percentile_in_group, classification, rank_in_group, rating_version) "
        "VALUES (?,?,?,?,?,?,?,?,?,'v1')",
        out,
    )
    print(f"  player_ratings: {len(out)}")


def _classify(rank: int, pct: float) -> str:
    if rank == 1:
        return "Best In Position"
    if pct >= 0.95:
        return "World-Class"
    if pct >= 0.85:
        return "Elite"
    if pct >= 0.65:
        return "Above Average"
    if pct >= 0.35:
        return "Average"
    return "Below Average"


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def load_all() -> None:
    con = duckdb.connect(str(DB_PATH))

    players = _read("player_season_stats")
    schedule = _read("schedule")
    tms = _read("team_match_stats")

    print("Loading dimensions ...")
    load_leagues(con)
    load_teams(con, players, schedule)
    load_players(con, players)

    print("Loading facts ...")
    load_matches(con, schedule)
    load_team_match_stats(con, tms)
    load_player_season_stats(con, players)

    print("Building derived tables ...")
    build_standings(con)
    build_ratings(con)

    con.close()
    print(f"\nLoad complete -> {DB_PATH}")


if __name__ == "__main__":
    load_all()
