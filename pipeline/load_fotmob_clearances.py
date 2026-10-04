"""
Backfill `clearances_per_90` onto `player_wyscout` from FotMob, for seasons where
SofaScore's domestic-defense scrape (pipeline.load_sofa_domestic) can't run.

SofaScore now blocks even the Mac's residential IP (see [[aws-deployment]],
[[datamb-season-fix]] -- a separate, already-exhausted hard problem), so
2026/27's clearances_per_90/errors_per_90 columns were landing NULL -> neutral
for every player, dropping a real signal out of the DM vector's
"blocks_clearances" metric (rate.py BLKCLR, 10% weight).

FotMob's public per-league CDN carries a "Clearances per 90" stat
(`effective_clearance`) that's already a per-90 RATE in its own StatValue (not
a total needing backout -- verified against real low-minute players before
trusting it). No FotMob equivalent exists for defensive ERRORS at this
granularity, so `errors_per_90` stays NULL/neutral -- an honest gap, not
something this loader pretends to fill.

Matching reuses load_sofa_domestic's exact (first-initial, last-name) + team
fuzzy-match helpers (same problem: datamb abbreviates first names) rather than
reimplementing them.

Run after pipeline.load_datamb, before pipeline.load_sofa_domestic (so a real
SofaScore backfill, on the rare run where it succeeds, overwrites this lower-
coverage FotMob version rather than the other way around):
    python -m pipeline.load_fotmob_clearances
"""
import gzip
import json
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, FOTMOB_LEAGUE_IDS, DATAMB_SEASON
from analytics.queries import connect_retry
from pipeline.fotmob_auth import FotmobAuth
from pipeline.load_sofa_domestic import _key, _team, _fold
from rapidfuzz import fuzz

_auth = FotmobAuth()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
RATE_LIMIT_SEC = 0.3


def _cdn_stat_list(league_id: int, season_id: str, stat_key: str) -> list:
    url = f"https://data.fotmob.com/stats/{league_id}/season/{season_id}/{stat_key}.json"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    raw = urllib.request.urlopen(req, timeout=20).read()
    try:
        raw = gzip.decompress(raw)
    except OSError:                                        # noqa: BLE001 -- not gzipped
        pass
    lists = json.loads(raw).get("TopLists") or []
    return (lists[0].get("StatList") or []) if lists else []


def _season_id(league_id: int) -> str | None:
    data = _auth.get(f"/api/data/leagues?id={league_id}")
    for p in ((data.get("stats") or {}).get("players") or []):
        m = p.get("fetchAllUrl") or ""
        if "/season/" in m:
            return m.split("/season/")[1].split("/")[0]
    return None


def _fetch_clearances() -> list:
    """[(player_name, team_name, clearances_per_90), ...] across all 5 leagues."""
    out = []
    for league_key, league_id in FOTMOB_LEAGUE_IDS.items():
        sid = _season_id(league_id)
        if not sid:
            print(f"  ! {league_key}: no current season id yet")
            continue
        time.sleep(RATE_LIMIT_SEC)
        try:
            rows = _cdn_stat_list(league_id, sid, "effective_clearance")
        except Exception as e:                             # noqa: BLE001
            print(f"  ! {league_key}: {type(e).__name__} {str(e)[:80]}")
            continue
        for r in rows:
            name, team, val = r.get("ParticipantName"), r.get("TeamName"), r.get("StatValue")
            if name and team and val is not None:
                out.append((name, team, float(val)))
    return out


def load_fotmob_clearances(season: str = DATAMB_SEASON) -> int:
    fm = _fetch_clearances()
    by_key = defaultdict(list)
    for name, team, val in fm:
        by_key[_key(name)].append((name, team, val))

    con = connect_retry(DB_PATH, read_only=False)
    try:
        # load_sofa_domestic normally adds this column -- but only once it gets past its
        # own "raw file missing" early-return, which it never does when SofaScore itself
        # is unreachable (see [[datamb-season-fix]]). A fresh load_datamb() DROP+recreate
        # of player_wyscout has no clearances_per_90 column at all in that case.
        con.execute("ALTER TABLE player_wyscout ADD COLUMN IF NOT EXISTS clearances_per_90 DOUBLE")
        dm = con.execute(
            "SELECT DISTINCT player, team_within_selected_timeframe AS team "
            "FROM player_wyscout WHERE season = ? AND clearances_per_90 IS NULL",
            [season]).fetchall()

        updates, matched, fuzzy = [], 0, 0
        for d_player, d_team in dm:
            cands = by_key.get(_key(d_player), [])
            rec = None
            if len(cands) == 1:
                rec = cands[0]
            elif len(cands) > 1:
                rec = max(cands, key=lambda c: fuzz.token_set_ratio(_team(c[1]), _team(d_team)))
                if fuzz.token_set_ratio(_team(rec[1]), _team(d_team)) < 60:
                    rec = None
            if rec is None:                                # fuzzy fallback within team
                dt = _team(d_team)
                pool = [c for c in fm if fuzz.token_set_ratio(_team(c[1]), dt) >= 80]
                if pool:
                    best = max(pool, key=lambda c: fuzz.token_sort_ratio(_fold(c[0]), _fold(d_player)))
                    if fuzz.token_sort_ratio(_fold(best[0]), _fold(d_player)) >= 80:
                        rec, fuzzy = best, fuzzy + 1
            if rec is None:
                continue
            matched += 1
            updates.append((rec[2], d_player, d_team))

        con.executemany(
            "UPDATE player_wyscout SET clearances_per_90 = ? "
            "WHERE player = ? AND team_within_selected_timeframe = ? AND clearances_per_90 IS NULL",
            updates)
        print(f"player_wyscout: backfilled clearances_per_90 (FotMob) for {matched}/{len(dm)} "
              f"players missing it ({fuzzy} via fuzzy fallback). errors_per_90 stays NULL -- "
              f"no FotMob equivalent at this granularity.")
        return matched
    finally:
        con.close()


if __name__ == "__main__":
    load_fotmob_clearances()
