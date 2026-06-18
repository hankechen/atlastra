"""
SofaScore season heatmaps for Top-5 domestic players (the profile-page heatmap).

For each league we list the season's players (>= MIN_MINUTES) from the statistics
endpoint, then pull each player's aggregated season heatmap
(/player/{id}/unique-tournament/{tid}/season/{sid}/heatmap -> {x,y,count} points)
and bin it into a compact GW x GH density grid (normalised 0-1) so it stores small
and renders directly. Same bare-header tls_requests access as scrape_ucl /
scrape_sofa_domestic. Saved incrementally per league so a mid-run failure keeps
the leagues already done.

Output: data/raw/sofascore/heatmaps_<season>.parquet
Run:    python -m pipeline.scrape_sofa_heatmaps
Map to our player_id + load with: python -m pipeline.load_sofa_heatmaps
"""
import json
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
LIST_SLEEP = 1.2          # between paginated list calls
HM_SLEEP = 0.6            # between per-player heatmap calls
PAGE_SIZE = 100
MIN_MINUTES = 270         # ~3 full matches
GW, GH = 30, 20           # heatmap grid: length (x) x width (y)


def _get(path: str) -> dict:
    r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def _season_id(tid: int, code: str):
    year = f"{code[:2]}/{code[2:]}"
    for s in _get(f"/unique-tournament/{tid}/seasons").get("seasons", []):
        if s.get("year") == year:
            return s["id"]
    return None


def _players(tid: int, sid: int) -> list:
    base = (f"/unique-tournament/{tid}/season/{sid}/statistics?accumulation=total"
            f"&group=ALL&order=-minutesPlayed&fields=minutesPlayed&limit={PAGE_SIZE}")
    out, offset = [], 0
    while True:
        page = _get(f"{base}&offset={offset}")
        results = page.get("results", [])
        for p in results:
            mins = p.get("minutesPlayed") or 0
            if mins >= MIN_MINUTES:
                out.append((p["player"]["id"], p["player"]["name"],
                            p.get("team", {}).get("name"), mins))
        last = (mins < MIN_MINUTES) if results else True   # ordered desc -> stop early
        if last or offset // PAGE_SIZE + 1 >= page.get("pages", 1) or not results:
            break
        offset += PAGE_SIZE
        time.sleep(LIST_SLEEP)
    return out


def _grid(points: list) -> str:
    """Bin {x,y,count} points into a normalised GH x GW grid -> JSON string."""
    g = [[0.0] * GW for _ in range(GH)]
    for p in points:
        x, y, c = p.get("x"), p.get("y"), p.get("count", 1)
        if x is None or y is None:
            continue
        cx = min(GW - 1, max(0, int(x / 100 * GW)))
        cy = min(GH - 1, max(0, int(y / 100 * GH)))
        g[cy][cx] += c
    mx = max((v for row in g for v in row), default=0)
    if mx:
        g = [[round(v / mx, 3) for v in row] for row in g]
    return json.dumps(g)


def scrape(season: str = FOCUS_SEASON) -> None:
    SOFA_RAW.mkdir(parents=True, exist_ok=True)
    path = SOFA_RAW / f"heatmaps_{season}.parquet"
    print(f"=== SofaScore heatmaps {season} (grid {GW}x{GH}, min {MIN_MINUTES} min) ===")
    rows = []
    for lk, tid in SOFASCORE_TOP5_TOURNAMENTS.items():
        try:
            sid = _season_id(tid, season)
            if not sid:
                print(f"  {lk}: no season -- skipped"); continue
            time.sleep(LIST_SLEEP)
            players = _players(tid, sid)
        except Exception as e:  # noqa: BLE001
            print(f"  {lk} LIST FAILED: {repr(e)[:80]}"); continue
        ok = 0
        for spid, name, team, mins in players:
            try:
                hm = _get(f"/player/{spid}/unique-tournament/{tid}/season/{sid}/heatmap")
                pts = hm.get("heatmap") or hm.get("points") or []
                if pts:
                    rows.append({"sofascore_player_id": spid, "player_name": name,
                                 "team_name": team, "league_key": lk, "season": season,
                                 "grid": _grid(pts)})
                    ok += 1
            except Exception:  # noqa: BLE001 -- skip one player, keep going
                pass
            time.sleep(HM_SLEEP)
        pd.DataFrame(rows).to_parquet(path)     # incremental save after each league
        print(f"  {lk}: {ok}/{len(players)} heatmaps  (total {len(rows)})")
    print(f"\nSaved {len(rows)} heatmaps -> {path}")


if __name__ == "__main__":
    scrape()
