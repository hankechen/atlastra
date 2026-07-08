"""
World Cup hub data from FotMob (server-side, no proxy/no Mac).

The SofaScore-based load_wc.py can only run from a residential IP (the Mac). FotMob
answers from a datacenter IP, so this rebuilds the same WC warehouse tables
(wc_matches / wc_standings / wc_leaders / wc_player_stats + atlas ratings /
wc_bracket) from FotMob and reuses load_wc.write_wc_rows to persist them.

    python -m pipeline.load_wc_fotmob        # refresh the current edition
"""
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tls_requests

from pipeline.fotmob_auth import FotmobAuth
from pipeline import rate_wc, load_wc
from pipeline.load_live_fotmob import NAT_ISO

WC_LEAGUE = 77
_auth = FotmobAuth()

# rate_wc input field -> (FotMob CDN stat file, is_per90). pass%/duels% aren't on the
# FotMob leaderboards, so they drop out of the rating vector (it renormalises).
_STAT_FILES = {
    "goals": ("goals", False), "assists": ("goal_assist", False),
    "rating": ("rating", False), "xg": ("expected_goals", False),
    "shots": ("total_scoring_att", True), "chances_created": ("total_att_assist", False),
    "big_chances_created": ("big_chance_created", False),
    "dribbles_completed": ("won_contest", True), "tackles": ("total_tackle", True),
    "interceptions": ("interception", True), "passes_completed": ("accurate_pass", True),
    "saves": ("saves", True), "clean_sheets": ("clean_sheet", False),
    "goals_conceded": ("goals_conceded", True), "goals_prevented": ("_goals_prevented", False),
}


def _iso_ts(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except (ValueError, TypeError):
            pass
    return None


def _line(positions):
    p = (positions or [0])[0] or 0
    if p == 11:
        return "G"
    if 30 <= p <= 39:
        return "D"
    if p >= 90:
        return "F"
    return "M"


def _cdn(season_id, name):
    try:
        r = tls_requests.get(f"https://data.fotmob.com/stats/{WC_LEAGUE}/season/{season_id}/{name}.json",
                             timeout=20)
        return (r.json().get("TopLists") or [{}])[0].get("StatList") or []
    except Exception:                                # noqa: BLE001
        return []


# Tournament pass% / duels% / duels-won aren't on FotMob's per-stat leaderboards, so
# aggregate them from each finished WC match's per-player stats. Finished matches don't
# change -> cache per match (module life), so only new matches are fetched each refresh.
_MATCH_AGG: dict = {}   # match_id -> {player_id: [accurate, attempted, duels_won, duels_lost]}


def _match_agg(mid: int) -> dict:
    if mid in _MATCH_AGG:
        return _MATCH_AGG[mid]
    try:
        ps = (_auth.get(f"/api/data/matchDetails?matchId={mid}")["content"]["playerStats"])
    except Exception:                                # noqa: BLE001
        return {}
    agg = {}
    for p in ps.values():
        flat = {}
        for g in p.get("stats") or []:
            for _, obj in (g.get("stats") or {}).items():
                if obj.get("key"):
                    flat[obj["key"]] = obj.get("stat") or {}
        ap = flat.get("accurate_passes") or {}
        agg[p.get("id")] = [ap.get("value"), ap.get("total"),
                            (flat.get("duel_won") or {}).get("value"),
                            (flat.get("duel_lost") or {}).get("value")]
    _MATCH_AGG[mid] = agg
    return agg


def _pass_duels(finished_ids: list) -> dict:
    """player_id -> (pass_accuracy_pct, duels_won_pct, duels_won) over the tournament,
    summed from each finished match's per-player passes/duels."""
    tot = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for agg in pool.map(_match_agg, finished_ids):
            for pid, (acc, att, won, lost) in agg.items():
                t = tot.setdefault(pid, [0, 0, 0, 0])
                t[0] += acc or 0
                t[1] += att or 0
                t[2] += won or 0
                t[3] += lost or 0
    out = {}
    for pid, (acc, att, won, lost) in tot.items():
        out[pid] = (round(acc / att * 100) if att else None,
                    round(won / (won + lost) * 100) if (won + lost) else None,
                    won or None)
    return out


def _players(season_id, season, finished_ids=None):
    """Merge the FotMob per-stat leaderboards into a per-player sheet, grade the
    atlas rating with rate_wc, and emit wc_player_stats rows. pass%/duels% are
    aggregated from the finished matches' per-player stats (not on the leaderboards)."""
    pd_map = _pass_duels(finished_ids or [])
    merged = {}
    for field, (fn, per90) in _STAT_FILES.items():
        for row in _cdn(season_id, fn):
            pid = row.get("ParticiantId")
            if pid is None:
                continue
            mins = row.get("MinutesPlayed") or 0
            p = merged.setdefault(pid, {
                "player_id": pid, "player": row.get("ParticipantName"),
                "team": row.get("TeamName"), "season": season, "minutes": 0,
                "appearances": row.get("MatchesPlayed"),
                "position": _line(row.get("Positions")),
                "pass_accuracy_pct": None, "duels_won_pct": None})
            v = row.get("StatValue")
            if v is not None and per90 and mins:
                v = v * mins / 90.0
            p[field] = v
            p["minutes"] = max(p.get("minutes") or 0, mins)
            p["appearances"] = p.get("appearances") or row.get("MatchesPlayed")
    stats = list(merged.values())
    rated = rate_wc.compute(stats)
    rows = []
    for p in stats:
        r = rated.get((season, p["player_id"])) or {}
        pa_pct, dw_pct, dw = pd_map.get(p["player_id"], (None, None, None))
        rows.append((season, p["position"], p["player_id"], p["player"], p["team"],
                     _r(p.get("rating")), _i(p.get("appearances")), _i(p.get("minutes")),
                     r.get("rating"), r.get("classification"),
                     _i(p.get("goals")), _i(p.get("assists")), _r(p.get("xg")),
                     _i(p.get("shots")), _i(p.get("chances_created")),
                     _i(p.get("big_chances_created")), _i(p.get("dribbles_completed")),
                     _i(p.get("tackles")), _i(p.get("interceptions")),
                     _i(p.get("passes_completed")), pa_pct, dw_pct, dw))
    return rows


def _i(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _r(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


# FotMob knockout stage -> (web_worldcup round name, round_order). web_worldcup keys
# the bracket off wc_matches.round being exactly these strings.
_STAGE = {"1/16": ("Round of 32", 0), "1/8": ("Round of 16", 1), "1/4": ("Quarterfinals", 2),
          "1/2": ("Semifinals", 3), "final": ("Final", 4)}

# FotMob leaderboard category -> the stat_key web_wc_leaders (_WC_LEADER_STATS) expects
_LEADER_KEY = {
    "Top scorer": "goals", "Assists": "assists", "Goals + Assists": "goalsAssistsSum",
    "FotMob rating": "rating", "Expected goals (xG)": "expectedGoals",
    "Expected assist (xA)": "expectedAssists", "Big chances created": "bigChancesCreated",
    "Shots per 90": "totalShots", "Shots on target per 90": "shotsOnTarget",
    "Chances created": "keyPasses", "Successful dribbles per 90": "successfulDribbles",
    "Tackles per 90": "tackles", "Interceptions per 90": "interceptions",
    "Clearances per 90": "clearances", "Saves per 90": "saves",
}


def fetch_wc_rows(season: str = "2026") -> dict:
    L = _auth.get(f"/api/data/leagues?id={WC_LEAGUE}")
    season_id = re.search(r"/season/(\d+)/", (L["stats"]["players"][0].get("fetchAllUrl") or "")).group(1)

    # knockout match id -> (round name, round order), from the playoff bracket
    ko_round, ko_order = {}, {}
    for rnd in (L.get("playoff") or {}).get("rounds") or []:
        name_order = _STAGE.get(rnd.get("stage"))
        if not name_order:
            continue
        for mu in rnd.get("matchups") or []:
            for leg in mu.get("matches") or []:
                mid = _i(leg.get("matchId"))
                if mid is not None:
                    ko_round[mid], ko_order[mid] = name_order

    # matches
    match_rows = []
    for m in ((L.get("fixtures") or {}).get("allMatches")) or []:
        st = m.get("status") or {}
        home, away = m.get("home") or {}, m.get("away") or {}
        # regulation/ET score from scoreStr (strip any "(pens)" bracket); e.g. "0 - 6"
        nums = re.findall(r"\d+", re.sub(r"\([^)]*\)", "", st.get("scoreStr") or ""))
        hs = _i(nums[0]) if (st.get("started") and len(nums) >= 2) else None
        aw = _i(nums[1]) if (st.get("started") and len(nums) >= 2) else None
        winner = (1 if hs > aw else 2 if aw > hs else 3) if (st.get("finished") and hs is not None and aw is not None) else None
        grp, mid = m.get("group"), _i(m.get("id"))
        rnd = f"Group {grp}" if grp else ko_round.get(mid, "Knockout")
        match_rows.append((mid, season, _iso_ts(st.get("utcTime")), rnd,
                           home.get("name"), NAT_ISO.get(home.get("name")),
                           away.get("name"), NAT_ISO.get(away.get("name")),
                           hs, aw, None, None, winner,
                           "finished" if st.get("finished") else "inprogress" if st.get("started") else "notstarted"))

    # standings
    stand_rows = []
    for grp in (L["table"][0]["data"].get("tables") or []):
        gname = (grp.get("leagueName") or grp.get("groupName") or "").replace("Grp.", "Group")
        tbl = grp.get("table")
        rows = tbl.get("all") if isinstance(tbl, dict) else tbl
        for t in rows or []:
            gf, ga = (t.get("scoresStr") or "0-0").split("-")[:2] if t.get("scoresStr") else (None, None)
            stand_rows.append((season, gname, _i(t.get("idx")), t.get("name"),
                               NAT_ISO.get(t.get("name")), _i(t.get("played")),
                               _i(t.get("wins")), _i(t.get("draws")), _i(t.get("losses")),
                               _i(gf), _i(ga), _i(t.get("pts"))))

    # leaders (top-3 per category)
    leader_rows = []
    for c in L["stats"]["players"]:
        key = _LEADER_KEY.get(c.get("header"))
        if not key:
            continue
        for t in (c.get("topThree") or [])[:3]:
            leader_rows.append((season, key, _i(t.get("rank")), t.get("name"),
                                _i(t.get("id")), t.get("teamName"),
                                _r((t.get("stat") or {}).get("value")), None))

    # bracket (knockout match ids in visual order)
    bracket_rows = []
    for rnd in (L.get("playoff") or {}).get("rounds") or []:
        name_order = _STAGE.get(rnd.get("stage"))
        if not name_order:
            continue
        ro = name_order[1]
        for seq, mu in enumerate(rnd.get("matchups") or []):
            leg = (mu.get("matches") or [{}])[0]     # WC knockouts are single-leg
            mid = _i(leg.get("matchId"))
            if mid is not None:
                bracket_rows.append((season, ro, seq, mid))

    finished_ids = [r[0] for r in match_rows if r[13] == "finished" and r[0] is not None]
    players = _players(season_id, season, finished_ids)
    return {"matches": match_rows, "standings": stand_rows, "leaders": leader_rows,
            "players": players, "bracket": bracket_rows}


def refresh(season: str = "2026") -> dict:
    data = fetch_wc_rows(season)
    load_wc.write_wc_rows(data)
    return {k: len(v) for k, v in data.items()}


if __name__ == "__main__":
    print("WC (FotMob) refresh:", refresh())
