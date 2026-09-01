"""
Live per-player season totals from FotMob -> table `player_stats_fotmob`.

The player profile's stat tiles (analytics/queries.py web_player) read
v_stats_combined_player, which is Understat-sourced and only as fresh as the last
manual pipeline run -- like team standings before this same fix (see
pipeline/load_standings_fotmob.py), a real matchday can go unrecorded for weeks,
and right now Understat hasn't been scraped for the 2026/27 season at all.

FotMob publishes a full per-stat leaderboard for every top-5 league on a public,
KEYLESS CDN -- data.fotmob.com/stats/<leagueId>/season/<seasonId>/<stat>.json --
the same trick pipeline/load_wc_fotmob.py already uses for World Cup ratings. Each
entry carries the stat's value AND that player's minutes/matches played, so pulling
a handful of these gives every active player's season TOTALS (goals, assists, xG,
xA, chances created, minutes, matches) in ~9 calls per league. Per-90 rates are
computed from those totals at query time (analytics/queries.py), the same way the
existing Understat-based tiles already do -- not fetched as their own stat, so
there's one arithmetic path for both sources instead of two.

`mins_played` is the base population (every player with at least 1 minute this
season); the other stats only list players with a nonzero value, so anyone absent
from e.g. the goals list genuinely has 0.

The `<seasonId>` isn't guessable -- it changes every year -- so it's pulled out of
`fetchAllUrl` on the SAME /api/data/leagues response load_standings_fotmob.py
already fetches for standings/leaders, rather than hardcoded or looked up separately.

    python -m pipeline.load_player_stats_fotmob        # one refresh
"""
import gzip
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, FOTMOB_LEAGUE_IDS
from analytics.queries import connect_retry
from pipeline.fotmob_auth import FotmobAuth

_auth = FotmobAuth()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
RATE_LIMIT_SEC = 0.3

# CDN stat key -> our column. mins_played is fetched first and seeds the row for
# every other stat to merge into (see _fetch_league).
STAT_KEYS = [
    ("goals", "goals"), ("goal_assist", "assists"),
    ("expected_goals", "xg"), ("expected_assists", "xa"),
    ("total_att_assist", "chances_created"), ("big_chance_created", "big_chances_created"),
    ("won_contest", "dribbles_completed"), ("rating", "rating"),
]
COLS = ["league_key", "fotmob_player_id", "fotmob_team_id", "player_name",
        "matches", "minutes", "goals", "assists", "xg", "xa", "chances_created",
        "big_chances_created", "dribbles_completed", "rating", "updated_at"]


def _cdn_stat_list(league_id: int, season_id: str, stat_key: str) -> list:
    url = f"https://data.fotmob.com/stats/{league_id}/season/{season_id}/{stat_key}.json"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    raw = urllib.request.urlopen(req, timeout=20).read()
    try:
        raw = gzip.decompress(raw)
    except OSError:                                        # noqa: BLE001 -- not gzipped
        pass
    d = json.loads(raw)
    lists = d.get("TopLists") or []
    return (lists[0].get("StatList") or []) if lists else []


def _season_id(league_id: int) -> str | None:
    """Pull the current season's numeric id out of the league's own stats widget
    (already carries a fetchAllUrl per category) rather than guessing it."""
    try:
        data = _auth.get(f"/api/data/leagues?id={league_id}")
    except Exception:                                      # noqa: BLE001
        return None
    for p in ((data.get("stats") or {}).get("players") or []):
        m = re.search(r"/season/(\d+)/", p.get("fetchAllUrl") or "")
        if m:
            return m.group(1)
    return None


def _fetch_league(league_key: str, league_id: int, ua: datetime) -> list:
    season_id = _season_id(league_id)
    if not season_id:
        print(f"  ! {league_key}: no season id yet (stats widget not populated)", flush=True)
        return []
    time.sleep(RATE_LIMIT_SEC)
    try:
        base_list = _cdn_stat_list(league_id, season_id, "mins_played")
    except Exception as e:                                 # noqa: BLE001
        print(f"  ! {league_key}: {type(e).__name__} {str(e)[:80]}", flush=True)
        return []
    rows = {}
    for item in base_list:
        pid = item.get("ParticiantId")
        if pid is None:
            continue
        rows[pid] = {"player_name": item.get("ParticipantName"), "fotmob_team_id": item.get("TeamId"),
                     "matches": item.get("MatchesPlayed") or 0, "minutes": item.get("StatValue") or 0,
                     "goals": 0, "assists": 0, "xg": 0.0, "xa": 0.0, "chances_created": 0,
                     "big_chances_created": 0, "dribbles_completed": 0, "rating": None}
    for stat_key, field in STAT_KEYS:
        time.sleep(RATE_LIMIT_SEC)
        try:
            stat_list = _cdn_stat_list(league_id, season_id, stat_key)
        except Exception as e:                             # noqa: BLE001
            print(f"  ! {league_key}/{stat_key}: {type(e).__name__} {str(e)[:80]}", flush=True)
            continue
        for item in stat_list:
            pid = item.get("ParticiantId")
            if pid is None:
                continue
            if pid not in rows:                            # has this stat, missed mins_played somehow
                rows[pid] = {"player_name": item.get("ParticipantName"), "fotmob_team_id": item.get("TeamId"),
                             "matches": item.get("MatchesPlayed") or 0,
                             "minutes": item.get("MinutesPlayed") or 0,
                             "goals": 0, "assists": 0, "xg": 0.0, "xa": 0.0, "chances_created": 0,
                             "big_chances_created": 0, "dribbles_completed": 0, "rating": None}
            rows[pid][field] = item.get("StatValue")
    return [(league_key, pid, r["fotmob_team_id"], r["player_name"], r["matches"], r["minutes"],
             r["goals"], r["assists"], r["xg"], r["xa"], r["chances_created"],
             r["big_chances_created"], r["dribbles_completed"], r["rating"], ua)
            for pid, r in rows.items()]


def refresh() -> int:
    """Rebuild player_stats_fotmob wholesale from FotMob. Returns row count."""
    ua = datetime.utcnow()
    all_rows = []
    for league_key, league_id in FOTMOB_LEAGUE_IDS.items():
        all_rows += _fetch_league(league_key, league_id, ua)

    con = connect_retry(DB_PATH, read_only=False)
    try:
        con.execute("DROP TABLE IF EXISTS player_stats_fotmob")
        con.execute("""CREATE TABLE player_stats_fotmob (
            league_key VARCHAR, fotmob_player_id BIGINT, fotmob_team_id BIGINT,
            player_name VARCHAR, matches INTEGER, minutes INTEGER,
            goals DOUBLE, assists DOUBLE, xg DOUBLE, xa DOUBLE, chances_created DOUBLE,
            big_chances_created DOUBLE, dribbles_completed DOUBLE, rating DOUBLE,
            updated_at TIMESTAMP)""")
        if all_rows:
            con.executemany(
                f"INSERT INTO player_stats_fotmob ({','.join(COLS)}) "
                f"VALUES ({','.join(['?'] * len(COLS))})", all_rows)
        con.execute("CREATE INDEX IF NOT EXISTS idx_pstats_fotmob_pid "
                    "ON player_stats_fotmob(fotmob_player_id)")
    finally:
        con.close()
    return len(all_rows)


if __name__ == "__main__":
    n = refresh()
    print(f"FotMob player-stats refresh: {n} player-rows across {len(FOTMOB_LEAGUE_IDS)} leagues")
