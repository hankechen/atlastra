"""
Match-detail feed from FotMob (server-side, no proxy) — the FotMob counterpart of
webapp/live_feed.py. Every function returns the SAME shape match.js already renders,
so the frontend is unchanged; only the source flips from SofaScore to FotMob.

One signed /api/data/matchDetails call per match feeds every tab (header, stats,
lineups, shot map, timeline, players), cached briefly so opening a match is one
fetch. FotMob answers 200 from a datacenter IP, so this runs on the server 24/7.
"""
import time
from datetime import datetime, timezone

from pipeline.fotmob_auth import FotmobAuth
from pipeline.load_live_fotmob import NAT_ISO, COVERED, QUAL

_auth = FotmobAuth()
_CACHE: dict = {}                                    # matchId -> (expires, matchDetails)
_TTL_LIVE = 20                                       # seconds


def _md(eid: int) -> dict | None:
    """Cached matchDetails for a match id (short TTL so live tabs stay fresh)."""
    now = time.time()
    hit = _CACHE.get(eid)
    if hit and hit[0] > now:
        return hit[1]
    try:
        d = _auth.get(f"/api/data/matchDetails?matchId={int(eid)}")
    except Exception:                                # noqa: BLE001
        return (hit[1] if hit else None)             # serve stale on a blip
    _CACHE[eid] = (now + _TTL_LIVE, d)
    if len(_CACHE) > 400:                            # simple bound
        for k in list(_CACHE)[:200]:
            _CACHE.pop(k, None)
    return d


def _num(v):
    """A stat value like 56, '0.63', '12 (46%)' -> a float for bar widths (or None)."""
    if v is None:
        return None
    s = str(v).strip()
    num = ""
    for ch in s:
        if ch.isdigit() or ch in ".-":
            num += ch
        elif num:
            break
    try:
        return float(num) if num not in ("", "-", ".") else None
    except ValueError:
        return None


def _status(general: dict, hstatus: dict):
    """(status_type, status_desc, minute) in the live_matches/header vocabulary."""
    reason = (hstatus.get("reason") or {})
    if general.get("finished") or reason.get("longKey") == "finished":
        return "finished", reason.get("long") or "Full-Time", None
    if general.get("started"):
        lt = hstatus.get("liveTime") or {}
        mn = _num((lt.get("short") or lt.get("long") or "").split(":")[0])
        return "inprogress", (reason.get("long") or "In progress"), (int(mn) if mn else None)
    return "notstarted", reason.get("long") or "Not started", None


def _national(general: dict) -> bool:
    pid = general.get("parentLeagueId")
    return pid in {77, 50, 44}                        # WC / EURO / Copa


def header(eid: int) -> dict:
    d = _md(eid)
    g = (d or {}).get("general") or {}
    h = (d or {}).get("header") or {}
    teams = h.get("teams") or []
    if not d or len(teams) < 2:
        return {"available": False, "event_id": eid}
    home, away = teams[0], teams[1]
    st = h.get("status") or {}
    stype, sdesc, minute = _status(g, st)
    intl = _national(g)
    ib = ((d.get("content") or {}).get("matchFacts") or {}).get("infoBox") or {}
    ref = (ib.get("Referee") or {}).get("text")
    ut = g.get("matchTimeUTC")
    try:
        start_ts = int(datetime.strptime(ut, "%a, %b %d, %Y, %H:%M UTC")
                       .replace(tzinfo=timezone.utc).timestamp()) if ut else None
    except (ValueError, TypeError):
        start_ts = None
    return {
        "available": True, "event_id": eid, "ut_id": g.get("parentLeagueId"),
        "competition": g.get("leagueName"),
        "round": g.get("leagueRoundName") or g.get("matchRound"),
        "start_ts": start_ts,
        "status": stype, "status_desc": sdesc, "minute": minute,
        "home": home.get("name"), "home_id": home.get("id"),
        "home_country": NAT_ISO.get(home.get("name")) if intl else None,
        "home_national": intl,
        "away": away.get("name"), "away_id": away.get("id"),
        "away_country": NAT_ISO.get(away.get("name")) if intl else None,
        "away_national": intl,
        "home_score": home.get("score"), "away_score": away.get("score"),
        "home_pens": None, "away_pens": None,
        "xg_available": True,
        "referee": {"name": ref, "country": None} if ref else None,
    }


def statistics(eid: int) -> dict:
    d = _md(eid)
    periods = (((d or {}).get("content") or {}).get("stats") or {}).get("Periods") or {}
    groups_in = ((periods.get("All") or {}).get("stats")) or []
    if not groups_in:
        return {"available": False, "groups": []}
    groups = []
    for grp in groups_in:
        items = []
        for it in grp.get("stats", []):
            vals = it.get("stats") or [None, None]
            items.append({
                "name": it.get("title"), "key": it.get("key"),
                "home": vals[0], "away": vals[1],
                "home_value": _num(vals[0]), "away_value": _num(vals[1]),
            })
        if items:
            groups.append({"name": grp.get("title"), "items": items})
    return {"available": True, "groups": groups}


_POS_LINE = {11: "G"}                                 # positionId 11 = goalkeeper


def _lineup_side(team: dict, subs_evts: dict) -> dict:
    def row(p, starter):
        pid = p.get("id")
        return {"id": pid, "name": p.get("name"),
                "number": p.get("shirtNumber"),
                "position": "G" if p.get("positionId") == 11 else None,
                "captain": bool(p.get("isCaptain")),
                "rating": (p.get("performance") or {}).get("rating"),
                "subbed_in": subs_evts.get(pid, {}).get("in"),
                "subbed_out": subs_evts.get(pid, {}).get("out")}
    starters = [row(p, True) for p in (team.get("starters") or [])]
    substitutes = [row(p, False) for p in (team.get("subs") or [])]
    return {"formation": team.get("formation"), "starting_xi": starters,
            "substitutes": substitutes, "manager": (team.get("coach") or {}).get("name")}


def _sub_events(d: dict) -> dict:
    """player_id -> {'in'/'out': minute} from the timeline substitutions."""
    evs = ((((d or {}).get("content") or {}).get("matchFacts") or {})
           .get("events") or {}).get("events") or []
    m: dict = {}
    for e in evs:
        if e.get("type") != "Substitution":
            continue
        mn = e.get("time")
        swap = e.get("swap") or []                    # [0] = in, [1] = out; ids are strings
        for idx, key in ((0, "in"), (1, "out")):
            if len(swap) > idx:
                try:
                    m.setdefault(int(swap[idx]["id"]), {})[key] = mn
                except (KeyError, TypeError, ValueError):
                    pass
    return m


def lineups(eid: int) -> dict:
    d = _md(eid)
    lu = ((d or {}).get("content") or {}).get("lineup") or {}
    home, away = lu.get("homeTeam"), lu.get("awayTeam")
    if not home or not away:
        return {"available": False, "confirmed": False, "home": None, "away": None}
    se = _sub_events(d)
    return {"available": True,
            "confirmed": (lu.get("lineupType") == "standard"),
            "home": _lineup_side(home, se), "away": _lineup_side(away, se)}


_SHOT_TYPE = {"Goal": "goal", "AttemptSaved": "save", "Miss": "miss",
              "Post": "post", "AttemptBlocked": "block"}


def shotmap(eid: int) -> dict:
    d = _md(eid)
    g = (d or {}).get("general") or {}
    home_id = (g.get("homeTeam") or {}).get("id")
    shots_in = (((d or {}).get("content") or {}).get("shotmap") or {}).get("shots") or []
    if not shots_in:
        return {"available": False, "shots": []}
    out = []
    for s in shots_in:
        et = s.get("eventType")
        out.append({
            "player": s.get("playerName"),
            "is_home": s.get("teamId") == home_id,
            "x": s.get("x"), "y": s.get("y"),
            "xg": s.get("expectedGoals"), "xgot": s.get("expectedGoalsOnTarget"),
            "body_part": s.get("shotType"), "situation": s.get("situation"),
            "shot_type": _SHOT_TYPE.get(et, (et or "").lower()),
            "is_goal": et == "Goal",
            "is_on_target": bool(s.get("isOnTarget")),
            "minute": s.get("min"), "added_time": s.get("minAdded"),
        })
    return {"available": True, "shots": out}


_EVT_TYPE = {"Goal": "goal", "Card": "card", "Substitution": "substitution",
             "AddedTime": "period", "Half": "period"}


def timeline(eid: int) -> dict:
    d = _md(eid)
    evs = ((((d or {}).get("content") or {}).get("matchFacts") or {})
           .get("events") or {}).get("events")
    if evs is None:
        return {"available": False, "events": []}
    events = []
    for e in evs:
        typ = _EVT_TYPE.get(e.get("type"), (e.get("type") or "").lower())
        side = "home" if e.get("isHome") else "away" if e.get("isHome") is False else None
        base = {"type": typ, "side": side, "minute": e.get("time"),
                "added_time": (e.get("overloadTime") or None), "klass": None}
        if typ == "goal":
            ns = e.get("newScore") or [None, None]     # score AFTER the goal
            base.update(player=(e.get("player") or {}).get("name") or e.get("nameStr"),
                        assist=e.get("assistInput") or None,
                        home_score=ns[0], away_score=ns[1])
        elif typ == "card":
            base.update(player=(e.get("player") or {}).get("name") or e.get("nameStr"),
                        detail=e.get("card") or e.get("cardType"))
        elif typ == "substitution":
            swap = e.get("swap") or []                 # [0] = in, [1] = out
            base.update(player_in=(swap[0].get("name") if len(swap) > 0 else None),
                        player_out=(swap[1].get("name") if len(swap) > 1 else None))
        events.append(base)
    return {"available": True, "events": events}   # FotMob already oldest-first
