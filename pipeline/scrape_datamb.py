"""
datamb.football enrichment scraper -- Wyscout-grade per-player season stats.

This is the source that finally carries the metrics neither Understat nor FotMob
expose: **progressive passes & carries per 90**, touches in box, take-ons (+
success %), aerial %, crosses into box, PSxG-GA ("prevented goals"), save %, and
~130 more -- enough to drive the full position-weighted rating spec.

How it works:
  datamb serves a static Wyscout .xlsx per position bucket on its CDN:
    https://datamb.football/database/CURRENT/TOP7<season>/<POS>/<POS>.xlsx
  There are six buckets (GK, CB, FB, CM, FW, ST) covering the "TOP7" leagues
  (our Top-5 + Eredivisie + Primeira Liga). Each file shares the same 143-column
  schema. We download all six, tag every row with its datamb position bucket,
  concat, and cache one parquet per season. The files carry no league column
  (only the team name), so league assignment is left to the load step.

  Only the CURRENT season is published, so this is 2025/26 only.

Output: data/raw/datamb/player_wyscout_<season>.parquet
Run:    python -m pipeline.scrape_datamb
"""
import io
import sys
import time
import warnings

import pandas as pd
import tls_requests

try:
    from config import DATAMB_BASE, DATAMB_POSITIONS, DATAMB_SEASONS, RAW_DIR
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DATAMB_BASE, DATAMB_POSITIONS, DATAMB_SEASONS, RAW_DIR

warnings.filterwarnings("ignore")

DATAMB_RAW = RAW_DIR.parent / "datamb"
RATE_LIMIT_SEC = 0.5


def _datamb_season(code: str) -> str:
    """'2526' -> 'TOP72526' (the path segment datamb uses for a season)."""
    return f"TOP7{code}"


# datamb's player index labels each player with ONE canonical position; we use it
# to pick a player's MAIN position (season minutes are identical across a player's
# buckets, so they can't disambiguate). label -> our bucket code.
SEARCHBAR_URL = "https://datamb.football/database/searchbar.csv"
LABEL_TO_BUCKET = {
    "Goalkeeper": "GK", "Centre-back": "CB", "Full-back": "FB",
    "Midfielder": "CM", "Winger": "FW", "Striker": "ST",
}


def _fetch_main_positions() -> pd.DataFrame:
    """Player -> main position bucket, from datamb's searchbar index."""
    import re
    raw = tls_requests.get(SEARCHBAR_URL, timeout=30).text
    rows = []
    for line in raw.splitlines():
        m = re.match(r"^(.*?)\s*\(([^)]+)\),", line)
        if not m:
            continue
        label = m.group(2).strip()
        if label in LABEL_TO_BUCKET:
            rows.append({"player": m.group(1).strip(),
                         "main_label": label,
                         "main_position": LABEL_TO_BUCKET[label]})
    return pd.DataFrame(rows).drop_duplicates("player")


def _fetch_position(season_code: str, pos_code: str) -> pd.DataFrame:
    """Download and parse one position .xlsx, tagged with its bucket + season."""
    season = _datamb_season(season_code)
    url = f"{DATAMB_BASE}/{season}/{pos_code}/{pos_code}.xlsx"
    r = tls_requests.get(url, timeout=40)
    if r.status_code != 200:
        print(f"    {pos_code:3s} HTTP {r.status_code} -- skipped ({url})")
        return pd.DataFrame()
    df = pd.read_excel(io.BytesIO(r.content))
    df.insert(0, "season", season_code)
    df.insert(1, "datamb_position", pos_code)
    df.insert(2, "position_label", DATAMB_POSITIONS[pos_code])
    print(f"    {pos_code:3s} {len(df):>4} players, {df.shape[1]} cols")
    return df


def scrape_season(season_code: str) -> None:
    frames = []
    print(f"\n=== datamb.football {season_code} (Wyscout per-position) ===")
    for pos_code in DATAMB_POSITIONS:
        try:
            df = _fetch_position(season_code, pos_code)
        except Exception as e:
            print(f"    {pos_code:3s} FAILED: {repr(e)[:80]}")
            df = pd.DataFrame()
        if not df.empty:
            frames.append(df)
        time.sleep(RATE_LIMIT_SEC)
    if not frames:
        print(f"  no datamb data for {season_code}")
        return
    out = pd.concat(frames, ignore_index=True)
    path = DATAMB_RAW / f"player_wyscout_{season_code}.parquet"
    out.to_parquet(path)
    print(f"  saved {len(out)} rows across {len(frames)} buckets -> {path.name}")

    try:
        pos = _fetch_main_positions()
        pos_path = DATAMB_RAW / f"player_positions_{season_code}.parquet"
        pos.to_parquet(pos_path)
        print(f"  saved {len(pos)} main-position labels -> {pos_path.name}")
    except Exception as e:
        print(f"  main-position index FAILED: {repr(e)[:80]}")


def scrape(seasons=None) -> None:
    DATAMB_RAW.mkdir(parents=True, exist_ok=True)
    for season_code in (seasons or DATAMB_SEASONS):
        scrape_season(season_code)
    print("\ndatamb.football scrape complete.")


if __name__ == "__main__":
    scrape()
