"""
SofaScore Top-5 domestic goalkeeper stats, every season.

Why this exists: the rating engine can only rate a keeper on keeper metrics
(save %, goals conceded, clean sheets, saves), and its domestic source for those
is datamb/Wyscout -- which publishes the CURRENT season only. So league GK
ratings existed for 2025/26 and for no other season, which in turn meant no
goalkeeper had a season-to-season transition anywhere in the warehouse: the
trajectory model had to exclude keepers outright, and their profiles were
thinner than everyone else's.

SofaScore's domestic statistics endpoint carries the same four metrics and goes
back as far as the league does, so it can fill the other eleven seasons.

Note `group=goalkeeping` selects the *stat group*, not the players -- the list is
still the whole league. Ordering by -saves puts the keepers first, so one page of
100 covers a league's keepers and we stop as soon as the saves run out, rather
than paging through 500 outfielders per league-season.

Output: data/raw/sofascore/domestic_gk_<season>.parquet
Run:    python -m pipeline.scrape_sofa_gk            # every season
        python -m pipeline.scrape_sofa_gk 1819       # one season
"""
import sys
import time
import warnings

import pandas as pd
import tls_requests

try:
    from config import (RAW_DIR, SOFASCORE_BASE, SOFASCORE_TOP5_TOURNAMENTS,
                        ALL_SEASONS)
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import (RAW_DIR, SOFASCORE_BASE, SOFASCORE_TOP5_TOURNAMENTS,
                        ALL_SEASONS)

warnings.filterwarnings("ignore")

SOFA_RAW = RAW_DIR.parent / "sofascore"
RATE_LIMIT_SEC = 1.2
PAGE_SIZE = 100
MAX_PAGES = 4          # a top-5 league has ~35-60 keepers with any minutes
FIELDS = ("saves,goalsConceded,cleanSheet,savedShotsFromInsideTheBox,"
          "savedShotsFromOutsideTheBox,minutesPlayed,appearances")


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
        print(f"  {league_key}: no {_season_year(code)} season -- skipped", flush=True)
        return pd.DataFrame()
    time.sleep(RATE_LIMIT_SEC)
    base = (f"/unique-tournament/{tid}/season/{sid}/statistics"
            f"?accumulation=total&group=goalkeeping&order=-saves&fields={FIELDS}"
            f"&limit={PAGE_SIZE}")
    rows = []
    for page_i in range(MAX_PAGES):
        page = _get(f"{base}&offset={page_i * PAGE_SIZE}")
        results = page.get("results", [])
        keepers = [p for p in results if (p.get("saves") or 0) > 0]
        for p in keepers:
            rows.append({
                "league_key": league_key,
                "sofascore_player_id": p.get("player", {}).get("id"),
                "player_name": p.get("player", {}).get("name"),
                "team_name": p.get("team", {}).get("name"),
                "saves": p.get("saves"),
                "goals_conceded": p.get("goalsConceded"),
                "clean_sheets": p.get("cleanSheet"),
                "saves_inside_box": p.get("savedShotsFromInsideTheBox"),
                "saves_outside_box": p.get("savedShotsFromOutsideTheBox"),
                "minutes_played": p.get("minutesPlayed"),
                "appearances": p.get("appearances"),
            })
        # sorted by saves descending, so once a page stops being all keepers the
        # rest of the league is outfielders and there is nothing left to fetch
        if len(keepers) < len(results) or not results:
            break
        if page_i + 1 >= page.get("pages", 1):
            break
        time.sleep(RATE_LIMIT_SEC)
    print(f"  {league_key}: {len(rows)} keepers", flush=True)
    return pd.DataFrame(rows)


def scrape(season: str) -> int:
    SOFA_RAW.mkdir(parents=True, exist_ok=True)
    print(f"=== SofaScore Top-5 goalkeepers {_season_year(season)} ===", flush=True)
    frames = []
    for league_key, tid in SOFASCORE_TOP5_TOURNAMENTS.items():
        try:
            df = scrape_league(league_key, tid, season)
        except Exception as e:  # noqa: BLE001
            print(f"  {league_key} FAILED: {repr(e)[:90]}", flush=True)
            time.sleep(RATE_LIMIT_SEC)
            continue
        if not df.empty:
            frames.append(df)
        time.sleep(RATE_LIMIT_SEC)
    if not frames:
        print("  no goalkeeper data", flush=True)
        return 0
    out = pd.concat(frames, ignore_index=True)
    out["season"] = season
    out.to_parquet(SOFA_RAW / f"domestic_gk_{season}.parquet")
    print(f"Saved {len(out)} keepers -> domestic_gk_{season}.parquet\n", flush=True)
    return len(out)


if __name__ == "__main__":
    seasons = [sys.argv[1]] if len(sys.argv) > 1 else ALL_SEASONS
    total = 0
    for s in seasons:
        # already-scraped seasons are skipped so a re-run resumes rather than refetches
        if len(seasons) > 1 and (SOFA_RAW / f"domestic_gk_{s}.parquet").exists():
            print(f"=== {s} already cached -- skipped ===", flush=True)
            continue
        total += scrape(s)
    print(f"total {total} keeper-seasons", flush=True)
