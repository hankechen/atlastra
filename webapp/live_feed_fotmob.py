"""
Match-detail feed from FotMob (server-side, no proxy) — the FotMob counterpart of
webapp/live_feed.py. Every function returns the SAME shape match.js already renders,
so the frontend is unchanged; only the source flips from SofaScore to FotMob.

One signed /api/data/matchDetails call per match feeds every tab (header, stats,
lineups, shot map, timeline, players), cached briefly so opening a match is one
fetch. FotMob answers 200 from a datacenter IP, so this runs on the server 24/7.
"""
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from pipeline.fotmob_auth import FotmobAuth
from pipeline.load_live_fotmob import NAT_ISO, COVERED, QUAL

_auth = FotmobAuth()
_CACHE: dict = {}                                    # matchId -> (expires, matchDetails)
_TTL_LIVE = 20                                       # seconds
CACHE_MODE = False                                   # FotMob fetches directly; no relay cache


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


# FotMob's knockout round notation ("1/8") -> readable name
_ROUND = {"1/16": "Round of 32", "1/8": "Round of 16", "1/4": "Quarter-final",
          "1/2": "Semi-final", "final": "Final"}


def _round_name(raw):
    if not raw:
        return None
    r = _ROUND.get(str(raw).lower())
    if r:
        return r
    return f"Matchday {raw}" if str(raw).isdigit() else raw   # group stage


# Words in a round name that mean it's a single-leg knockout (can't end in a draw).
_KO_WORDS = ("final", "quarter", "semi", "round of", "knockout", "play-off", "playoff", "1/")


def _is_knockout(d, round_name=None):
    """True if this match is a knockout tie (goes to ET/penalties, so no draw)."""
    g = (d or {}).get("general") or {}
    raw = str(g.get("leagueRoundName") or g.get("matchRound") or "").strip().lower()
    if raw in _ROUND:                                     # "1/16","1/8","1/4","1/2","final"
        return True
    rn = (round_name or "").strip().lower()
    return any(w in raw or w in rn for w in _KO_WORDS)


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
        "round": _round_name(g.get("leagueRoundName") or g.get("matchRound")),
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
    coach = team.get("coach") or {}
    return {"formation": team.get("formation"), "starting_xi": starters,
            "substitutes": substitutes, "manager": coach.get("name"),
            "manager_id": coach.get("id")}


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


# ---- per-player match stats -------------------------------------------------
def _flat_stats(pl: dict) -> dict:
    """FotMob nests player stats by display name; flatten to {statKey: statObj}."""
    out = {}
    for g in pl.get("stats") or []:
        for _, obj in (g.get("stats") or {}).items():
            k = obj.get("key")
            if k:
                out[k] = obj.get("stat") or {}
    return out


def player_stats(eid: int) -> dict:
    d = _md(eid)
    content = (d or {}).get("content") or {}
    ps = content.get("playerStats") or {}
    if not ps:
        return {"available": False, "players": []}
    lu = content.get("lineup") or {}
    home_id = (lu.get("homeTeam") or {}).get("id")
    ratings, starter_ids = {}, set()
    for t in ("homeTeam", "awayTeam"):
        team = lu.get(t) or {}
        for p in team.get("starters") or []:
            ratings[p.get("id")] = (p.get("performance") or {}).get("rating")
            starter_ids.add(p.get("id"))
        for p in team.get("subs") or []:
            ratings[p.get("id")] = (p.get("performance") or {}).get("rating")
    # cards per player from the timeline
    cards: dict = {}
    for e in ((content.get("matchFacts") or {}).get("events") or {}).get("events") or []:
        if e.get("type") == "Card":
            pid = (e.get("player") or {}).get("id")
            if pid is None:
                continue
            kind = "red" if "red" in str(e.get("card") or "").lower() else "yellow"
            cards.setdefault(pid, {}).setdefault(kind, 0)
            cards[pid][kind] += 1

    def v(f, k):
        return (f.get(k) or {}).get("value")

    players = []
    for p in ps.values():
        f = _flat_stats(p)
        pid = p.get("id")
        ap = f.get("accurate_passes") or {}
        c = cards.get(pid, {})
        players.append({
            "id": pid, "name": p.get("name"), "team": p.get("teamName"),
            "is_home": p.get("teamId") == home_id,
            "number": p.get("shirtNumber"), "position": p.get("usualPosition"),
            "started": pid in starter_ids, "rating": ratings.get(pid),
            "minutes": v(f, "minutes_played"),
            "goals": v(f, "goals"), "assists": v(f, "assists"),
            "shots": v(f, "total_shots"), "shots_on_target": v(f, "ShotsOnTarget"),
            "xg": v(f, "expected_goals"), "xa": None,
            "passes": ap.get("total"), "accurate_passes": ap.get("value"),
            "key_passes": v(f, "chances_created"), "big_chances_created": None,
            "dribbles": v(f, "dribbles_succeeded"), "dribble_attempts": None,
            "recoveries": v(f, "recoveries"),
            "tackles": v(f, "matchstats.headers.tackles"), "duels_won": v(f, "duel_won"),
            "touches": v(f, "touches"), "fouls": v(f, "fouls"),
            "yellow": c.get("yellow", 0), "red": c.get("red", 0),
        })
    return {"available": bool(players), "players": players}


# ---- per-player heatmap -----------------------------------------------------
# FotMob serves heatmaps as an SVG of <circle cx cy r> points on a 105x68 pitch;
# parse them out and normalise to the 0-100 grid match.js's renderer expects.
_CIRCLE_RE = re.compile(r'cx="([-\d.]+)"\s+cy="([-\d.]+)"')
_HM_CACHE: dict = {}


def _heatmaps(eid: int) -> dict:
    now = time.time()
    hit = _HM_CACHE.get(eid)
    if hit and hit[0] > now:
        return hit[1]
    e = int(eid)
    url = (f"/api/data/heatmap/match/{e}/heatmaps?heatmapUrl="
           f"https%3A%2F%2Fpub.fotmob.com%2Fprod%2Fdb%2Fapi%2Fheatmap%2Fmatch%2F{e}")
    try:
        d = _auth.get(url)
    except Exception:                                # noqa: BLE001
        return (hit[1] if hit else {})
    parsed = {}
    for k, svg in ((d or {}).get("players") or {}).items():
        try:
            pid = int(k[1:] if str(k).startswith("p") else k)
        except (ValueError, TypeError):
            continue
        parsed[pid] = [{"x": round(float(x) / 105 * 100, 1),
                        "y": round(float(y) / 68 * 100, 1)}
                       for x, y in _CIRCLE_RE.findall(svg)]
    _HM_CACHE[eid] = (now + 300, parsed)
    if len(_HM_CACHE) > 200:
        for kk in list(_HM_CACHE)[:100]:
            _HM_CACHE.pop(kk, None)
    return parsed


def player_heatmap(eid: int, pid: int) -> dict:
    # the heatmap SVG is keyed by Opta id; lineups pass the FotMob id, so bridge via
    # playerStats (which carries both).
    ps = ((_md(eid) or {}).get("content") or {}).get("playerStats") or {}
    opta = next((p.get("optaId") for p in ps.values() if p.get("id") == int(pid)), None)
    pts = _heatmaps(eid).get(int(opta)) if opta else None
    if not pts:
        return {"available": False, "points": []}
    return {"available": True, "points": pts}


# ---- prediction: an Atlastra MODEL (FotMob has no 1X2 odds market) -----------
# Poisson goals model. Each side gets a rating -- FIFA rank for national teams,
# recent form (PPG + goal diff) for clubs -- turned into expected goals with a small
# home edge (near-neutral for WC venues), then a 0-8 x 0-8 grid gives 1X2 + the most
# likely scoreline. Self-contained: no external odds feed, can't be blocked.
import math as _math   # noqa: E402


def _rank_elo(rank):
    return max(1350.0, 2050.0 - 6.5 * rank) if rank else 1550.0


def _form_rating(team_id):
    form = _form_for(team_id)
    if not form:
        return 1550.0
    ppg = sum(3 if f["result"] == "W" else 1 if f["result"] == "D" else 0 for f in form) / len(form)
    gd = sum((f["gf"] or 0) - (f["ga"] or 0) for f in form) / len(form)
    return 1600.0 + (ppg - 1.4) * 120.0 + gd * 28.0


def _rating(team_id, national, rank):
    if national and rank:
        return 0.65 * _rank_elo(rank) + 0.35 * _form_rating(team_id)
    return _form_rating(team_id)


def _quality(eid, side, team_id):
    """Individual-squad quality: the published XI's total market value (best signal,
    live/near-kickoff), else the team's top-players' average rating (always available
    pre-match). Returns (kind, value) or None."""
    lu = ((_md(eid) or {}).get("content") or {}).get("lineup") or {}
    mv = (lu.get(side + "Team") or {}).get("totalStarterMarketValue")
    if mv:
        return ("mv", mv)
    tp = ((_team(team_id) or {}).get("overview") or {}).get("topPlayers") or {}
    ratings = [p.get("value") for p in ((tp.get("byRating") or {}).get("players") or [])[:5]
               if p.get("value")]
    return ("rt", sum(ratings) / len(ratings)) if ratings else None


def _quality_elos(eid, hid, aid):
    """(home, away) individual-quality Elo on a comparable scale, or (None, None) if the
    two teams don't share a signal type (so we never mix market value vs ratings)."""
    hq, aq = _quality(eid, "home", hid), _quality(eid, "away", aid)
    if not hq or not aq or hq[0] != aq[0]:
        return None, None
    if hq[0] == "mv":                                # squad market value (€) -> log Elo
        f = lambda v: 1350.0 + 130.0 * _math.log10(max(v, 1) / 1e6)
    else:                                            # avg top-player rating -> Elo
        f = lambda v: 1400.0 + (v - 6.8) * 250.0
    return f(hq[1]), f(aq[1])


def _pois(k, lam):
    return _math.exp(-lam) * lam ** k / _math.factorial(k)


def _model(eid):
    """(consensus %, predicted key, most-likely scoreline) from the goals model."""
    d = _md(eid)
    h = header(eid)
    teams = (d or {}).get("header", {}).get("teams") or []
    if not h.get("available") or len(teams) < 2 or not h.get("home_id"):
        return None
    hr = _rating(h["home_id"], h["home_national"], teams[0].get("fifaRank"))
    ar = _rating(h["away_id"], h["away_national"], teams[1].get("fifaRank"))
    # factor in INDIVIDUAL squad quality (XI market value / top-players' ratings), not
    # just team results -- blended 40% with the form/rank signal
    hq, aq = _quality_elos(eid, h["home_id"], h["away_id"])
    if hq is not None:
        hr, ar = 0.6 * hr + 0.4 * hq, 0.6 * ar + 0.4 * aq
    adv = 0.20 * (0.4 if h["home_national"] else 1.0)     # WC venues ~neutral
    # Stronger supremacy (wider clamp + 0.65 weight vs the old 0.5) so lopsided games push
    # the favourite's expected goals up to ~3-4 -> 3-0/4-0 scorelines become likely, not
    # just 2-0. Even games stay ~1.4 each.
    sup = max(-3.0, min(3.0, (hr - ar) / 230.0))
    hx = max(0.2, 1.35 + sup * 0.65 + adv)
    ax = max(0.2, 1.35 - sup * 0.65)
    ph = pd = pa = 0.0
    grid = []
    for i in range(9):
        for j in range(9):
            p = _pois(i, hx) * _pois(j, ax)
            grid.append(((i, j), p))
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    ko = _is_knockout(d, h.get("round"))
    if ko and pd > 0:
        # Knockout tie: it can't finish level. The drawn mass is resolved in extra time /
        # penalties, split by each side's regulation win chance (the stronger side keeps a
        # small edge in the shootout).
        base = ph + pa
        if base > 0:
            ph += pd * ph / base
            pa += pd * pa / base
        else:
            ph += pd / 2
            pa += pd / 2
        pd = 0.0
    tot = ph + pd + pa or 1
    if ko:
        home = round(ph / tot * 100)
        cons = {"home": home, "draw": 0, "away": 100 - home}
    else:
        home, draw = round(ph / tot * 100), round(pd / tot * 100)
        cons = {"home": home, "draw": draw, "away": 100 - home - draw}
    predicted = max(cons, key=cons.get)
    # Top 3 most-likely EXACT scorelines with probabilities (shown on the match page).
    # In a knockout, drop level scorelines — the tie can't end drawn.
    score_grid = [x for x in grid if x[0][0] != x[0][1]] if ko else list(grid)
    score_grid.sort(key=lambda x: -x[1])
    scores = [{"home": i, "away": j, "pct": round(p / tot * 100)} for (i, j), p in score_grid[:3]]
    best = score_grid[0][0]
    return {"consensus": cons, "predicted": predicted, "score": best, "scores": scores,
            "result": predicted, "conf": round(max(ph, pd, pa) / tot * 100)}


def prediction(eid: int) -> dict:
    m = _model(eid)
    if not m:
        return {"available": False, "consensus": None, "books": []}
    return {"available": True, "consensus": m["consensus"], "predicted": m["predicted"],
            "books": [], "n_books": 0, "source": "model"}


def score_prediction(eid: int, consensus=None):
    m = _model(eid)
    if not m:
        return None
    return {"home": m["score"][0], "away": m["score"][1], "result": m["result"],
            "result_conf": m["conf"], "scores": m["scores"], "live": False}


# FotMob shot vocabulary -> readable, for the auto-generated Key Moments commentary
_KM_BODY = {"LeftFoot": "left-footed effort", "RightFoot": "right-footed effort", "Header": "header"}
_KM_SIT = {"RegularPlay": "open play", "IndividualPlay": "open play", "FastBreak": "a fast break",
           "SetPiece": "a set piece", "FromCorner": "a corner", "FreeKick": "a free kick",
           "ThrowInSetPiece": "a throw-in", "Penalty": "the spot"}


def key_moments(eid: int) -> dict:
    """Chronological feed of the match's KEY events — goals + red cards (timeline) and
    the big chances / notable shots (shot map, by xG + outcome) — each with a one-line
    auto-generated commentary string. Rule-based off FotMob's structured data."""
    hdr = header(eid)
    if not hdr.get("available"):
        return {"available": False, "moments": []}
    sm, tl = shotmap(eid), timeline(eid)
    if not sm.get("available") and not tl.get("available"):
        return {"available": False, "moments": []}
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
        elif e.get("type") == "card" and "red" in str(e.get("detail") or "").lower():
            tm = team(side == "home")
            moments.append({"minute": mn, "added_time": at, "kind": "red", "side": side,
                            "icon": "🟥", "text": f"Red card — {e.get('player')} ({tm}) is sent off."})

    for s in (sm.get("shots") or []):
        if s.get("is_goal"):
            continue                                  # goals already covered above
        xg, st = s.get("xg") or 0, s.get("shot_type")
        if not (xg >= 0.30 or (st == "save" and xg >= 0.15) or st == "post"):
            continue
        tm = team(s.get("is_home"))
        body = _KM_BODY.get(s.get("body_part"), "effort")
        sit = _KM_SIT.get(s.get("situation"))
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


# ---- national teams + fixture preview (FotMob team endpoint) -----------------
def _iso_ts(s):
    if not s:
        return None
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ")
                   .replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError):
        return None


_TEAM_CACHE: dict = {}


def _team(team_id: int) -> dict | None:
    now = time.time()
    hit = _TEAM_CACHE.get(team_id)
    if hit and hit[0] > now:
        return hit[1]
    try:
        d = _auth.get(f"/api/data/teams?id={int(team_id)}")
    except Exception:                                # noqa: BLE001
        return hit[1] if hit else None
    _TEAM_CACHE[team_id] = (now + 300, d)
    return d


def _team_event_row(fx: dict) -> dict:
    st = fx.get("status") or {}
    started = st.get("started")
    home, away = fx.get("home") or {}, fx.get("away") or {}
    return {"event_id": fx.get("id"), "home": home.get("name"), "away": away.get("name"),
            "home_score": home.get("score") if started else None,
            "away_score": away.get("score") if started else None,
            "competition": (fx.get("tournament") or {}).get("name"),
            "ts": _iso_ts(st.get("utcTime")),
            "status": "finished" if st.get("finished") else "inprogress" if started else "notstarted"}


_SQUAD_POS = {"keepers": "Goalkeeper", "defenders": "Defender",
              "midfielders": "Midfielder", "attackers": "Forward"}


def national_team(team_id: int) -> dict:
    t = _team(team_id)
    det = (t or {}).get("details") or {}
    if not det.get("name"):
        return {"available": False}
    ov = t.get("overview") or {}
    squad, manager, manager_id = [], None, None
    for g in ((t.get("squad") or {}).get("squad")) or []:
        title = g.get("title")
        for m in g.get("members") or []:
            if title == "coach":
                if manager is None:
                    manager, manager_id = m.get("name"), m.get("id")
            else:
                squad.append({"id": m.get("id"), "name": m.get("name"),
                              "position": _SQUAD_POS.get(title), "number": m.get("shirtNumber")})
    fixt = ov.get("overviewFixtures") or []
    fin = [f for f in fixt if (f.get("status") or {}).get("finished")]
    results = sorted((_team_event_row(f) for f in fin),
                     key=lambda r: r["ts"] or 0, reverse=True)[:12]
    fixtures = sorted((_team_event_row(f) for f in fixt if not (f.get("status") or {}).get("started")),
                      key=lambda r: r["ts"] or 0)[:8]
    # latest XI from the most recent finished match that has a published lineup
    latest_xi = None
    for f in sorted(fin, key=lambda f: (f.get("status") or {}).get("utcTime") or "", reverse=True)[:4]:
        lu = lineups(f.get("id"))
        if not lu.get("available"):
            continue
        is_home = (f.get("home") or {}).get("id") == team_id
        side = lu["home"] if is_home else lu["away"]
        if side and side.get("starting_xi"):
            latest_xi = {"event_id": f.get("id"),
                         "opponent": ((f.get("away") if is_home else f.get("home")) or {}).get("name"),
                         "is_home": is_home,
                         "home_score": (f.get("home") or {}).get("score"),
                         "away_score": (f.get("away") or {}).get("score"),
                         "ts": _iso_ts((f.get("status") or {}).get("utcTime")),
                         "formation": side.get("formation"), "starting_xi": side.get("starting_xi")}
            break
    return {"available": True, "id": team_id, "name": det.get("name"),
            "country_code": NAT_ISO.get(det.get("name")), "manager": manager,
            "manager_id": manager_id,
            "latest_xi": latest_xi, "results": results, "fixtures": fixtures, "squad": squad}


def _form_for(team_id: int):
    ov = (_team(team_id) or {}).get("overview") or {}
    rows = []
    for f in reversed(ov.get("overviewFixtures") or []):
        st = f.get("status") or {}
        if not st.get("finished"):
            continue
        home, away = f.get("home") or {}, f.get("away") or {}
        is_home = home.get("id") == team_id
        gf = (home if is_home else away).get("score")
        ga = (away if is_home else home).get("score")
        if gf is None or ga is None:
            continue
        rows.append({"opponent": (away if is_home else home).get("name"), "gf": gf, "ga": ga,
                     "result": "W" if gf > ga else "D" if gf == ga else "L",
                     "comp": (f.get("tournament") or {}).get("name")})
        if len(rows) >= 6:
            break
    return rows


def _squad_names(team_id: int):
    return [p["name"] for p in national_team(team_id).get("squad") or [] if p.get("name")]


def fixture_preview(eid: int) -> dict:
    """Upcoming-fixture preview from FotMob: recent form + h2h + squad names (the
    server enriches those into key players from our ratings). No bookmaker odds."""
    h = header(eid)
    if not h.get("available") or not h.get("home_id"):
        return {"available": False, "error": "Fixture not found."}
    hid, aid = h["home_id"], h["away_id"]
    summ = (((_md(eid) or {}).get("content") or {}).get("h2h") or {}).get("summary")
    h2h = ({"home_wins": summ[0], "draws": summ[1], "away_wins": summ[2]}
           if isinstance(summ, list) and len(summ) == 3 else None)
    m = _model(eid)
    return {
        "available": True, "pending": False, "event_id": eid,
        "competition": h.get("competition"), "round": h.get("round"),
        "kickoff_ts": h.get("start_ts"), "status": h.get("status"),
        "home": {"name": h["home"], "id": hid, "country": h.get("home_country"),
                 "national": h.get("home_national"), "recent": _form_for(hid), "squad": _squad_names(hid)},
        "away": {"name": h["away"], "id": aid, "country": h.get("away_country"),
                 "national": h.get("away_national"), "recent": _form_for(aid), "squad": _squad_names(aid)},
        "prediction": m["consensus"] if m else None,
        "h2h": h2h,
    }


def coach(coach_id: int) -> dict:
    """A coach/manager's profile — coaching career (teams managed, with dates) and
    trophies — from FotMob's playerData endpoint (coaches share the player id space)."""
    try:
        d = _auth.get(f"/api/data/playerData?id={int(coach_id)}")
    except Exception:                                # noqa: BLE001
        return {"available": False}
    if not d or not d.get("name"):
        return {"available": False}
    ci = ((d.get("careerHistory") or {}).get("careerItems") or {}).get("coach") or {}
    career = []
    for e in (ci.get("teamEntries") or []):
        career.append({"team": e.get("team"), "team_id": e.get("teamId"),
                       "start": (e.get("startDate") or "")[:10] or None,
                       "end": (e.get("endDate") or "")[:10] or None,
                       "active": bool(e.get("active"))})
    career.sort(key=lambda c: c["start"] or "", reverse=True)
    trophies = []
    for t in ((d.get("trophies") or {}).get("playerTrophies") or []):
        for tour in (t.get("tournaments") or []):
            won = tour.get("seasonsWon") or []
            if won:
                trophies.append({"team": t.get("teamName"), "competition": tour.get("leagueName"),
                                 "count": len(won), "seasons": won})
    trophies.sort(key=lambda x: x["count"], reverse=True)
    pt = d.get("primaryTeam") or {}
    pi = d.get("playerInformation") or []
    ctry = next((x.get("value", {}).get("fallback") for x in pi
                 if x.get("title") == "Country" or x.get("icon", {}).get("id") == "flag"), None)
    return {"available": True, "id": int(coach_id), "name": d.get("name"),
            "photo": f"https://images.fotmob.com/image_resources/playerimages/{int(coach_id)}.png",
            "country": ctry, "current_team": pt.get("teamName"), "current_team_id": pt.get("teamId"),
            "career": career, "trophies": trophies}


# --- Top Highlights --------------------------------------------------------
# No global "top videos" endpoint exists, so we aggregate per-match: gather
# finished matches in a window, pull each match's highlight clip (thumbnail +
# video URL), rank by competition importance + goals + recency, take the top N.
# A finished match's highlight never changes, so we cache it forever per id.

# competition importance (lower = more important) — mirrors live.js compRank
_COMP_RANK = {77: 0, 50: 1, 44: 2, 42: 3, 47: 4, 87: 5, 55: 6, 54: 7, 53: 8}
_HL_CACHE: dict[int, dict | None] = {}                 # match_id -> clip | None (permanent)

_YT_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|embed/|v/|shorts/))([\w-]{11})")


def _yt_embed(url: str):
    """A youtube.com/embed URL if `url` is a YouTube link, else None (only these
    can play inline; FIFA.com and other sources open in a new tab)."""
    m = _YT_RE.search(url or "")
    return f"https://www.youtube.com/embed/{m.group(1)}" if m else None


def _match_highlight(mid: int) -> dict | None:
    """The highlight clip for a finished match (cached permanently), or None."""
    if mid in _HL_CACHE:
        return _HL_CACHE[mid]
    res = None
    try:
        mf = ((_md(mid) or {}).get("content") or {}).get("matchFacts") or {}
        hl = mf.get("highlights") or {}
        if hl.get("url"):
            res = {"url": hl["url"], "image": hl.get("image"), "source": hl.get("source")}
    except Exception:                                    # noqa: BLE001
        res = None
    _HL_CACHE[mid] = res
    return res


def _goals_from(m: dict) -> int:
    h = _num((m.get("home") or {}).get("score"))
    a = _num((m.get("away") or {}).get("score"))
    return int((h or 0) + (a or 0))


def highlights(period: str = "day", limit: int = 10) -> dict:
    """Top highlight clips of the day/week across covered competitions, ranked by
    competition importance, then goals, then recency."""
    days = 7 if period == "week" else 1
    today = date.today()
    cands, seen = [], set()
    for delta in range(0, days + 1):                     # today back `days` days
        d = today - timedelta(days=delta)
        try:
            data = _auth.get(f"/api/data/matches?date={d:%Y%m%d}")
        except Exception:                                # noqa: BLE001
            continue
        for L in (data.get("leagues") or []):
            pid = L.get("primaryId")
            meta = COVERED.get(pid) or QUAL.get(pid)
            if not meta:
                continue
            key, name, group = meta
            for m in (L.get("matches") or []):
                st = m.get("status") or {}
                mid = m.get("id")
                if not st.get("finished") or mid in seen:
                    continue
                seen.add(mid)
                ts = m.get("timeTS")
                cands.append({
                    "m": m, "pid": pid, "competition": name, "group": group,
                    "goals": _goals_from(m),
                    "ts": int(ts / 1000) if ts else 0,
                    "qual": pid in QUAL,
                })
    # rank first (cheap), then fetch highlights only for the top candidates
    cands.sort(key=lambda c: (_COMP_RANK.get(c["pid"], 99) + (10 if c["qual"] else 0),
                              -c["goals"], -c["ts"]))
    top = cands[:max(limit * 3, 24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        hls = list(pool.map(lambda c: _match_highlight(c["m"].get("id")), top))
    clips = []
    for c, hl in zip(top, hls):
        if not hl:
            continue
        m = c["m"]
        home, away = m.get("home") or {}, m.get("away") or {}
        intl = c["group"] == "International"
        hs, as_ = _num(home.get("score")), _num(away.get("score"))
        clips.append({
            "event_id": m.get("id"),
            "competition": c["competition"],
            "round": ("Qualification" if c["qual"]
                      else _round_name(m.get("tournamentStage"))),
            "kickoff_ts": c["ts"],
            "home": home.get("name"), "home_id": home.get("id"),
            "home_cc": NAT_ISO.get(home.get("name")) if intl else None,
            "away": away.get("name"), "away_id": away.get("id"),
            "away_cc": NAT_ISO.get(away.get("name")) if intl else None,
            "home_score": int(hs) if hs is not None else None,
            "away_score": int(as_) if as_ is not None else None,
            "thumbnail": hl["image"], "url": hl["url"],
            "source": hl["source"], "embed": _yt_embed(hl["url"]),
        })
        if len(clips) >= limit:
            break
    return {"available": True, "period": period, "clips": clips}


# --- Top Goals (YouTube-sourced individual goal clips) ---------------------
# FotMob has no per-action video, but it has the DATA for every goal (scorer,
# assist, minute, xG). We rank goals by competition + how spectacular the finish
# was (low xG = screamer), then find each goal's clip on YouTube — those ARE
# embeddable, so unlike the FIFA match reels they play in-page. Search results
# for a finished goal never change, so we cache each query permanently.

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
_YT_CACHE: dict[str, list] = {}                        # query -> [videoId, ...] (permanent)
_YT_ONLY_VIDEO = "EgIQAQ%3D%3D"                        # search filter: type = Video


def _yt_search(query: str, want: int = 1) -> list:
    """Top YouTube video ids for a query (keyless scrape of the results page)."""
    if query not in _YT_CACHE:
        ids, seen = [], set()
        try:
            url = (f"https://www.youtube.com/results?search_query="
                   f"{urllib.parse.quote(query)}&sp={_YT_ONLY_VIDEO}")
            req = urllib.request.Request(url, headers={
                "User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9",
                "Cookie": "CONSENT=YES+1"})              # skip the EU consent interstitial
            html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
            for vid in re.findall(r'"videoRenderer":\{"videoId":"([\w-]{11})"', html):
                if vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
                if len(ids) >= 6:
                    break
        except Exception:                                # noqa: BLE001 — YouTube blip -> no clip
            ids = []
        _YT_CACHE[query] = ids
    return _YT_CACHE[query][:want]


def _goal_events(mid: int) -> list:
    """Open-play/penalty goals from a finished match (own goals excluded)."""
    d = _md(mid) or {}
    ev = ((d.get("content") or {}).get("matchFacts") or {}).get("events") or {}
    out = []
    for e in (ev.get("events") or []):
        if e.get("type") != "Goal" or e.get("ownGoal") or e.get("isPenaltyShootoutEvent"):
            continue
        sm = e.get("shotmapEvent") or {}
        xg = sm.get("expectedGoals")
        out.append({
            "scorer": e.get("fullName") or e.get("nameStr"),
            "scorer_id": e.get("playerId"),
            "assist": (e.get("assistStr") or None),
            "minute": e.get("timeStr") or (str(e.get("time")) if e.get("time") is not None else None),
            "is_home": bool(e.get("isHome")),
            "xg": xg if isinstance(xg, (int, float)) else None,
            "penalty": (sm.get("situation") == "Penalty") or (e.get("goalDescriptionKey") == "penalty"),
        })
    return out


def top_goals(period: str = "day", limit: int = 12) -> dict:
    """The best goals of the day/week with an embeddable YouTube clip each,
    ranked by competition importance, then how spectacular the finish was."""
    days = 7 if period == "week" else 1
    today = date.today()
    matches, seen = [], set()
    for delta in range(0, days + 1):
        d = today - timedelta(days=delta)
        try:
            data = _auth.get(f"/api/data/matches?date={d:%Y%m%d}")
        except Exception:                                # noqa: BLE001
            continue
        for L in (data.get("leagues") or []):
            pid = L.get("primaryId")
            meta = COVERED.get(pid) or QUAL.get(pid)
            if not meta:
                continue
            _key, name, group = meta
            for m in (L.get("matches") or []):
                st = m.get("status") or {}
                mid = m.get("id")
                if not st.get("finished") or mid in seen or not _goals_from(m):
                    continue
                seen.add(mid)
                rank = _COMP_RANK.get(pid, 99) + (10 if pid in QUAL else 0)
                matches.append({"m": m, "rank": rank, "competition": name, "group": group,
                                "ngoals": _goals_from(m), "ts": int((m.get("timeTS") or 0) / 1000)})
    # mine goals only from the more important matches (bounds matchDetails fetches)
    matches.sort(key=lambda c: (c["rank"], -c["ngoals"], -c["ts"]))
    top_m = matches[:20]
    with ThreadPoolExecutor(max_workers=8) as pool:
        per = list(pool.map(lambda c: _goal_events(c["m"].get("id")), top_m))
    goals = []
    for c, gl in zip(top_m, per):
        m = c["m"]
        home, away = m.get("home") or {}, m.get("away") or {}
        intl = c["group"] == "International"
        for g in gl:
            if not g["scorer"]:
                continue
            team = home if g["is_home"] else away
            opp = away if g["is_home"] else home
            xg = g["xg"]
            wow = (1 - xg) if xg is not None else 0.5     # low xG (screamer) ranks higher
            goals.append({**g, "team": team.get("name"), "opponent": opp.get("name"),
                          "team_cc": NAT_ISO.get(team.get("name")) if intl else None,
                          "competition": c["competition"], "rank": c["rank"], "ts": c["ts"],
                          "event_id": m.get("id"), "wow": wow,
                          "worldie": xg is not None and xg < 0.06 and not g["penalty"]})
    goals.sort(key=lambda g: (g["rank"], -g["wow"], -g["ts"]))
    goals = goals[:limit]

    def _query(g):
        yr = datetime.utcfromtimestamp(g["ts"]).year if g["ts"] else ""
        return f'{g["scorer"]} goal vs {g["opponent"]} {g["competition"]} {yr}'.strip()

    with ThreadPoolExecutor(max_workers=6) as pool:
        vids = list(pool.map(lambda g: _yt_search(_query(g), 1), goals))
    clips = []
    for g, v in zip(goals, vids):
        if not v:
            continue
        vid = v[0]
        clips.append({
            "scorer": g["scorer"], "scorer_id": g["scorer_id"], "assist": g["assist"],
            "minute": g["minute"], "team": g["team"], "team_cc": g["team_cc"],
            "opponent": g["opponent"], "competition": g["competition"],
            "xg": round(g["xg"], 2) if g["xg"] is not None else None,
            "penalty": g["penalty"], "worldie": g["worldie"], "event_id": g["event_id"],
            "video_id": vid, "url": f"https://youtu.be/{vid}",
            # note: no in-page embed — broadcaster/FIFA content-ID blocks embedding on
            # most football clips (undetectable keyless), so cards open YouTube directly.
            "embed": None,
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        })
    return {"available": True, "period": period, "clips": _enrich_stats(clips)}


# --- Trending (most-viewed football clips on YouTube) ----------------------
# The closest free stand-in for "viral clips of the day" (X/Reddit are paywalled/
# blocked). We search YouTube across football queries, keep clips uploaded within
# the window, and rank by view count — surfacing official highlights AND viral
# creator content (edits, skills comps). Results move, so we cache with a TTL.

_TREND_QUERIES = ["football goals", "soccer skills", "football highlights",
                  "world cup goals", "premier league goals", "football wonder goal"]
_TREND_CACHE: dict[str, tuple] = {}                    # period -> (expiry, payload)
_TREND_TTL = 1800                                      # 30 min
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def _parse_views(s: str) -> int:
    m = re.search(r'([\d,.]+)\s*(K|M|B)?\s*views', s or "")
    if not m:
        return 0
    return int(float(m.group(1).replace(",", "")) * {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2), 1))


def _age_days(s: str):
    m = re.search(r'(\d+)\s*(minute|hour|day|week|month|year)s?\s*ago', s or "")
    return int(m.group(1)) * _UNIT_DAYS[m.group(2)] if m else None


def _yt_results(query: str, sp: str = "EgQIAxABGAE%3D") -> list:
    """Parsed YouTube search results: id/title/views/age/etc. Default sp filters to
    'uploaded this week'; pass sp='' for relevance-ranked all-time results."""
    url = (f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
           + (f"&sp={sp}" if sp else ""))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    except Exception:                                    # noqa: BLE001
        return []
    out = []
    for chunk in html.split('"videoRenderer":{')[1:]:
        chunk = chunk[:4000]
        vid = re.match(r'"videoId":"([\w-]{11})"', chunk)
        title = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', chunk)
        if not vid or not title:
            continue
        views = re.search(r'"viewCountText":\{"simpleText":"((?:[^"\\]|\\.)*)"', chunk)
        pub = re.search(r'"publishedTimeText":\{"simpleText":"((?:[^"\\]|\\.)*)"', chunk)
        length = re.search(r'"lengthText":\{[^}]*"simpleText":"([\d:]+)"', chunk)
        ch = re.search(r'"ownerText":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', chunk)
        out.append({
            "id": vid.group(1),
            "title": _json_unescape(title.group(1)),
            "views": _parse_views(views.group(1) if views else ""),
            "age": pub.group(1) if pub else "",
            "length": length.group(1) if length else "",
            "channel": _json_unescape(ch.group(1)) if ch else "",
        })
    return out


def _json_unescape(s: str) -> str:
    try:
        return json.loads('"' + s + '"')
    except Exception:                                    # noqa: BLE001
        return s


# --- YouTube per-video engagement (views + likes) -------------------------- #
# Views come free in search results, but LIKES only live on the watch page, so we
# scrape it per video (cached 6h). NOTE: YouTube removed public DISLIKE counts in
# Nov 2021 — there is no real dislike number to show, for any video.
_YTSTATS_CACHE: dict[str, tuple] = {}                  # vid -> (expiry, {views,likes})
_YTSTATS_TTL = 6 * 3600


def _yt_stat_one(vid: str) -> dict:
    hit = _YTSTATS_CACHE.get(vid)
    if hit and hit[0] > time.time():
        return hit[1]
    stats = {"views": None, "likes": None}
    try:
        req = urllib.request.Request("https://www.youtube.com/watch?v=" + vid, headers={
            "User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"})
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "replace")
        v = (re.search(r'"viewCount":\{"simpleText":"([\d,]+)', html)
             or re.search(r'"viewCount":"(\d+)"', html))
        if v:
            stats["views"] = int(re.sub(r"[^\d]", "", v.group(1)))
        lk = (re.search(r'like this video along with ([\d,]+) other people', html)
              or re.search(r'"accessibilityText":"([\d,]+) likes"', html)
              or re.search(r'"likeCount":"(\d+)"', html))
        if lk:
            stats["likes"] = int(re.sub(r"[^\d]", "", lk.group(1)))
    except Exception:                                    # noqa: BLE001
        pass
    _YTSTATS_CACHE[vid] = (time.time() + _YTSTATS_TTL, stats)
    return stats


def _enrich_stats(clips: list) -> list:
    """Attach fresh views/likes (from the YouTube watch page) to clips carrying a
    video_id. Best-effort: a failed fetch just leaves that clip's stats as-is/None."""
    ids = [c.get("video_id") for c in clips if c.get("video_id")]
    if not ids:
        return clips
    with ThreadPoolExecutor(max_workers=8) as pool:
        stats = dict(zip(ids, pool.map(_yt_stat_one, ids)))
    for c in clips:
        s = stats.get(c.get("video_id"))
        if not s:
            continue
        if s.get("views") is not None:
            c["views"] = s["views"]
        c["likes"] = s.get("likes")
    return clips


def trending(period: str = "week") -> dict:
    """Most-viewed football clips uploaded within the window (day/week)."""
    hit = _TREND_CACHE.get(period)
    if hit and hit[0] > time.time():
        return hit[1]
    max_days = 2 if period == "day" else 7
    pool = {}
    for q in _TREND_QUERIES:
        for v in _yt_results(q):
            age = _age_days(v["age"])
            if age is not None and age <= max_days and v["id"] not in pool:
                pool[v["id"]] = v
    top = sorted(pool.values(), key=lambda v: -v["views"])[:15]
    clips = [{
        "video_id": v["id"], "title": v["title"], "channel": v["channel"],
        "views": v["views"], "length": v["length"], "age": v["age"],
        "url": f"https://youtu.be/{v['id']}", "embed": None,
        "thumbnail": f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg",
    } for v in top]
    out = {"available": True, "period": period, "clips": _enrich_stats(clips)}
    _TREND_CACHE[period] = (time.time() + _TREND_TTL, out)
    return out


# --- Shorts (viral football skills / dribbling / edits, TikTok-style) ------
# YouTube Shorts are the accessible stand-in for TikTok (which is captcha-walled).
# They come back in search as `shortsLockupViewModel` (not videoRenderer), with
# title+views inside accessibilityText. Ranked by views (skills content is
# evergreen, so no date window). Cached 1h.

_SHORTS_QUERIES = ["football skills shorts", "football dribbling shorts", "football edit shorts",
                   "soccer nutmeg shorts", "football freestyle shorts", "football skills"]
_SHORTS_CACHE: dict[str, tuple] = {}
_SHORTS_TTL = 3600


def _acc_views(s: str) -> int:
    m = re.search(r'([\d.,]+)\s*(million|billion|thousand)?\s*views', s or "")
    if not m:
        return 0
    return int(float(m.group(1).replace(",", ""))
               * {"thousand": 1e3, "million": 1e6, "billion": 1e9}.get(m.group(2), 1))


def _shorts_results(query: str) -> list:
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")
    except Exception:                                    # noqa: BLE001
        return []
    out = []
    for chunk in html.split('"shortsLockupViewModel"')[1:]:
        chunk = chunk[:1500]
        vid = re.search(r'"videoId":"([\w-]{11})"', chunk)
        acc = re.search(r'"accessibilityText":"((?:[^"\\]|\\.)*?)"', chunk)
        if not vid or not acc:
            continue
        a = _json_unescape(acc.group(1))
        title = re.match(r'(.*?),\s*[\d.,]+\s*(?:million|billion|thousand)?\s*views', a)
        out.append({"id": vid.group(1), "title": title.group(1) if title else a,
                    "views": _acc_views(a)})
    return out


def shorts() -> dict:
    """Most-viewed football skills / dribbling / edit Shorts (TikTok-style)."""
    hit = _SHORTS_CACHE.get("all")
    if hit and hit[0] > time.time():
        return hit[1]
    pool = {}
    for q in _SHORTS_QUERIES:
        for v in _shorts_results(q):
            if v["id"] not in pool or v["views"] > pool[v["id"]]["views"]:
                pool[v["id"]] = v
    top = sorted(pool.values(), key=lambda v: -v["views"])[:18]
    clips = [{
        "video_id": v["id"], "title": v["title"], "views": v["views"],
        "url": f"https://www.youtube.com/shorts/{v['id']}", "embed": None,
        "thumbnail": f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg",
    } for v in top]
    out = {"available": True, "clips": _enrich_stats(clips)}
    _SHORTS_CACHE["all"] = (time.time() + _SHORTS_TTL, out)
    return out


# --- Top 25 stars (reputation-ranked) + their best skills video ------------
# Reputation isn't in our data, so this is a curated editorial list of the most
# renowned attackers/midfielders. An "agent" fetches each one's best skills/
# highlights video from YouTube. List + best videos are evergreen → cached 24h.

_STARS = [
    ("Harry Kane", "Bayern Munich", "ST"),
    ("Michael Olise", "Bayern Munich", "W"),
    ("Lamine Yamal", "Barcelona", "W"),
    ("Kylian Mbappé", "Real Madrid", "ST"),
    ("Declan Rice", "Arsenal", "CM"),
    ("Ousmane Dembélé", "PSG", "FW"),
    ("Luis Díaz", "Bayern Munich", "W"),
    ("Khvicha Kvaratskhelia", "PSG", "W"),
    ("Bruno Fernandes", "Manchester United", "AM"),
    ("Vitinha", "PSG", "CM"),
    ("Erling Haaland", "Manchester City", "ST"),
    ("Arda Güler", "Real Madrid", "AM"),
    ("Pedri", "Barcelona", "CM"),
    ("Rayan Cherki", "Manchester City", "AM"),
    ("Nico Paz", "Como", "AM"),
    ("Raphinha", "Barcelona", "W"),
    ("Nuno Mendes", "PSG", "LB"),
    ("Vinícius Jr", "Real Madrid", "W"),
    ("Julián Álvarez", "Atlético Madrid", "ST"),
    ("Joshua Kimmich", "Bayern Munich", "CM"),
    ("Achraf Hakimi", "PSG", "RB"),
    ("Yan Diomande", "RB Leipzig", "W"),
    ("Antoine Semenyo", "Bournemouth", "W"),
    ("Jude Bellingham", "Real Madrid", "CM"),
    ("João Neves", "PSG", "CM"),
]
# less-famous names need the club to disambiguate the YouTube search (e.g. two Vitinhas)
_STARS_QUALIFY = {"Vitinha", "Nico Paz", "Yan Diomande", "João Neves", "Nuno Mendes",
                  "Rayan Cherki", "Arda Güler", "Michael Olise"}
_STARS_CACHE: dict[str, tuple] = {}
_STARS_TTL = 86400                                     # 24h
_YEAR_2025_PLUS = re.compile(r'20(2[5-9]|[3-9]\d)')    # a 2025+ year in a title


def _skills_video(name: str, club: str | None = None) -> dict | None:
    """Best RECENT (2025+) skills/highlights video for a player, or None. Biases the
    search to recent uploads, then picks the first result that is actually from 2025
    onward (by upload age or a 2025+ year in the title), falling back to the top hit."""
    q = f"{name} {club} skills goals 2025" if club else f"{name} skills goals 2025"
    res = _yt_results(q, sp="")                              # relevance-ranked
    if not res:
        return None

    def recent(v):
        ad = _age_days(v.get("age"))
        return (ad is not None and ad <= 550) or bool(_YEAR_2025_PLUS.search(v.get("title") or ""))

    return next((v for v in res if recent(v)), res[0])


def top_stars() -> dict:
    """The curated top-25 (by reputation, not our ratings), each with their best
    recent (2025+) skills/highlights video (searched on YouTube)."""
    hit = _STARS_CACHE.get("all")
    if hit and hit[0] > time.time():
        return hit[1]

    def fetch(item):
        idx, (name, club, pos) = item
        v = _skills_video(name, club if name in _STARS_QUALIFY else None)
        return {
            "rank": idx + 1, "player": name, "club": club, "position": pos,
            "video_id": v["id"] if v else None,
            "title": v["title"] if v else None,
            "url": f"https://youtu.be/{v['id']}" if v else None,
            "embed": None,
            "thumbnail": f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg" if v else None,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        clips = list(pool.map(fetch, enumerate(_STARS)))
    clips = [c for c in clips if c["video_id"]]
    out = {"available": True, "clips": _enrich_stats(clips)}
    _STARS_CACHE["all"] = (time.time() + _STARS_TTL, out)
    return out


_PVID_CACHE: dict[str, tuple] = {}                     # name -> (expiry, payload)


def player_video(name: str) -> dict:
    """Best skills/highlights video for any player, searched on YouTube. Cached 24h."""
    name = (name or "").strip()
    if not name:
        return {"available": False}
    hit = _PVID_CACHE.get(name)
    if hit and hit[0] > time.time():
        return hit[1]
    v = _skills_video(name)                              # recent (2025+) skills video
    out = ({"available": True, "player": name, "video_id": v["id"], "title": v["title"],
            "url": f"https://youtu.be/{v['id']}",
            "thumbnail": f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg"}
           if v else {"available": False, "player": name})
    _PVID_CACHE[name] = (time.time() + 86400, out)
    return out


# --- Player recent form (FotMob per-match log) -----------------------------
# FotMob's playerData carries a `recentMatches` list (rating, minutes, G/A, cards,
# result) — a real game-by-game form log. We key by the FotMob player id, which the
# profile already has embedded in the player's photo URL (playerimages/<id>.png).
_PFORM_CACHE: dict[int, tuple] = {}                    # pid -> (expiry, payload)
_PFORM_TTL = 3600


def player_form(pid: int, limit: int = 8) -> dict:
    """Recent games for a FotMob player id: opponent, result, rating, G/A, minutes."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"available": False}
    hit = _PFORM_CACHE.get(pid)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        d = _auth.get(f"/api/data/playerData?id={pid}")
    except Exception:                                    # noqa: BLE001
        return {"available": False}
    raw = (d or {}).get("recentMatches") or []
    matches = []
    for m in raw:
        if not m.get("playedInMatch") or m.get("onBench"):
            continue                                     # only games he actually featured in
        home = m.get("isHomeTeam")
        hs, as_ = _num(m.get("homeScore")), _num(m.get("awayScore"))
        gf = hs if home else as_
        ga = as_ if home else hs
        result = None
        if gf is not None and ga is not None:
            result = "W" if gf > ga else ("D" if gf == ga else "L")
        rr = (m.get("ratingProps") or {}).get("rating")
        try:
            rating = float(rr) if rr not in (None, "") else None
        except (TypeError, ValueError):
            rating = None
        if rating is not None and rating <= 0:           # 0.0 = too few minutes to be rated
            rating = None
        ut = ((m.get("matchDate") or {}).get("utcTime"))
        try:
            ts = int(datetime.strptime(ut, "%Y-%m-%dT%H:%M:%S.%fZ")
                     .replace(tzinfo=timezone.utc).timestamp()) if ut else None
        except (ValueError, TypeError):
            ts = None
        matches.append({
            "event_id": m.get("id"),
            "date_ts": ts,
            "competition": m.get("leagueName"),
            "team": m.get("teamName"),
            "opponent": m.get("opponentTeamName"),
            "opponent_id": m.get("opponentTeamId"),
            "home": bool(home),
            "gf": int(gf) if gf is not None else None,
            "ga": int(ga) if ga is not None else None,
            "result": result,
            "rating": round(rating, 1) if rating is not None else None,
            "goals": int(_num(m.get("goals")) or 0),
            "assists": int(_num(m.get("assists")) or 0),
            "yellow": int(_num(m.get("yellowCards")) or 0),
            "red": int(_num(m.get("redCards")) or 0),
            "minutes": int(_num(m.get("minutesPlayed")) or 0),
            "motm": bool(m.get("playerOfTheMatch")),
        })
    matches.sort(key=lambda x: -(x["date_ts"] or 0))
    matches = matches[:max(1, limit)]
    rated = [x["rating"] for x in matches if x["rating"] is not None]
    summary = {
        "form": "".join(x["result"] for x in matches if x["result"])[::-1],  # oldest→newest L-to-R
        "avg_rating": round(sum(rated) / len(rated), 2) if rated else None,
        "goals": sum(x["goals"] for x in matches),
        "assists": sum(x["assists"] for x in matches),
        "games": len(matches),
    }
    out = {"available": bool(matches), "player_id": pid,
           "matches": matches, "summary": summary}
    _PFORM_CACHE[pid] = (time.time() + _PFORM_TTL, out)
    return out


# --- Player bio (foot, height) from FotMob playerData ----------------------
_PBIO_CACHE: dict[int, tuple] = {}                     # pid -> (expiry, payload)


def player_bio(pid: int) -> dict:
    """Preferred foot + height for a FotMob player id (from playerData.playerInformation).
    Cheap facts FotMob has that our DB doesn't. Cached 24h."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"available": False}
    hit = _PBIO_CACHE.get(pid)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        d = _auth.get(f"/api/data/playerData?id={pid}")
    except Exception:                                    # noqa: BLE001
        return {"available": False}
    info = {(it.get("title") or "").lower(): it.get("value")
            for it in ((d or {}).get("playerInformation") or [])}

    def val(k):
        v = info.get(k)
        if isinstance(v, dict):
            return v.get("fallback") or v.get("key") or v.get("numberValue")
        return v
    foot = val("preferred foot")
    hv = info.get("height")
    height_cm = hv.get("numberValue") if isinstance(hv, dict) else None
    out = {"available": True, "player_id": pid,
           "foot": (str(foot).title() if foot else None),
           "height_cm": int(height_cm) if height_cm else None,
           "height": (val("height") or None)}
    _PBIO_CACHE[pid] = (time.time() + 86400, out)
    return out


# --- Weekly recap data -----------------------------------------------------
# Raw facts (results, standout performers, best goals) for the AI week-in-review.

def _flat_pstats(pl: dict) -> dict:
    out = {}
    for g in (pl.get("stats") or []):
        for k, v in (g.get("stats") or {}).items():
            out[k] = (v.get("stat") or {}).get("value") if isinstance(v, dict) else v
    return out


def week_summary_data() -> dict:
    """Results, top-rated performers and best goals across the past 7 days."""
    today = date.today()
    matches, seen = [], set()
    for delta in range(0, 8):
        d = today - timedelta(days=delta)
        try:
            data = _auth.get(f"/api/data/matches?date={d:%Y%m%d}")
        except Exception:                                # noqa: BLE001
            continue
        for L in (data.get("leagues") or []):
            pid = L.get("primaryId")
            meta = COVERED.get(pid) or QUAL.get(pid)
            if not meta:
                continue
            _key, name, group = meta
            for m in (L.get("matches") or []):
                st = m.get("status") or {}
                mid = m.get("id")
                if not st.get("finished") or mid in seen:
                    continue
                seen.add(mid)
                rank = _COMP_RANK.get(pid, 99) + (10 if pid in QUAL else 0)
                matches.append({"m": m, "rank": rank, "competition": name,
                                "ngoals": _goals_from(m), "ts": int((m.get("timeTS") or 0) / 1000)})
    matches.sort(key=lambda c: (c["rank"], -c["ngoals"], -c["ts"]))

    results = []
    for c in matches[:14]:
        m = c["m"]
        h, a = m.get("home") or {}, m.get("away") or {}
        hs, as_ = _num(h.get("score")), _num(a.get("score"))
        results.append({
            "competition": c["competition"],
            "round": ("Qualification" if c["rank"] >= 10 else _round_name(m.get("tournamentStage"))),
            "home": h.get("name"), "away": a.get("name"),
            "home_score": int(hs) if hs is not None else None,
            "away_score": int(as_) if as_ is not None else None})

    perf = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        mds = list(pool.map(lambda c: _md(c["m"].get("id")), matches[:16]))
    for c, d in zip(matches[:16], mds):
        ps = ((d or {}).get("content") or {}).get("playerStats") or {}
        h, a = c["m"].get("home") or {}, c["m"].get("away") or {}
        for pl in ps.values():
            f = _flat_pstats(pl)
            r = f.get("FotMob rating")
            if not isinstance(r, (int, float)):
                continue
            team = pl.get("teamName")
            opp = a.get("name") if team == h.get("name") else h.get("name")
            perf.append({"name": pl.get("name"), "team": team, "opponent": opp,
                         "rating": round(r, 2), "goals": int(f.get("Goals") or 0),
                         "assists": int(f.get("Assists") or 0), "potm": bool(pl.get("isPotm")),
                         "competition": c["competition"]})
    perf.sort(key=lambda p: -p["rating"])
    top_perf, seen_p = [], set()
    for p in perf:                                       # one row per player (their best game)
        if p["name"] in seen_p:
            continue
        seen_p.add(p["name"])
        top_perf.append(p)
        if len(top_perf) >= 12:
            break

    goals = [{"scorer": g["scorer"], "team": g["team"], "opponent": g["opponent"],
              "minute": g["minute"], "xg": g["xg"], "worldie": g["worldie"], "assist": g["assist"]}
             for g in top_goals("week", limit=8).get("clips", [])]
    return {"results": results, "performers": top_perf, "goals": goals}


def player_club(pid: int) -> dict:
    """A player's current club — from FotMob playerData (used in the lineup modal)."""
    try:
        d = _auth.get(f"/api/data/playerData?id={int(pid)}")
    except Exception:                                # noqa: BLE001
        return {"available": False}
    tm = (d or {}).get("primaryTeam") or {}
    if not tm.get("teamName"):
        return {"available": False}
    tid = tm.get("teamId")
    return {"available": True, "team": tm.get("teamName"), "team_id": tid,
            "national": False,
            "logo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{tid}.png" if tid else None}


def team_image(team_id: int):
    """No proxy needed — FotMob logos are on a public CDN the browser can hit
    directly; callers can use the URL. Returns None (server falls back to the URL)."""
    return None


def team_logo_url(team_id: int) -> str | None:
    return f"https://images.fotmob.com/image_resources/logo/teamlogo/{int(team_id)}.png" if team_id else None


# ---- prewarm / relay no-ops (FotMob fetches directly; there is no relay cache) --
def prewarm(eid, *a, **k):
    return None


def prewarm_players(eid, player_ids=None, *a, **k):
    return None


def prewarm_preview(eid, home_id=None, away_id=None, *a, **k):
    return None


def prewarm_team(tid, *a, **k):
    return None


def queue_pending(limit: int = 40):
    return []


def queue_has(prefix: str) -> bool:
    return False


def cache_put(items):
    return 0


def venue(eid, warm: bool = False):
    # the frontend renders m.venue as a plain string ("Stadium · City · Country")
    st = (((_md(eid) or {}).get("content") or {}).get("matchFacts", {}).get("infoBox") or {}).get("Stadium") or {}
    parts = [st.get("name"), st.get("city"), st.get("country")]
    return " · ".join(p for p in parts if p) or None
