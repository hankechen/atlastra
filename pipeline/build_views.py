"""
Build the player-statistics "tab" views over a single canonical schema:

  v_stats_ucl             -- UCL only        (SofaScore, ucl_player_stats, 18 seasons)
  v_stats_top5            -- Top-5 domestic  (Understat + FotMob, by player_id)
  v_stats_combined        -- both, ROW-STACKED (UNION ALL, tagged by `competition`)
  v_stats_combined_player -- both, PER-PLAYER AGGREGATED (one row per player+season,
                             domestic + UCL totals summed, rates minutes-weighted)

The two sources have different native schemas, so each is projected onto the same
canonical columns; a stat a source doesn't carry is NULL on that side:
  - UCL has no xA.
  - Top-5 (Understat+FotMob) has no shots_on_target / clearances / aerial%.
    (datamb/player_wyscout carries those but is 2025/26-only + name-keyed, so it
     is intentionally NOT joined here -- this spine stays multi-season, id-keyed.)

UCL is SofaScore-id keyed and Top-5 is Understat-id keyed, so the per-player
aggregate needs a crosswalk: `ucl_understat_xwalk` maps sofascore_player_id ->
Understat player_id by accent-folded fuzzy name match within a season (same
two-phase logic as pipeline.load_enrich). ~61% of UCL rows match -- the rest are
players from non-Top-5 clubs (Benfica, Galatasaray, ...) with no Understat entry,
correctly left out of the aggregate.

`competition` is 'UCL' for UCL rows and the league_key (e.g. 'ESP-La Liga') for
Top-5 rows. Run after the loaders (ucl + enrich):
    python -m pipeline.build_views
"""
import sys
import unicodedata
import warnings

import duckdb
from rapidfuzz import fuzz, process

try:
    from config import DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH

warnings.filterwarnings("ignore")

MATCH_THRESHOLD = 80    # phase 1: token_sort
RECOVER_THRESHOLD = 90  # phase 2: token_set + guard


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def _name_compatible(a: str, b: str) -> bool:
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    return sa <= sb or sb <= sa or ta[-1] == tb[-1]


def _build_xwalk(con: duckdb.DuckDBPyConnection) -> int:
    """Map UCL sofascore_player_id -> Understat player_id per season (two-phase
    fuzzy name match) into table `ucl_understat_xwalk`."""
    ucl = con.execute(
        "SELECT sofascore_player_id AS pid, player_name, season FROM ucl_player_stats"
    ).df()
    us = con.execute(
        "SELECT DISTINCT ps.player_id, p.player_name, ps.season "
        "FROM player_season_stats ps JOIN players p USING (player_id)"
    ).df()
    pools = {
        se: ([int(r.player_id) for r in g.itertuples()],
             [_norm(r.player_name) for r in g.itertuples()])
        for se, g in us.groupby("season")
    }

    rows = []
    for se, grp in ucl.groupby("season"):
        ids, names = pools.get(se, ([], []))
        if not names:
            continue
        used, leftover = set(), []
        # phase 1: high-precision token_sort
        for r in grp.itertuples():
            target = _norm(r.player_name)
            if not target:
                continue
            best = process.extractOne(target, names, scorer=fuzz.token_sort_ratio)
            if not best or best[1] < MATCH_THRESHOLD:
                leftover.append((r, target))
                continue
            pid = ids[best[2]]
            if pid in used:
                leftover.append((r, target))
                continue
            used.add(pid)
            rows.append((int(r.pid), se, pid, float(best[1])))
        # phase 2: token_set recovery on leftovers, globally assigned
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
            rows.append((int(r.pid), se, pid, float(score)))

    con.execute("DROP TABLE IF EXISTS ucl_understat_xwalk")
    con.execute(
        "CREATE TABLE ucl_understat_xwalk "
        "(sofascore_player_id BIGINT, season VARCHAR, player_id BIGINT, match_confidence DOUBLE)"
    )
    if rows:
        con.executemany("INSERT INTO ucl_understat_xwalk VALUES (?,?,?,?)", rows)
    return len(rows)


# UCL (SofaScore) -> canonical. SofaScore key_passes == chances created.
# player_id comes from the crosswalk (NULL for non-Top-5 UCL players).
UCL_SELECT = """
SELECT
    x.player_id,
    u.player_name                         AS player,
    u.team_name                           AS team,
    'UCL'                                 AS competition,
    u.season,
    CAST(u.appearances AS DOUBLE)         AS games,
    CAST(u.minutes_played AS DOUBLE)      AS minutes,
    CAST(u.goals AS DOUBLE)               AS goals,
    CAST(u.assists AS DOUBLE)             AS assists,
    u.expected_goals                      AS xg,
    CAST(NULL AS DOUBLE)                  AS xa,
    CAST(u.total_shots AS DOUBLE)         AS shots,
    CAST(u.shots_on_target AS DOUBLE)     AS shots_on_target,
    CAST(u.key_passes AS DOUBLE)          AS chances_created,
    CAST(u.big_chances_created AS DOUBLE) AS big_chances_created,
    CAST(u.big_chances_missed AS DOUBLE)  AS big_chances_missed,
    CAST(u.successful_dribbles AS DOUBLE) AS dribbles_completed,
    u.successful_dribbles_percentage      AS dribble_success_pct,
    u.total_duels_won_percentage          AS duels_won_pct,
    CAST(u.tackles AS DOUBLE)             AS tackles,
    CAST(u.interceptions AS DOUBLE)       AS interceptions,
    CAST(u.accurate_passes AS DOUBLE)     AS passes_completed,
    u.accurate_passes_percentage          AS pass_accuracy_pct,
    CAST(u.clearances AS DOUBLE)          AS clearances,
    u.aerial_duels_won_percentage         AS aerial_duels_won_pct,
    u.rating
FROM ucl_player_stats u
LEFT JOIN ucl_understat_xwalk x
       ON x.sofascore_player_id = u.sofascore_player_id AND x.season = u.season
"""

# Top-5 domestic: Understat base (s) + FotMob enrichment (e) + names/teams.
TOP5_SELECT = """
SELECT
    s.player_id,
    p.player_name                       AS player,
    t.team_name                         AS team,
    s.league_key                        AS competition,
    s.season,
    CAST(s.matches AS DOUBLE)           AS games,
    CAST(s.minutes AS DOUBLE)           AS minutes,
    CAST(s.goals AS DOUBLE)             AS goals,
    CAST(s.assists AS DOUBLE)           AS assists,
    s.xg,
    s.xa,
    CAST(s.shots AS DOUBLE)             AS shots,
    CAST(NULL AS DOUBLE)                AS shots_on_target,
    CAST(COALESCE(e.chances_created, s.key_passes) AS DOUBLE) AS chances_created,
    CAST(e.big_chances_created AS DOUBLE) AS big_chances_created,
    CAST(e.big_chances_missed AS DOUBLE)  AS big_chances_missed,
    CAST(e.dribbles_completed AS DOUBLE)  AS dribbles_completed,
    e.dribble_success_pct,
    e.duels_won_pct,
    CAST(e.tackles AS DOUBLE)           AS tackles,
    CAST(e.interceptions AS DOUBLE)     AS interceptions,
    CAST(e.passes_completed AS DOUBLE)  AS passes_completed,
    e.pass_accuracy_pct,
    CAST(NULL AS DOUBLE)                AS clearances,
    CAST(NULL AS DOUBLE)                AS aerial_duels_won_pct,
    e.fotmob_rating                     AS rating
FROM player_season_stats s
LEFT JOIN players p USING (player_id)
LEFT JOIN teams t ON t.team_id = s.team_id
LEFT JOIN player_enrichment e
       ON e.player_id = s.player_id AND e.season = s.season
      AND e.league_key = s.league_key AND e.source = 'fotmob'
"""


def _wavg(col):
    """minutes-weighted average of a rate column over rows where it's non-null."""
    return (f"ROUND(SUM({col} * minutes) FILTER (WHERE {col} IS NOT NULL) "
            f"/ NULLIF(SUM(minutes) FILTER (WHERE {col} IS NOT NULL), 0), 1) AS {col}")


# Per-player aggregate: sum volume stats across competitions, minutes-weight rates.
COMBINED_PLAYER_SELECT = f"""
SELECT
    player_id,
    season,
    arg_max(player, minutes)                       AS player,
    arg_max(team, minutes)                         AS team,
    string_agg(DISTINCT competition, ' + ')        AS competitions,
    SUM(games)               AS games,
    SUM(minutes)             AS minutes,
    SUM(goals)               AS goals,
    SUM(assists)             AS assists,
    ROUND(SUM(xg), 2)        AS xg,
    ROUND(SUM(xa), 2)        AS xa,
    SUM(shots)               AS shots,
    SUM(shots_on_target)     AS shots_on_target,
    SUM(chances_created)     AS chances_created,
    SUM(big_chances_created) AS big_chances_created,
    SUM(big_chances_missed)  AS big_chances_missed,
    SUM(dribbles_completed)  AS dribbles_completed,
    SUM(tackles)             AS tackles,
    SUM(interceptions)       AS interceptions,
    SUM(passes_completed)    AS passes_completed,
    SUM(clearances)          AS clearances,
    {_wavg('dribble_success_pct')},
    {_wavg('duels_won_pct')},
    {_wavg('pass_accuracy_pct')},
    {_wavg('aerial_duels_won_pct')},
    {_wavg('rating')}
FROM v_stats_combined
WHERE player_id IS NOT NULL
GROUP BY player_id, season
"""


def build_views() -> None:
    con = duckdb.connect(str(DB_PATH))
    n_xwalk = _build_xwalk(con)
    print(f"ucl_understat_xwalk: {n_xwalk} UCL player-seasons mapped to Understat ids")
    con.execute(f"CREATE OR REPLACE VIEW v_stats_ucl  AS {UCL_SELECT}")
    con.execute(f"CREATE OR REPLACE VIEW v_stats_top5 AS {TOP5_SELECT}")
    con.execute(
        "CREATE OR REPLACE VIEW v_stats_combined AS "
        "SELECT * FROM v_stats_ucl UNION ALL SELECT * FROM v_stats_top5"
    )
    con.execute(f"CREATE OR REPLACE VIEW v_stats_combined_player AS {COMBINED_PLAYER_SELECT}")
    # Career timeline (use case 3): one row per player+season, team & per-comp totals.
    con.execute("""
        CREATE OR REPLACE VIEW v_player_career AS
        SELECT player_id, season, player, team, competitions,
               games, minutes, goals, assists, xg, xa
        FROM v_stats_combined_player
        ORDER BY player_id, season
    """)
    # Per-season domestic stat spine (use case 4: cross-year progression).
    # Understat (all seasons) + FotMob enrichment (2020/21+ only) aggregated per
    # (player_id, season); counts SUMmed across leagues, rates minutes-weighted.
    con.execute("""
        CREATE OR REPLACE VIEW v_player_season_stats AS
        WITH us AS (
            SELECT player_id, season, arg_max(team_id, minutes) AS team_id,
                   SUM(matches) AS games, SUM(minutes) AS minutes,
                   SUM(goals) AS goals, SUM(assists) AS assists,
                   SUM(xg) AS xg, SUM(xa) AS xa, SUM(shots) AS shots,
                   SUM(key_passes) AS key_passes
            FROM player_season_stats GROUP BY player_id, season
        ), en AS (
            SELECT player_id, season,
                   SUM(chances_created) AS chances_created,
                   SUM(big_chances_created) AS big_chances_created,
                   SUM(big_chances_missed) AS big_chances_missed,
                   SUM(dribbles_completed) AS dribbles_completed,
                   SUM(tackles) AS tackles, SUM(interceptions) AS interceptions,
                   SUM(recoveries) AS recoveries, SUM(passes_completed) AS passes_completed,
                   SUM(duels_won) AS duels_won,
                   SUM(duels_won_pct * minutes_played)
                       / NULLIF(SUM(minutes_played), 0) AS duels_won_pct,
                   SUM(dribble_success_pct * minutes_played)
                       / NULLIF(SUM(minutes_played), 0) AS dribble_success_pct,
                   SUM(pass_accuracy_pct * minutes_played)
                       / NULLIF(SUM(minutes_played), 0) AS pass_accuracy_pct,
                   SUM(fotmob_rating * minutes_played)
                       / NULLIF(SUM(minutes_played), 0) AS fotmob_rating
            FROM player_enrichment WHERE source = 'fotmob' GROUP BY player_id, season
        )
        SELECT us.player_id, us.season, t.team_name AS team,
               us.games, us.minutes, us.goals, us.assists,
               us.goals + us.assists AS ga, us.xg, us.xa, us.shots, us.key_passes,
               ROUND(us.goals    / NULLIF(us.minutes, 0) * 90, 3) AS goals_per90,
               ROUND(us.assists  / NULLIF(us.minutes, 0) * 90, 3) AS assists_per90,
               ROUND((us.goals + us.assists) / NULLIF(us.minutes, 0) * 90, 3) AS ga_per90,
               ROUND(us.xg / NULLIF(us.minutes, 0) * 90, 3) AS xg_per90,
               ROUND(us.xa / NULLIF(us.minutes, 0) * 90, 3) AS xa_per90,
               en.chances_created, en.big_chances_created, en.big_chances_missed,
               en.dribbles_completed, en.tackles, en.interceptions, en.recoveries,
               en.passes_completed, en.duels_won,
               ROUND(en.duels_won_pct, 1) AS duels_won_pct,
               ROUND(en.dribble_success_pct, 1) AS dribble_success_pct,
               ROUND(en.pass_accuracy_pct, 1) AS pass_accuracy_pct,
               ROUND(en.fotmob_rating, 2) AS fotmob_rating
        FROM us
        LEFT JOIN en USING (player_id, season)
        LEFT JOIN teams t ON t.team_id = us.team_id
    """)
    for v in ("v_stats_ucl", "v_stats_top5", "v_stats_combined", "v_stats_combined_player",
              "v_player_career", "v_player_season_stats"):
        n = con.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
        seasons = con.execute(f"SELECT COUNT(DISTINCT season) FROM {v}").fetchone()[0]
        print(f"{v}: {n} rows across {seasons} seasons")
    con.close()


if __name__ == "__main__":
    build_views()
