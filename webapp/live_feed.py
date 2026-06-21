"""
Live match-detail feed (SofaScore), server-side only.

The live/fixtures table (pipeline/load_live.py) gives one row per match with its
SofaScore event id. This module fetches the rich PER-MATCH detail on demand for a
single event -- statistics, lineups, shot map, timeline, per-player stats and
per-player heatmaps -- and shapes each into a small JSON payload for the webapp.

It must run server-side: SofaScore only answers a bare browser-TLS request (the
tls_requests fingerprint), so the browser can't call it directly (CORS + bot
challenge). The webapp proxies these through /api/match/* (see webapp/server.py).

Responses are cached in-memory with a short TTL so the dashboard can poll every
30-60s without hammering SofaScore: live data (header/stats/timeline) gets a short
TTL, mostly-static data (lineups/shotmap/player stats/heatmap) a longer one.
"""
import sys
import threading
import time
from pathlib import Path

import tls_requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SOFASCORE_BASE

_CACHE: dict[str, tuple[float, object]] = {}
_LOCK = threading.Lock()


def _get(path: str, ttl: float):
    """Cached bare GET. Returns parsed JSON dict, or None on non-200 (e.g. a 404
    heatmap for a player who never came on). NO extra headers -- see module docs."""
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=25)
        data = r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 -- network/parse hiccup -> treat as no data
        data = None
    with _LOCK:
        _CACHE[path] = (now, data)
    return data


def _num(d: dict, *keys):
    """First present numeric stat key, else 0 (SofaScore omits zero-valued keys)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return 0


# ---- header -----------------------------------------------------------------
def header(eid: int) -> dict:
    d = _get(f"/event/{eid}", ttl=20)
    ev = (d or {}).get("event") or {}
    if not ev:
        return {"available": False, "event_id": eid}
    st = ev.get("status") or {}
    t = ev.get("time") or {}
    ut = (ev.get("tournament") or {}).get("uniqueTournament") or {}
    ri = ev.get("roundInfo") or {}
    home, away = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}

    def country(team):
        return (team.get("country") or {}).get("alpha2") if team.get("national") else None

    # live clock minute (mirrors pipeline/load_live._minute)
    minute = None
    if st.get("type") == "inprogress" and t.get("currentPeriodStartTimestamp"):
        desc = (st.get("description") or "").lower()
        if not any(k in desc for k in ("halftime", "half-time", "penalties", "break")):
            elapsed = (t.get("initial") or 0) + max(0, int(time.time()) - t["currentPeriodStartTimestamp"])
            minute = int(elapsed // 60) + 1
    return {
        "available": True, "event_id": eid,
        "competition": ut.get("name") or (ev.get("tournament") or {}).get("name"),
        "round": ri.get("name") or (f"Round {ri['round']}" if ri.get("round") else None),
        "start_ts": ev.get("startTimestamp"),
        "status": st.get("type"), "status_desc": st.get("description"), "minute": minute,
        "home": home.get("name"), "home_id": home.get("id"), "home_country": country(home),
        "away": away.get("name"), "away_id": away.get("id"), "away_country": country(away),
        "home_score": (ev.get("homeScore") or {}).get("current"),
        "away_score": (ev.get("awayScore") or {}).get("current"),
        "xg_available": bool(ev.get("hasXg")),
    }


# ---- statistics (possession / shots / xG / corners / cards / ...) -----------
def statistics(eid: int) -> dict:
    d = _get(f"/event/{eid}/statistics", ttl=20)
    periods = (d or {}).get("statistics") or []
    if not periods:
        return {"available": False, "groups": []}
    all_period = next((p for p in periods if p.get("period") == "ALL"), periods[0])
    groups = []
    for g in all_period.get("groups", []):
        items = [{
            "name": it.get("name"), "key": it.get("key"),
            "home": it.get("home"), "away": it.get("away"),
            "home_value": it.get("homeValue"), "away_value": it.get("awayValue"),
        } for it in g.get("statisticsItems", [])]
        groups.append({"name": g.get("groupName"), "items": items})
    return {"available": True, "groups": groups}


# ---- lineups ----------------------------------------------------------------
def _lineup_side(side: dict) -> dict:
    starters, subs = [], []
    for p in side.get("players", []):
        pl = p.get("player") or {}
        row = {"id": pl.get("id"), "name": pl.get("name"),
               "number": p.get("jerseyNumber") or p.get("shirtNumber"),
               "position": p.get("position"), "captain": bool(p.get("captain")),
               "rating": (p.get("statistics") or {}).get("rating")}
        (subs if p.get("substitute") else starters).append(row)
    return {"formation": side.get("formation"), "starting_xi": starters, "substitutes": subs}


def lineups(eid: int) -> dict:
    d = _get(f"/event/{eid}/lineups", ttl=60)
    if not d or "home" not in d:
        return {"available": False, "confirmed": False, "home": None, "away": None}
    return {"available": True, "confirmed": bool(d.get("confirmed")),
            "home": _lineup_side(d["home"]), "away": _lineup_side(d["away"])}


# ---- shot map ---------------------------------------------------------------
_SHOT_GOAL = {"goal"}
_SHOT_ON_TARGET = {"goal", "save"}


def shotmap(eid: int) -> dict:
    d = _get(f"/event/{eid}/shotmap", ttl=45)
    shots = (d or {}).get("shotmap")
    if not shots:
        return {"available": False, "shots": []}
    out = []
    for s in shots:
        pc = s.get("playerCoordinates") or {}
        stype = s.get("shotType")
        out.append({
            "player": (s.get("player") or {}).get("name"),
            "is_home": s.get("isHome"),
            "x": pc.get("x"), "y": pc.get("y"),
            "xg": s.get("xg"), "xgot": s.get("xgot"),
            "body_part": s.get("bodyPart"), "situation": s.get("situation"),
            "shot_type": stype,
            "is_goal": stype in _SHOT_GOAL,
            "is_on_target": stype in _SHOT_ON_TARGET,
            "minute": s.get("time"), "added_time": s.get("addedTime"),
        })
    return {"available": True, "shots": out}


# ---- timeline (incidents) ---------------------------------------------------
def timeline(eid: int) -> dict:
    d = _get(f"/event/{eid}/incidents", ttl=30)
    incs = (d or {}).get("incidents")
    if incs is None:
        return {"available": False, "events": []}
    events = []
    for i in incs:
        typ = i.get("incidentType")
        side = "home" if i.get("isHome") else "away" if i.get("isHome") is False else None
        base = {"type": typ, "side": side, "minute": i.get("time"),
                "added_time": i.get("addedTime"), "klass": i.get("incidentClass")}
        if typ == "goal":
            base.update(player=(i.get("player") or {}).get("name"),
                        home_score=i.get("homeScore"), away_score=i.get("awayScore"))
        elif typ == "card":
            base.update(player=i.get("playerName") or (i.get("player") or {}).get("name"),
                        detail=i.get("reason"))
        elif typ == "substitution":
            base.update(player_in=(i.get("playerIn") or {}).get("name"),
                        player_out=(i.get("playerOut") or {}).get("name"))
        elif typ == "period":
            base.update(text=i.get("text"))
        events.append(base)
    # SofaScore returns newest-first; present oldest-first for a chronological feed
    events.reverse()
    return {"available": True, "events": events}


# ---- per-player stats -------------------------------------------------------
def _player_rows(side: dict, team_name: str, is_home: bool, cards: dict) -> list:
    rows = []
    for p in side.get("players", []):
        st = p.get("statistics") or {}
        if not st:
            continue
        pl = p.get("player") or {}
        pid = pl.get("id")
        c = cards.get(pid, {})
        rows.append({
            "id": pid, "name": pl.get("name"), "team": team_name, "is_home": is_home,
            "number": p.get("jerseyNumber"), "position": p.get("position"),
            "started": not p.get("substitute"),
            "rating": st.get("rating"),
            "minutes": _num(st, "minutesPlayed"),
            "goals": _num(st, "goals"), "assists": _num(st, "goalAssist"),
            "shots": _num(st, "totalShots"), "shots_on_target": _num(st, "onTargetScoringAttempt"),
            "xg": _num(st, "expectedGoals"), "xa": _num(st, "expectedAssists"),
            "passes": _num(st, "totalPass"), "accurate_passes": _num(st, "accuratePass"),
            "key_passes": _num(st, "keyPass"),
            "tackles": _num(st, "totalTackle"), "duels_won": _num(st, "duelWon"),
            "touches": _num(st, "touches"), "fouls": _num(st, "fouls"),
            "yellow": c.get("yellow", 0), "red": c.get("red", 0),
        })
    return rows


def player_stats(eid: int) -> dict:
    d = _get(f"/event/{eid}/lineups", ttl=60)
    if not d or "home" not in d:
        return {"available": False, "players": []}
    # cards per player from the timeline (player statistics don't carry them)
    cards: dict = {}
    inc = (_get(f"/event/{eid}/incidents", ttl=30) or {}).get("incidents") or []
    for i in inc:
        if i.get("incidentType") == "card":
            pid = (i.get("player") or {}).get("id")
            if pid is None:
                continue
            kind = "red" if "red" in (i.get("incidentClass") or "").lower() else "yellow"
            cards.setdefault(pid, {}).setdefault(kind, 0)
            cards[pid][kind] += 1
    ev = _get(f"/event/{eid}", ttl=20) or {}
    e = ev.get("event") or {}
    hn = (e.get("homeTeam") or {}).get("name") or "Home"
    an = (e.get("awayTeam") or {}).get("name") or "Away"
    players = (_player_rows(d["home"], hn, True, cards)
               + _player_rows(d["away"], an, False, cards))
    return {"available": bool(players), "players": players}


# ---- player heatmap ---------------------------------------------------------
def player_heatmap(eid: int, pid: int) -> dict:
    d = _get(f"/event/{eid}/player/{pid}/heatmap", ttl=300)
    pts = (d or {}).get("heatmap")
    if not pts:
        return {"available": False, "points": []}
    return {"available": True,
            "points": [{"x": p.get("x"), "y": p.get("y")} for p in pts]}
