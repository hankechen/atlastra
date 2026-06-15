-- ===========================================================================
-- Soccer Analytics warehouse schema (DuckDB)
--
-- Source-honest: every column below is something Understat actually provides
-- (or is derived from it). Stats the README asks for that Understat does NOT
-- carry -- duels, dribbles, tackles, interceptions, big chances, passes
-- completed, progressive passes -- are intentionally absent rather than faked.
-- See NOTES.md.
--
-- Grain summary:
--   leagues / teams / players          -> dimensions (natural keys = Understat ids)
--   matches                            -> 1 row per fixture
--   team_match_stats                   -> 1 row per (match, team)  [2 per match]
--   player_season_stats                -> 1 row per (player, team, league, season)
--   team_season_stats                  -> 1 row per (team, league, season) [standings]
--   player_ratings                     -> 1 row per (player, league, season) [derived]
-- ===========================================================================

-- ---------- Dimensions ----------

CREATE TABLE IF NOT EXISTS leagues (
    league_key      VARCHAR PRIMARY KEY,   -- e.g. 'ENG-Premier League'
    league_name     VARCHAR NOT NULL,      -- e.g. 'Premier League'
    country         VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    team_id         BIGINT PRIMARY KEY,    -- Understat team id
    team_name       VARCHAR NOT NULL,
    team_code       VARCHAR,               -- short code, e.g. 'LIV'
    league_key      VARCHAR REFERENCES leagues(league_key)
);

CREATE TABLE IF NOT EXISTS players (
    player_id        BIGINT PRIMARY KEY,   -- Understat player id
    player_name      VARCHAR NOT NULL,
    primary_position VARCHAR,              -- raw Understat code, e.g. 'F'
    position_group   VARCHAR               -- GK / DEF / MID / FWD
);

-- ---------- Match-level facts ----------

CREATE TABLE IF NOT EXISTS matches (
    game_id         BIGINT PRIMARY KEY,    -- Understat game id
    league_key      VARCHAR NOT NULL REFERENCES leagues(league_key),
    season          VARCHAR NOT NULL,      -- '2526'
    match_date      TIMESTAMP,
    home_team_id    BIGINT NOT NULL REFERENCES teams(team_id),
    away_team_id    BIGINT NOT NULL REFERENCES teams(team_id),
    home_goals      INTEGER,
    away_goals      INTEGER,
    home_xg         DOUBLE,
    away_xg         DOUBLE,
    is_result       BOOLEAN                -- played (true) vs scheduled (false)
);

CREATE TABLE IF NOT EXISTS team_match_stats (
    game_id             BIGINT NOT NULL REFERENCES matches(game_id),
    team_id             BIGINT NOT NULL REFERENCES teams(team_id),
    league_key          VARCHAR NOT NULL,
    season              VARCHAR NOT NULL,
    match_date          TIMESTAMP,
    is_home             BOOLEAN,
    opponent_team_id    BIGINT,
    goals_for           INTEGER,
    goals_against       INTEGER,
    xg_for              DOUBLE,
    xg_against          DOUBLE,
    np_xg_for           DOUBLE,
    points              INTEGER,           -- 3/1/0
    expected_points     DOUBLE,            -- Understat xPoints
    ppda                DOUBLE,            -- passes allowed per defensive action
    deep_completions    INTEGER,           -- passes completed near opp. goal
    PRIMARY KEY (game_id, team_id)
);

-- ---------- Season aggregates ----------

CREATE TABLE IF NOT EXISTS player_season_stats (
    player_id           BIGINT NOT NULL REFERENCES players(player_id),
    team_id             BIGINT NOT NULL REFERENCES teams(team_id),
    league_key          VARCHAR NOT NULL REFERENCES leagues(league_key),
    season              VARCHAR NOT NULL,
    position            VARCHAR,           -- raw Understat code for this season
    position_group      VARCHAR,
    matches             INTEGER,
    minutes             INTEGER,
    goals               INTEGER,
    assists             INTEGER,
    shots               INTEGER,
    key_passes          INTEGER,           -- "chances created" (Opta-style)
    xg                  DOUBLE,
    np_goals            INTEGER,
    np_xg               DOUBLE,
    xa                  DOUBLE,
    xg_chain            DOUBLE,            -- xG of every possession the player was in
    xg_buildup          DOUBLE,            -- xg_chain excluding shots & key passes
    yellow_cards        INTEGER,
    red_cards           INTEGER,
    -- derived per-90 rates (NULL when minutes == 0)
    goals_per90         DOUBLE,
    assists_per90       DOUBLE,
    ga_per90            DOUBLE,            -- (goals + assists) / 90
    xg_per90            DOUBLE,
    xa_per90            DOUBLE,
    npxg_per90          DOUBLE,
    shots_per90         DOUBLE,
    key_passes_per90    DOUBLE,
    PRIMARY KEY (player_id, team_id, league_key, season)
);

CREATE TABLE IF NOT EXISTS team_season_stats (
    team_id             BIGINT NOT NULL REFERENCES teams(team_id),
    league_key          VARCHAR NOT NULL REFERENCES leagues(league_key),
    season              VARCHAR NOT NULL,
    matches_played      INTEGER,
    wins                INTEGER,
    draws               INTEGER,
    losses              INTEGER,
    goals_for           INTEGER,
    goals_against       INTEGER,
    goal_difference     INTEGER,
    xg_for              DOUBLE,
    xg_against          DOUBLE,
    points              INTEGER,
    expected_points     DOUBLE,
    league_position     INTEGER,
    PRIMARY KEY (team_id, league_key, season)
);

-- ---------- Derived: ratings & classification (README use case #2) ----------

CREATE TABLE IF NOT EXISTS player_ratings (
    player_id           BIGINT NOT NULL REFERENCES players(player_id),
    league_key          VARCHAR NOT NULL,
    season              VARCHAR NOT NULL,
    position_group      VARCHAR,
    minutes             INTEGER,
    rating              DOUBLE,            -- 0..100 percentile-based score
    percentile_in_group DOUBLE,           -- 0..1 within position group + league
    classification      VARCHAR,          -- Best In Position / World-Class / ...
    rank_in_group       INTEGER,          -- 1 = best at that position in league
    rating_version      VARCHAR DEFAULT 'v1',
    PRIMARY KEY (player_id, league_key, season, rating_version)
);

-- ---------- Enrichment: stats Understat lacks, sourced from FotMob ----------
-- Kept in its own table (different provider, partial coverage) so the
-- Understat-sourced facts above stay source-honest. Joined to players by
-- fuzzy name+team match in pipeline.load_enrich.
CREATE TABLE IF NOT EXISTS player_enrichment (
    player_id            BIGINT REFERENCES players(player_id),
    league_key           VARCHAR NOT NULL,
    season               VARCHAR NOT NULL,
    fotmob_player_id     BIGINT,
    match_confidence     DOUBLE,            -- 0..100 fuzzy name-match score
    -- season totals
    big_chances_created  INTEGER,
    big_chances_missed   INTEGER,
    chances_created      INTEGER,           -- FotMob's total chances created
    dribbles_completed   INTEGER,
    tackles              INTEGER,
    interceptions        INTEGER,
    recoveries           INTEGER,
    passes_completed     INTEGER,
    duels_won            INTEGER,
    duels_won_pct        DOUBLE,
    -- per-90 rates / percentages
    dribbles_per90       DOUBLE,
    dribble_success_pct  DOUBLE,
    tackles_per90        DOUBLE,
    interceptions_per90  DOUBLE,
    recoveries_per90     DOUBLE,
    pass_accuracy_pct    DOUBLE,
    fotmob_rating        DOUBLE,            -- FotMob's own 0..10 season rating
    minutes_played       INTEGER,
    matches_played       INTEGER,
    source               VARCHAR DEFAULT 'fotmob',
    PRIMARY KEY (player_id, league_key, season, source)
);

-- ---------- Historical match data (football-data.co.uk) ----------
-- Fills the pre-Understat gap (2008/09-2013/14). Basic match stats only --
-- NO xG, NO player-level data (see NOTES.md / scrape_history.py). Kept in its
-- own name-based tables because Understat has no team_ids for this era, so it
-- cannot join the `teams` dimension; teams are identified by football-data's
-- club names. 1 row per fixture.
CREATE TABLE IF NOT EXISTS matches_history (
    league_key      VARCHAR NOT NULL REFERENCES leagues(league_key),
    season          VARCHAR NOT NULL,      -- '0809'
    match_date      DATE,
    home_team       VARCHAR NOT NULL,
    away_team       VARCHAR NOT NULL,
    home_goals      INTEGER,
    away_goals      INTEGER,
    result          VARCHAR,               -- H / D / A
    home_goals_ht   INTEGER,
    away_goals_ht   INTEGER,
    result_ht       VARCHAR,
    home_shots      INTEGER,
    away_shots      INTEGER,
    home_shots_ot   INTEGER,               -- on target
    away_shots_ot   INTEGER,
    home_fouls      INTEGER,
    away_fouls      INTEGER,
    home_corners    INTEGER,
    away_corners    INTEGER,
    home_yellows    INTEGER,
    away_yellows    INTEGER,
    home_reds       INTEGER,
    away_reds       INTEGER,
    referee         VARCHAR,               -- England only in this era
    source          VARCHAR DEFAULT 'football-data.co.uk',
    PRIMARY KEY (league_key, season, home_team, away_team)
);

-- Derived standings for the historical seasons (name-based, mirrors
-- team_season_stats but with the basic shot/discipline aggregates this source
-- carries and no xG/xPoints). league_position uses points, then GD, then GF.
CREATE TABLE IF NOT EXISTS team_season_history (
    league_key      VARCHAR NOT NULL REFERENCES leagues(league_key),
    season          VARCHAR NOT NULL,
    team_name       VARCHAR NOT NULL,
    matches_played  INTEGER,
    wins            INTEGER,
    draws           INTEGER,
    losses          INTEGER,
    goals_for       INTEGER,
    goals_against   INTEGER,
    goal_difference INTEGER,
    points          INTEGER,
    shots_for       INTEGER,
    shots_ot_for    INTEGER,
    corners_for     INTEGER,
    yellows         INTEGER,
    reds            INTEGER,
    league_position INTEGER,
    PRIMARY KEY (league_key, season, team_name)
);

-- ---------- Team-name canonicalization across data sources ----------
-- football-data.co.uk (historical) and Understat spell many clubs differently
-- ("Man United" vs "Manchester United", "Ath Bilbao" vs "Athletic Club"). This
-- map folds a raw source spelling to one canonical name so a single club can be
-- tracked across the era boundary. Populated by pipeline.load_name_map; clubs
-- that appear in only one era (and need no rename) are simply absent here.
CREATE TABLE IF NOT EXISTS team_name_map (
    raw_name        VARCHAR PRIMARY KEY,
    canonical_name  VARCHAR NOT NULL
);

-- ---------- Bridge view: one continuous standings history (2008/09-2025/26) --
-- Unions the name-based historical standings (team_season_history, 08/09-13/14,
-- no xG) with the Understat-keyed standings (team_season_stats, 14/15-, with xG)
-- into a single 18-season league-table view. `team_name` is canonicalized via
-- team_name_map so a club is one identity across both eras; `raw_name` keeps the
-- original source spelling. Columns a given era lacks are NULL: xG/xPoints are
-- NULL before 14/15; shots/corners/cards are NULL from 14/15 on.
CREATE OR REPLACE VIEW standings_all AS
SELECT
    'historical' AS era,
    h.league_key, h.season,
    COALESCE(m.canonical_name, h.team_name) AS team_name,
    h.team_name AS raw_name,
    h.matches_played, h.wins, h.draws, h.losses,
    h.goals_for, h.goals_against, h.goal_difference,
    h.points, h.league_position,
    CAST(NULL AS DOUBLE) AS xg_for,
    CAST(NULL AS DOUBLE) AS xg_against,
    CAST(NULL AS DOUBLE) AS expected_points,
    h.shots_for, h.shots_ot_for, h.corners_for, h.yellows, h.reds
FROM team_season_history h
LEFT JOIN team_name_map m ON m.raw_name = h.team_name
UNION ALL
SELECT
    'understat' AS era,
    s.league_key, s.season,
    COALESCE(m.canonical_name, t.team_name) AS team_name,
    t.team_name AS raw_name,
    s.matches_played, s.wins, s.draws, s.losses,
    s.goals_for, s.goals_against, s.goal_difference,
    s.points, s.league_position,
    s.xg_for, s.xg_against, s.expected_points,
    CAST(NULL AS INTEGER) AS shots_for,
    CAST(NULL AS INTEGER) AS shots_ot_for,
    CAST(NULL AS INTEGER) AS corners_for,
    CAST(NULL AS INTEGER) AS yellows,
    CAST(NULL AS INTEGER) AS reds
FROM team_season_stats s
JOIN teams t USING (team_id)
LEFT JOIN team_name_map m ON m.raw_name = t.team_name;

-- ---------- Helpful indexes ----------
CREATE INDEX IF NOT EXISTS idx_pss_season   ON player_season_stats(league_key, season);
CREATE INDEX IF NOT EXISTS idx_pss_player   ON player_season_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pss_posgroup ON player_season_stats(position_group);
CREATE INDEX IF NOT EXISTS idx_matches_season ON matches(league_key, season);
CREATE INDEX IF NOT EXISTS idx_tms_team     ON team_match_stats(team_id, season);
CREATE INDEX IF NOT EXISTS idx_ratings_grp  ON player_ratings(league_key, season, position_group);
