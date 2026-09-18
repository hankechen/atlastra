"""
Live UEFA Champions League player stats from FotMob -> `ucl_player_stats`.

SofaScore (pipeline.scrape_ucl / load_ucl) is the UCL source for every completed
season back to 2008/09, but it's Mac-only (WAF blocks the EC2 datacenter IP, see
[[aws-deployment]]) and someone has to run it by hand -- so a brand-new season
(2026/27) sits with zero UCL data until that happens. FotMob answers from the
server's own IP and already backs [[live-matches]] / [[player-total-stats-fotmob]],
so this reuses the same public per-stat CDN trick as pipeline.load_wc_fotmob
(same provider, same "UEFA competition" shape) to fill in the CURRENT UCL season
only -- historical seasons stay exactly as SofaScore left them.

Writes into the SAME `ucl_player_stats` table SofaScore's loader builds, not a
parallel one, replacing only the current season's rows -- so build_views.py's
UCL_SELECT/_build_xwalk and rate_combined.py's _gk_ucl_df need no changes, they
already read this table by season. A `data_source` column (ALTER TABLE ADD
COLUMN IF NOT EXISTS, same pattern as pipeline.load_sofa_domestic) records
provenance per row so a later real SofaScore scrape of the same season knows
it's overwriting a FotMob stand-in, not another SofaScore row -- delete is
scoped to `data_source = 'fotmob'`, never touches real SofaScore rows.

Column gaps vs. SofaScore (FotMob's leaderboards don't carry these -- same
honest gaps as the domestic FotMob loader, see [[player-total-stats-fotmob]]):
duels_won/_pct, pass_accuracy_pct, aerial_duels_won_pct, dribble success_pct.
Also: FotMob has one `goals_conceded` total, not SofaScore's inside/outside-box
split -- stored under goals_conceded_inside_the_box with outside left 0 so the
_gk_ucl_df SUM() still gets the right total.

    python -m pipeline.load_ucl_fotmob
"""
import gzip
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, FOTMOB_UCL_LEAGUE_ID
from analytics.queries import connect_retry
from pipeline.fotmob_auth import FotmobAuth

_auth = FotmobAuth()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
RATE_LIMIT_SEC = 0.3

# our ucl_player_stats column -> (FotMob CDN stat key, is_per90). Chosen to match
# exactly what build_views.UCL_SELECT / rate_combined._gk_ucl_df already read.
_STAT_FILES = {
    "goals": ("goals", False),
    "assists": ("goal_assist", False),
    "expected_goals": ("expected_goals", False),
    "total_shots": ("total_scoring_att", True),
    "shots_on_target": ("ontarget_scoring_att", True),
    "key_passes": ("total_att_assist", False),
    "big_chances_created": ("big_chance_created", False),
    "big_chances_missed": ("big_chance_missed", False),
    "successful_dribbles": ("won_contest", True),
    "tackles": ("total_tackle", True),
    "interceptions": ("interception", True),
    "accurate_passes": ("accurate_pass", True),
    "clearances": ("effective_clearance", True),
    "rating": ("rating", False),
    "saves": ("saves", True),
    "clean_sheet": ("clean_sheet", False),
    "goals_conceded_inside_the_box": ("goals_conceded", True),
}

COLS = ["season", "competition", "sofascore_player_id", "player_name", "team_name",
        "appearances", "minutes_played", *_STAT_FILES.keys(), "data_source"]


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


def _season() -> tuple[str, str] | tuple[None, None]:
    """(season_id, season_code) for the current UCL season, e.g. ('47760', '2627'),
    read live off the league page -- not guessable/hardcoded, changes every year."""
    data = _auth.get(f"/api/data/leagues?id={FOTMOB_UCL_LEAGUE_ID}")
    label = (data.get("details") or {}).get("selectedSeason") or ""   # "2026/2027"
    m = re.match(r"(\d{4})/(\d{4})", label)
    if not m:
        return None, None
    code = m.group(1)[2:] + m.group(2)[2:]
    for p in ((data.get("stats") or {}).get("players") or []):
        sm = re.search(r"/season/(\d+)/", p.get("fetchAllUrl") or "")
        if sm:
            return sm.group(1), code
    return None, code


def fetch_rows() -> list:
    season_id, season_code = _season()
    if not season_id or not season_code:
        print("  ! no current UCL season id yet -- skipping")
        return []
    time.sleep(RATE_LIMIT_SEC)
    base = _cdn_stat_list(FOTMOB_UCL_LEAGUE_ID, season_id, "mins_played")
    rows = {}
    for item in base:
        pid = item.get("ParticiantId")
        if pid is None:
            continue
        rows[pid] = {"player_name": item.get("ParticipantName"), "team_name": item.get("TeamName"),
                     "appearances": item.get("MatchesPlayed") or 0, "minutes_played": item.get("StatValue") or 0,
                     **{f: (None if f == "rating" else 0.0) for f in _STAT_FILES}}
    for field, (stat_key, per90) in _STAT_FILES.items():
        time.sleep(RATE_LIMIT_SEC)
        try:
            stat_list = _cdn_stat_list(FOTMOB_UCL_LEAGUE_ID, season_id, stat_key)
        except Exception as e:                             # noqa: BLE001
            print(f"  ! {field}/{stat_key}: {type(e).__name__} {str(e)[:80]}")
            continue
        for item in stat_list:
            pid = item.get("ParticiantId")
            if pid is None:
                continue
            if pid not in rows:                            # has this stat, missed mins_played somehow
                rows[pid] = {"player_name": item.get("ParticipantName"), "team_name": item.get("TeamName"),
                             "appearances": item.get("MatchesPlayed") or 0,
                             "minutes_played": item.get("MinutesPlayed") or 0}
            val = item.get("StatValue")
            if per90 and val is not None:
                mins = item.get("MinutesPlayed") or rows[pid].get("minutes_played") or 0
                val = round(val * mins / 90, 2) if mins else 0.0
            rows[pid][field] = val

    out = []
    for pid, r in rows.items():
        out.append((
            season_code, "UCL", pid, r.get("player_name"), r.get("team_name"),
            r.get("appearances"), r.get("minutes_played"),
            *[r.get(f) for f in _STAT_FILES], "fotmob",
        ))
    return out


def refresh() -> int:
    rows = fetch_rows()

    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute("ALTER TABLE ucl_player_stats ADD COLUMN IF NOT EXISTS data_source VARCHAR")
        con.execute("UPDATE ucl_player_stats SET data_source = 'sofascore' WHERE data_source IS NULL")
        if rows:
            season_code = rows[0][0]
            con.execute("DELETE FROM ucl_player_stats WHERE season = ? AND data_source = 'fotmob'",
                        [season_code])
            con.executemany(
                f"INSERT INTO ucl_player_stats ({','.join(COLS)}) "
                f"VALUES ({','.join(['?'] * len(COLS))})", rows)
    finally:
        con.close()
    return len(rows)


if __name__ == "__main__":
    n = refresh()
    print(f"UCL (FotMob) refresh: {n} player rows for the current season")
