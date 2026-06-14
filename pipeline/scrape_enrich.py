"""
FotMob enrichment scraper -- backfills the Opta-style stats Understat does not
carry (big chances, chances created, successful dribbles, tackles,
interceptions, recoveries, pass completion) for the Top-5 leagues, 2025/26.

How it works (verified against the live API, June 2026):
  1. ONE signed request per league to /api/data/leagues?id=<id>&season=...
     returns `stats.players` -- 37 ranked stat categories, each with a
     `fetchAllUrl` on the public data.fotmob.com CDN (no auth needed).
  2. For each stat we care about, fetch that CDN list (all ranked players) and
     merge by FotMob player id into one row per player.

Output: data/raw/fotmob/player_enrichment_<season>.parquet
Run:    python -m pipeline.scrape_enrich
"""
import sys
import time
import warnings

import pandas as pd
import tls_requests

try:
    from config import FOTMOB_LEAGUE_IDS, RAW_DIR, ENRICH_SEASONS, fotmob_season
    from pipeline.fotmob_auth import FotmobAuth
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import FOTMOB_LEAGUE_IDS, RAW_DIR, ENRICH_SEASONS, fotmob_season
    from pipeline.fotmob_auth import FotmobAuth

warnings.filterwarnings("ignore")

FOTMOB_RAW = RAW_DIR.parent / "fotmob"
RATE_LIMIT_SEC = 0.4

# FotMob stat `name` -> extractor(row) -> {column: value}.
#
# Field semantics were reverse-engineered + verified against the live CDN:
#   * "total" categories  -> StatValue IS the season total.
#   * "per 90" categories -> StatValue is the per-90 rate; SubStatValue is the
#       season TOTAL for tackles/interceptions/recoveries, but a PERCENTAGE
#       (success% / accuracy%) for dribbles & passes -- so for those two the
#       total is derived as round(per90 * minutes / 90).
# StatValueCount is NOT a clean season total and is deliberately ignored.
def _total_from_per90(r) -> int | None:
    sv, mins = r.get("StatValue"), r.get("MinutesPlayed")
    if sv is None or not mins:
        return None
    return round(sv * mins / 90)


STAT_EXTRACT = {
    "big_chance_created": lambda r: {"big_chances_created": r.get("StatValue")},
    "big_chance_missed":  lambda r: {"big_chances_missed": r.get("StatValue")},
    "total_att_assist":   lambda r: {"chances_created": r.get("StatValue")},
    "won_contest":        lambda r: {"dribbles_completed": _total_from_per90(r),
                                     "dribbles_per90": r.get("StatValue"),
                                     "dribble_success_pct": r.get("SubStatValue")},
    "total_tackle":       lambda r: {"tackles": r.get("SubStatValue"),
                                     "tackles_per90": r.get("StatValue")},
    "interception":       lambda r: {"interceptions": r.get("SubStatValue"),
                                     "interceptions_per90": r.get("StatValue")},
    "ball_recovery":      lambda r: {"recoveries": r.get("SubStatValue"),
                                     "recoveries_per90": r.get("StatValue")},
    "accurate_pass":      lambda r: {"passes_completed": _total_from_per90(r),
                                     "pass_accuracy_pct": r.get("SubStatValue")},
    "rating":             lambda r: {"fotmob_rating": r.get("StatValue")},
}


def _fetch_cdn_list(url: str) -> list[dict]:
    """Fetch a data.fotmob.com per-stat list (public CDN, no auth)."""
    r = tls_requests.get(url, timeout=25)
    r.raise_for_status()
    data = r.json()
    toplists = data.get("TopLists") or []
    return toplists[0].get("StatList", []) if toplists else []


def scrape_league(auth: FotmobAuth, league_key: str, league_id: int,
                  season_code: str) -> pd.DataFrame:
    season = fotmob_season(season_code).replace("/", "%2F")
    print(f"  {league_key} (FotMob id={league_id}) {fotmob_season(season_code)} ...")
    league = auth.get(f"/api/data/leagues?id={league_id}&season={season}")
    stats = (league.get("stats") or {}).get("players") or []
    cats = {c["name"]: c for c in stats}
    if not cats:
        print("    (no player stats for this season)")
        return pd.DataFrame()

    players: dict[int, dict] = {}
    for stat_name, extractor in STAT_EXTRACT.items():
        cat = cats.get(stat_name)
        if not cat or not cat.get("fetchAllUrl"):
            print(f"    {stat_name:20s} (not offered for this league)")
            continue
        try:
            rows = _fetch_cdn_list(cat["fetchAllUrl"])
        except Exception as e:
            print(f"    {stat_name:20s} FAILED: {repr(e)[:70]}")
            time.sleep(RATE_LIMIT_SEC)
            continue
        for row in rows:
            pid = row.get("ParticiantId") or row.get("ParticipantId")  # FotMob's own typo
            if pid is None:
                continue
            rec = players.setdefault(int(pid), {
                "fotmob_player_id": int(pid),
                "player_name": row.get("ParticipantName"),
                "team_name": row.get("TeamName"),
                "minutes_played": row.get("MinutesPlayed"),
                "matches_played": row.get("MatchesPlayed"),
            })
            rec.update(extractor(row))
        print(f"    {stat_name:20s} {len(rows):>4} players")
        time.sleep(RATE_LIMIT_SEC)

    if not players:
        return pd.DataFrame()
    df = pd.DataFrame(players.values())
    df.insert(0, "league_key", league_key)
    df.insert(1, "season", season_code)
    return df


def scrape_season(auth: FotmobAuth, season_code: str) -> None:
    """Scrape one season across all leagues -> one per-season parquet."""
    frames = []
    print(f"\n=== FotMob enrichment {fotmob_season(season_code)} ===")
    for league_key, league_id in FOTMOB_LEAGUE_IDS.items():
        try:
            df = scrape_league(auth, league_key, league_id, season_code)
        except Exception as e:
            print(f"  {league_key} FAILED: {repr(e)[:100]}")
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        print(f"  no enrichment for {season_code}")
        return
    out = pd.concat(frames, ignore_index=True)
    path = FOTMOB_RAW / f"player_enrichment_{season_code}.parquet"
    out.to_parquet(path)
    print(f"  saved {len(out)} rows across {len(frames)} leagues -> {path.name}")


def scrape(seasons=None) -> None:
    FOTMOB_RAW.mkdir(parents=True, exist_ok=True)
    auth = FotmobAuth()
    for season_code in (seasons or ENRICH_SEASONS):
        scrape_season(auth, season_code)
    print("\nFotMob enrichment scrape complete.")


if __name__ == "__main__":
    scrape()
