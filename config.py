"""
Central configuration for the soccer-analytics pipeline.

Phase One: collect Top-5 European league data for 2025/26 into DuckDB.

Data source: Understat (free, web-scraped via the `soccerdata` library).
FBref is IP-blocked (HTTP 403) in this environment and the paid APIs listed in
the README (API-Football / Sportmonks / RapidAPI) require keys that are not
present, so Understat is the working free source. See NOTES.md for the
implications (which README stats are / are not available).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw" / "understat"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
DB_PATH = WAREHOUSE_DIR / "soccer.duckdb"
SCHEMA_PATH = ROOT / "pipeline" / "schema.sql"

# The "Top 5" European leagues, using soccerdata's Understat identifiers.
# country is stored on the league dimension.
LEAGUES = {
    "ENG-Premier League": {"name": "Premier League", "country": "England"},
    "ESP-La Liga":        {"name": "La Liga",        "country": "Spain"},
    "ITA-Serie A":        {"name": "Serie A",        "country": "Italy"},
    "GER-Bundesliga":     {"name": "Bundesliga",     "country": "Germany"},
    "FRA-Ligue 1":        {"name": "Ligue 1",        "country": "France"},
}

# Primary / required season for Phase One.
FOCUS_SEASON = "2526"  # 2025/26

# All seasons to collect, back to Understat's earliest (2014/15). Codes are
# soccerdata-style: "1415" == 2014/15. Understat has xG/xA from 2014/15 on.
ALL_SEASONS = [
    "1415", "1516", "1617", "1718", "1819", "1920",
    "2021", "2122", "2223", "2324", "2425", "2526",
]

# FotMob advanced-stat enrichment is only attempted from 2020/21 on (earlier
# seasons predate full Opta coverage on FotMob). Codes: "2021" == 2020/21.
ENRICH_SEASONS = ["2021", "2122", "2223", "2324", "2425", "2526"]

# Per-player duels come from playerData.firstSeasonStats, which only exposes the
# player's *current* season -- so duels are limited to the current season.
DUELS_SEASONS = [FOCUS_SEASON]

# FotMob numeric league ids for the Top-5 (used by the enrichment scraper to
# backfill the Opta-style stats Understat lacks: big chances, dribbles, tackles,
# interceptions, chances created, pass completion). See pipeline/fotmob_auth.py.
FOTMOB_LEAGUE_IDS = {
    "ENG-Premier League": 47,
    "ESP-La Liga":        87,
    "ITA-Serie A":        55,
    "GER-Bundesliga":     54,
    "FRA-Ligue 1":        53,
}
# FotMob season label format, e.g. "2526" -> "2025/2026".
def fotmob_season(code: str) -> str:
    return f"20{code[:2]}/20{code[2:]}"

# ---------------------------------------------------------------------------
# datamb.football enrichment -- Wyscout-grade per-player season stats.
#
# This is the source that finally carries the progressive / carrying / shot-
# creation style metrics neither Understat nor FotMob expose: progressive
# passes & carries per 90, touches in box, take-ons (+ success %), aerial %,
# crosses into box, PSxG-GA ("prevented goals"), save %, etc. -- 143 columns.
#
# Served as static per-position .xlsx files (Wyscout export), current season
# only, for the "TOP7" leagues (our Top-5 + Eredivisie + Primeira Liga):
#   https://datamb.football/database/CURRENT/TOP7<season>/<POS>/<POS>.xlsx
# Files carry no league column -- only "Team within selected timeframe" -- so
# league is assigned at load time from the team name. Only the CURRENT season
# is published (no historical archive), so this enrichment is 2025/26 only.
DATAMB_BASE = "https://datamb.football/database/CURRENT"

# datamb's six position buckets -> (file/code, human label as used in its index).
# Note datamb collapses all central midfielders (defensive / central / attacking)
# into one "CM" bucket, and treats wide forwards as "FW" (its "Winger" label).
DATAMB_POSITIONS = {
    "GK": "Goalkeeper",
    "CB": "Centre-back",
    "FB": "Full-back",
    "CM": "Midfielder",
    "FW": "Winger",
    "ST": "Striker",
}

# datamb publishes the current season only.
DATAMB_SEASONS = [FOCUS_SEASON]

# datamb covers "TOP7" = our Top-5 PLUS the Eredivisie (NL) and Primeira Liga
# (PT). The rating engine pools "within position group", and mixing in the two
# weaker leagues inflates their players' per-90s, so we restrict the pool to the
# Top-5 by EXCLUDING these two leagues' clubs. datamb carries no league column,
# but TOP7 minus NL minus PT == Top-5 exactly (verified: 132 - 36 = 96 clubs).
# Names are datamb's exact spellings; this set is season-specific (promotion/
# relegation changes it) and datamb is current-season-only, so it tracks 2025/26.
DATAMB_NON_TOP5_TEAMS = frozenset({
    # Eredivisie
    "AZ", "Ajax", "Excelsior", "Feyenoord", "Fortuna Sittard", "Go Ahead Eagles",
    "Groningen", "Heerenveen", "Heracles", "NAC Breda", "NEC", "PEC Zwolle",
    "PSV", "Sparta Rotterdam", "Telstar", "Twente", "Utrecht", "Volendam",
    # Primeira Liga
    "AVS", "Alverca", "Arouca", "Benfica", "Casa Pia AC", "Estoril",
    "Estrela Amadora", "Famalicão", "Gil Vicente", "Moreirense", "Nacional",
    "Porto", "Rio Ave", "Santa Clara", "Sporting Braga", "Sporting CP",
    "Tondela", "Vitória Guimarães",
})

# ---------------------------------------------------------------------------
# SofaScore -- UEFA Champions League player stats.
#
# The Top-5 domestic sources (Understat/FotMob/datamb) carry no continental
# competition. SofaScore does, and with a rich per-player season-stats endpoint
# (~77 fields: goals, xG, key passes, tackles, interceptions, dribbles, duels,
# clearances, saves, ...) going all the way back to **2008/09** -- nearly the
# full field set even in the oldest seasons (only xG and a couple of fields are
# absent pre-~2020). So UCL gets "advanced" stats for every season, not just
# recent ones.
#
# SofaScore is bot-protected: it 403s ("challenge") the moment you send the
# usual CORS/sec-fetch headers, but a *bare* browser-TLS request (tls_requests
# with no extra headers) passes. So the scraper deliberately sends no headers.
#
# UCL uniqueTournament id = 7. Season ids are looked up live from the seasons
# endpoint and floored at 2008/09 (UCL_MIN_SEASON_CODE).
SOFASCORE_BASE = "https://api.sofascore.com/api/v1"
SOFASCORE_UCL_TOURNAMENT_ID = 7
UCL_MIN_SEASON_CODE = "0809"  # earliest UCL season to collect (2008/09)

# Top-5 domestic uniqueTournament ids on SofaScore -- used to backfill the two
# defensive metrics datamb lacks (clearances, errors) so the rating engine's CB
# and DM vectors don't have to drop them. league_key -> SofaScore tournament id.
SOFASCORE_TOP5_TOURNAMENTS = {
    "ENG-Premier League": 17,
    "ESP-La Liga":        8,
    "ITA-Serie A":        23,
    "GER-Bundesliga":     35,
    "FRA-Ligue 1":        34,
}

# Seasons to pull from Understat. Player stats, matches and team stats are all
# collected for the full history so standings and cross-year progression work
# across every season.
PLAYER_SEASONS = ALL_SEASONS
MATCH_SEASONS = ALL_SEASONS

# Map a soccerdata season code like "2526" to a human label "2025/26".
def season_label(code: str) -> str:
    return f"20{code[:2]}/{code[2:]}"

# Minimum minutes for a player to be eligible for ratings / classification,
# so a 20-minute cameo doesn't top the percentile charts.
MIN_MINUTES_FOR_RATING = 600

# Minutes at which a player gets full "reliability" credit in the rating.
# Per-90 rates reward small samples, so the composite is scaled by
# min(1, minutes / FULL_SEASON_MINUTES) to keep a hot 700-minute cameo from
# outranking an ever-present 27-goal striker. ~20 full matches.
FULL_SEASON_MINUTES = 1800

# Position-group mapping from Understat position codes.
# Understat encodes positions as space-separated tokens, e.g. "F M S"
# (forward / midfield / substitute). "S"/"Sub" means "appeared as sub".
POSITION_GROUP = {
    "GK": "GK",
    "D": "DEF",
    "M": "MID",
    "F": "FWD",
}
