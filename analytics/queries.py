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

try:
    from analytics.archetype_defs import ARCHETYPES
except ModuleNotFoundError:  # pragma: no cover
    from archetype_defs import ARCHETYPES


# small coercion helpers for the web bundles (JSON-friendly, NaN-safe)
def _i(v):
    return None if v is None or pd.isna(v) else int(round(float(v)))


def _r(v, nd=1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _split(s):
    return [x.strip() for x in s.split(",")] if s else []


def _fmt_season(code):
    return season_label(code) if code else code


# FotMob image CDNs -- crests keyed by team id, photos by player id.
# Set True to prefer licensed Wikimedia Commons photos (table player_image, built
# by pipeline.load_wikimedia_images) over the FotMob CDN, with per-photo credits.
# Off for now -- the app shows FotMob headshots; flip to re-enable.
USE_LICENSED_PHOTOS = False
FOTMOB_PLAYER_IMG = "https://images.fotmob.com/image_resources/playerimages/{}.png"
FOTMOB_TEAM_IMG = "https://images.fotmob.com/image_resources/logo/teamlogo/{}.png"


def _norm_team(s):
    """Accent/punctuation-folded team name for logo lookup (mirrors load_team_logos)."""
    import unicodedata
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s)
    for junk in (" fc", " cf", " afc", " calcio", " 1899"):
        s = s.replace(junk, " ")
    return " ".join(s.split())


class SoccerDB:
    def __init__(self, db_path=None, read_only=True):
        self.con = duckdb.connect(str(db_path or DB_PATH), read_only=read_only)
        self._logo_map = None
        self._wiki_photos = None

    def team_logo(self, name):
        """Crest URL for a team display name, or None. Resolves by normalized name
        against BOTH the FotMob name and our team_name, so it works whether the
        UI shows the Understat or FotMob spelling."""
        if self._logo_map is None:
            self._logo_map = {}
            try:
                rows = self.con.execute(
                    "SELECT fotmob_team_id, fotmob_name, team_name FROM team_logos "
                    "WHERE fotmob_team_id IS NOT NULL").fetchall()
            except Exception:  # noqa: BLE001 -- table may not exist yet
                rows = []
            for fid, fname, tname in rows:
                for n in (fname, tname):
                    k = _norm_team(n)
                    if k:
                        self._logo_map.setdefault(k, fid)
        fid = self._logo_map.get(_norm_team(name))
        return FOTMOB_TEAM_IMG.format(fid) if fid else None

    def player_photo(self, fotmob_player_id):
        """Photo URL for a player, keyed by FotMob id. Prefers a licensed
        Wikimedia Commons image (table player_image, backfilled by
        load_wikimedia_images) and falls back to the FotMob CDN for anyone
        Commons doesn't cover."""
        if fotmob_player_id is None or pd.isna(fotmob_player_id):
            return None
        fid = int(fotmob_player_id)
        if not USE_LICENSED_PHOTOS:
            return FOTMOB_PLAYER_IMG.format(fid)
        if self._wiki_photos is None:
            self._wiki_photos = {}
            try:
                rows = self.con.execute(
                    "SELECT fotmob_player_id, image_url FROM player_image "
                    "WHERE fotmob_player_id IS NOT NULL AND image_url IS NOT NULL"
                ).fetchall()
            except Exception:  # noqa: BLE001 -- table not built yet
                rows = []
            self._wiki_photos = {int(f): u for f, u in rows}
        return self._wiki_photos.get(fid) or FOTMOB_PLAYER_IMG.format(fid)

    def _photo_credit(self, pid: int) -> dict | None:
        """Attribution for a player's licensed Commons photo (None if the photo
        is the FotMob fallback or the table isn't built)."""
        if not USE_LICENSED_PHOTOS:
            return None
        try:
            r = self.con.execute(
                "SELECT credit, license, file_page FROM player_image "
                "WHERE player_id = ? AND image_url IS NOT NULL", [pid]).fetchone()
        except Exception:  # noqa: BLE001 -- table not built yet
            return None
        if not r:
            return None
        return {"credit": r[0], "license": r[1], "page": r[2]}

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
    # Labels must match those stored in player_radar_metrics (pipeline.profile
    # PRETTY) -- every player is scored on all of these, so no axis is ever empty.
    # An entry is either a label (equal weight) or a (label, weight) tuple; missing
    # metrics drop out and the remaining weights renormalise (see _radar_values).
    RADAR_AXES = {
        "Chance Creation": ["shot creation", "key passes", "goal creation",
                            "expected assists", "crosses to box"],
        "Progression": ["progressive passes", "progressive carries",
                        "progressive receptions", "progressive pass accuracy"],
        "Passing": ["pass completion %", "forward pass %", "passes into final third"],
        "Finishing": ["non-penalty xG", "non-penalty goals", "finishing",
                      "shots on target", "touches in box"],
        "Defending": ["tackles + interceptions", "tackle win %", "recoveries",
                      "interceptions (adj)", "blocks + clearances"],
        "Dribbling": [("take-ons", 0.8), ("take-on %", 0.2)],
    }

    def _radar_values(self, pcts: dict) -> list[tuple]:
        """(axis, value) per RADAR_AXES, value = weighted mean of the present
        metric percentiles (None if the player has none of an axis's metrics)."""
        out = []
        for axis, keys in self.RADAR_AXES.items():
            num = den = 0.0
            for k in keys:
                label, w = k if isinstance(k, tuple) else (k, 1.0)
                if label in pcts:
                    num += w * pcts[label]
                    den += w
            out.append((axis, round(num / den) if den else None))
        return out

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
        # combined-League rating (same engine the directory + profile use) so the
        # Top-10 rail agrees with the rest of the app; full names via players join.
        df = self.con.execute("""
            SELECT pl.player_name AS player, f.team,
                   COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                   c.rating, pe.fpid
            FROM player_ratings_combined c
            JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = c.player_id
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = c.player_id
            WHERE c.scope = 'league' AND c.season = ?
            ORDER BY c.rating DESC, c.percentile DESC LIMIT ?
        """, [season, limit]).df()
        return [{"rank": i + 1, "player": r.player, "team": r.team,
                 "position": r.position, "rating": int(r.rating),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for i, r in enumerate(df.itertuples())]

    # tab groups for the Players directory -> rating position_groups
    PLAYER_GROUPS = {"FWD": ["ST", "W"], "MID": ["AM", "CM", "DM"],
                     "DEF": ["FB", "CB"], "GK": ["GK"]}

    def web_players(self, group: str = "all", search: str | None = None,
                    limit: int = 24, season: str = FOCUS_SEASON,
                    scope: str = "league") -> list[dict]:
        """Top-rated players for the directory grid: full name, team, position,
        rating/classification, market value and this season's G/A. Uses
        v_player_profile_full (player_id-keyed, full names).

        scope='league' -> top-5-league players with the combined-League rating
        (same number the profile page leads with) and domestic G/A; players the
        combined engine doesn't cover (notably GKs) fall back to the datamb
        rating. scope='ucl' -> only Champions League players, showing the
        combined-UCL rating and their UCL G/A."""
        if scope == "ucl":
            return self._web_players_ucl(group, search, limit, season)
        if scope == "former":
            return self._web_players_former(group, search, limit)
        # params order matches the ?-placeholders below: v.season, c.season, [group], [search], limit
        where, params = ["f.player_id IS NOT NULL"], [season, season]
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
                   COALESCE(f.detailed_position, f.main_position) AS position,
                   COALESCE(c.rating, f.rating) AS rating,
                   COALESCE(c.classification, f.classification) AS classification,
                   f.market_value_eur, v.goals, v.assists, pe.fpid
            FROM v_player_profile_full f
            JOIN players pl ON pl.player_id = f.player_id
            LEFT JOIN v_player_season_stats v
                   ON v.player_id = f.player_id AND v.season = ?
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = f.player_id AND c.scope = 'league' AND c.season = ?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = f.player_id
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(c.rating, f.rating) DESC, f.market_value_eur DESC NULLS LAST
            LIMIT ?
        """, params).df()
        return [{"player": r.player, "team": r.team, "position": r.position,
                 "rating": _i(r.rating), "classification": r.classification,
                 "market_value_eur": None if pd.isna(r.market_value_eur) else float(r.market_value_eur),
                 "goals": _i(r.goals), "assists": _i(r.assists),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for r in df.itertuples()]

    def _web_players_ucl(self, group, search, limit, season) -> list[dict]:
        """UCL directory: players with a combined-UCL rating, showing that rating
        and their Champions League G/A (from v_stats_ucl)."""
        where = ["c.scope = 'ucl'", "c.season = ?"]
        params = [season, season]                     # u.season (JOIN), then c.season
        if group and group != "all" and group in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[group]
            where.append(f"COALESCE(f.main_position, pl.position_group) IN ({','.join(['?'] * len(gs))})")
            params += gs
        if search:
            where.append("strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))")
            params.append(search)
        params.append(limit)
        df = self.con.execute(f"""
            SELECT pl.player_name AS player, COALESCE(u.team, f.team) AS team,
                   COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                   c.rating, c.classification, f.market_value_eur,
                   u.goals, u.assists, pe.fpid
            FROM player_ratings_combined c
            JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = c.player_id
            LEFT JOIN v_stats_ucl u ON u.player_id = c.player_id AND u.season = ?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = c.player_id
            WHERE {' AND '.join(where)}
            ORDER BY c.rating DESC, f.market_value_eur DESC NULLS LAST
            LIMIT ?
        """, params).df()
        return [{"player": r.player, "team": r.team, "position": r.position,
                 "rating": _i(r.rating), "classification": r.classification,
                 "market_value_eur": None if pd.isna(r.market_value_eur) else float(r.market_value_eur),
                 "goals": _i(r.goals), "assists": _i(r.assists),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for r in df.itertuples()]

    @staticmethod
    def _tier(rating) -> str:
        """Absolute rating -> classification label (rank-free, for synthetic numbers
        like the combined rating; mirrors rate.py _classify's tiers)."""
        if rating is None:
            return ""
        if rating >= 90: return "World-Class"
        if rating >= 80: return "Elite"
        if rating >= 65: return "Above Average"
        if rating >= 50: return "Average"
        return "Below Average"

    def _web_players_former(self, group, search, limit) -> list[dict]:
        """'Former Players' directory: notable players whose last top-5-league
        season is before FOCUS_SEASON (left for another league, retired, etc. --
        Messi, Ronaldo, Suárez …). Each card shows that player's BEST former season
        by a COMBINED League+UCL rating (minutes-weighted blend of the two scope
        ratings -- same 'Combined' idea as the profile's stat scopes), with that
        season's advanced stats. Built on the per-season backfilled ratings +
        per-season positions ([[atlastra-webapp]], [[combined-ucl-league-ratings]])."""
        where = ["x.rn = 1", "x.lg_r IS NOT NULL"]
        params: list = [FOCUS_SEASON]                 # the < FOCUS_SEASON filter
        if group and group != "all" and group in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[group]
            where.append(f"x.position IN ({','.join(['?'] * len(gs))})")
            params += gs
        if search:
            where.append("strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))")
            params.append(search)
        params.append(limit)
        df = self.con.execute(f"""
            WITH latest AS (
                SELECT player_id, max(season) AS last_season
                FROM player_ratings_combined GROUP BY player_id),
            former AS (SELECT player_id FROM latest WHERE last_season < ?),
            seas AS (   -- pivot league & ucl scope rows into one row per season
                SELECT c.player_id, c.season,
                       max(CASE WHEN scope='league' THEN rating END)  AS lg_r,
                       max(CASE WHEN scope='league' THEN minutes END) AS lg_m,
                       max(CASE WHEN scope='ucl'    THEN rating END)  AS ucl_r,
                       max(CASE WHEN scope='ucl'    THEN minutes END) AS ucl_m,
                       max(CASE WHEN scope='league' THEN position_group END) AS position
                FROM player_ratings_combined c JOIN former USING (player_id)
                GROUP BY c.player_id, c.season),
            comb AS (   -- minutes-weighted League+UCL blend; pick each player's best
                SELECT *, CASE WHEN ucl_r IS NULL THEN lg_r
                               ELSE round((lg_r*lg_m + ucl_r*ucl_m)
                                          / NULLIF(lg_m + ucl_m, 0)) END AS comb_r
                FROM seas),
            x AS (
                SELECT *, row_number() OVER (PARTITION BY player_id
                           ORDER BY comb_r DESC, lg_m DESC) AS rn FROM comb)
            SELECT pl.player_name AS player, x.season, x.position,
                   x.comb_r AS rating, x.lg_r AS rating_league, x.ucl_r AS rating_ucl,
                   v.team, v.goals, v.assists, v.xg, v.xa,
                   v.key_passes, v.dribbles_completed, pe.fpid
            FROM x
            JOIN players pl USING (player_id)
            LEFT JOIN v_player_season_stats v
                   ON v.player_id = x.player_id AND v.season = x.season
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = x.player_id
            WHERE {' AND '.join(where)}
            -- players who didn't feature in the UCL that season sort to the bottom
            -- (their "combined" is league-only, so it isn't comparable to a true blend)
            ORDER BY (x.ucl_r IS NULL), x.comb_r DESC, v.goals DESC NULLS LAST
            LIMIT ?
        """, params).df()
        return [{"player": r.player, "team": r.team, "position": r.position,
                 "season": _fmt_season(r.season), "season_code": r.season,
                 "rating": _i(r.rating), "classification": self._tier(_i(r.rating)),
                 "rating_league": _i(r.rating_league), "rating_ucl": _i(r.rating_ucl),
                 "goals": _i(r.goals), "assists": _i(r.assists),
                 "xg": _r(r.xg, 1), "xa": _r(r.xa, 1),
                 "key_passes": _i(r.key_passes), "dribbles": _i(r.dribbles_completed),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for r in df.itertuples()]

    def web_spotlight(self, season: str = FOCUS_SEASON) -> dict:
        def top(col, tbl="v_player_season_stats", rnd=0):
            r = self.con.execute(
                f"SELECT pl.player_name, v.{col} AS val, v.team, "
                "  (SELECT max(fotmob_player_id) FROM player_enrichment e "
                "   WHERE e.player_id = v.player_id AND e.fotmob_player_id IS NOT NULL) AS fpid "
                f"FROM {tbl} v JOIN players pl USING(player_id) "
                f"WHERE v.season = ? AND v.{col} IS NOT NULL ORDER BY v.{col} DESC LIMIT 1",
                [season]).fetchone()
            return {"player": r[0], "value": round(float(r[1]), rnd) if rnd else int(r[1]),
                    "photo": self.player_photo(r[3]), "team_logo": self.team_logo(r[2])} if r else None
        return {
            "top_scorer": top("goals"), "top_assists": top("assists"),
            "most_xg": top("xg", rnd=1), "most_chances": top("chances_created"),
            "most_dribbles": top("dribbles_completed"),
        }

    def web_live(self, limit_recent: int = 40, limit_upcoming: int = 40) -> dict:
        """Live / upcoming / recent matches from the live_matches table
        (pipeline/load_live.py). Three buckets, each a flat list ordered for the UI;
        live first by kickoff, upcoming soonest-first, recent newest-first."""
        empty = {"live": [], "upcoming": [], "recent": [], "updated_at": None}
        try:
            df = self.con.execute("""
                SELECT event_id, tournament_key, tournament_name, tournament_group,
                       round_name, start_timestamp, status_type, status_desc, minute,
                       home_team, home_country, away_team, away_country,
                       home_score, away_score, winner_code,
                       CAST(updated_at AS VARCHAR) AS updated_at
                FROM live_matches
            """).df()
        except Exception:  # noqa: BLE001 -- table may not exist yet
            return empty
        if df.empty:
            return empty

        def match(r) -> dict:
            none = lambda v: None if pd.isna(v) else v
            return {
                "event_id": int(r.event_id),
                "competition": r.tournament_name, "group": r.tournament_group,
                "round": none(r.round_name), "kickoff_ts": int(r.start_timestamp),
                "status": r.status_type, "status_desc": none(r.status_desc),
                "minute": None if pd.isna(r.minute) else int(r.minute),
                "home": r.home_team, "home_logo": self.team_logo(r.home_team),
                "home_country": none(r.home_country),
                "home_score": None if pd.isna(r.home_score) else int(r.home_score),
                "away": r.away_team, "away_logo": self.team_logo(r.away_team),
                "away_country": none(r.away_country),
                "away_score": None if pd.isna(r.away_score) else int(r.away_score),
                "winner": None if pd.isna(r.winner_code) else int(r.winner_code),
            }

        live = [match(r) for r in df[df.status_type == "inprogress"]
                .sort_values("start_timestamp").itertuples()]
        upcoming = [match(r) for r in df[df.status_type == "notstarted"]
                    .sort_values("start_timestamp").head(limit_upcoming).itertuples()]
        recent = [match(r) for r in df[df.status_type == "finished"]
                  .sort_values("start_timestamp", ascending=False).head(limit_recent).itertuples()]
        return {"live": live, "upcoming": upcoming, "recent": recent,
                "updated_at": df["updated_at"].iloc[0]}

    def _team_form(self, team_name: str, season: str, n: int = 5) -> list:
        """Last-n results as W/D/L, oldest→newest (for a form string)."""
        form = self.con.execute("""
            SELECT CASE WHEN points=3 THEN 'W' WHEN points=1 THEN 'D' ELSE 'L' END
            FROM team_match_stats tms JOIN teams t USING(team_id)
            WHERE t.team_name = ? AND tms.season = ? AND tms.goals_for IS NOT NULL
            ORDER BY tms.match_date DESC LIMIT ?
        """, [team_name, season, n]).df().iloc[:, 0].tolist()
        return list(reversed(form))

    def web_standings(self, league_key: str, season: str = FOCUS_SEASON, limit: int = 6) -> list[dict]:
        df = self.league_standings(league_key, season).head(limit)
        return [{"pos": int(r.pos), "team": r.team, "p": int(r.mp),
                 "w": int(r.w), "d": int(r.d), "l": int(r.l),
                 "gd": int(r.gd), "pts": int(r.pts),
                 "form": self._team_form(r.team, season)}
                for r in df.itertuples()]

    # league key -> display name, for the Teams page tabs
    LEAGUE_TABS = [("ENG-Premier League", "Premier League"), ("ESP-La Liga", "La Liga"),
                   ("ITA-Serie A", "Serie A"), ("GER-Bundesliga", "Bundesliga"),
                   ("FRA-Ligue 1", "Ligue 1")]

    def web_leagues(self) -> list[dict]:
        return [{"key": k, "name": n} for k, n in self.LEAGUE_TABS]

    def web_league_table(self, league_key: str, season: str = FOCUS_SEASON) -> list[dict]:
        """Full league standings (use case 6) — every team, with crest, xG/xPts
        and recent form. Rows link to the team page."""
        df = self.league_standings(league_key, season)
        return [{"pos": int(r.pos), "team": r.team, "team_logo": self.team_logo(r.team),
                 "p": int(r.mp), "w": int(r.w), "d": int(r.d), "l": int(r.l),
                 "gf": int(r.gf), "ga": int(r.ga), "gd": int(r.gd), "pts": int(r.pts),
                 "xg_for": _r(r.xg_for, 1), "xg_against": _r(r.xg_against, 1),
                 "xpts": _r(r.xpts, 1), "form": self._team_form(r.team, season)}
                for r in df.itertuples()]

    def web_team(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """Team performance bundle (use case 6): standing, record, goals, xG/xPts,
        form, recent results and top scorers."""
        tid = self.find_team_id(name)
        if tid is None:
            return {}
        head = self.con.execute("""
            SELECT t.team_name, t.team_code, t.league_key, l.league_name, l.country
            FROM teams t JOIN leagues l USING(league_key) WHERE t.team_id = ?
        """, [tid]).fetchone()
        s = self.con.execute("""
            SELECT league_position, matches_played, wins, draws, losses, goals_for,
                   goals_against, goal_difference, points, xg_for, xg_against, expected_points
            FROM team_season_stats WHERE team_id = ? AND season = ?
        """, [tid, season]).fetchone()
        n_teams = self.con.execute(
            "SELECT COUNT(*) FROM team_season_stats WHERE league_key = ? AND season = ?",
            [head[2], season]).fetchone()[0]
        rf = self.team_form(head[0], season, last=8)
        results = [{"date": str(r.date), "venue": r.venue, "opponent": r.opponent,
                    "opponent_logo": self.team_logo(r.opponent),
                    "gf": int(r.gf), "ga": int(r.ga), "result": r.result,
                    "xg_for": _r(r.xg_for, 2), "xg_against": _r(r.xg_against, 2)}
                   for r in rf.itertuples()]
        scorers = self.con.execute("""
            SELECT pl.player_name, ps.goals, ps.assists
            FROM player_season_stats ps JOIN players pl USING(player_id)
            WHERE ps.team_id = ? AND ps.season = ?
            ORDER BY ps.goals DESC, ps.assists DESC LIMIT 5
        """, [tid, season]).df()
        # use case 7: manager + venue (FotMob), and full squad
        meta = self.con.execute(
            "SELECT manager, venue, city, capacity, opened, surface "
            "FROM team_meta WHERE team_id = ?", [tid]).fetchone()
        squad = self.con.execute("""
            SELECT pl.player_name, ps.position_group, ps.matches, ps.minutes,
                   ps.goals, ps.assists, pe.fpid, bio.fotmob_age
            FROM player_season_stats ps
            JOIN players pl USING(player_id)
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = ps.player_id
            LEFT JOIN player_bio bio ON bio.player_id = ps.player_id
            WHERE ps.team_id = ? AND ps.season = ?
            ORDER BY ps.minutes DESC
        """, [tid, season]).df()
        ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
        squad_rows = [{"player": r.player_name, "position_group": r.position_group,
                       "apps": _i(r.matches), "minutes": _i(r.minutes),
                       "goals": _i(r.goals), "assists": _i(r.assists),
                       "age": _i(r.fotmob_age), "photo": self.player_photo(r.fpid)}
                      for r in squad.itertuples()]
        squad_rows.sort(key=lambda p: (ORDER.get(p["position_group"], 4), -(p["minutes"] or 0)))
        return {
            "team": head[0], "team_code": head[1], "league_key": head[2],
            "league": head[3], "country": head[4], "team_logo": self.team_logo(head[0]),
            "season": _fmt_season(season), "n_teams": n_teams,
            "manager": meta[0] if meta else None,
            "venue": meta[1] if meta else None, "city": meta[2] if meta else None,
            "capacity": _i(meta[3]) if meta else None,
            "opened": _i(meta[4]) if meta else None,
            "surface": meta[5] if meta else None,
            "stats": None if not s else {
                "position": _i(s[0]), "played": _i(s[1]), "wins": _i(s[2]),
                "draws": _i(s[3]), "losses": _i(s[4]), "goals_for": _i(s[5]),
                "goals_against": _i(s[6]), "goal_difference": _i(s[7]), "points": _i(s[8]),
                "xg_for": _r(s[9], 1), "xg_against": _r(s[10], 1), "xpts": _r(s[11], 1)},
            "form": self._team_form(head[0], season),
            "results": results,
            "top_scorers": [{"player": r.player_name, "goals": _i(r.goals),
                             "assists": _i(r.assists)} for r in scorers.itertuples()],
            "squad": squad_rows,
        }

    def web_search(self, query: str, season: str = FOCUS_SEASON, limit: int = 12) -> dict:
        """Use case 8: unified player + team search for one query string."""
        q = (query or "").strip()
        if not q:
            return {"query": q, "players": [], "teams": []}
        # Search ALL seasons (so former players surface too); show each player's
        # most-recent season's club/stats. Active players (current season) rank
        # first, then by most recent season, then minutes.
        pdf = self.con.execute("""
            WITH m AS (
                SELECT ps.player_id, pl.player_name, max(ps.season) AS last_season
                FROM player_season_stats ps JOIN players pl USING(player_id)
                WHERE strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))
                GROUP BY ps.player_id, pl.player_name)
            SELECT m.player_name, m.last_season,
                   arg_max(t.team_name, ps.minutes) AS team,
                   any_value(ps.position_group) AS position,
                   SUM(ps.goals) AS goals, SUM(ps.assists) AS assists,
                   SUM(ps.minutes) AS minutes, max(pe.fpid) AS fpid
            FROM m
            JOIN player_season_stats ps ON ps.player_id = m.player_id AND ps.season = m.last_season
            JOIN teams t USING(team_id)
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = m.player_id
            GROUP BY m.player_id, m.player_name, m.last_season
            ORDER BY (m.last_season = ?) DESC, m.last_season DESC, minutes DESC
            LIMIT ?
        """, [q, season, limit]).df()
        tdf = self.con.execute("""
            SELECT t.team_name, l.league_name AS league, l.country
            FROM teams t JOIN leagues l USING(league_key)
            WHERE strip_accents(lower(t.team_name)) LIKE strip_accents(lower('%'||?||'%'))
            ORDER BY t.team_name LIMIT 8
        """, [q]).df()
        return {
            "query": q,
            "players": [{"player": r.player_name, "team": r.team, "position": r.position,
                         "team_logo": self.team_logo(r.team), "photo": self.player_photo(r.fpid),
                         "goals": _i(r.goals), "assists": _i(r.assists),
                         "season": _fmt_season(r.last_season),
                         "former": r.last_season != season}
                        for r in pdf.itertuples()],
            "teams": [{"team": r.team_name, "league": r.league, "country": r.country,
                       "team_logo": self.team_logo(r.team_name)} for r in tdf.itertuples()],
        }

    def web_match_search(self, team_a: str, team_b: str, season: str = FOCUS_SEASON) -> dict:
        """Use case 8: head-to-head fixtures between two teams, most recent first."""
        ta, tb = self.find_team_id(team_a), self.find_team_id(team_b)
        if ta is None or tb is None:
            return {"team_a": team_a if ta else None, "team_b": team_b if tb else None,
                    "matches": []}
        names = dict(self.con.execute(
            "SELECT team_id, team_name FROM teams WHERE team_id IN (?, ?)", [ta, tb]).fetchall())
        df = self.search_matches(names[ta], names[tb], season)
        matches = [{"date": str(r.date), "home": r.home, "away": r.away,
                    "home_logo": self.team_logo(r.home), "away_logo": self.team_logo(r.away),
                    "home_goals": _i(r.home_goals), "away_goals": _i(r.away_goals),
                    "home_xg": _r(r.home_xg, 2), "away_xg": _r(r.away_xg, 2)}
                   for r in df.itertuples()]
        return {
            "team_a": {"name": names[ta], "logo": self.team_logo(names[ta])},
            "team_b": {"name": names[tb], "logo": self.team_logo(names[tb])},
            "matches": matches,
        }

    # ----- use case 10: player archetypes -------------------------------- #
    GROUP_LABELS = {"ST": "Strikers", "W": "Wingers", "AM": "Attacking Mids",
                    "CM": "Central Mids", "DM": "Defensive Mids", "FB": "Full-Backs",
                    "CB": "Centre-Backs", "GK": "Goalkeepers"}

    def _archetype_def(self, name: str):
        for grp, archs in ARCHETYPES.items():
            for a in archs:
                if a["name"].lower() == name.lower():
                    return grp, a
        return None, None

    def _player_tendencies(self, pid: int, top: int = 5, min_pct: int = 55) -> list:
        """Use case 9: the on-ball actions this player does most relative to position
        peers (highest-percentile TENDENCIES), as name + per-90 value + percentile."""
        df = self.con.execute(
            "SELECT tendency, value, percentile FROM player_tendencies "
            "WHERE player_id = ? AND percentile >= ? ORDER BY percentile DESC LIMIT ?",
            [pid, min_pct, top]).df()
        return [{"name": r.tendency, "value": _r(r.value, 2), "percentile": _i(r.percentile)}
                for r in df.itertuples()]

    def _player_heatmap(self, pid: int, season: str = FOCUS_SEASON):
        """Binned SofaScore season heatmap grid (rows×cols of 0-1 density), or None."""
        try:
            r = self.con.execute(
                "SELECT grid FROM player_heatmap WHERE player_id = ? AND season = ?",
                [pid, season]).fetchone()
        except Exception:  # noqa: BLE001 -- table not built yet
            return None
        if not r or not r[0]:
            return None
        import json
        try:
            return json.loads(r[0])
        except Exception:  # noqa: BLE001
            return None

    def _player_archetype(self, pid: int) -> dict:
        """Archetype label + fit + signature traits + similar players for a player."""
        a = self.con.execute(
            "SELECT position_group, archetype, fit, archetype2, fit2 "
            "FROM player_archetypes WHERE player_id = ?", [pid]).fetchone()
        if not a:
            return {}
        grp, defn = self._archetype_def(a[1])
        # signature traits: the role's "high" metrics where the player ranks well
        src = "player_profile_metrics" if a[0] == "GK" else "player_radar_metrics"
        pm = dict(self.con.execute(
            f"SELECT metric_label, percentile FROM {src} WHERE player_id = ?", [pid]).fetchall())
        traits = sorted(((m, pm[m]) for m in (defn["high"] if defn else []) if m in pm),
                        key=lambda x: -x[1])
        traits = [{"label": m, "pct": _i(p)} for m, p in traits if p is not None and p >= 55][:4]
        sim = self.con.execute("""
            SELECT pl.player_name, s.similarity,
                   COALESCE(f.detailed_position, f.main_position) AS position, f.team,
                   COALESCE(c.rating, f.rating) AS rating, pe.fpid
            FROM player_similar s JOIN players pl ON pl.player_id = s.similar_player_id
            LEFT JOIN v_player_profile_full f ON f.player_id = s.similar_player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = s.similar_player_id AND c.scope='league' AND c.season=?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = s.similar_player_id
            WHERE s.player_id = ? ORDER BY s.rank LIMIT 6
        """, [FOCUS_SEASON, pid]).df()
        return {
            "archetype": a[1], "fit": _i(a[2]),
            "archetype2": a[3], "fit2": _i(a[4]),
            "group": grp, "group_label": self.GROUP_LABELS.get(grp, grp),
            "blurb": defn["blurb"] if defn else None,
            "traits": traits,
            "similar": [{"player": r.player_name, "similarity": _r(r.similarity, 0),
                         "position": r.position, "team": r.team, "rating": _i(r.rating),
                         "photo": self.player_photo(r.fpid)} for r in sim.itertuples()],
        }

    def web_archetypes(self) -> list[dict]:
        """All archetypes grouped by position, with blurb, signature metrics and
        how many players carry each (for the explorer)."""
        cmap = dict(self.con.execute(
            "SELECT archetype, COUNT(*) FROM player_archetypes GROUP BY archetype").fetchall())
        return [{"group": grp, "group_label": self.GROUP_LABELS.get(grp, grp),
                 "archetypes": [{"name": a["name"], "blurb": a["blurb"],
                                 "signature": a["high"], "count": cmap.get(a["name"], 0)}
                                for a in archs]}
                for grp, archs in ARCHETYPES.items()]

    def web_team_of_season(self, season: str = FOCUS_SEASON, min_minutes: int = 1500) -> dict:
        """Best XI in a 4-3-3 by average match rating (FotMob/SofaScore, minutes-
        weighted across competitions), one player per detailed-position slot."""
        df = self.con.execute("""
            SELECT pl.player_name AS name,
                   COALESCE(f.detailed_position, f.main_position) AS pos,
                   cp.rating AS avg_rating, f.team, cp.minutes, pe.fpid,
                   COALESCE(c.rating, f.rating) AS atlastra
            FROM v_stats_combined_player cp JOIN players pl USING(player_id)
            JOIN v_player_profile_full f ON f.player_id = cp.player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = cp.player_id AND c.scope='league' AND c.season = cp.season
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = cp.player_id
            WHERE cp.season = ? AND cp.rating IS NOT NULL AND cp.minutes >= ?
            ORDER BY cp.rating DESC, cp.minutes DESC
        """, [season, min_minutes]).df()
        used = set()

        def pick(positions, n):
            out = []
            for r in df.itertuples():
                if len(out) >= n:
                    break
                if r.name not in used and r.pos in positions:
                    used.add(r.name)
                    out.append(r)
            return out

        # left-to-right per line: LB-CB-CB-RB ; LW-ST-RW
        lines = {"GK": pick({"GK"}, 1),
                 "DEF": pick({"LB"}, 1) + pick({"CB"}, 2) + pick({"RB"}, 1),
                 "MID": pick({"AM", "CM", "DM"}, 3),
                 "FWD": pick({"LW"}, 1) + pick({"ST"}, 1) + pick({"RW"}, 1)}

        def fmt(rows):
            return [{"player": r.name, "position": r.pos, "avg_rating": _r(r.avg_rating, 2),
                     "team": r.team, "team_logo": self.team_logo(r.team),
                     "photo": self.player_photo(r.fpid), "rating": _i(r.atlastra)}
                    for r in rows]
        return {"formation": "4-3-3", "season": _fmt_season(season),
                "lines": [{"label": k, "players": fmt(v)} for k, v in lines.items()]}

    def web_archetype(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """One archetype: definition + its top players (by fit, then rating)."""
        grp, defn = self._archetype_def(name)
        if not defn:
            return {}
        df = self.con.execute("""
            SELECT pl.player_name, a.fit,
                   COALESCE(f.detailed_position, f.main_position) AS position, f.team,
                   COALESCE(c.rating, f.rating) AS rating, pe.fpid,
                   f.team AS team2
            FROM player_archetypes a JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = a.player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = a.player_id AND c.scope='league' AND c.season=?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = a.player_id
            WHERE a.archetype = ?
            ORDER BY rating DESC NULLS LAST, a.fit DESC NULLS LAST LIMIT 24
        """, [season, defn["name"]]).df()
        return {
            "name": defn["name"], "group": grp,
            "group_label": self.GROUP_LABELS.get(grp, grp),
            "blurb": defn["blurb"], "signature": defn["high"],
            "players": [{"player": r.player_name, "fit": _i(r.fit), "position": r.position,
                         "team": r.team, "team_logo": self.team_logo(r.team),
                         "rating": _i(r.rating), "photo": self.player_photo(r.fpid)}
                        for r in df.itertuples()],
        }

    # ---- Scout / Player Finder (use case: discovery) ----------------------
    # key -> (v_player_season_stats column, label, fmt). fmt in int/f1/f2/pct.
    SCOUT_METRICS = {
        "rating": (None, "Atlastra Rating", "int"),
        "goals": ("goals", "Goals", "int"),
        "assists": ("assists", "Assists", "int"),
        "ga": ("ga", "Goals + Assists", "int"),
        "xg": ("xg", "xG", "f1"),
        "xa": ("xa", "xA", "f1"),
        "goals_per90": ("goals_per90", "Goals / 90", "f2"),
        "xa_per90": ("xa_per90", "xA / 90", "f2"),
        "ga_per90": ("ga_per90", "G+A / 90", "f2"),
        "key_passes": ("key_passes", "Key Passes", "int"),
        "chances_created": ("chances_created", "Chances Created", "int"),
        "big_chances_created": ("big_chances_created", "Big Chances Created", "int"),
        "dribbles_completed": ("dribbles_completed", "Dribbles", "int"),
        "tackles": ("tackles", "Tackles", "int"),
        "interceptions": ("interceptions", "Interceptions", "int"),
        "recoveries": ("recoveries", "Recoveries", "int"),
        "duels_won_pct": ("duels_won_pct", "Duels Won %", "pct"),
        "pass_accuracy_pct": ("pass_accuracy_pct", "Pass Accuracy %", "pct"),
    }

    def web_scout(self, pos: str = "all", metric: str = "rating",
                  max_value_m: float = 0, min_minutes: int = 450,
                  max_age: int = 0, min_rating: int = 0,
                  limit: int = 40, season: str = FOCUS_SEASON) -> dict:
        """Player Finder: filter the player pool by position, budget (market-value
        ceiling, €m), reliability (min minutes), age and rating, ranked by a chosen
        metric. Returns the metric catalogue + matching players."""
        metric = metric if metric in self.SCOUT_METRICS else "rating"
        col = self.SCOUT_METRICS[metric][0]
        rating_sql = "COALESCE(c.rating, f.rating)"
        metric_sql = rating_sql if col is None else f"v.{col}"
        # placeholder order matches the query text: c.season (LEFT JOIN) then
        # v.season (WHERE), then the conditional filters, then LIMIT.
        where = ["f.player_id IS NOT NULL", "v.season = ?"]
        params: list = [season, season]                      # c.season, v.season
        if pos and pos != "all" and pos in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[pos]
            where.append(f"f.main_position IN ({','.join(['?'] * len(gs))})")
            params += gs
        if min_minutes:
            where.append("v.minutes >= ?"); params.append(min_minutes)
        if max_value_m:
            where.append("f.market_value_eur IS NOT NULL AND f.market_value_eur <= ?")
            params.append(max_value_m * 1_000_000)
        if max_age:
            where.append("b.fotmob_age IS NOT NULL AND b.fotmob_age <= ?")
            params.append(max_age)
        if min_rating:
            where.append(f"{rating_sql} >= ?"); params.append(min_rating)
        where.append(f"{metric_sql} IS NOT NULL")
        params.append(limit)
        df = self.con.execute(f"""
            SELECT pl.player_name AS player, f.team,
                   COALESCE(f.detailed_position, f.main_position) AS position,
                   {rating_sql} AS rating, f.classification, f.market_value_eur,
                   b.fotmob_age AS age, b.nationality, v.minutes,
                   {metric_sql} AS metric_val, pe.fpid
            FROM v_player_profile_full f
            JOIN players pl ON pl.player_id = f.player_id
            JOIN v_player_season_stats v ON v.player_id = f.player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = f.player_id AND c.scope='league' AND c.season=?
            LEFT JOIN player_bio b ON b.player_id = f.player_id
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = f.player_id
            WHERE {' AND '.join(where)}
            ORDER BY metric_val DESC NULLS LAST, {rating_sql} DESC NULLS LAST
            LIMIT ?
        """, params).df()
        fmt = self.SCOUT_METRICS[metric][2]
        def mv(v):
            if v is None or pd.isna(v):
                return None
            return _i(v) if fmt == "int" else _r(v, 2 if fmt == "f2" else 1)
        players = [{"player": r.player, "team": r.team, "position": r.position,
                    "rating": _i(r.rating), "classification": r.classification,
                    "market_value_eur": None if pd.isna(r.market_value_eur) else float(r.market_value_eur),
                    "age": _i(r.age), "nationality": r.nationality, "minutes": _i(r.minutes),
                    "metric_val": mv(r.metric_val),
                    "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                   for r in df.itertuples()]
        return {
            "metrics": [{"key": k, "label": lbl} for k, (_c, lbl, _f) in self.SCOUT_METRICS.items()],
            "groups": [{"key": "all", "label": "All positions"}]
                      + [{"key": g, "label": g} for g in self.PLAYER_GROUPS],
            "metric": metric, "metric_label": self.SCOUT_METRICS[metric][1],
            "metric_fmt": fmt, "count": len(players), "players": players,
        }

    # ---- Team style fingerprint (use case: team identity) -----------------
    STYLE_AXES = [
        ("atk", "Attack"), ("def", "Defence"), ("press", "Pressing"),
        ("pen", "Penetration"), ("fin", "Finishing"), ("ctrl", "Control"),
    ]

    def web_team_options(self, season: str = FOCUS_SEASON) -> list[dict]:
        """Every team with season stats, for the style-comparison pickers."""
        df = self.con.execute("""
            SELECT t.team_name, l.league_name
            FROM team_season_stats s JOIN teams t USING(team_id)
            JOIN leagues l ON l.league_key = t.league_key
            WHERE s.season = ? ORDER BY l.league_name, t.team_name
        """, [season]).df()
        return [{"team": r.team_name, "league": r.league_name,
                 "team_logo": self.team_logo(r.team_name)} for r in df.itertuples()]

    def web_team_style(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """A team's playing-style fingerprint: six axes scored 0-100 as the
        percentile of the team's per-match profile against every team in the
        season (cross-league). Axes: attack (xG for), defence (xG against,
        inverted), pressing (PPDA, inverted), penetration (deep completions),
        finishing (goals minus xG), control (share of total xG)."""
        tid = self.find_team_id(name)
        if tid is None:
            return {}
        row = self.con.execute("""
            WITH agg AS (
              SELECT t.team_id, t.team_name, l.league_name,
                     avg(m.xg_for) xgf, avg(m.xg_against) xga, avg(m.ppda) ppda,
                     avg(m.deep_completions) deep, avg(m.goals_for - m.xg_for) fin,
                     avg(m.xg_for) / NULLIF(avg(m.xg_for) + avg(m.xg_against), 0) ctrl
              FROM team_match_stats m JOIN teams t USING(team_id)
              JOIN leagues l ON l.league_key = t.league_key
              WHERE m.season = ? GROUP BY 1, 2, 3
            ), ranked AS (
              SELECT *,
                100*percent_rank() OVER (ORDER BY xgf)       s_atk,
                100*percent_rank() OVER (ORDER BY xga DESC)  s_def,
                100*percent_rank() OVER (ORDER BY ppda DESC) s_press,
                100*percent_rank() OVER (ORDER BY deep)      s_pen,
                100*percent_rank() OVER (ORDER BY fin)       s_fin,
                100*percent_rank() OVER (ORDER BY ctrl)      s_ctrl
              FROM agg
            )
            SELECT * FROM ranked WHERE team_id = ?
        """, [season, tid]).df()
        if row.empty:
            return {}
        r = row.iloc[0]
        scores = {"atk": r.s_atk, "def": r.s_def, "press": r.s_press,
                  "pen": r.s_pen, "fin": r.s_fin, "ctrl": r.s_ctrl}
        raws = {"atk": _r(r.xgf, 2), "def": _r(r.xga, 2), "press": _r(r.ppda, 1),
                "pen": _r(r.deep, 1), "fin": _r(r.fin, 2), "ctrl": _i(r.ctrl * 100)}
        return {
            "team": r.team_name, "league": r.league_name,
            "team_logo": self.team_logo(r.team_name),
            "axes": [{"key": k, "label": lbl, "score": _i(scores[k]), "raw": raws[k]}
                     for k, lbl in self.STYLE_AXES],
        }

    # cumulative stats we expose per scope (frontend derives per-90 from these)
    _SCOPE_COUNTS = ["games", "minutes", "goals", "assists", "xg", "xa", "shots",
                     "chances_created", "big_chances_created", "dribbles_completed",
                     "duels_won", "tackles", "interceptions", "passes_completed"]
    _SCOPE_RATES = ["pass_accuracy_pct", "duels_won_pct"]   # minutes-weighted %

    # Understat's per-season position code -> readable label. The code is a
    # space-joined set (e.g. "F M S"); the first non-sub token is the primary.
    _UNDERSTAT_POS_LABEL = {"GK": "Goalkeeper", "D": "Defender",
                            "M": "Midfielder", "F": "Forward"}
    # coarse group (player_position_history.coarse_group) -> readable fallback word
    _COARSE_POS_LABEL = {"GK": "Goalkeeper", "DEF": "Defender",
                         "MID": "Midfielder", "FWD": "Forward"}

    def _player_season_position(self, pid: int, season: str) -> str | None:
        """The player's position for ONE season -- how a career position CHANGE
        surfaces (Bellingham CM→AM→ST→AM; Kimmich FB→CM). Prefers the stat-derived
        FINE group from `player_position_history` (ST/W/AM/CM/DM/CB/FB/GK, inferred
        from that season's stat profile -- see pipeline/positions_history.py), and
        falls back to the readable coarse word when that season couldn't be split
        (pre-2020/21, no FotMob defensive stats). Final fallback: Understat's raw
        per-season position string."""
        row = self.con.execute(
            "SELECT fine_position, coarse_group FROM player_position_history "
            "WHERE player_id = ? AND season = ?", [pid, season]).fetchone()
        if row:
            if row[0]:                                  # fine code, e.g. "AM"
                return row[0]
            if row[1]:                                  # coarse -> "Midfielder"
                return self._COARSE_POS_LABEL.get(row[1])
        us = self.con.execute(
            "SELECT position FROM player_season_stats "
            "WHERE player_id = ? AND season = ? AND position IS NOT NULL "
            "ORDER BY minutes DESC LIMIT 1", [pid, season]).fetchone()
        if not us or not us[0]:
            return None
        for tok in str(us[0]).split():
            if tok in ("S", "Sub"):
                continue
            return self._UNDERSTAT_POS_LABEL.get(tok)
        return None

    def _player_stat_scopes(self, pid: int, season: str = FOCUS_SEASON) -> dict:
        """Cumulative totals split into league / ucl / combined, from the canonical
        v_stats_combined (row-stacked by competition). Combined = league + ucl
        (counts summed, % rates minutes-weighted). Scopes with no minutes are
        omitted so the UI can disable them."""
        rate_sql = ", ".join(
            f"SUM({c} * minutes) FILTER (WHERE {c} IS NOT NULL) "
            f"/ NULLIF(SUM(minutes) FILTER (WHERE {c} IS NOT NULL), 0) AS {c}"
            for c in self._SCOPE_RATES)
        df = self.con.execute(f"""
            SELECT CASE WHEN competition = 'UCL' THEN 'ucl' ELSE 'league' END AS scp,
                   {', '.join(f'SUM({c}) AS {c}' for c in self._SCOPE_COUNTS)}, {rate_sql}
            FROM v_stats_combined WHERE player_id = ? AND season = ?
            GROUP BY scp
        """, [pid, season]).df()
        scopes = {}
        for r in df.itertuples():
            d = {c: _r(getattr(r, c), 2) for c in self._SCOPE_COUNTS}
            for c in self._SCOPE_RATES:
                d[c] = _i(getattr(r, c))
            if d["minutes"]:
                scopes[r.scp] = d
        lg, ucl = scopes.get("league"), scopes.get("ucl")
        if lg and ucl:
            comb = {c: round((lg[c] or 0) + (ucl[c] or 0), 2) for c in self._SCOPE_COUNTS}
            tm = (lg["minutes"] or 0) + (ucl["minutes"] or 0)
            for c in self._SCOPE_RATES:
                comb[c] = round(((lg[c] or 0) * (lg["minutes"] or 0)
                                 + (ucl[c] or 0) * (ucl["minutes"] or 0)) / tm) if tm else None
            scopes["combined"] = comb
        elif lg or ucl:
            scopes["combined"] = dict(lg or ucl)
        return scopes

    def web_player(self, name: str, career_stat: str = "xa",
                   season: str | None = None) -> dict:
        prof = self.player_profile(name)
        if not prof:
            return {}
        pid = self.find_player_id(name)
        # The season selector drives the parts that have real history (stat tiles,
        # cumulative scopes, avg match rating, the common-metric League/UCL gauges).
        # The datamb analysis (composite rating, radar, SWOT, archetype, heatmap,
        # signature actions, market value) only exists for FOCUS_SEASON and stays
        # pinned there -- see web_player's `pinned_season`/`is_current` outputs.
        seasons_avail = [r[0] for r in self.con.execute(
            "SELECT DISTINCT season FROM v_stats_combined_player "
            "WHERE player_id = ? ORDER BY season DESC", [pid]).fetchall()]
        if season not in seasons_avail:
            season = FOCUS_SEASON if FOCUS_SEASON in seasons_avail else (
                seasons_avail[0] if seasons_avail else FOCUS_SEASON)
        # selected-season tiles from the COMBINED (domestic + UCL) per-player view
        t = self.con.execute("""
            SELECT games, goals, assists, xg, xa, chances_created, big_chances_created,
                   dribbles_completed, minutes, pass_accuracy_pct, rating
            FROM v_stats_combined_player WHERE player_id = ? AND season = ?
        """, [pid, season]).fetchone()
        avg_rating = _r(t[10], 2) if t and t[10] is not None else None   # FotMob/SofaScore
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
        # Radar + SWOT. Current season = the full datamb analysis (6 axes, vs same
        # position). Past 2020/21+ seasons = the reduced per-season radar/SWOT
        # (player_radar_hist / player_swot_hist, 5 axes, Understat+FotMob only --
        # see pipeline/profile_history.py). Older seasons have neither. hist_level
        # tells the UI which (current | reduced | none).
        _AXIS_ORDER = ["Finishing", "Chance Creation", "Dribbling", "Passing", "Defending"]
        if season == FOCUS_SEASON:
            pm = self.con.execute(
                "SELECT metric_label, percentile FROM player_radar_metrics WHERE player_id = ?",
                [pid]).df()
            pcts = dict(zip(pm.metric_label, pm.percentile))
            radar = [{"axis": a, "value": v} for a, v in self._radar_values(pcts)]
            strengths = _split(prof["strengths"])
            weaknesses = _split(prof["weaknesses"])
            areas = _split(prof["areas_of_improvement"])
            hist_level = "current"
        else:
            rh = self.con.execute(
                "SELECT axis, value FROM player_radar_hist WHERE player_id=? AND season=?",
                [pid, season]).df()
            if len(rh):
                rmap = dict(zip(rh.axis, rh.value))
                radar = [{"axis": a, "value": _i(rmap[a])} for a in _AXIS_ORDER if a in rmap]
                sw = self.con.execute(
                    "SELECT strengths, weaknesses, areas_of_improvement "
                    "FROM player_swot_hist WHERE player_id=? AND season=?", [pid, season]).fetchone()
                strengths = _split(sw[0]) if sw else []
                weaknesses = _split(sw[1]) if sw else []
                areas = _split(sw[2]) if sw else []
                hist_level = "reduced"
            else:
                radar, strengths, weaknesses, areas = [], [], [], []
                hist_level = "none"
        # career progression of one stat; team = the SELECTED season's club (so a
        # past-season view shows the right crest, e.g. Bellingham at Dortmund), not
        # just the latest team.
        prog = self.player_progression(name, stats=[career_stat])
        career_stat = career_stat if career_stat in prog.columns else "ga_per90"
        career = [{"season": _fmt_season(r.season), "value": _r(getattr(r, career_stat))}
                  for r in prog.itertuples() if pd.notna(getattr(r, career_stat))]
        team = None
        if len(prog):
            match = prog[prog["season"] == season]
            team = (match.iloc[-1]["team"] if len(match) else prog.iloc[-1]["team"])
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
        fpid = self.con.execute(
            "SELECT max(fotmob_player_id) FROM player_enrichment "
            "WHERE player_id=? AND fotmob_player_id IS NOT NULL", [pid]).fetchone()
        # Position for the SELECTED season. Current season keeps the rich FotMob
        # detail (LW/RW/CAM); past seasons use Understat's per-season position so a
        # career position change actually shows (coarse, but real for all players).
        if season == FOCUS_SEASON:
            pos_group, pos_detail = prof["position_group"], prof.get("detailed_position")
        else:
            season_pos = self._player_season_position(pid, season)
            pos_group, pos_detail = (season_pos or prof["position_group"]), None
        return {
            "name": prof["player_name"], "team": team,
            "photo": self.player_photo(fpid[0] if fpid else None),
            "photo_credit": self._photo_credit(pid),  # CC attribution if licensed
            "team_logo": self.team_logo(team),
            "position_group": pos_group,           # per-season (Understat) for past seasons
            "detailed_position": pos_detail,        # LW/RW/LB/RB/CAM/... (FotMob, current season only)
            "age": self._player_age(prof["player_name"]) or (bio[3] if bio else None),
            "nationality": bio[0] if bio else None,
            "country_code": bio[1] if bio else None,
            "date_of_birth": bio[2] if bio else None,
            "market_value_eur": prof["market_value_eur"],
            "rating": prof["rating"], "classification": prof["classification"],
            "rank_in_group": ctx[0] if ctx else None,
            "percentile": round(ctx[1]) if ctx and ctx[1] is not None else None,
            "ratings": ratings,  # {"league": {...}, "ucl": {...}}  common-metric
            "avg_rating": avg_rating,  # FotMob/SofaScore average match rating (all comps)
            "tiles": tiles, "radar": radar,
            "stats_scopes": self._player_stat_scopes(pid, season),  # league/ucl/combined cumulative
            "archetype": self._player_archetype(pid),  # use case 10: role + traits + similar
            "signature_actions": self._player_tendencies(pid),  # use case 9
            "heatmap": self._player_heatmap(pid, season),  # season-aware (past seasons scraped from SofaScore)
            "career_stat": career_stat, "career": career,
            # season selector: which season the stats/gauges above reflect, the
            # available list, and whether the pinned analysis matches it
            "season": season,
            "seasons": [{"value": s, "label": _fmt_season(s)} for s in seasons_avail],
            "is_current": season == FOCUS_SEASON,
            "pinned_season": _fmt_season(FOCUS_SEASON),
            "hist_level": hist_level,   # current | reduced | none (radar/SWOT/heatmap)
            "strengths": strengths,
            "weaknesses": weaknesses,
            "areas_of_improvement": areas,
        }

    # nice labels for the comparison table (use case 5)
    COMPARE_STAT_LABELS = {
        "games": "Apps", "minutes": "Minutes", "goals": "Goals", "assists": "Assists",
        "ga_per90": "G+A per 90", "chances_created": "Chances Created",
        "big_chances_created": "Big Chances Created", "dribbles_completed": "Dribbles",
        "dribble_success_pct": "Dribble %", "passes_completed": "Passes Completed",
        "pass_accuracy_pct": "Pass Accuracy %", "duels_won": "Duels Won",
        "duels_won_pct": "Duels Won %", "tackles": "Tackles",
        "interceptions": "Interceptions", "recoveries": "Recoveries",
        "xg": "xG", "xa": "xA",
    }

    def web_compare(self, names: list[str], stats: list[str] | None = None,
                    season: str = FOCUS_SEASON) -> dict:
        """Use case 5: side-by-side comparison of 2-3 players on user-chosen
        stats (or the position-based defaults from use case 4), plus an
        overlaid percentile radar. Reuses v_player_season_stats so domestic +
        FotMob enrichment are already merged."""
        # de-dupe (case-insensitive), preserve order, cap at 3 for the UI
        names = [n.strip() for n in (names or []) if n and n.strip()]
        seen, uniq = set(), []
        for n in names:
            if n.lower() not in seen:
                seen.add(n.lower()); uniq.append(n)
        ids = [(n, pid) for n in uniq[:3]
               if (pid := self.find_player_id(n, season)) is not None]
        if len(ids) < 2:
            return {"players": [], "stats": [], "radar_axes": [],
                    "season": _fmt_season(season)}

        groups = {}
        for _, pid in ids:
            g = self.con.execute(
                "SELECT position_group FROM players WHERE player_id=?", [pid]).fetchone()
            groups[pid] = g[0] if g else None
        allowed = {r[1] for r in self.con.execute(
            "PRAGMA table_info('v_player_season_stats')").fetchall()}
        distinct_groups = {g for g in groups.values() if g}
        if len(distinct_groups) == 1:
            default_cols = self.DEFAULT_PROGRESSION_STATS.get(next(iter(distinct_groups)), [])
        else:                                   # mixed positions -> generic set
            default_cols = ["ga_per90", "chances_created", "tackles", "duels_won"]
        base = ["games", "minutes", "goals", "assists"]
        # default position stats always show; user-chosen stats are ADDED on top
        defaults, added = [], []
        for c in default_cols:
            if c in allowed and c not in base and c not in defaults:
                defaults.append(c)
        for c in (stats or []):
            if c in allowed and c not in base and c not in defaults and c not in added:
                added.append(c)
        added_set = set(added)
        statcols = base + defaults + added

        def radar_for(pid):
            pm = self.con.execute(
                "SELECT metric_label, percentile FROM player_radar_metrics WHERE player_id=?",
                [pid]).df()
            pcts = dict(zip(pm.metric_label, pm.percentile))
            return [v for _, v in self._radar_values(pcts)]

        players, statvals = [], {}
        for name, pid in ids:
            # rating = combined-League (datamb fallback), same as directory/profile
            h = self.con.execute("""
                SELECT pl.player_name, f.team,
                       COALESCE(f.detailed_position, f.main_position) AS position,
                       COALESCE(c.rating, f.rating) AS rating,
                       COALESCE(c.classification, f.classification) AS classification,
                       f.market_value_eur
                FROM v_player_profile_full f JOIN players pl ON pl.player_id = f.player_id
                LEFT JOIN player_ratings_combined c
                       ON c.player_id = f.player_id AND c.scope = 'league' AND c.season = ?
                WHERE f.player_id = ?
            """, [season, pid]).fetchone()
            bio = self.con.execute(
                "SELECT country_code FROM player_bio WHERE player_id=?", [pid]).fetchone()
            fpid = self.con.execute(
                "SELECT max(fotmob_player_id) FROM player_enrichment "
                "WHERE player_id=? AND fotmob_player_id IS NOT NULL", [pid]).fetchone()
            r = self.con.execute(
                f"SELECT {', '.join(statcols)} FROM v_player_season_stats "
                "WHERE player_id=? AND season=? ORDER BY minutes DESC LIMIT 1",
                [pid, season]).fetchone()
            statvals[pid] = dict(zip(statcols, r)) if r else {}
            team = h[1] if h else None
            players.append({
                "name": h[0] if h else name, "team": team,
                "position": h[2] if h else groups.get(pid),
                "rating": _i(h[3]) if h else None,
                "classification": h[4] if h else None,
                "market_value_eur": None if (not h or pd.isna(h[5])) else float(h[5]),
                "country_code": bio[0] if bio else None,
                "photo": self.player_photo(fpid[0] if fpid else None),
                "team_logo": self.team_logo(team),
                "radar": radar_for(pid),
            })

        stat_rows = []
        for c in statcols:
            raw = [statvals[pid].get(c) for _, pid in ids]
            vals = [None if (v is None or pd.isna(v)) else round(float(v), 2) for v in raw]
            numeric = [v for v in vals if v is not None]
            best = max(numeric) if numeric else None
            best_index = next((i for i, v in enumerate(vals) if v == best), None) \
                if best is not None else None
            stat_rows.append({
                "key": c,
                "label": self.COMPARE_STAT_LABELS.get(c, c.replace("_", " ").title()),
                "values": vals, "best_index": best_index,
                "added": c in added_set})

        return {"players": players, "stats": stat_rows,
                "radar_axes": list(self.RADAR_AXES.keys()),
                "season": _fmt_season(season)}

    def _player_age(self, datamb_name: str) -> int | None:
        r = self.con.execute(
            "SELECT age FROM player_wyscout WHERE player = ? AND age IS NOT NULL LIMIT 1",
            [datamb_name]).fetchone()
        return int(r[0]) if r else None
