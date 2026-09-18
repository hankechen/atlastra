"""
Domestic Top-5 player-season TOTALS for the CURRENT season (2026/27), sourced
from FotMob -- written straight into `player_season_stats`, the SAME table
pipeline.load builds from Understat.

Understat has published ZERO 2026/27 data (confirmed live, see
[[player-total-stats-fotmob]]), so that table has no season='2627' rows at all
right now. This fills them in from FotMob's server-reachable CDN, applying the
exact pattern already proven for the UCL gap ([[ucl-fotmob-loader]]) to the
domestic spine instead: fetch per-stat leaderboards, match to an existing
internal player_id, INSERT OR REPLACE into the real table -- so every
downstream consumer (build_views' TOP5_SELECT/v_stats_top5/v_stats_combined,
rate_combined's LEAGUE scope) needs ZERO changes and computes 2026/27 domestic
ratings automatically as real per-90 volume accrues past the existing
MIN_MINUTES gates.

**This does NOT unblock the PRIMARY 0-99 rating engine** (pipeline.rate /
player_ratings_v2) -- that's a separate, datamb/Wyscout-only pipeline (see
[[rating-engine]]), and datamb.football is still paywalled. It only feeds the
common-metric `player_ratings_combined` LEAGUE scope (see
[[combined-ucl-league-ratings]]), same as this UCL sibling feeds the UCL scope.

Player match: fuzzy name match against the LATEST real domestic season's pool
(FOCUS_SEASON), per league_key, no team gate -- same production pattern
pipeline.load_enrich already runs for the current-season FotMob enrichment
overlay. Only players already known from a prior Top-5 season get a row; a
brand-new signing/academy graduate with no FOCUS_SEASON row is an honest,
documented gap until Understat itself publishes 2026/27.

Team match: NOT re-invented -- resolved off `team_logos`
(fotmob_team_id -> team_id, pipeline.load_team_logos, run fresh here first) so
a summer transfer lands on the player's CURRENT club, not last season's.

    python -m pipeline.load_player_season_fotmob
"""
import gzip
import json
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, FOTMOB_LEAGUE_IDS, FOCUS_SEASON
from analytics.queries import connect_retry
from pipeline.fotmob_auth import FotmobAuth
from pipeline.load_team_logos import load_team_logos
from rapidfuzz import fuzz, process

_auth = FotmobAuth()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
RATE_LIMIT_SEC = 0.3
MATCH_THRESHOLD = 80    # phase 1: token_sort
RECOVER_THRESHOLD = 90  # phase 2: token_set recovery

# our player_season_stats field -> (FotMob CDN stat key, is_per90)
_STAT_FILES = {
    "goals": ("goals", False),
    "assists": ("goal_assist", False),
    "xg": ("expected_goals", False),
    "xa": ("expected_assists", False),
    "key_passes": ("total_att_assist", False),   # FotMob "chances created" ~= key_passes, the
                                                   # same equivalence build_views.TOP5_SELECT
                                                   # already applies (COALESCE(e.chances_created,
                                                   # s.key_passes))
    "shots": ("total_scoring_att", True),
    "yellow_cards": ("yellow_card", False),
    "red_cards": ("red_card", False),
}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


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


def _season(league_id: int) -> tuple[str, str] | tuple[None, None]:
    """(season_id, season_code) for the current domestic season, e.g. ('12345', '2627')."""
    data = _auth.get(f"/api/data/leagues?id={league_id}")
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


def _fetch_league(league_id: int) -> tuple[list, str | None]:
    season_id, season_code = _season(league_id)
    if not season_id or not season_code:
        return [], None
    time.sleep(RATE_LIMIT_SEC)
    base = _cdn_stat_list(league_id, season_id, "mins_played")
    rows = {}
    for item in base:
        pid = item.get("ParticiantId")
        if pid is None:
            continue
        rows[pid] = {"player_name": item.get("ParticipantName"), "fotmob_team_id": item.get("TeamId"),
                     "matches": item.get("MatchesPlayed") or 0, "minutes": item.get("StatValue") or 0,
                     **{f: 0.0 for f in _STAT_FILES}}
    for field, (stat_key, per90) in _STAT_FILES.items():
        time.sleep(RATE_LIMIT_SEC)
        try:
            stat_list = _cdn_stat_list(league_id, season_id, stat_key)
        except Exception as e:                             # noqa: BLE001
            print(f"  ! {stat_key}: {type(e).__name__} {str(e)[:80]}")
            continue
        for item in stat_list:
            pid = item.get("ParticiantId")
            if pid is None:
                continue
            if pid not in rows:
                rows[pid] = {"player_name": item.get("ParticipantName"), "fotmob_team_id": item.get("TeamId"),
                             "matches": item.get("MatchesPlayed") or 0,
                             "minutes": item.get("MinutesPlayed") or 0,
                             **{f: 0.0 for f in _STAT_FILES}}
            val = item.get("StatValue")
            if per90 and val is not None:
                mins = item.get("MinutesPlayed") or rows[pid].get("minutes") or 0
                val = round(val * mins / 90) if mins else 0
            rows[pid][field] = val
    return list(rows.items()), season_code


def _per90(value, minutes):
    return round(value / minutes * 90, 3) if (value is not None and minutes) else None


def _match_league(con, league_key: str, fm_rows: list) -> list:
    """fuzzy-match this league's FotMob rows to an existing internal player_id,
    using the SAME phase1(token_sort)/phase2(token_set) pattern pipeline.load_enrich
    already runs in production for the same problem."""
    pool = con.execute(
        "SELECT ps.player_id, p.player_name FROM player_season_stats ps "
        "JOIN players p USING (player_id) "
        "WHERE ps.league_key = ? AND ps.season = ?", [league_key, FOCUS_SEASON]).fetchall()
    ids = [int(r[0]) for r in pool]
    names = [_norm(r[1]) for r in pool]
    if not names:
        return []

    used, leftover, matched = set(), [], []
    for pid_fm, r in fm_rows:
        target = _norm(r.get("player_name") or "")
        if not target:
            continue
        best = process.extractOne(target, names, scorer=fuzz.token_sort_ratio)
        if not best or best[1] < MATCH_THRESHOLD or ids[best[2]] in used:
            leftover.append((pid_fm, r, target))
            continue
        used.add(ids[best[2]])
        matched.append((ids[best[2]], r))

    cands = []
    for pid_fm, r, target in leftover:
        for i, nm in enumerate(names):
            if ids[i] in used:
                continue
            score = fuzz.token_set_ratio(target, nm)
            if score >= RECOVER_THRESHOLD:
                cands.append((score, pid_fm, r, ids[i]))
    cands.sort(key=lambda c: -c[0])
    claimed = set()
    for score, pid_fm, r, our_id in cands:
        if pid_fm in claimed or our_id in used:
            continue
        claimed.add(pid_fm)
        used.add(our_id)
        matched.append((our_id, r))
    return matched


def refresh() -> int:
    load_team_logos()   # keep the fotmob_team_id -> team_id crosswalk current first

    con = connect_retry(DB_PATH, read_only=False)
    try:
        team_map = {int(fid): int(tid) for tid, fid in
                    con.execute("SELECT team_id, fotmob_team_id FROM team_logos "
                                "WHERE team_id IS NOT NULL AND fotmob_team_id IS NOT NULL").fetchall()}
        total = 0
        for league_key, league_id in FOTMOB_LEAGUE_IDS.items():
            fm_rows, season_code = _fetch_league(league_id)
            if not season_code:
                print(f"  ! {league_key}: no current season id yet -- skipped")
                continue
            matched = _match_league(con, league_key, fm_rows)
            pos_map = {int(pid): (pos, grp) for pid, pos, grp in con.execute(
                "SELECT player_id, position, position_group FROM player_season_stats "
                "WHERE league_key = ? AND season = ?", [league_key, FOCUS_SEASON]).fetchall()}

            rows = []
            for our_id, r in matched:
                fid = r.get("fotmob_team_id")
                team_id = team_map.get(int(fid)) if fid is not None else None
                if team_id is None:
                    continue
                position, position_group = pos_map.get(our_id, (None, None))
                mins = r.get("minutes") or 0
                goals, assists = r.get("goals"), r.get("assists")
                ga = (goals or 0) + (assists or 0)
                xg, xa = r.get("xg"), r.get("xa")
                shots, key_passes = r.get("shots"), r.get("key_passes")
                rows.append((
                    our_id, team_id, league_key, season_code, position, position_group,
                    int(r.get("matches") or 0), int(mins),
                    int(goals) if goals is not None else None,
                    int(assists) if assists is not None else None,
                    int(shots) if shots is not None else None,
                    int(key_passes) if key_passes is not None else None,
                    xg, None, None, xa, None, None,
                    int(r.get("yellow_cards")) if r.get("yellow_cards") is not None else None,
                    int(r.get("red_cards")) if r.get("red_cards") is not None else None,
                    _per90(goals, mins), _per90(assists, mins), _per90(ga, mins),
                    _per90(xg, mins), _per90(xa, mins), None,
                    _per90(shots, mins), _per90(key_passes, mins),
                ))
            if rows:
                con.executemany(
                    "INSERT OR REPLACE INTO player_season_stats "
                    "(player_id, team_id, league_key, season, position, position_group, matches, minutes, "
                    " goals, assists, shots, key_passes, xg, np_goals, np_xg, xa, xg_chain, xg_buildup, "
                    " yellow_cards, red_cards, goals_per90, assists_per90, ga_per90, xg_per90, xa_per90, "
                    " npxg_per90, shots_per90, key_passes_per90) "
                    "VALUES (" + ",".join(["?"] * 28) + ")", rows)
            print(f"  {league_key}: {len(rows)}/{len(fm_rows)} FotMob players matched -> {season_code}")
            total += len(rows)
    finally:
        con.close()
    return total


if __name__ == "__main__":
    n = refresh()
    print(f"Domestic (FotMob) season refresh: {n} player-season rows")
