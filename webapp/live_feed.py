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
        "home_national": bool(home.get("national")),
        "away": away.get("name"), "away_id": away.get("id"), "away_country": country(away),
        "away_national": bool(away.get("national")),
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


# ---- prediction (from bookmaker odds) ---------------------------------------
# SofaScore exposes a handful of bookmaker feeds per event under /odds/{providerId}.
# We read each one's 1X2 (home/draw/away) market, convert to implied probability,
# strip the bookmaker margin (over-round), and average across books for a consensus.
_ODDS_PROVIDERS = (1, 5, 8, 11, 14, 16)


def _frac_to_decimal(frac):
    """SofaScore fractional odds ('21/50', '7/1') -> decimal (1.42, 8.0)."""
    try:
        s = str(frac)
        if "/" in s:
            n, d = s.split("/")
            return round(int(n) / int(d) + 1, 2)
        return round(float(s), 2)
    except Exception:  # noqa: BLE001
        return None


def prediction(eid: int) -> dict:
    """Consensus match prediction implied by bookmaker 1X2 odds (margin removed,
    averaged across the available SofaScore odds feeds)."""
    books = []
    for prov in _ODDS_PROVIDERS:
        # short TTL so in-play odds move with the game (the match page re-polls ~30s)
        d = _get(f"/event/{eid}/odds/{prov}/featured", ttl=25)
        feat = (d or {}).get("featured") or {}
        # in-play: the 1X2 lives under 'fullTime' (default rotates to corners/BTTS/…);
        # pre-match: the 1X2 IS the 'default'.
        mk = feat.get("fullTime") or {}
        if mk.get("marketGroup") != "1X2":
            mk = feat.get("default") or {}
        if mk.get("marketGroup") != "1X2":
            continue
        ch = {c.get("name"): c.get("fractionalValue") for c in mk.get("choices", [])}
        dec = {k: _frac_to_decimal(ch.get(k)) for k in ("1", "X", "2")}
        if not all(dec.values()):
            continue
        inv = {k: 1.0 / dec[k] for k in dec}
        over = sum(inv.values())                       # >1 = the bookmaker margin
        books.append({
            "odds": {"home": dec["1"], "draw": dec["X"], "away": dec["2"]},
            "probs": {"home": inv["1"] / over, "draw": inv["X"] / over, "away": inv["2"] / over},
        })
    if not books:
        return {"available": False}
    raw = {k: sum(b["probs"][k] for b in books) / len(books) * 100 for k in ("home", "draw", "away")}
    cons = {k: round(v) for k, v in raw.items()}
    fix = 100 - sum(cons.values())                     # keep the 3 ints summing to 100
    if fix:
        cons[max(raw, key=raw.get)] += fix
    return {"available": True, "n_books": len(books), "consensus": cons,
            "predicted": max(cons, key=cons.get),
            "books": [{"odds": b["odds"]} for b in books]}


# ---- national team (squad + recent results + fixtures) ----------------------
def _team_event_row(e: dict) -> dict:
    return {"event_id": e.get("id"),
            "home": (e.get("homeTeam") or {}).get("name"),
            "away": (e.get("awayTeam") or {}).get("name"),
            "home_score": (e.get("homeScore") or {}).get("current"),
            "away_score": (e.get("awayScore") or {}).get("current"),
            "competition": (e.get("tournament") or {}).get("name"),
            "ts": e.get("startTimestamp"),
            "status": (e.get("status") or {}).get("type")}


def national_team(team_id: int) -> dict:
    """A national team's roster + recent results + upcoming fixtures, live from
    SofaScore team endpoints (keyed by the team id we store in live_matches)."""
    info = (_get(f"/team/{team_id}", ttl=600) or {}).get("team") or {}
    if not info:
        return {"available": False}
    squad = []
    for p in (_get(f"/team/{team_id}/players", ttl=600) or {}).get("players", []):
        pl = p.get("player") or {}
        squad.append({"id": pl.get("id"), "name": pl.get("name"),
                      "position": pl.get("position"), "number": pl.get("jerseyNumber")})
    last = (_get(f"/team/{team_id}/events/last/0", ttl=180) or {}).get("events", [])
    nxt = (_get(f"/team/{team_id}/events/next/0", ttl=300) or {}).get("events", [])

    # latest starting XI: this team's lineup from its most recent finished match
    # that has one published (try a few back; each is one lineups() call).
    latest_xi, tried = None, 0
    for e in reversed(last):
        if (e.get("status") or {}).get("type") != "finished":
            continue
        if tried >= 4:
            break
        tried += 1
        lu = lineups(e.get("id"))
        if not lu.get("available"):
            continue
        is_home = (e.get("homeTeam") or {}).get("id") == team_id
        side = lu["home"] if is_home else lu["away"]
        if side and side.get("starting_xi"):
            opp = ((e.get("awayTeam") if is_home else e.get("homeTeam")) or {}).get("name")
            latest_xi = {"event_id": e.get("id"), "opponent": opp, "is_home": is_home,
                         "home_score": (e.get("homeScore") or {}).get("current"),
                         "away_score": (e.get("awayScore") or {}).get("current"),
                         "ts": e.get("startTimestamp"),
                         "formation": side.get("formation"), "starting_xi": side.get("starting_xi")}
            break

    return {
        "available": True, "id": team_id, "name": info.get("name"),
        "country_code": (info.get("country") or {}).get("alpha2"),
        "manager": (info.get("manager") or {}).get("name"),
        "latest_xi": latest_xi,
        "results": [_team_event_row(e) for e in reversed(last)][:12],   # most recent first
        "fixtures": [_team_event_row(e) for e in nxt][:8],
        "squad": squad,
    }
