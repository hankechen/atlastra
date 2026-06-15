"""Historical match data from football-data.co.uk (back to 2008/09).

Understat (the primary source) only starts at 2014/15. football-data.co.uk
publishes free per-season CSVs of match results plus basic match stats (shots,
shots on target, fouls, corners, cards) for the Top-5 leagues going back decades
-- the "easy data without advanced stats" layer. No xG, no player-level data.

soccerdata's MatchHistory reader wraps the same site but its TLS client is
served 503s here, so we fetch the CSVs directly with urllib (HTTP 200).

Usage:
    python -m pipeline.scrape_history            # 2008/09 -> 2025/26, all 5 leagues
    python -m pipeline.scrape_history --gap      # only 2008/09 -> 2013/14 (pre-Understat)

Caches raw CSVs and a combined Parquet under data/raw/matchhistory/.
"""
import argparse
import io
import sys
import urllib.request as request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "matchhistory"

# football-data.co.uk league file codes -> our league_key.
LEAGUE_CODES = {
    "E0":  "ENG-Premier League",
    "SP1": "ESP-La Liga",
    "I1":  "ITA-Serie A",
    "D1":  "GER-Bundesliga",
    "F1":  "FRA-Ligue 1",
}

# Seasons in football-data's "0809" URL style.
def _season_codes(start_yy: int, end_yy: int) -> list[str]:
    out = []
    for yy in range(start_yy, end_yy):
        out.append(f"{yy % 100:02d}{(yy + 1) % 100:02d}")
    return out

ALL_HISTORY = _season_codes(2008, 2026)   # 0809 .. 2526
GAP_ONLY    = _season_codes(2008, 2014)   # 0809 .. 1314 (before Understat)

BASE = "https://www.football-data.co.uk/mmz4281"

# Columns we keep (renamed). Everything else (betting odds) is dropped.
KEEP = {
    "Date": "match_date", "HomeTeam": "home_team", "AwayTeam": "away_team",
    "FTHG": "home_goals", "FTAG": "away_goals", "FTR": "result",
    "HTHG": "home_goals_ht", "HTAG": "away_goals_ht", "HTR": "result_ht",
    "Referee": "referee",
    "HS": "home_shots", "AS": "away_shots",
    "HST": "home_shots_ot", "AST": "away_shots_ot",
    "HF": "home_fouls", "AF": "away_fouls",
    "HC": "home_corners", "AC": "away_corners",
    "HY": "home_yellows", "AY": "away_yellows",
    "HR": "home_reds", "AR": "away_reds",
}


def _fetch(code: str, season: str) -> pd.DataFrame | None:
    url = f"{BASE}/{season}/{code}.csv"
    cache = RAW / season / f"{code}.csv"
    if cache.exists():
        raw = cache.read_bytes()
    else:
        try:
            req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = request.urlopen(req, timeout=30).read()
        except Exception as e:  # noqa: BLE001
            print(f"  !! {code} {season}: {e}")
            return None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    # latin-1: some files have non-UTF8 bytes (accented club/referee names).
    df = pd.read_csv(io.BytesIO(raw), encoding="latin-1",
                     on_bad_lines="skip")
    df = df.dropna(how="all")
    df = df[[c for c in KEEP if c in df.columns]].rename(columns=KEEP)
    df = df.dropna(subset=["home_team", "away_team"])
    df.insert(0, "league_key", LEAGUE_CODES[code])
    df.insert(1, "season", season)
    return df


def scrape(seasons: list[str]) -> pd.DataFrame:
    frames = []
    for code, lk in LEAGUE_CODES.items():
        for s in seasons:
            df = _fetch(code, s)
            if df is not None and len(df):
                frames.append(df)
                print(f"  {lk:20} {s}: {len(df):3} matches")
    combined = pd.concat(frames, ignore_index=True)
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / "matches.parquet"
    combined.to_parquet(out)
    print(f"\nSaved {len(combined):,} matches -> {out.relative_to(ROOT)}")
    return combined


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", action="store_true",
                    help="only 2008/09-2013/14 (the pre-Understat gap)")
    args = ap.parse_args()
    seasons = GAP_ONLY if args.gap else ALL_HISTORY
    print(f"Fetching {len(seasons)} seasons x {len(LEAGUE_CODES)} leagues "
          f"from football-data.co.uk ...")
    df = scrape(seasons)

    # --- summary: what stats did we actually get? ---
    stat_cols = [c for c in df.columns if c not in ("league_key", "season")]
    print("\n=== Stat availability (non-null %) ===")
    for c in stat_cols:
        pct = 100 * df[c].notna().mean()
        print(f"  {c:16} {pct:5.1f}%")
    print("\n=== Matches per season ===")
    print(df.groupby("season").size().to_string())
    sys.stdout.flush()
