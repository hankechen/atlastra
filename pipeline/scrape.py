"""
Scrape Top-5 league data for 2025/26 (plus 2024/25 player stats for the
cross-year progression use case) from Understat and cache it as Parquet under
data/raw/understat/.

Understat is used because FBref is IP-blocked (403) in this environment and the
paid APIs in the README require keys that are not available. See NOTES.md.

Usage:
    python -m pipeline.scrape                # scrape everything
    python -m pipeline.scrape --quick        # only the focus season, players only
"""
import argparse
import sys
import warnings

import soccerdata as sd

try:
    from config import LEAGUES, RAW_DIR, PLAYER_SEASONS, MATCH_SEASONS, FOCUS_SEASON
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import LEAGUES, RAW_DIR, PLAYER_SEASONS, MATCH_SEASONS, FOCUS_SEASON

warnings.filterwarnings("ignore")

LEAGUE_KEYS = list(LEAGUES.keys())


def _reader(seasons):
    return sd.Understat(leagues=LEAGUE_KEYS, seasons=seasons)


def _save(df, name: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"{name}.parquet"
    # reset_index so the MultiIndex (league/season/...) becomes real columns.
    df.reset_index().to_parquet(out)
    print(f"  -> {name}: {len(df):>5} rows  ->  {out.relative_to(RAW_DIR.parent.parent)}")


def scrape(quick: bool = False) -> None:
    print(f"Leagues: {', '.join(LEAGUE_KEYS)}")

    # 1) Player season stats -- pulled for every season so progression works.
    player_seasons = [FOCUS_SEASON] if quick else PLAYER_SEASONS
    print(f"\n[1/3] Player season stats for seasons {player_seasons} ...")
    df = _reader(player_seasons).read_player_season_stats()
    _save(df, "player_season_stats")

    if quick:
        print("\nQuick mode: skipping matches & team stats.")
        return

    # 2) Schedule / matches -- all seasons (enables historical standings).
    print(f"\n[2/3] Schedule (matches) for seasons {MATCH_SEASONS} ...")
    df = _reader(MATCH_SEASONS).read_schedule()
    _save(df, "schedule")

    # 3) Team match stats -- all seasons.
    print(f"\n[3/3] Team match stats for seasons {MATCH_SEASONS} ...")
    df = _reader(MATCH_SEASONS).read_team_match_stats()
    _save(df, "team_match_stats")

    print("\nScrape complete.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="focus season, players only")
    args = ap.parse_args()
    scrape(quick=args.quick)
