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
