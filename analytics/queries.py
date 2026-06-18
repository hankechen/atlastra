"""
Read-only query layer over the DuckDB warehouse.

Each public method maps to one of the README's Phase-One use cases and returns a
pandas DataFrame (or dict), so it can be reused by tests, notebooks, or a future
web/API layer.

Stats that Understat does not provide (duels, dribbles, tackles, interceptions,
big chances, passes completed, market value, manager, venue) are simply not
returned; see NOTES.md.
"""
import sys

import duckdb
import pandas as pd

try:
    from config import DB_PATH, FOCUS_SEASON, season_label
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON, season_label


# small coercion helpers for the web bundles (JSON-friendly, NaN-safe)
def _i(v):
    return None if v is None or pd.isna(v) else int(round(float(v)))


def _r(v, nd=1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _split(s):
    return [x.strip() for x in s.split(",")] if s else []


def _fmt_season(code):
    return season_label(code) if code else code


class SoccerDB:
    def __init__(self, db_path=None, read_only=True):
        self.con = duckdb.connect(str(db_path or DB_PATH), read_only=read_only)

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ----- small lookup helpers ------------------------------------------- #
    def find_player_id(self, name: str, season: str = FOCUS_SEASON) -> int | None:
        """Case-insensitive partial match. Ranks exact, then whole-word (so
        'Saka' -> Bukayo Saka, not Wan-Bis*saka*), then substring; ties by
        minutes."""
        df = self.con.execute(
            """
            SELECT p.player_id, p.player_name, sum(ps.minutes) AS mins
            FROM players p JOIN player_season_stats ps USING(player_id)
            WHERE strip_accents(lower(p.player_name)) LIKE '%' || strip_accents(lower(?)) || '%'
            GROUP BY 1, 2
            ORDER BY
              (strip_accents(lower(p.player_name)) = strip_accents(lower(?))) DESC,
              -- whole-word match: pad with spaces and treat '-' as a boundary, so
              -- 'Mbappe' matches both 'Ethan Mbappe' and 'Kylian Mbappe-Lottin'
              -- (then minutes break the tie -> Kylian), not Wan-Bis*saka*.
              ((' ' || replace(strip_accents(lower(p.player_name)), '-', ' ') || ' ')
                 LIKE '% ' || strip_accents(lower(?)) || ' %') DESC,
              mins DESC
            LIMIT 1
            """,
            [name, name, name],
        ).fetchone()
        return None if df is None else int(df[0])

    def find_team_id(self, name: str) -> int | None:
        row = self.con.execute(
            "SELECT team_id FROM teams "
            "WHERE strip_accents(lower(team_name)) LIKE strip_accents(lower('%'||?||'%')) LIMIT 1",
            [name],
        ).fetchone()
        return None if row is None else int(row[0])

    # ----- use case 1: player statistics ---------------------------------- #
    def player_statistics(self, player: str, season: str = FOCUS_SEASON) -> pd.DataFrame:
        """Understat core stats + FotMob enrichment (dribbles, tackles,
        interceptions, big chances, pass completion) where available."""
        pid = self.find_player_id(player, season)
        if pid is None:
            return pd.DataFrame()
        return self.con.execute(
            """
            SELECT pl.player_name, l.league_name AS league, t.team_name AS team, ps.season,
                   ps.position, ps.position_group,
                   ps.matches, ps.minutes, ps.goals, ps.assists,
                   ps.goals + ps.assists AS goal_contributions,
                   ps.shots, ps.key_passes AS chances_created,
                   ps.xg, ps.np_xg, ps.xa, ps.xg_chain, ps.xg_buildup,
                   ps.goals_per90, ps.assists_per90, ps.xg_per90, ps.xa_per90,
                   ps.key_passes_per90,
                   -- FotMob enrichment (NULL if no match):
                   e.big_chances_created, e.big_chances_missed,
                   e.dribbles_completed, e.dribble_success_pct,
                   e.tackles, e.interceptions, e.recoveries,
                   e.duels_won, e.duels_won_pct,
                   e.passes_completed, e.pass_accuracy_pct, e.fotmob_rating
            FROM player_season_stats ps
            JOIN players pl USING(player_id)
            JOIN teams   t  USING(team_id)
            JOIN leagues l  ON l.league_key = ps.league_key
            LEFT JOIN player_enrichment e
                   ON e.player_id = ps.player_id AND e.season = ps.season
                  AND e.league_key = ps.league_key
            WHERE ps.player_id = ? AND ps.season = ?
            ORDER BY ps.minutes DESC
            """,
            [pid, season],
        ).df()

    # ----- use case 2: player classification ------------------------------ #
    def player_classification(self, player: str, season: str = FOCUS_SEASON) -> pd.DataFrame:
        pid = self.find_player_id(player, season)
        if pid is None:
            return pd.DataFrame()
        return self.con.execute(
            """
            SELECT pl.player_name, r.league_key, r.season, r.position_group,
                   r.rating, r.classification, r.rank_in_group,
                   round(r.percentile_in_group * 100, 1) AS percentile
            FROM player_ratings r JOIN players pl USING(player_id)
            WHERE r.player_id = ? AND r.season = ?
            ORDER BY r.minutes DESC
            """,
            [pid, season],
        ).df()

    def best_in_position(self, league_key: str, position_group: str,
                         season: str = FOCUS_SEASON, limit: int = 10) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT r.rank_in_group AS rank, pl.player_name, t.team_name AS team,
                   r.rating, r.classification, ps.goals, ps.assists, round(ps.xg,1) AS xg
            FROM player_ratings r
            JOIN players pl USING(player_id)
            JOIN player_season_stats ps
              ON ps.player_id=r.player_id AND ps.season=r.season AND ps.league_key=r.league_key
            JOIN teams t ON t.team_id = ps.team_id
            WHERE r.league_key = ? AND r.position_group = ? AND r.season = ?
            ORDER BY r.rank_in_group LIMIT ?
            """,
            [league_key, position_group, season, limit],
        ).df()

    # ----- use case 3: player profile ------------------------------------- #
    def player_profile(self, player: str) -> dict:
        pid = self.find_player_id(player)
        if pid is None:
            return {}
        head = self.con.execute(
            "SELECT player_name, primary_position, position_group FROM players WHERE player_id=?",
            [pid],
        ).fetchone()
        # Career timeline (domestic + UCL merged per season) from v_player_career.
        career = self.con.execute(
            "SELECT season, team, competitions, games, minutes, goals, assists "
            "FROM v_player_career WHERE player_id = ? ORDER BY season",
            [pid],
        ).df()
        # Market value (Transfermarkt) + strengths/weaknesses/areas (rating engine).
        mv = self.con.execute(
            "SELECT market_value_eur FROM player_market_value WHERE player_id=? AND season=?",
            [pid, FOCUS_SEASON],
        ).fetchone()
        swot = self.con.execute(
            "SELECT rating, classification, strengths, weaknesses, areas_of_improvement, "
            "detailed_position FROM v_player_profile_full WHERE player_id=?",
            [pid],
        ).fetchone()
        return {
            "player_name": head[0],
            "main_position": head[1],
            "position_group": head[2],
            "detailed_position": swot[5] if swot else None,  # FotMob LW/RW/LB/RB/CAM/...
            "market_value_eur": mv[0] if mv else None,
            "rating": swot[0] if swot else None,
            "classification": swot[1] if swot else None,
            "strengths": swot[2] if swot else None,
            "weaknesses": swot[3] if swot else None,
            "areas_of_improvement": swot[4] if swot else None,
            "career": career,           # seasons present in this warehouse
        }

    # ----- use case 4: cross-year progression ----------------------------- #
    # README default comparison stats per position group. FotMob-sourced stats
    # (dribbles/big-chances/passes/duels/tackles/interceptions/recoveries) are
    # only populated from 2020/21 on; earlier seasons show goals/assists only.
    DEFAULT_PROGRESSION_STATS = {
        "FWD": ["ga_per90", "dribbles_completed", "chances_created"],
        "MID": ["ga_per90", "big_chances_created", "passes_completed", "duels_won"],
        "DEF": ["tackles", "interceptions", "duels_won", "recoveries"],
        "GK":  ["ga_per90", "pass_accuracy_pct"],
    }
    _PROGRESSION_BASE = ["season", "team", "games", "minutes", "goals", "assists"]

    def player_progression(self, player: str, stats: list[str] | None = None) -> pd.DataFrame:
        pid = self.find_player_id(player)
        if pid is None:
            return pd.DataFrame()
        grp = self.con.execute(
            "SELECT position_group FROM players WHERE player_id=?", [pid]
        ).fetchone()[0]
        # only allow real columns of the season-stats view (guards custom input)
        allowed = {r[1] for r in self.con.execute(
            "PRAGMA table_info('v_player_season_stats')").fetchall()}
        cols = stats or self.DEFAULT_PROGRESSION_STATS.get(grp, ["ga_per90", "goals", "assists"])
        cols = [c for c in cols if c in allowed and c not in self._PROGRESSION_BASE]
        select_cols = ", ".join(self._PROGRESSION_BASE + cols)
        return self.con.execute(
            f"SELECT {select_cols} FROM v_player_season_stats "
            "WHERE player_id = ? ORDER BY season",
            [pid],
        ).df()

    # ----- use case 5: player comparison ---------------------------------- #
    # FotMob-enrichment stat columns (used by stat_leaders to validate input).
    ENRICH_STATS = {
        "big_chances_created", "big_chances_missed", "dribbles_completed",
        "dribble_success_pct", "tackles", "interceptions", "recoveries",
        "passes_completed", "pass_accuracy_pct", "fotmob_rating",
        "duels_won", "duels_won_pct",
    }

    def compare_players(self, players: list[str], season: str = FOCUS_SEASON,
                        stats: list[str] | None = None) -> pd.DataFrame:
        """Compare players side-by-side for one season on custom stats, or the
        same position-based default stats as cross-year progression (use case 4).
        Pulls from v_player_season_stats so both sources are already merged."""
        ids = [i for i in (self.find_player_id(p, season) for p in players) if i is not None]
        if not ids:
            return pd.DataFrame()
        groups = [r[0] for r in self.con.execute(
            f"SELECT position_group FROM players WHERE player_id IN ({','.join(['?']*len(ids))})",
            ids).fetchall()]
        allowed = {r[1] for r in self.con.execute(
            "PRAGMA table_info('v_player_season_stats')").fetchall()}
        if stats:
            cols = [c for c in stats if c in allowed]
        elif len(set(groups)) == 1:                      # all same position -> its defaults
            cols = self.DEFAULT_PROGRESSION_STATS.get(groups[0], [])
        else:                                            # mixed positions -> generic set
            cols = ["ga_per90", "goals", "assists", "chances_created"]
        base = ["games", "minutes", "goals", "assists"]
        statcols = base + [c for c in cols if c in allowed and c not in base]
        select_cols = ", ".join(f"v.{c}" for c in statcols)
        placeholders = ",".join(["?"] * len(ids))
        df = self.con.execute(
            f"""
            SELECT pl.player_name, v.team, pl.position_group, {select_cols}
            FROM v_player_season_stats v JOIN players pl USING(player_id)
            WHERE v.player_id IN ({placeholders}) AND v.season = ?
            ORDER BY v.minutes DESC
            """,
            ids + [season],
        ).df()
        # one row per player, transposed so stats are rows and players are columns
        df = df.drop_duplicates("player_name", keep="first").set_index("player_name")
        return df.T

    def stat_leaders(self, stat: str, league_key: str | None = None,
                     season: str = FOCUS_SEASON, limit: int = 10,
                     min_minutes: int = 600) -> pd.DataFrame:
        """League leaders for any FotMob enrichment stat (e.g. 'tackles',
        'big_chances_created', 'interceptions')."""
        if stat not in self.ENRICH_STATS:
            raise ValueError(f"{stat} is not an enrichment stat; choose from {sorted(self.ENRICH_STATS)}")
        where = "e.season = ? AND e.minutes_played >= ?"
        params: list = [season, min_minutes]
        if league_key:
            where += " AND e.league_key = ?"
            params.append(league_key)
        return self.con.execute(
            f"""
            SELECT pl.player_name, t.team_name AS team, l.league_name AS league,
                   e.{stat} AS {stat}, e.minutes_played AS minutes, e.fotmob_rating
            FROM player_enrichment e
            JOIN players pl USING(player_id)
            JOIN leagues l ON l.league_key = e.league_key
            LEFT JOIN player_season_stats ps
                   ON ps.player_id = e.player_id AND ps.season = e.season
                  AND ps.league_key = e.league_key
            LEFT JOIN teams t ON t.team_id = ps.team_id
            WHERE {where} AND e.{stat} IS NOT NULL
            ORDER BY e.{stat} DESC LIMIT {int(limit)}
            """,
            params,
        ).df()

    # ----- use case 6: team performance / standings ----------------------- #
    def league_standings(self, league_key: str, season: str = FOCUS_SEASON) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT s.league_position AS pos, t.team_name AS team,
                   s.matches_played AS mp, s.wins AS w, s.draws AS d, s.losses AS l,
                   s.goals_for AS gf, s.goals_against AS ga, s.goal_difference AS gd,
                   s.points AS pts, s.xg_for, s.xg_against, s.expected_points AS xpts
            FROM team_season_stats s JOIN teams t USING(team_id)
            WHERE s.league_key = ? AND s.season = ?
            ORDER BY s.league_position
            """,
            [league_key, season],
        ).df()

    def team_form(self, team: str, season: str = FOCUS_SEASON, last: int = 5) -> pd.DataFrame:
        tid = self.find_team_id(team)
        if tid is None:
            return pd.DataFrame()
        return self.con.execute(
            """
            SELECT tms.match_date::DATE AS date,
                   CASE WHEN tms.is_home THEN 'H' ELSE 'A' END AS venue,
                   opp.team_name AS opponent,
                   tms.goals_for AS gf, tms.goals_against AS ga,
                   CASE WHEN tms.points=3 THEN 'W' WHEN tms.points=1 THEN 'D' ELSE 'L' END AS result,
                   round(tms.xg_for,2) AS xg_for, round(tms.xg_against,2) AS xg_against
            FROM team_match_stats tms JOIN teams opp ON opp.team_id = tms.opponent_team_id
            WHERE tms.team_id = ? AND tms.season = ? AND tms.goals_for IS NOT NULL
            ORDER BY tms.match_date DESC LIMIT ?
            """,
            [tid, season, last],
        ).df()

    # ----- use case 7: team information ----------------------------------- #
    def team_info(self, team: str, season: str = FOCUS_SEASON) -> dict:
        tid = self.find_team_id(team)
        if tid is None:
            return {}
        head = self.con.execute(
            """
            SELECT t.team_name, t.team_code, l.league_name, l.country
            FROM teams t JOIN leagues l USING(league_key) WHERE t.team_id=?
            """,
            [tid],
        ).fetchone()
        squad = self.con.execute(
            """
            SELECT pl.player_name, ps.position_group, ps.matches, ps.minutes,
                   ps.goals, ps.assists
            FROM player_season_stats ps JOIN players pl USING(player_id)
            WHERE ps.team_id = ? AND ps.season = ?
            ORDER BY ps.minutes DESC
            """,
            [tid, season],
        ).df()
        return {
            "team_name": head[0],
            "team_code": head[1],
            "league": head[2],
            "country": head[3],
            "manager": None,   # not available from Understat (see NOTES.md)
            "venue": None,     # not available from Understat (see NOTES.md)
            "squad": squad,
        }

    # ----- use case 8: search --------------------------------------------- #
    def search_players(self, query: str, season: str = FOCUS_SEASON, limit: int = 10) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT pl.player_name, t.team_name AS team, l.league_name AS league,
                   ps.position_group, ps.goals, ps.assists, ps.minutes
            FROM player_season_stats ps
            JOIN players pl USING(player_id)
            JOIN teams t USING(team_id)
            JOIN leagues l ON l.league_key = ps.league_key
            WHERE strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))
              AND ps.season = ?
            ORDER BY ps.minutes DESC LIMIT ?
            """,
            [query, season, limit],
        ).df()

    def search_teams(self, query: str) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT t.team_name, t.team_code, l.league_name AS league, l.country
            FROM teams t JOIN leagues l USING(league_key)
            WHERE strip_accents(lower(t.team_name)) LIKE strip_accents(lower('%'||?||'%'))
            ORDER BY t.team_name
            """,
            [query],
        ).df()

    def search_matches(self, team_a: str, team_b: str, season: str = FOCUS_SEASON) -> pd.DataFrame:
        """Head-to-head fixtures between two teams, most recent first."""
        ta, tb = self.find_team_id(team_a), self.find_team_id(team_b)
        if ta is None or tb is None:
            return pd.DataFrame()
        return self.con.execute(
            """
            SELECT m.match_date::DATE AS date, h.team_name AS home, a.team_name AS away,
                   m.home_goals, m.away_goals, round(m.home_xg,2) AS home_xg,
                   round(m.away_xg,2) AS away_xg
            FROM matches m
            JOIN teams h ON h.team_id = m.home_team_id
            JOIN teams a ON a.team_id = m.away_team_id
            WHERE m.season = ?
              AND ((m.home_team_id=? AND m.away_team_id=?) OR (m.home_team_id=? AND m.away_team_id=?))
              AND m.is_result
            ORDER BY m.match_date DESC
            """,
            [season, ta, tb, tb, ta],
        ).df()

    # ----- web UI bundles (Atlastra frontend) ----------------------------- #
    # Group the rating-engine metric_labels into the 6 radar axes the UI shows.
    RADAR_AXES = {
        "Chance Creation": ["shot creation", "key passes", "goal creation",
                            "expected assists", "chances", "crosses to box"],
        "Progression": ["progressive passes", "progressive carries",
                        "progressive receptions", "progressive pass accuracy"],
        "Passing": ["pass completion %", "forward pass %", "passes into final third"],
        "Finishing": ["non-penalty xG", "non-penalty goals", "finishing",
                      "shots on target", "touches in box"],
        "Defending": ["tackles + interceptions", "tackle win %", "recoveries",
                      "interceptions (adj)", "tackles (adj)", "blocks", "clearances"],
        "Dribbling": ["take-ons", "take-on %", "aerial duels %"],
    }

    def web_overview(self) -> dict:
        row = self.con.execute("""
            SELECT (SELECT COUNT(*) FROM leagues),
                   (SELECT COUNT(*) FROM teams),
                   (SELECT COUNT(DISTINCT player_id) FROM player_season_stats),
                   (SELECT COUNT(*) FROM matches)
        """).fetchone()
        return {"leagues": row[0], "teams": row[1], "players": row[2],
                "matches": row[3], "stats_tracked": 250}

    def web_rankings(self, limit: int = 10, season: str = FOCUS_SEASON) -> list[dict]:
        df = self.con.execute("""
            SELECT player, team, position_group, rating
            FROM player_ratings_v2 WHERE season = ?
            ORDER BY rating DESC, percentile DESC LIMIT ?
        """, [season, limit]).df()
        return [{"rank": i + 1, "player": r.player, "team": r.team,
                 "position": r.position_group, "rating": int(r.rating)}
                for i, r in enumerate(df.itertuples())]

    # tab groups for the Players directory -> rating position_groups
    PLAYER_GROUPS = {"FWD": ["ST", "W"], "MID": ["AM", "CM", "DM"],
                     "DEF": ["FB", "CB"], "GK": ["GK"]}

    def web_players(self, group: str = "all", search: str | None = None,
                    limit: int = 24, season: str = FOCUS_SEASON) -> list[dict]:
        """Top-rated players for the directory grid: full name, team, position,
        rating/classification, market value and this season's G/A. Uses
        v_player_profile_full (player_id-keyed, full names)."""
        where, params = ["f.player_id IS NOT NULL"], [season]
        if group and group != "all" and group in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[group]
            where.append(f"f.main_position IN ({','.join(['?'] * len(gs))})")
            params += gs
        if search:
            where.append("strip_accents(lower(f.player)) LIKE strip_accents(lower('%'||?||'%'))")
            params.append(search)
        params.append(limit)
        # use the Understat full name (pl.player_name) -- f.player is datamb's
        # abbreviated form ('K. Mbappé') which find_player_id can't resolve.
        df = self.con.execute(f"""
            SELECT pl.player_name AS player, f.team,
                   COALESCE(f.detailed_position, f.main_position) AS position, f.rating,
                   f.classification, f.market_value_eur, v.goals, v.assists
            FROM v_player_profile_full f
            JOIN players pl ON pl.player_id = f.player_id
            LEFT JOIN v_player_season_stats v
                   ON v.player_id = f.player_id AND v.season = ?
            WHERE {' AND '.join(where)}
            ORDER BY f.rating DESC, f.market_value_eur DESC NULLS LAST
            LIMIT ?
        """, params).df()
        return [{"player": r.player, "team": r.team, "position": r.position,
                 "rating": _i(r.rating), "classification": r.classification,
                 "market_value_eur": None if pd.isna(r.market_value_eur) else float(r.market_value_eur),
                 "goals": _i(r.goals), "assists": _i(r.assists)}
                for r in df.itertuples()]

    def web_spotlight(self, season: str = FOCUS_SEASON) -> dict:
        def top(col, tbl="v_player_season_stats", rnd=0):
            r = self.con.execute(
                f"SELECT pl.player_name, v.{col} AS val FROM {tbl} v "
                "JOIN players pl USING(player_id) "
                f"WHERE v.season = ? AND v.{col} IS NOT NULL ORDER BY v.{col} DESC LIMIT 1",
                [season]).fetchone()
            return {"player": r[0], "value": round(float(r[1]), rnd) if rnd else int(r[1])} if r else None
        return {
            "top_scorer": top("goals"), "top_assists": top("assists"),
            "most_xg": top("xg", rnd=1), "most_chances": top("chances_created"),
            "most_dribbles": top("dribbles_completed"),
        }

    def web_standings(self, league_key: str, season: str = FOCUS_SEASON, limit: int = 6) -> list[dict]:
        df = self.league_standings(league_key, season).head(limit)
        out = []
        for r in df.itertuples():
            form = self.con.execute("""
                SELECT CASE WHEN points=3 THEN 'W' WHEN points=1 THEN 'D' ELSE 'L' END
                FROM team_match_stats tms JOIN teams t USING(team_id)
                WHERE t.team_name = ? AND tms.season = ? AND tms.goals_for IS NOT NULL
                ORDER BY tms.match_date DESC LIMIT 5
            """, [r.team, season]).df().iloc[:, 0].tolist()
            out.append({"pos": int(r.pos), "team": r.team, "p": int(r.mp),
                        "w": int(r.w), "d": int(r.d), "l": int(r.l),
                        "gd": int(r.gd), "pts": int(r.pts), "form": list(reversed(form))})
        return out

    def web_player(self, name: str, career_stat: str = "xa") -> dict:
        prof = self.player_profile(name)
        if not prof:
            return {}
        pid = self.find_player_id(name)
        season = FOCUS_SEASON
        # current-season tiles from the COMBINED (domestic + UCL) per-player view
        t = self.con.execute("""
            SELECT games, goals, assists, xg, xa, chances_created, big_chances_created,
                   dribbles_completed, minutes, pass_accuracy_pct
            FROM v_stats_combined_player WHERE player_id = ? AND season = ?
        """, [pid, season]).fetchone()
        # League + UCL ratings (common-metric, comparable; see rate_combined.py)
        cr = self.con.execute("""
            SELECT scope, rating, classification, percentile, minutes
            FROM player_ratings_combined WHERE player_id = ? AND season = ?
        """, [pid, season]).df()
        ratings = {r.scope: {"rating": _i(r.rating), "classification": r.classification,
                             "percentile": _i(r.percentile), "minutes": _i(r.minutes)}
                   for r in cr.itertuples()}
        tiles = {}
        if t:
            mins = t[8] or 0
            p90 = lambda v: _r((v or 0) / mins * 90, 2) if mins else None  # noqa: E731
            tiles = {"apps": _i(t[0]),            # Apps stays a count
                     "goals": p90(t[1]), "assists": p90(t[2]),
                     "xg": p90(t[3]), "xa": p90(t[4]),
                     "chances_created": p90(t[5]), "big_chances_created": p90(t[6]),
                     "dribbles_per90": p90(t[7]),
                     "pass_accuracy": _i(t[9])}    # already a %
        # percentile radar from the profile metrics
        pm = self.con.execute(
            "SELECT metric_label, percentile FROM player_profile_metrics WHERE player_id = ?",
            [pid]).df()
        pcts = dict(zip(pm.metric_label, pm.percentile))
        radar = []
        for axis, keys in self.RADAR_AXES.items():
            vals = [pcts[k] for k in keys if k in pcts]
            radar.append({"axis": axis, "value": round(sum(vals) / len(vals)) if vals else None})
        # career progression of one stat (+ current team = latest season's team)
        prog = self.player_progression(name, stats=[career_stat])
        career_stat = career_stat if career_stat in prog.columns else "ga_per90"
        career = [{"season": _fmt_season(r.season), "value": _r(getattr(r, career_stat))}
                  for r in prog.itertuples() if pd.notna(getattr(r, career_stat))]
        team = prog.iloc[-1]["team"] if len(prog) else None
        # rank-in-group + percentile (player_ratings_v2 is datamb-name keyed; bridge
        # via player_profile_metrics which carries both datamb name and player_id)
        ctx = self.con.execute("""
            SELECT r.rank_in_group, r.percentile FROM player_ratings_v2 r
            JOIN (SELECT DISTINCT player, player_id FROM player_profile_metrics) m
              ON m.player = r.player AND r.position_group = (
                 SELECT position_group FROM player_profile_metrics WHERE player_id = ? LIMIT 1)
            WHERE m.player_id = ? LIMIT 1
        """, [pid, pid]).fetchone()
        bio = self.con.execute(
            "SELECT nationality, country_code, date_of_birth, fotmob_age "
            "FROM player_bio WHERE player_id=?", [pid]).fetchone()
        return {
            "name": prof["player_name"], "team": team,
            "position_group": prof["position_group"],
            "detailed_position": prof.get("detailed_position"),  # LW/RW/LB/RB/CAM/... (FotMob)
            "age": self._player_age(prof["player_name"]) or (bio[3] if bio else None),
            "nationality": bio[0] if bio else None,
            "country_code": bio[1] if bio else None,
            "date_of_birth": bio[2] if bio else None,
            "market_value_eur": prof["market_value_eur"],
            "rating": prof["rating"], "classification": prof["classification"],
            "rank_in_group": ctx[0] if ctx else None,
            "percentile": round(ctx[1]) if ctx and ctx[1] is not None else None,
            "ratings": ratings,  # {"league": {...}, "ucl": {...}}  common-metric
            "tiles": tiles, "radar": radar,
            "career_stat": career_stat, "career": career,
            "strengths": _split(prof["strengths"]),
            "weaknesses": _split(prof["weaknesses"]),
            "areas_of_improvement": _split(prof["areas_of_improvement"]),
        }

    def _player_age(self, datamb_name: str) -> int | None:
        r = self.con.execute(
            "SELECT age FROM player_wyscout WHERE player = ? AND age IS NOT NULL LIMIT 1",
            [datamb_name]).fetchone()
        return int(r[0]) if r else None
