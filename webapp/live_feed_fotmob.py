"""
Match-detail feed from FotMob (server-side, no proxy) — the FotMob counterpart of
webapp/live_feed.py. Every function returns the SAME shape match.js already renders,
so the frontend is unchanged; only the source flips from SofaScore to FotMob.

One signed /api/data/matchDetails call per match feeds every tab (header, stats,
lineups, shot map, timeline, players), cached briefly so opening a match is one
fetch. FotMob answers 200 from a datacenter IP, so this runs on the server 24/7.
"""
import re
import time
from datetime import datetime, timezone

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
    adv = 0.20 * (0.4 if h["home_national"] else 1.0)     # WC venues ~neutral
    sup = max(-2.5, min(2.5, (hr - ar) / 250.0))
    hx = max(0.25, 1.35 + sup / 2 + adv)
    ax = max(0.25, 1.35 - sup / 2)
    ph = pd = pa = 0.0
    best, bestp = (0, 0), -1.0
    for i in range(9):
        for j in range(9):
            p = _pois(i, hx) * _pois(j, ax)
            if p > bestp:
                best, bestp = (i, j), p
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    tot = ph + pd + pa or 1
    home, draw = round(ph / tot * 100), round(pd / tot * 100)
    cons = {"home": home, "draw": draw, "away": 100 - home - draw}
    predicted = max(cons, key=cons.get)
    return {"consensus": cons, "predicted": predicted, "score": best,
            "result": ("home" if best[0] > best[1] else "away" if best[1] > best[0] else "draw"),
            "conf": round(max(ph, pd, pa) / tot * 100)}


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
            "result_conf": m["conf"], "live": False}


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
    squad, manager = [], None
    for g in ((t.get("squad") or {}).get("squad")) or []:
        title = g.get("title")
        for m in g.get("members") or []:
            if title == "coach":
                manager = manager or m.get("name")
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
    h2h = (((_md(eid) or {}).get("content") or {}).get("h2h") or {}).get("summary")
    m = _model(eid)
    pred = {"available": True, **m["consensus"], "source": "model"} if m else {"available": False}
    return {"available": True, "event_id": eid,
            "home": h["home"], "away": h["away"], "home_id": hid, "away_id": aid,
            "home_country": h.get("home_country"), "away_country": h.get("away_country"),
            "kickoff_ts": h.get("start_ts"), "competition": h.get("competition"),
            "prediction": pred,
            "h2h": {"home_wins": (h2h or [None, None, None])[0] if h2h else None,
                    "draws": (h2h or [None, None, None])[1] if h2h else None,
                    "away_wins": (h2h or [None, None, None])[2] if h2h else None},
            "home_form": _form_for(hid), "away_form": _form_for(aid),
            "home_squad": _squad_names(hid), "away_squad": _squad_names(aid)}


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
