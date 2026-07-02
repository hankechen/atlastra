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
import atexit
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import tls_requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import SOFASCORE_BASE

# Optional residential proxy for SofaScore (datacenter IPs are 403-blocked). Set
# SOFASCORE_PROXY=http://user:pass@host:port to route the live calls through it.
_PROXY = os.environ.get("SOFASCORE_PROXY") or None

_CACHE: dict[str, tuple[float, object]] = {}
_LOCK = threading.Lock()

# Pull-through cache mode (ATLASTRA_SOFA_CACHE=1): the cloud host is WAF-blocked
# from SofaScore, so instead of fetching, _get serves from a cache that a
# non-blocked machine fills. On a miss/stale it records the path in a queue; the
# remote pusher polls the queue, fetches the path, and POSTs the JSON back.
CACHE_MODE = os.environ.get("ATLASTRA_SOFA_CACHE") == "1"
_PUSH_CACHE: dict[str, tuple[float, object]] = {}   # path -> (ts, data | None)
_QUEUE: dict[str, float] = {}                        # path -> first-requested ts

# The pushed cache is PERSISTED to disk so warmed match data (upcoming previews,
# lineups, form, h2h, odds, ...) survives a server restart AND keeps displaying while
# the remote scraper is offline (e.g. the pusher machine is asleep/off). For an
# upcoming fixture that data is essentially static, so a stored snapshot is exactly
# what we want -- the scraper just refreshes it when it's back online.
_CACHE_FILE = os.environ.get("ATLASTRA_CACHE_FILE") or str(
    Path(__file__).resolve().parent.parent / "data" / "sofa_cache.json")
_CACHE_FLUSH_SEC = 30            # debounce disk writes to at most one per this many s
_last_flush = 0.0


def _load_cache() -> None:
    """Restore the persisted pull-through cache on startup (cache mode only)."""
    if not CACHE_MODE:
        return
    try:
        with open(_CACHE_FILE) as fh:
            raw = json.load(fh)
        with _LOCK:
            for path, entry in raw.items():
                _PUSH_CACHE[path] = (entry[0], entry[1])
        print(f"live_feed: restored {len(raw)} cached SofaScore paths from disk", flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:                 # noqa: BLE001 -- corrupt/partial file -> start empty
        print(f"live_feed: cache restore failed: {type(e).__name__}: {e}", flush=True)


def _save_cache() -> None:
    """Atomically snapshot the pull-through cache to disk (temp file + rename)."""
    if not CACHE_MODE:
        return
    try:
        with _LOCK:
            snap = {p: [ts, data] for p, (ts, data) in _PUSH_CACHE.items()}
        Path(_CACHE_FILE).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{_CACHE_FILE}.tmp"
        with open(tmp, "w") as fh:
            json.dump(snap, fh)
        os.replace(tmp, _CACHE_FILE)
    except Exception as e:                 # noqa: BLE001 -- never let a disk hiccup break serving
        print(f"live_feed: cache save failed: {type(e).__name__}: {e}", flush=True)


if CACHE_MODE:
    _load_cache()                          # restore last snapshot on import (server startup)
    atexit.register(_save_cache)           # capture the final state on graceful shutdown


def _fetch_direct(path: str):
    """Real SofaScore GET (only where SofaScore is reachable). Parsed JSON or None.
    NO extra headers -- a bare browser-TLS request passes the bot challenge."""
    try:
        r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=25, proxy=_PROXY)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 -- network/parse hiccup -> treat as no data
        return None


def _get(path: str, ttl: float, queue: bool = True):
    """Cached bare GET. Returns parsed JSON dict, or None on non-200 (e.g. a 404
    heatmap for a player who never came on). queue=False = cache-only in relay mode:
    don't enqueue a miss for the pusher to fetch (used for low-value bulk lookups like
    per-player rating estimates, which would otherwise flood the relay)."""
    now = time.time()
    if CACHE_MODE:                       # serve from pushed cache; queue misses
        with _LOCK:
            hit = _PUSH_CACHE.get(path)
            if hit and now - hit[0] < ttl:
                return hit[1]
            if queue:
                _QUEUE[path] = _QUEUE.get(path, now)
            return hit[1] if hit else None   # serve stale while the pusher refetches
    with _LOCK:
        hit = _CACHE.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    data = _fetch_direct(path)
    with _LOCK:
        _CACHE[path] = (now, data)
    return data


def queue_pending(limit: int = 40) -> list[str]:
    """Paths the cloud server needs fetched (oldest first). Prunes stale requests."""
    now = time.time()
    with _LOCK:
        for p in [p for p, t in _QUEUE.items() if now - t > 180]:
            _QUEUE.pop(p, None)
        return [p for p, _ in sorted(_QUEUE.items(), key=lambda kv: kv[1])][:limit]


def prewarm(eid: int) -> None:
    """Queue all of a match's core SofaScore paths at once (called when the match is
    first opened) so the relay fetches them in one batch -- every tab is then ready
    together instead of each tab triggering its own ~one-cycle wait. player-stats is
    composed from these same paths, so it needs no separate entry."""
    if not CACHE_MODE:
        return
    now = time.time()
    with _LOCK:
        for p in (f"/event/{eid}", f"/event/{eid}/lineups", f"/event/{eid}/incidents",
                  f"/event/{eid}/statistics", f"/event/{eid}/shotmap", f"/event/{eid}/managers"):
            if p not in _PUSH_CACHE:
                _QUEUE.setdefault(p, now)


def prewarm_preview(eid: int, home_id=None, away_id=None) -> None:
    """Queue ALL of an upcoming fixture's preview paths at once. fixture_preview()
    itself can only queue the team form/squad/h2h/odds paths AFTER the event header is
    cached (it needs the team ids from it), so a cold preview costs two relay cycles.
    Given the team ids up front (from our live_matches snapshot) we queue everything in
    one shot, collapsing it to a single cycle. Idempotent -- already-queued/cached
    paths are skipped."""
    if not CACHE_MODE:
        return
    now = time.time()
    paths = [f"/event/{eid}", f"/event/{eid}/h2h"]
    paths += [f"/event/{eid}/odds/{prov}/featured" for prov in _ODDS_PROVIDERS]
    for tid in (home_id, away_id):
        if tid:
            paths += [f"/team/{tid}/events/last/0", f"/team/{tid}/players"]
    with _LOCK:
        for p in paths:
            if p not in _PUSH_CACHE:
                _QUEUE.setdefault(p, now)


def prewarm_team(tid: int) -> None:
    """Queue a national team's direct paths at once. Without this only /team/{id}
    is queued on the first call (national_team early-returns before the others), so
    squad/results/fixtures would each lag a relay cycle behind the header."""
    if not CACHE_MODE:
        return
    now = time.time()
    with _LOCK:
        for p in (f"/team/{tid}", f"/team/{tid}/players",
                  f"/team/{tid}/events/last/0", f"/team/{tid}/events/next/0"):
            if p not in _PUSH_CACHE:
                _QUEUE.setdefault(p, now)


def venue(eid: int, warm: bool = False):
    """Stadium · city for a match, from the event detail. Returns None if not yet
    cached. warm=True queues the event for the relay on a cache miss (so an
    upcoming match's venue fills in within a relay cycle); warm=False is cache-only
    (used for live games, whose detail is already warmed)."""
    d = _get(f"/event/{eid}", ttl=3600, queue=warm) or {}
    v = (d.get("event") or {}).get("venue") or {}
    if not v:
        return None
    parts = [(v.get("stadium") or {}).get("name") or v.get("name"),
             (v.get("city") or {}).get("name"),
             (v.get("country") or {}).get("name")]
    return " · ".join(p for p in parts if p) or None


def prewarm_players(eid: int, player_ids) -> None:
    """Queue each player's club + this-match heatmap so the lineup player modal opens
    instantly. Called when a lineup is viewed -> the relay fetches them in the
    background while the user reads the lineup, so a click finds them cached. Already-
    cached paths (e.g. a live match the pusher pre-warms) are skipped."""
    if not CACHE_MODE:
        return
    now = time.time()
    with _LOCK:
        for pid in player_ids:
            if not pid:
                continue
            for p in (f"/player/{pid}", f"/event/{eid}/player/{pid}/heatmap"):
                if p not in _PUSH_CACHE:
                    _QUEUE.setdefault(p, now)


def queue_has(prefix: str) -> bool:
    """True if some path under `prefix` is queued but not yet cached -- i.e. the
    client should keep waiting (data is coming) rather than show 'unavailable'."""
    if not CACHE_MODE:
        return False
    with _LOCK:
        return any(p.startswith(prefix) and p not in _PUSH_CACHE for p in _QUEUE)


def cache_put(items: list[dict]) -> int:
    """Store pushed {path, body} responses and clear them from the queue, then flush the
    cache to disk (debounced) so the snapshot survives restarts / scraper downtime."""
    global _last_flush
    now = time.time()
    with _LOCK:
        for it in items or []:
            p = it.get("path")
            if p:
                _PUSH_CACHE[p] = (now, it.get("body"))
                _QUEUE.pop(p, None)
    if now - _last_flush > _CACHE_FLUSH_SEC:      # persist outside the lock, at most 1/30s
        _last_flush = now
        _save_cache()
    return len(items or [])


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
    _hs, _as = ev.get("homeScore") or {}, ev.get("awayScore") or {}

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
        "available": True, "event_id": eid, "ut_id": ut.get("id"),
        "competition": ut.get("name") or (ev.get("tournament") or {}).get("name"),
        "round": ri.get("name") or (f"Round {ri['round']}" if ri.get("round") else None),
        "start_ts": ev.get("startTimestamp"),
        "status": st.get("type"), "status_desc": st.get("description"), "minute": minute,
        "home": home.get("name"), "home_id": home.get("id"), "home_country": country(home),
        "home_national": bool(home.get("national")),
        "away": away.get("name"), "away_id": away.get("id"), "away_country": country(away),
        "away_national": bool(away.get("national")),
        # `display` is the regulation/ET goals score; `current` folds the penalty
        # shootout in (a 1-1 final reads as 5-4). Show display + the shootout apart.
        "home_score": _hs.get("display") if _hs.get("display") is not None else _hs.get("current"),
        "away_score": _as.get("display") if _as.get("display") is not None else _as.get("current"),
        "home_pens": _hs.get("penalties"), "away_pens": _as.get("penalties"),
        "xg_available": bool(ev.get("hasXg")),
        "referee": (lambda r: {"name": r.get("name"),
                               "country": (r.get("country") or {}).get("alpha2")}
                    if r and r.get("name") else None)(ev.get("referee") or {}),
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


# SofaScore avg match rating (~6.0–8.3) -> our 0–100 scale. Calibrated by least-
# squares against ~1,360 players present in BOTH systems (shared slope + per-line
# intercept, R^2≈0.55), which corrects the old hand-tuned map that ran ~8–11 points
# hot. The coarse lineup line (G/D/M/F) shifts the intercept because SofaScore rates
# the lines a few points apart for the same Atlastra value.
_EST_SLOPE = 29.3
_EST_INTERCEPT = {"G": -147.0, "D": -145.0, "M": -146.0, "F": -143.0}
_EST_DEFAULT = -145.0


def season_estimate(player_id: int, position: str | None = None) -> int | None:
    """Estimate a CONSTANT, overall Atlastra-scale rating (~30–92) for a player not
    in our warehouse. It is NOT a single match/tournament: we appearance-weight the
    player's average SofaScore rating across their recent competitions (league, cup,
    continental, national team), so one game can't swing it. `position` is the coarse
    lineup line (G/D/M/F) used to pick the calibration intercept. Cached 7 days."""
    if not player_id:
        return None
    seas = _get(f"/player/{player_id}/statistics/seasons", ttl=604800, queue=False)
    tot_r, tot_a = 0.0, 0                             # appearance-weighted sums
    for blk in ((seas or {}).get("uniqueTournamentSeasons") or [])[:5]:
        ut = (blk.get("uniqueTournament") or {}).get("id")
        sns = blk.get("seasons") or []
        if not ut or not sns:
            continue
        ov = _get(f"/player/{player_id}/unique-tournament/{ut}/season/{sns[0]['id']}/statistics/overall",
                  ttl=604800, queue=False)
        st = (ov or {}).get("statistics") or {}
        rt, ap = st.get("rating"), st.get("appearances") or 0
        if rt and ap >= 2:
            tot_r += float(rt) * ap
            tot_a += ap
    if tot_a < 5:                                     # not enough of a sample to estimate
        return None
    sofa = tot_r / tot_a                              # career-level average rating
    intercept = _EST_INTERCEPT.get((position or "")[:1].upper(), _EST_DEFAULT)
    return max(30, min(92, round(_EST_SLOPE * sofa + intercept)))


def lineups(eid: int) -> dict:
    d = _get(f"/event/{eid}/lineups", ttl=60)
    if not d or "home" not in d:
        return {"available": False, "confirmed": False, "home": None, "away": None}
    mg = _get(f"/event/{eid}/managers", ttl=600) or {}
    home, away = _lineup_side(d["home"]), _lineup_side(d["away"])
    home["manager"] = (mg.get("homeManager") or {}).get("name")
    away["manager"] = (mg.get("awayManager") or {}).get("name")
    return {"available": True, "confirmed": bool(d.get("confirmed")),
            "home": home, "away": away}


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
                        assist=(i.get("assist1") or {}).get("name"),
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


# ---- key moments + generated commentary -------------------------------------
_SITUATION = {"assisted": "an assist", "regular": "open play", "fast-break": "a fast break",
              "set-piece": "a set piece", "corner": "a corner", "penalty": "the spot",
              "free-kick": "a free kick", "throw-in-set-piece": "a throw-in",
              "fast_break": "a fast break"}
_BODY = {"head": "header", "left-foot": "left-footed effort",
         "right-foot": "right-footed effort", "other": "effort"}


def _mclock(mn, at):
    return f"{mn}+{at}'" if at else (f"{mn}'" if mn is not None else "")


def key_moments(eid: int) -> dict:
    """A chronological feed of the match's KEY events -- goals + red cards (from
    incidents) and the big chances/notable shots (from the shot map, by xG + outcome)
    -- each with a one-line auto-generated commentary string. Rule-based off the
    structured SofaScore data: no model, no API key, always available."""
    hdr = header(eid)
    if not hdr.get("available"):
        return {"available": False, "moments": []}
    sm, tl = shotmap(eid), timeline(eid)
    if not sm.get("available") and not tl.get("available"):
        return {"available": False, "moments": []}   # still loading -> wrapper flags pending
    home, away = hdr.get("home"), hdr.get("away")
    team = lambda is_home: home if is_home else away
    moments = []

    for e in (tl.get("events") or []):
        mn, at, side = e.get("minute"), e.get("added_time"), e.get("side")
        if e.get("type") == "goal":
            tm = team(side == "home")
            txt = f"GOAL — {tm}! {e.get('player')} makes it {e.get('home_score')}-{e.get('away_score')}"
            txt += f", set up by {e['assist']}." if e.get("assist") else "."
            moments.append({"minute": mn, "added_time": at, "kind": "goal", "side": side,
                            "icon": "⚽", "text": txt})
        elif e.get("type") == "card" and "red" in (e.get("klass") or "").lower():
            tm = team(side == "home")
            moments.append({"minute": mn, "added_time": at, "kind": "red", "side": side,
                            "icon": "🟥", "text": f"Red card — {e.get('player')} ({tm}) is sent off."})

    for s in (sm.get("shots") or []):
        if s.get("is_goal"):
            continue                                  # goals already covered (with assist + score)
        xg = s.get("xg") or 0
        st = s.get("shot_type")
        if not (xg >= 0.30 or (st == "save" and xg >= 0.15) or st == "post"):
            continue
        tm = team(s.get("is_home"))
        body = _BODY.get(s.get("body_part"), "effort")
        sit = _SITUATION.get(s.get("situation"))
        frm = f" from {sit}" if sit else ""
        xgs = f" (xG {xg:.2f})" if xg else ""
        if st == "save":
            icon, txt = "🧤", f"Big chance for {tm}! {s.get('player')}'s {body}{frm} forces a save{xgs}."
        elif st == "post":
            icon, txt = "🪵", f"Off the woodwork! {s.get('player')} ({tm}) strikes the frame{xgs}."
        elif st == "block":
            icon, txt = "🧱", f"{s.get('player')}'s {body}{frm} is blocked{xgs}."
        else:
            icon, txt = "😬", f"Big chance missed! {s.get('player')} ({tm}) sends a {body}{frm} off target{xgs}."
        moments.append({"minute": s.get("minute"), "added_time": s.get("added_time"),
                        "kind": "chance", "side": "home" if s.get("is_home") else "away",
                        "icon": icon, "text": txt})

    moments.sort(key=lambda m: ((m.get("minute") if m.get("minute") is not None else 0),
                                m.get("added_time") or 0))
    return {"available": True, "home": home, "away": away, "moments": moments}


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
            "key_passes": _num(st, "keyPass"), "big_chances_created": _num(st, "bigChanceCreated"),
            "dribbles": _num(st, "wonContest"), "dribble_attempts": _num(st, "totalContest"),
            "recoveries": _num(st, "ballRecovery"),
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


# ---- Atlastra score prediction ----------------------------------------------
def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_prediction(eid: int, consensus: dict | None) -> dict | None:
    """Atlastra's most-likely final scoreline. A bivariate-Poisson model: fit the two
    teams' expected goals so the implied win/draw/loss matches the market 1X2
    (consensus), assuming a typical ~2.7-goal game, then take the modal scoreline. For
    a LIVE match it projects forward -- scales the remaining expected goals by the time
    left and adds them to the current score, so the call updates as the game unfolds."""
    if not consensus:
        return None
    p_home = (consensus.get("home") or 0) / 100.0
    p_away = (consensus.get("away") or 0) / 100.0
    if p_home <= 0 and p_away <= 0:
        return None
    TG = 2.7                                          # baseline total expected goals
    # fit the goal supremacy S (home_xg - away_xg) to the market win/loss split
    best_s, best_err = 0.0, 9e9
    s = -3.0
    while s <= 3.0001:
        lh, la = max((TG + s) / 2, 0.04), max((TG - s) / 2, 0.04)
        mh = [_pois(h, lh) for h in range(8)]
        ma = [_pois(a, la) for a in range(8)]
        ph = sum(mh[h] * ma[a] for h in range(8) for a in range(8) if h > a)
        pa = sum(mh[h] * ma[a] for h in range(8) for a in range(8) if h < a)
        err = (ph - p_home) ** 2 + (pa - p_away) ** 2
        if err < best_err:
            best_err, best_s = err, s
        s += 0.1
    lh, la = max((TG + best_s) / 2, 0.04), max((TG - best_s) / 2, 0.04)

    hdr = header(eid)
    live = hdr.get("status") == "inprogress" and hdr.get("home_score") is not None
    base_h = base_a = 0
    if live:
        minute = hdr.get("minute") or 0
        rem = max(0.04, (94 - min(minute, 94)) / 90.0)   # fraction of match left (+stoppage)
        lh, la = lh * rem, la * rem
        base_h, base_a = hdr.get("home_score") or 0, hdr.get("away_score") or 0

    mh = [_pois(h, lh) for h in range(7)]
    ma = [_pois(a, la) for a in range(7)]
    res = lambda h, a: "home" if h > a else "away" if a > h else "draw"  # noqa: E731
    pr = {"home": 0.0, "draw": 0.0, "away": 0.0}
    dist = []
    for h in range(7):
        for a in range(7):
            p = mh[h] * ma[a]
            fh, fa = base_h + h, base_a + a
            pr[res(fh, fa)] += p
            dist.append((p, fh, fa))
    fav = max(pr, key=pr.get)
    # the headline score is the most likely EXACT score consistent with that favoured
    # result -- so a clear away favourite reads as e.g. 0-1, not a 1-1 draw that merely
    # happens to be the single most probable cell.
    bp, bh, ba = max((c for c in dist if res(c[1], c[2]) == fav), key=lambda x: x[0])
    return {"home": bh, "away": ba, "result": fav,
            "result_conf": round(pr[fav] * 100), "confidence": round(bp * 100),
            "live": live}


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


def player_club(pid: int) -> dict:
    """A player's current club (SofaScore /player) -- used in the lineup modal so a
    national-team player shows the club they actually play for."""
    tm = ((_get(f"/player/{pid}", ttl=900) or {}).get("player") or {}).get("team") or {}
    if not tm.get("name"):
        return {"available": False}
    return {"available": True, "team": tm.get("name"), "team_id": tm.get("id"),
            "national": bool(tm.get("national")),
            "logo": f"/api/sofa_team_img?id={tm['id']}" if tm.get("id") else None}


_IMG_CACHE: dict[str, tuple[float, object]] = {}


def team_image(team_id: int):
    """Fetch a SofaScore team crest (bytes, content-type) via the TLS bypass and
    cache it -- the browser can't hit SofaScore directly, so the webapp proxies it."""
    key = f"img:{team_id}"
    now = time.time()
    with _LOCK:
        hit = _IMG_CACHE.get(key)
        if hit and now - hit[0] < 86400:
            return hit[1]
    res = None
    try:
        r = tls_requests.get(f"{SOFASCORE_BASE}/team/{int(team_id)}/image", timeout=15, proxy=_PROXY)
        if r.status_code == 200 and (r.headers.get("content-type") or "").startswith("image/"):
            res = (r.content, r.headers["content-type"])
    except Exception:  # noqa: BLE001
        res = None
    with _LOCK:
        _IMG_CACHE[key] = (now, res)
    return res


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


def _form_for(team_id: int):
    """A national/club team's last results from its own perspective (most recent first)."""
    last = (_get(f"/team/{team_id}/events/last/0", ttl=1800) or {}).get("events", [])
    rows = []
    for e in reversed(last):                       # API returns oldest-first
        ht, at = e.get("homeTeam") or {}, e.get("awayTeam") or {}
        is_home = ht.get("id") == team_id
        gf = (e.get("homeScore") if is_home else e.get("awayScore") or {}).get("current")
        ga = (e.get("awayScore") if is_home else e.get("homeScore") or {}).get("current")
        if gf is None or ga is None:
            continue
        rows.append({"opponent": (at if is_home else ht).get("name"), "gf": gf, "ga": ga,
                     "result": "W" if gf > ga else "D" if gf == ga else "L",
                     "comp": (e.get("tournament") or {}).get("name")})
        if len(rows) >= 6:
            break
    return rows


def _squad_names(team_id: int):
    ps = (_get(f"/team/{team_id}/players", ttl=3600) or {}).get("players", [])
    return [(p.get("player") or {}).get("name") for p in ps if (p.get("player") or {}).get("name")]


def fixture_preview(eid: int) -> dict:
    """SofaScore-driven preview for an upcoming fixture (works for national teams):
    recent form, head-to-head, bookmaker projection, and squad names for the
    server to enrich into key players from our ratings."""
    h = header(eid)
    if not h.get("available") or not h.get("home_id"):
        # In relay mode the event detail lands a cycle after header() queues it above;
        # report that as pending (not "not found") so the client waits + retries
        # instead of flashing "Preview not available" on a cold/just-restarted cache.
        if CACHE_MODE and queue_has(f"/event/{eid}"):
            return {"available": False, "pending": True, "event_id": eid}
        return {"available": False, "error": "Fixture not found."}
    hid, aid = h["home_id"], h["away_id"]
    pred = prediction(eid)
    # upcoming-fixture h2h is static -> cache long so rechecks stay warm (no re-queue)
    td = (_get(f"/event/{eid}/h2h", ttl=3600) or {}).get("teamDuel")
    # In cache mode each of those calls queues a miss for the relay rather than
    # fetching directly; until the relay fills them the preview would render empty.
    # Flag that as pending so the client waits + retries instead of showing blanks.
    pending = CACHE_MODE and (
        queue_has(f"/team/{hid}/events/last/0") or queue_has(f"/team/{aid}/events/last/0")
        or queue_has(f"/team/{hid}/players") or queue_has(f"/team/{aid}/players")
        or queue_has(f"/event/{eid}/h2h"))
    return {
        "available": True, "pending": pending, "event_id": eid, "competition": h.get("competition"),
        "round": h.get("round"), "kickoff_ts": h.get("start_ts"), "status": h.get("status"),
        "home": {"name": h["home"], "id": hid, "country": h.get("home_country"),
                 "national": h.get("home_national"), "recent": _form_for(hid), "squad": _squad_names(hid)},
        "away": {"name": h["away"], "id": aid, "country": h.get("away_country"),
                 "national": h.get("away_national"), "recent": _form_for(aid), "squad": _squad_names(aid)},
        "prediction": pred.get("consensus") if pred.get("available") else None,
        "h2h": ({"home_wins": td.get("homeWins"), "draws": td.get("draws"),
                 "away_wins": td.get("awayWins")} if td else None),
    }
