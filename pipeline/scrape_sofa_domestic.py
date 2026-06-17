"""
SofaScore Top-5 domestic defensive-stat scraper.

datamb (the rating engine's source) carries no clearances or errors, so the CB
and DM weight vectors had to drop them. SofaScore does carry both, for the same
2025/26 domestic leagues -- so we pull just those fields here and fuzzy-join them
onto `player_wyscout` in pipeline.load_sofa_domestic.

Fields: clearances, errorLeadToGoal, errorLeadToShot (+ minutesPlayed for per-90,
appearances). Same bare-header SofaScore access + pagination as scrape_ucl.

Output: data/raw/sofascore/domestic_defense_<season>.parquet
Run:    python -m pipeline.scrape_sofa_domestic
"""
import sys
import time
import warnings

import pandas as pd
import tls_requests

try:
    from config import (RAW_DIR, SOFASCORE_BASE, SOFASCORE_TOP5_TOURNAMENTS,
                        FOCUS_SEASON)
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import (RAW_DIR, SOFASCORE_BASE, SOFASCORE_TOP5_TOURNAMENTS,
                        FOCUS_SEASON)

warnings.filterwarnings("ignore")

SOFA_RAW = RAW_DIR.parent / "sofascore"
RATE_LIMIT_SEC = 1.2
PAGE_SIZE = 100
FIELDS = "clearances,errorLeadToGoal,errorLeadToShot,minutesPlayed,appearances"


def _get(path: str) -> dict:
    r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _season_year(code: str) -> str:
    """'2526' -> '25/26' (SofaScore season label)."""
    return f"{code[:2]}/{code[2:]}"


def _season_id(tid: int, code: str) -> int | None:
    year = _season_year(code)
    for s in _get(f"/unique-tournament/{tid}/seasons").get("seasons", []):
        if s.get("year") == year:
            return s["id"]
    return None


def scrape_league(league_key: str, tid: int, code: str) -> pd.DataFrame:
    sid = _season_id(tid, code)
    if sid is None:
        print(f"  {league_key}: no {_season_year(code)} season -- skipped")
        return pd.DataFrame()
    time.sleep(RATE_LIMIT_SEC)
    base = (f"/unique-tournament/{tid}/season/{sid}/statistics"
            f"?accumulation=total&group=ALL&order=-clearances&fields={FIELDS}"
            f"&limit={PAGE_SIZE}")
    rows, offset = [], 0
    while True:
        page = _get(f"{base}&offset={offset}")
        pages, results = page.get("pages", 1), page.get("results", [])
        for p in results:
            rows.append({
                "league_key": league_key,
                "sofascore_player_id": p.get("player", {}).get("id"),
                "player_name": p.get("player", {}).get("name"),
                "team_name": p.get("team", {}).get("name"),
                "clearances": p.get("clearances"),
                "error_lead_to_goal": p.get("errorLeadToGoal"),
                "error_lead_to_shot": p.get("errorLeadToShot"),
                "minutes_played": p.get("minutesPlayed"),
                "appearances": p.get("appearances"),
            })
        if offset // PAGE_SIZE + 1 >= pages or not results:
            break
        offset += PAGE_SIZE
        time.sleep(RATE_LIMIT_SEC)
    print(f"  {league_key}: {len(rows)} players")
    return pd.DataFrame(rows)


def scrape(season: str = FOCUS_SEASON) -> None:
    SOFA_RAW.mkdir(parents=True, exist_ok=True)
    print(f"=== SofaScore Top-5 domestic defense {_season_year(season)} ===")
    time.sleep(RATE_LIMIT_SEC)
    frames = []
    for league_key, tid in SOFASCORE_TOP5_TOURNAMENTS.items():
        try:
            df = scrape_league(league_key, tid, season)
        except Exception as e:
            print(f"  {league_key} FAILED: {repr(e)[:90]}")
            time.sleep(RATE_LIMIT_SEC)
            continue
        if not df.empty:
            frames.append(df)
        time.sleep(RATE_LIMIT_SEC)
    if not frames:
        print("  no domestic defense data")
        return
    out = pd.concat(frames, ignore_index=True)
    out["season"] = season
    out.to_parquet(SOFA_RAW / f"domestic_defense_{season}.parquet")
    print(f"\nSaved {len(out)} players across {len(frames)} leagues "
          f"-> domestic_defense_{season}.parquet")


if __name__ == "__main__":
    scrape()
