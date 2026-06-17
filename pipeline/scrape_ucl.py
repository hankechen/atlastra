"""
SofaScore UEFA Champions League player-stats scraper.

The domestic sources (Understat / FotMob / datamb) carry no continental
competition. SofaScore does, via a rich per-player season-stats endpoint
(~77 fields) that goes back to **2008/09** -- nearly the full field set even in
the oldest seasons (only `expectedGoals` / `possessionLost` are missing pre-
~2020). So UCL gets advanced stats for every season we collect.

How it works:
  1. GET /unique-tournament/7/seasons               -> {year -> seasonId}
  2. per season, GET /.../statistics/info           -> which fields exist
  3. per season, page through /.../statistics        -> one row per player
       ?accumulation=total&group=ALL&order=-rating&fields=<csv>&limit=100&offset=N

SofaScore is bot-protected: it 403s ("challenge") if you send the usual
CORS/sec-fetch headers, but a *bare* browser-TLS request passes -- so we send
no headers at all (see config note). Polite ~1.2s delay between calls.

Output: data/raw/sofascore/ucl_player_stats_<season>.parquet  (per season)
        data/raw/sofascore/ucl_player_stats_all.parquet        (combined)
Run:    python -m pipeline.scrape_ucl
"""
import sys
import time
import warnings

import pandas as pd
import tls_requests

try:
    from config import (RAW_DIR, SOFASCORE_BASE, SOFASCORE_UCL_TOURNAMENT_ID,
                        UCL_MIN_SEASON_CODE, season_label)
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import (RAW_DIR, SOFASCORE_BASE, SOFASCORE_UCL_TOURNAMENT_ID,
                        UCL_MIN_SEASON_CODE, season_label)

warnings.filterwarnings("ignore")

SOFA_RAW = RAW_DIR.parent / "sofascore"
RATE_LIMIT_SEC = 1.2
PAGE_SIZE = 100
UT = SOFASCORE_UCL_TOURNAMENT_ID


def _get(path: str) -> dict:
    """Bare GET -- NO extra headers (adding CORS headers trips SofaScore's bot
    challenge; the default browser-TLS fingerprint alone is accepted)."""
    r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _season_code(year: str) -> str:
    """SofaScore '08/09' -> project code '0809'."""
    return year.replace("/", "")


def list_seasons() -> list[tuple[str, int]]:
    """[(season_code, season_id)] for UCL seasons >= UCL_MIN_SEASON_CODE,
    oldest first. Future seasons with no data yet are filtered out at scrape time."""
    seasons = _get(f"/unique-tournament/{UT}/seasons").get("seasons", [])
    out = []
    for s in seasons:
        code = _season_code(s["year"])
        # '0809'..'9900' -> sortable: seasons are 2-digit start year; everything
        # from 08/09 up to 25/26 sorts fine as strings except the 90s wrap, which
        # UCL (2008+) never hits. Keep codes whose start-year >= 08 and <= 26.
        start = int(code[:2])
        if 8 <= start <= 26:
            out.append((code, s["id"]))
    out.sort(key=lambda t: t[0])
    return [t for t in out if t[0] >= UCL_MIN_SEASON_CODE]


def _season_fields(season_id: int) -> list[str]:
    """The detailed stat fields actually offered for this season (the set we may
    request -- asking for a field a season lacks would 400 the call)."""
    info = _get(f"/unique-tournament/{UT}/season/{season_id}/statistics/info")
    detailed = (info.get("statisticsGroups") or {}).get("detailed") or {}
    fields = set()
    for items in detailed.values():
        fields.update(items)
    # ensure the identity/volume basics are present even if not in 'detailed'
    fields.update({"appearances", "minutesPlayed", "rating", "goals", "assists"})
    return sorted(fields)


def scrape_season(season_code: str, season_id: int) -> pd.DataFrame:
    print(f"  UCL {season_label(season_code)} (id={season_id}) ...")
    fields = _season_fields(season_id)
    time.sleep(RATE_LIMIT_SEC)
    fields_csv = ",".join(fields)
    base = (f"/unique-tournament/{UT}/season/{season_id}/statistics"
            f"?accumulation=total&group=ALL&order=-rating"
            f"&fields={fields_csv}&limit={PAGE_SIZE}")

    rows, offset, pages = [], 0, None
    while True:
        page = _get(f"{base}&offset={offset}")
        pages = page.get("pages", 1)
        results = page.get("results", [])
        for p in results:
            rec = {
                "sofascore_player_id": p.get("player", {}).get("id"),
                "player_name": p.get("player", {}).get("name"),
                "sofascore_team_id": p.get("team", {}).get("id"),
                "team_name": p.get("team", {}).get("name"),
            }
            for f in fields:
                rec[f] = p.get(f)
            rows.append(rec)
        cur = offset // PAGE_SIZE + 1
        if cur >= pages or not results:
            break
        offset += PAGE_SIZE
        time.sleep(RATE_LIMIT_SEC)

    df = pd.DataFrame(rows)
    if df.empty:
        print("    (no player stats)")
        return df
    df.insert(0, "season", season_code)
    df.insert(1, "competition", "UCL")
    print(f"    {len(df):>4} players, {df.shape[1]} cols ({pages} pages)")
    return df


def scrape(min_season: str = None) -> None:
    SOFA_RAW.mkdir(parents=True, exist_ok=True)
    floor = min_season or UCL_MIN_SEASON_CODE
    seasons = [s for s in list_seasons() if s[0] >= floor]
    print(f"=== SofaScore UCL: from {season_label(seasons[0][0])} "
          f"({len(seasons)} candidate seasons; not-yet-started ones are skipped) ===")
    time.sleep(RATE_LIMIT_SEC)

    frames = []
    for code, sid in seasons:
        try:
            df = scrape_season(code, sid)
        except tls_requests.exceptions.HTTPError as e:
            # A 404 here means the season exists in SofaScore's list but has no
            # stats yet -- i.e. an upcoming season that hasn't kicked off. Skip
            # quietly; it'll populate itself on a future run.
            if "404" in str(e):
                print(f"  UCL {season_label(code)}: no data yet -- skipped (season not started)")
            else:
                print(f"    {season_label(code)} FAILED: {repr(e)[:90]}")
            time.sleep(RATE_LIMIT_SEC)
            continue
        except Exception as e:
            print(f"    {season_label(code)} FAILED: {repr(e)[:90]}")
            time.sleep(RATE_LIMIT_SEC)
            continue
        if not df.empty:
            df.to_parquet(SOFA_RAW / f"ucl_player_stats_{code}.parquet")
            frames.append(df)
        time.sleep(RATE_LIMIT_SEC)

    # Rebuild the combined file from ALL per-season parquets on disk (not just
    # the ones scraped this run) so a partial/floored run never clobbers it.
    per_season = sorted(SOFA_RAW.glob("ucl_player_stats_[0-9]*.parquet"))
    if per_season:
        combined = pd.concat([pd.read_parquet(p) for p in per_season],
                            ignore_index=True)
        combined.to_parquet(SOFA_RAW / "ucl_player_stats_all.parquet")
        print(f"\nSaved {len(combined)} player-seasons across {len(per_season)} "
              f"seasons -> ucl_player_stats_all.parquet")
    print("SofaScore UCL scrape complete.")


if __name__ == "__main__":
    scrape()
