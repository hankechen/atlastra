"""
Absence spells and availability, derived from the per-match log.

Atlastra has never known anything about availability. A player rated 84 who plays
half a season and one who plays all of it look identical everywhere except the
minutes column, and the trajectory model's availability head has to guess from
season totals alone.

Nothing needs scraping for this: `player_match_log` now covers twelve seasons, and
`team_match_stats` says which matches each club actually played. A match the club
played and the player did not is an absence; a run of them is a spell.

What is genuinely hard here is deciding when a player was *available to be absent*,
and getting it wrong invents injuries out of transfers:

  * A January signing did not miss the first half of the season through injury --
    he was somewhere else. So a player's window at a club STARTS at his first
    appearance for it. The cost is that an injury a player carries into a new club
    is invisible, which is the safe direction to be wrong in.
  * A player sold in January did not miss the second half either. So a spell ENDS
    when he first appears for his next club that season -- which the match log can
    see, because it records the club he played for.
  * Otherwise the window runs to the club's last match, so a season-ending injury
    is counted rather than truncated at his final appearance.

What this cannot separate is *why* a player was absent. A three-match run is as
likely a suspension or a rotation as a knock. So nothing here is called an injury:
they are absence spells, and long ones are reported as long ones.

Tables:
  * player_absence_spell  -- one row per unbroken run of missed club matches
  * player_availability   -- per player-season-club share of the window played

Run:  python -m pipeline.build_absences
"""
import sys

import duckdb

try:
    from config import DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH

# A run this long stops looking like rotation. Not called an injury -- a long
# suspension reaches it too -- but it is the length at which a manager's choice
# stops being the likeliest explanation.
EXTENDED_SPELL = 5

SQL = f"""
-- One grid to derive everything from: every club match inside a player's window,
-- flagged played or not. Both outputs below read this, because deriving "played"
-- from his season-wide appearance count instead let the two disagree -- a player
-- who turns out for a former club after moving on had 20 appearances counted
-- against a 7-match window, and came out 800% available.
CREATE OR REPLACE TEMP TABLE _grid AS
WITH spell AS (
    SELECT player_id, season, team_id, league_key,
           min(match_date) AS first_app, count(*) AS apps
    FROM player_match_log GROUP BY 1, 2, 3, 4
),
nxt AS (
    SELECT s.*, lead(first_app) OVER (
               PARTITION BY player_id, season ORDER BY first_app) AS next_club_from
    FROM spell s
),
club_end AS (
    SELECT team_id, season, max(match_date) AS last_match
    FROM team_match_stats GROUP BY 1, 2
),
win AS (
    SELECT n.player_id, n.season, n.team_id, n.league_key,
           n.first_app AS win_from,
           LEAST(COALESCE(n.next_club_from - INTERVAL 1 DAY, DATE '9999-12-31'),
                 c.last_match) AS win_to
    FROM nxt n JOIN club_end c ON c.team_id = n.team_id AND c.season = n.season
)
SELECT w.player_id, w.season, w.team_id, w.league_key, w.win_from, w.win_to,
       t.match_date, t.game_id,
       CASE WHEN l.player_id IS NULL THEN 1 ELSE 0 END AS absent
FROM win w
JOIN team_match_stats t
  ON t.team_id = w.team_id AND t.season = w.season
 AND t.match_date BETWEEN w.win_from AND w.win_to
LEFT JOIN player_match_log l
  ON l.player_id = w.player_id AND l.game_id = t.game_id;

CREATE OR REPLACE TABLE player_absence_spell AS
WITH ord AS (
    SELECT *, row_number() OVER (PARTITION BY player_id, season, team_id
                                 ORDER BY match_date) AS rn
    FROM _grid
),
grp AS (               -- islands-and-gaps: consecutive absences share a key
    SELECT *, rn - row_number() OVER (PARTITION BY player_id, season, team_id, absent
                                      ORDER BY rn) AS island
    FROM ord
)
SELECT player_id, season, team_id, league_key,
       min(match_date) AS start_date, max(match_date) AS end_date,
       count(*) AS matches_missed,
       count(*) >= {EXTENDED_SPELL} AS extended
FROM grp WHERE absent = 1
GROUP BY player_id, season, team_id, league_key, island;

CREATE INDEX IF NOT EXISTS idx_abs ON player_absence_spell(player_id, season);

CREATE OR REPLACE TABLE player_availability AS
WITH tot AS (
    SELECT player_id, season, team_id, league_key,
           min(win_from) AS win_from, max(win_to) AS win_to,
           count(*) AS window_matches,
           sum(1 - absent) AS played,
           sum(absent) AS matches_missed
    FROM _grid GROUP BY 1, 2, 3, 4
),
sp AS (
    SELECT player_id, season, team_id,
           max(matches_missed) AS longest_spell,
           sum(CASE WHEN extended THEN 1 ELSE 0 END) AS extended_spells,
           sum(CASE WHEN extended THEN matches_missed ELSE 0 END) AS extended_missed
    FROM player_absence_spell GROUP BY 1, 2, 3
)
SELECT t.player_id, t.season, t.team_id, t.league_key,
       t.win_from, t.win_to, t.window_matches, t.played,
       round(100.0 * t.played / NULLIF(t.window_matches, 0), 1) AS availability_pct,
       t.matches_missed,
       COALESCE(sp.longest_spell, 0)    AS longest_spell,
       COALESCE(sp.extended_spells, 0)  AS extended_spells,
       COALESCE(sp.extended_missed, 0)  AS extended_missed
FROM tot t
LEFT JOIN sp ON sp.player_id = t.player_id AND sp.season = t.season
            AND sp.team_id = t.team_id;

CREATE INDEX IF NOT EXISTS idx_avail ON player_availability(player_id, season);
"""

def build() -> None:
    con = duckdb.connect(str(DB_PATH))
    con.execute(SQL)
    n_sp, n_av = (con.execute("SELECT count(*) FROM player_absence_spell").fetchone()[0],
                  con.execute("SELECT count(*) FROM player_availability").fetchone()[0])
    print(f"[absences] {n_sp} absence spells, {n_av} player-season-club windows")
    print(con.execute(f"""
        SELECT season,
               count(*) AS windows,
               round(avg(availability_pct), 1) AS avg_avail,
               sum(CASE WHEN extended_spells > 0 THEN 1 ELSE 0 END) AS with_long_spell,
               round(avg(longest_spell), 2) AS avg_longest
        FROM player_availability GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
    print(f"\n[absences] spell-length distribution (>= {EXTENDED_SPELL} counts as extended):")
    print(con.execute("""
        SELECT matches_missed AS len, count(*) AS n FROM player_absence_spell
        WHERE matches_missed <= 12 GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
    con.close()


if __name__ == "__main__":
    build()
