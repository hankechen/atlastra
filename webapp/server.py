"""
Atlastra web UI -- zero-dependency server (Python stdlib only).

Serves the static frontend (webapp/frontend) and a small JSON API backed by
analytics.queries.SoccerDB (real DuckDB data). Anything the warehouse doesn't
have (Ballon d'Or predictor, heatmap, technique
analysis, nationality, contract) is a clearly-labelled placeholder in the
frontend, per the design mock.

Run:  python -m webapp.server     ->  http://localhost:8000
"""
import json
import math
import os
import secrets
from datetime import datetime
import threading
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

# Same-origin image proxy for the player-card canvas: drawing a remote CDN image
# onto a canvas taints it and blocks toBlob()/toDataURL() (the download). Serving
# the bytes from our own origin keeps the canvas exportable. Host-allowlisted to
# the two CDNs we actually use (no open SSRF).
ALLOWED_IMG_HOSTS = ("fotmob.com", "wikimedia.org")


def fetch_image(url: str):
    try:
        h = urlparse(url)
        if h.scheme not in ("http", "https"):
            return None
        host = h.hostname or ""
        if not any(host == d or host.endswith("." + d) for d in ALLOWED_IMG_HOSTS):
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Atlastra"})
        with urllib.request.urlopen(req, timeout=8) as r:
            ctype = r.headers.get("Content-Type", "image/png")
            if not ctype.startswith("image/"):
                return None
            return r.read(6_000_000), ctype
    except Exception:  # noqa: BLE001
        return None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analytics.queries import SoccerDB  # noqa: E402
from config import FOCUS_SEASON, SOFASCORE_BASE  # noqa: E402
from webapp import auth  # noqa: E402


def _season(q):
    """Season code from the query string, defaulting to the current season."""
    return (q.get("season", [FOCUS_SEASON])[0] or FOCUS_SEASON)


def _int(v, default=0):
    """Parse a query-string int, falling back on missing/garbage instead of crashing."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
from webapp import live_feed  # noqa: E402
# FotMob answers from a datacenter IP (SofaScore doesn't), so with ATLASTRA_FOTMOB=1
# the whole match-detail/national-team/preview surface is served from FotMob on the
# server — no residential proxy, no Mac relay. The module is a drop-in for live_feed.
FOTMOB = os.environ.get("ATLASTRA_FOTMOB") == "1"
if FOTMOB:
    from webapp import live_feed_fotmob as live_feed  # noqa: F811, E402
from webapp import scout_ai  # noqa: E402
from webapp import weekly_recap  # noqa: E402
from webapp import signature_skills  # noqa: E402
from webapp import blog  # noqa: E402
from webapp import tactics  # noqa: E402

_TAC_SQUAD: dict = {}                                     # team -> (expiry, squad) cache
# Club → FotMob team id (authoritative live roster + correct player-photo ids).
_FOTMOB_TEAM_ID = {
    "Real Madrid": 8633, "Barcelona": 8634, "Manchester City": 8456, "Arsenal": 9825,
    "Liverpool": 8650, "Bayern München": 9823, "PSG": 9847, "Internazionale": 8636,
    "Atlético Madrid": 9906, "Bayer Leverkusen": 8178, "Manchester United": 10260,
    "Chelsea": 8455, "Tottenham Hotspur": 8586, "Newcastle United": 10261, "Napoli": 9875,
    "Milan": 8564, "Juventus": 9885, "Borussia Dortmund": 9789, "Aston Villa": 10252,
    "Bournemouth": 8678,
}
_GROUP_POS = {"keepers": "GK", "defenders": "CB", "midfielders": "CM", "attackers": "ST"}
# Manual roster overrides {team: [(player_name, fotmob_id)]} — force a player into a squad
# regardless of FotMob's live roster (e.g. loaned-out players the user wants available).
_SQUAD_ADD = {
    "Real Madrid": [("Endrick", 1406729)],
}


def _fotmob_mod():
    """The FotMob feed module. In production `live_feed` already IS it (ATLASTRA_FOTMOB=1);
    fall back to importing it directly so squads and the European field work either way."""
    if hasattr(live_feed, "fotmob_squad"):
        return live_feed
    from webapp import live_feed_fotmob
    return live_feed_fotmob


def _build_club_squad(team, tid):
    """Current squad from FotMob's team page (roster + correct player-photo ids), with FIFA/
    EA FC ratings + attributes matched in by name. `_SQUAD_ADD` force-includes extra players."""
    from webapp import fifa
    members = list(getattr(_fotmob_mod(), "fotmob_squad", lambda _i: [])(tid) or [])
    have = {m["name"].lower() for m in members}
    for nm, pid in _SQUAD_ADD.get(team, []):
        if nm.lower() not in have:
            members.append({"id": pid, "name": nm, "group": "attackers", "shirt": None})
    out = []
    for m in members:
        c = fifa.match(m["name"])
        pos = (c.get("pos") if c else None) or _GROUP_POS.get(m.get("group"), "CM")
        out.append({
            "player": m["name"], "position": pos, "rating": (c["o"] if c else 70),
            "minutes": None, "shirt": m.get("shirt"),
            "photo": f"https://images.fotmob.com/image_resources/playerimages/{m['id']}.png",
            "fifa": ({k: c[k] for k in ("o", "pac", "sho", "pas", "dri", "def", "phy", "hea")} if c else None),
            "per90": {}, "pct": {},
        })
    return out


_BREAKOUT_ATTRS = ("pac", "sho", "pas", "dri", "def", "phy", "hea")


_ATLAS_NORM: dict = {}                                    # cache: rating band -> median gap


def _atlas_band(o):
    """Card-rating band a player belongs to, for the norm below."""
    return "<70" if o < 70 else ("70-74" if o < 75 else
                                 ("75-79" if o < 80 else ("80-84" if o < 85 else "85+")))


def _atlas_norms(atlas):
    """Median (Atlas − EA FC) gap per card-rating band, measured on this season's index.

    The two numbers are NOT the same ruler: an Atlas rating is percentile- and
    season-based and sits ~21 points under an EA FC overall for a typical player (only ~4%
    of players clear their card at all). So a raw negative gap says nothing — comparing to
    zero would downgrade almost every player in the game. 'Below his card' only means
    something relative to what players at that card level normally rate, which is what
    this measures (and it does vary: the 85+ band runs ~10 under, the 70-79 bands ~22)."""
    if _ATLAS_NORM.get("bands"):
        return _ATLAS_NORM["bands"]
    from statistics import median
    from webapp import fifa
    by_band: dict = {}
    for nm, ar in (atlas or {}).items():
        if "|" in nm:                                    # initial+surname alias of a full name
            continue
        c = fifa.match(nm)
        if not c or not c.get("o"):
            continue
        by_band.setdefault(_atlas_band(c["o"]), []).append(ar - c["o"])
    bands = {b: median(g) for b, g in by_band.items() if len(g) >= 30}
    if bands:
        _ATLAS_NORM["bands"] = bands
    return bands


def _breakout_boost(base, ar, norm=None):
    """Signed rating adjustment from how a player's current-season combined Atlastra rating
    (best of their league & UCL rating — see atlas_rating_index) compares with their EA FC
    overall. Form cuts both ways:
      • gap  >= 10  -> flat +10  (a big league+UCL over-performer the EA FC card underrates)
      • gap  >   2  -> gentle scaled bump, capped at +6
      • rel <= -20  -> flat -5   (a season far below what his card level normally rates)
      • rel <  -10  -> gentle scaled cut, floored at -5
    `gap` is the raw difference (beating a scale that normally sits ~21 points lower is a
    strong signal on its own), while `rel` is the gap measured against `norm`, the median
    gap for that card band — see _atlas_norms for why the downside needs that reference.
    The cut is capped at HALF the upside: a card that flatters a player is weaker evidence
    than a season that beats it (injury, a bad team or a role change all drag the Atlas
    rating down without the player being worse). FIFA stays the base either way, so this
    tilts the sim rather than rewriting the player, and a player with no Atlas rating this
    season (under 450 minutes) is left alone."""
    if ar is None:
        return 0.0
    gap = ar - base
    if gap >= 10:
        return 10.0
    if gap > 2:
        return min(6.0, (gap - 2) * 0.55)
    if norm is None:
        return 0.0
    rel = gap - norm
    if rel <= -20:
        return -5.0
    if rel < -10:
        return max(-5.0, (rel + 10) * 0.5)
    return 0.0


# Positions that can be boosted but never cut. A defensive player's season rating leans on
# how his whole team defends and on output the rating scale measures poorly — a centre-back
# in a leaky side, or a keeper behind one, rates low without being a worse player — so a
# below-card number is weak evidence against him. Beating the card from back there is still
# a real signal, so the upside stays.
_NO_DOWNGRADE = {"GK", "CB", "FB"}


def _apply_breakout(p, atlas):
    """Apply the signed form adjustment to a player dict in place (effective rating + FIFA
    attrs). `p["breakout"]` carries the signed number, so the UI can badge a boost and a
    downgrade differently."""
    from analytics.queries import _fold
    f = p.get("fifa")
    if not f or not atlas:
        return
    nm = _fold(p.get("player") or "")
    t = nm.split()
    ar = atlas.get(nm)
    if ar is None and len(t) >= 2:
        ar = atlas.get(t[0][0] + "|" + t[-1])
    base = f.get("o", p.get("rating") or 70)
    boost = _breakout_boost(base, ar, _atlas_norms(atlas).get(_atlas_band(base)))
    if boost < 0 and (p.get("family")
                      or tactics.family_for_position(p.get("position") or "")) in _NO_DOWNGRADE:
        return                                           # defenders and fullbacks keep their card
    if not boost:
        return
    clamp = (lambda v: max(1, min(99, int(round(v)))))    # noqa: E731
    p["breakout"] = round(boost, 1)
    p["rating"] = clamp(base + boost)
    f["o"] = p["rating"]
    for k in _BREAKOUT_ATTRS:
        if k in f and f[k]:
            f[k] = clamp(f[k] + boost)


def _measured_roles_bulk(d, squad):
    """Attach the measured role to a whole squad in one query. Per-player lookups would be
    25 round trips for a screen that already does plenty."""
    names = [p.get("player") for p in squad if p.get("player")]
    if not names:
        return
    try:
        rows = d.con.execute(
            f"""SELECT lower(pl.player_name), r.role, r.confidence, r.depth, r.width,
                       r.position_group, r.season
                FROM player_learned_role r JOIN players pl ON pl.player_id = r.player_id
                WHERE lower(pl.player_name) IN ({','.join('?' * len(names))})""",
            [n.lower() for n in names]).fetchall()
    except Exception:                                      # noqa: BLE001
        return                                              # table absent until tools.roles runs
    best = {}
    for nm, role, cf, dep, wid, listed, season in rows:
        if cf and cf >= 0.30 and (nm not in best or season > best[nm][1]):
            best[nm] = ({"role": role, "conf": round(float(cf), 2), "depth": dep,
                         "width": wid, "listed": listed}, season)
    for p in squad:
        m = best.get((p.get("player") or "").lower())
        if m:
            p["measured"] = m[0]


def _tac_squad(d, team, tid=None):
    """Squad for the Tactics Lab. `tid` forces a FotMob team id, so any club in Europe
    (e.g. a Champions League opponent outside our own dropdown) can be built too."""
    import time as _t
    import re as _re
    hit = _TAC_SQUAD.get(team.lower())
    if hit and hit[0] > _t.time():
        return hit[1]
    from webapp import fifa
    tid = tid or _FOTMOB_TEAM_ID.get(team)
    if tid:                                              # club → FotMob roster + FIFA ratings
        sq = _build_club_squad(team, tid)
    else:                                                # national → World Cup squad + FIFA
        sq = d.tactics_squad(team) if team else []
        for p in sq:
            if p.get("fifa"):
                continue
            c = fifa.match(p.get("player") or "")
            if c:
                p["rating"] = c["o"]
                p["fifa"] = c
    # REAL top sprint speed (km/h) from the UCL physical-tracking leaderboard, keyed by the
    # FotMob player id embedded in each photo URL. FIFA pace wins in the engine, but keep it.
    speeds = getattr(live_feed, "ucl_top_speeds", lambda: {})() or {}
    if speeds:
        for p in sq:
            m = _re.search(r"playerimages/(\d+)", p.get("photo") or "")
            kmh = speeds.get(int(m.group(1))) if m else None
            if kmh:
                p["top_speed"] = kmh
    # Breakout boost: if a player's current-season Atlas league/UCL rating clears their FIFA
    # overall, they over-performed the market's assessment — nudge their effective rating and
    # attributes up so the sim rewards it. FIFA remains the base (Atlas is season-dependent).
    # A national side comes back on the tournament-performance scale, which is not the scale
    # the engine reads. Re-price it here so the user's own country is measured exactly the
    # way its opponents are — before this, only the opponents were corrected.
    from webapp import fifa as _fifa
    if not tid and not _fifa.is_club(team):
        _reprice_national(d, sq)
    atlas = d.atlas_rating_index() if hasattr(d, "atlas_rating_index") else {}
    for p in sq:
        _apply_breakout(p, atlas)
    _measured_roles_bulk(d, sq)
    _TAC_SQUAD[team.lower()] = (_t.time() + 600, sq)
    return sq


_TAC_PLAYER: dict = {}                                   # cache: player-name -> engine dict


def _split_season(name):
    """('Cristiano Ronaldo (2014/15)') -> ('Cristiano Ronaldo', '1415'). A past-season player
    carries his year in his display name, which is also his identity on the wire, so any
    rebuild — the sim, a substitution, a Champions League campaign — resolves the same man
    from the same season without the client having to track it separately."""
    import re as _re
    m = _re.match(r"^(.*)\s+\((\d{2})(\d{2})/(\d{2})\)$", (name or "").strip())
    if not m:
        return name, None
    return m.group(1).strip(), m.group(3) + m.group(4)


_LEARNED_ROLE: dict = {}


def _measured_role(d, name, season=None):
    """Where this player ACTUALLY stood, from his heatmap — see tools/roles.py. Independent
    of every listed position and of the engine's own role taxonomy, so it is the one part of
    the player card that cannot be argued with: it is 38 matches of occupancy.

    Returned only above a confidence floor. Positions are a continuum and a player sitting
    between two clusters gets an arbitrary winner; showing that as fact would be worse than
    showing nothing."""
    if not name:
        return None
    key = f"{name.lower()}|{season or ''}"
    if key in _LEARNED_ROLE:
        return _LEARNED_ROLE[key]
    out = None
    try:
        row = d.con.execute(
            """SELECT r.role, r.confidence, r.depth, r.width, r.position_group
               FROM player_learned_role r JOIN players pl ON pl.player_id = r.player_id
               WHERE lower(pl.player_name) = ?""" +
            (" AND r.season = ?" if season else "") +
            " ORDER BY r.season DESC LIMIT 1",
            [name.lower()] + ([season] if season else [])).fetchone()
        if row and row[1] and row[1] >= 0.30:
            out = {"role": row[0], "conf": round(float(row[1]), 2),
                   "depth": row[2], "width": row[3], "listed": row[4]}
    except Exception:                                      # noqa: BLE001
        out = None                                          # table absent until tools.roles runs
    _LEARNED_ROLE[key] = out
    return out


def _tac_player(d, name, season=None):
    """Resolve ONE player (from any team) into a full engine dict — for user-added
    'what-if' transfers (e.g. dropping Rodri into Real Madrid). Applies the same breakout
    boost the squad path uses and tags the player's best position family for auto-slotting.
    With `season`, resolves the player as he was that year instead (2014/15 onward)."""
    if not name:
        return None
    if not season:                                       # a past season travels in the name
        name, season = _split_season(name)
    key = f"{name.lower()}|{season or ''}"
    if key in _TAC_PLAYER:
        return _TAC_PLAYER[key]
    p = d.tactics_player(name, season) if hasattr(d, "tactics_player") else None
    if not p:
        return None
    atlas = d.atlas_rating_index() if hasattr(d, "atlas_rating_index") else {}
    if not p.get("season"):                              # a past season is already priced
        _apply_breakout(p, atlas)                        # league+UCL over-performer boost
    p["family"] = tactics.family_for_position(p.get("position"))
    # The role that actually suits him. An auto-XI profiles every player this way; a slot
    # you fill yourself kept whatever role the shape defaulted to, so a hand-built side had
    # its centre-backs cast as Ball-Playing and its holder as a Deep-Lying Playmaker
    # regardless of whether they could do it — and then took the miscast penalty for it.
    # ...for every shape he might be put in, since the right role depends on where he
    # plays: a fullback pushed into midfield wants a different job from the same man at
    # right back. The client picks the entry matching the slot it drops him into.
    p["best_role"] = tactics._best_role(p["family"], p)
    p["best_roles"] = {fam: tactics._best_role(fam, p) for fam in tactics.ROLES}
    p["measured"] = _measured_role(d, p.get("player"), season)
    _TAC_PLAYER[key] = p
    return p


def _xi_wire(xi):
    """Serialize XI slots for the client — display fields + role, no heavy stats."""
    out = []
    for s in xi:
        p = s.get("player")
        out.append({"id": s["id"], "family": s["family"], "line": s["line"],
                    "x": s["x"], "y": s["y"], "role": s["role"],
                    "player": ({"player": p["player"], "rating": p["rating"],
                                "position": p["position"], "photo": p.get("photo"),
                                "breakout": p.get("breakout")}
                               if p else None)})
    return out


_TAC_FORM: dict = {}                                     # cache: team -> form rating dict


def _team_form(d, team):
    """Cached recent league + UCL form rating for a club (see SoccerDB.team_form_rating)."""
    if not team:
        return {"form": 0.0}
    key = team.lower()
    if key not in _TAC_FORM:
        try:
            _TAC_FORM[key] = d.team_form_rating(team) or {"form": 0.0}
        except Exception:                                # noqa: BLE001
            _TAC_FORM[key] = {"form": 0.0}
    return _TAC_FORM[key]


def _tac_rebuild(d, team, slots):
    """Rebuild engine XI (full player stats) from wire slots {id,family,line,role,player}."""
    squad = _tac_squad(d, team)
    by_name = {p["player"]: p for p in squad}
    xi = []
    for s in (slots or []):
        pl = s.get("player")
        name = pl.get("player") if isinstance(pl, dict) else pl
        # squad first; if the player isn't on this team (a user-added what-if transfer),
        # resolve them independently so their real stats still drive the sim.
        full = by_name.get(name) or (_tac_player(d, name) if name else None)
        xi.append({"id": s.get("id"), "family": s.get("family"), "line": s.get("line"),
                   "x": s.get("x", 50), "y": s.get("y", 50),
                   "role": s.get("role") or tactics.DEFAULT_ROLE.get(s.get("family"), ""),
                   "player": full})
    return xi, squad


# ---- Champions League campaign: the REAL European field --------------------------- #
# The campaign simulator drops the user's side into the actual Champions League league
# phase rather than an invented one: FotMob's league-phase table gives all 36 clubs, the
# points they really took, and the team id that unlocks each of their real squads — so the
# opponents, and the players who score against them, are the genuine article.
_UCL_FIELD: dict = {}                                     # cache: {"rows": (expiry, rows)}
_UCL_LEAGUE_ID = 42                                       # FotMob's Champions League id
_UCL_SEASON = "2025%2F2026"                               # most recent completed edition


# Clubs held out of the simulated field by choice. Everything downstream keys off rank, so
# the remaining sides are renumbered to close the gap — the qualification bands (top 8
# straight through, 9th-24th to the playoff) then still mean what they say.
_UCL_EXCLUDE = {"tottenham hotspur", "tottenham"}


def _ucl_field():
    """The real Champions League league-phase table: the clubs with rank, points, goal
    difference, crest and FotMob team id. Cached 6h (it is a completed season)."""
    import time as _t
    hit = _UCL_FIELD.get("rows")
    if hit and hit[0] > _t.time():
        return hit[1]
    rows = []
    try:
        auth = getattr(_fotmob_mod(), "_auth", None)
        raw = auth.get(f"/api/data/leagues?id={_UCL_LEAGUE_ID}&season={_UCL_SEASON}") if auth else {}
        tbl = (((raw or {}).get("table") or [{}])[0].get("data") or {}).get("table") or {}
        for r in (tbl.get("all") or []):
            if not r.get("id") or (r.get("name") or "").strip().lower() in _UCL_EXCLUDE:
                continue
            rows.append({
                "name": r.get("name"), "id": r["id"], "rank": len(rows) + 1,
                "pts": r.get("pts") or 0, "gd": r.get("goalConDiff") or 0,
                "scores": r.get("scoresStr"),
                "logo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{r['id']}.png",
            })
    except Exception:                                     # noqa: BLE001
        rows = []
    if rows:
        _UCL_FIELD["rows"] = (_t.time() + 6 * 3600, rows)
    return rows


# ---- World Cup field -------------------------------------------------------------- #
# The 2026 tournament as it was actually drawn: 48 nations, twelve groups of four. Names in
# wc_standings do not always match the ones our player data is keyed on — six of the 48
# differ (Czechia/Czech Republic, Turkiye/Turkey, Ivory Coast/Côte d'Ivoire, and so on) —
# and without this map those sides silently drop out of the tournament.
_WC_ALIAS = {"czechia": "Czech Republic", "turkiye": "Turkey",
             "ivory coast": "Côte d'Ivoire", "curacao": "Curaçao",
             "cape verde": "Cape Verde Islands", "dr congo": "Congo DR",
             "south korea": "Korea Republic"}
_WC_HOSTS = ("United States", "Mexico", "Canada")          # 2026 is played across all three
# Six nations carry no country code anywhere in the warehouse — the same spellings that need
# the alias map above. Without these they draw no flag, which in a 48-team tournament is
# noticeable. ISO 3166-1 alpha-2.
_WC_CC = {"bosnia and herzegovina": "BA", "haiti": "HT", "turkiye": "TR",
          "curacao": "CW", "cape verde": "CV", "dr congo": "CD"}
_WC_FIELD: dict = {}


def _wc_field(d, season="2026"):
    """The 48-team field with each side's group and FIFA ranking. Cached — it is a fixed
    tournament, not a live table."""
    if _WC_FIELD.get("rows"):
        return _WC_FIELD["rows"]
    try:
        # A handful of sides have no country code on their standings row, so fall back to
        # the one their own matches carry — without it those nations draw no flag at all.
        rows = d.con.execute(
            """SELECT s.team, coalesce(s.cc, mh.home_cc, ma.away_cc),
                      s.group_name, s.position, f.ranking
               FROM wc_standings s
               LEFT JOIN fifa_rankings f ON f.team_name = s.team
               LEFT JOIN (SELECT DISTINCT season, home_name, home_cc FROM wc_matches
                          WHERE home_cc IS NOT NULL) mh
                 ON mh.season = s.season AND mh.home_name = s.team
               LEFT JOIN (SELECT DISTINCT season, away_name, away_cc FROM wc_matches
                          WHERE away_cc IS NOT NULL) ma
                 ON ma.season = s.season AND ma.away_name = s.team
               WHERE s.season = ? AND s.group_name LIKE 'Group%'
               ORDER BY s.group_name, s.position""", [season]).fetchall()
    except Exception:                                      # noqa: BLE001
        # Transient on startup: the World Cup refresher holds a write on wc_standings while
        # it runs, so a request landing in that window reads nothing. Not cached on failure,
        # so the next one succeeds — which is why this stays quiet rather than erroring.
        return []
    out = []
    for team, cc, grp, pos, rank in rows:
        out.append({"name": team, "squad_name": _WC_ALIAS.get((team or "").lower(), team),
                    "cc": cc or _WC_CC.get((team or "").lower()),
                    "group": grp, "pos": pos,
                    "rank": int(rank) if rank else 100})
    if out:
        _WC_FIELD["rows"] = out
    return out


def _national_squad(d, team):
    """A national squad on the scale the ENGINE speaks.

    tactics_squad() builds nations from wc_player_stats, whose `atlas_rating` measures how a
    player performed across seven tournament matches. That is the right number for the World
    Cup directory and the wrong one here: the engine's units, roles and card attributes are
    all calibrated on EA FC overalls, which measure ABILITY. Feeding it tournament form put
    Argentina's keeper at 36 and gave Haiti a better defence than Brazil, so a simulated
    Brazil went out bottom of its group to Scotland and Haiti.

    So each player is re-priced: his real EA FC card where he has one — most of a World Cup
    is top-5 club players — and his Atlas rating mapped onto the EA FC scale by rank where he
    does not, which is the same conversion the Lab already uses for historical seasons.
    """
    squad = d.tactics_squad(team) or []
    return _reprice_national(d, squad)


def _reprice_national(d, squad):
    """Put a national squad on the EA FC scale in place. See _national_squad for why."""
    if not squad:
        return squad
    from webapp import fifa
    season = (d.con.execute("SELECT max(season) FROM wc_player_stats").fetchone() or [None])[0]
    for p in squad:
        # Full names here, so the mononym tier is wanted: World Cup data lists "Mikel
        # Oyarzabal" and his card is filed as "Oyarzabal", which without it fell through to
        # a tournament rating of 64 against a real 82.
        card = fifa.match(p.get("player") or "", mononyms=True)
        if card and card.get("o"):
            p["rating"] = card["o"]
            p["fifa"] = {k: card[k] for k in ("o", "pac", "sho", "pas", "dri",
                                              "def", "phy", "hea")}
            if card.get("pos"):
                p["position"] = card["pos"]
        else:
            # No card: put the tournament rating on the EA FC scale by rank, so a nation of
            # uncapped players is weak rather than absurd.
            try:
                p["rating"] = d.atlas_to_fifa(p.get("rating") or 50, season,
                                              tactics.family_for_position(p.get("position")))
            except Exception:                              # noqa: BLE001
                p["rating"] = int(_clamp(p.get("rating") or 50, 45, 80))
    return squad


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _wc_opponent(d, row):
    """Materialise one nation — its World Cup squad, an auto XI and unit strengths. Built
    lazily and cached, so a tournament costs only the sides actually met."""
    squad = _national_squad(d, row.get("squad_name") or row["name"])
    if not squad:
        return None
    xi = tactics.build_xi(squad, "4-3-3")
    if not any(s.get("player") for s in xi):
        return None
    # Nations have no crest URL — the site draws them as a flag from the ISO country code,
    # so that is what has to travel with the side. Passing `logo` alone left every team in
    # the tournament with no image at all.
    return {"name": row["name"], "logo": row.get("logo"), "cc": row.get("cc"), "xi": xi,
            "units": tactics.team_units(xi, tactics.DEFAULT_TACTICS),
            "tactics": tactics.DEFAULT_TACTICS, "form": 0.0}


_UCL_LAST: dict = {}


def _ucl_last_matches():
    """team id -> the event id of that club's most recent finished CHAMPIONS LEAGUE match.

    Built from the competition's own fixture list rather than each club's recent form, which
    is the whole point: a club's genuinely last teamsheet is usually a league or cup game,
    and Bayern's was a 4-2-3-1 of reserves against Wehen Wiesbaden. What a side puts out in
    this competition is a different question from what it puts out on a Tuesday in the cup.
    """
    import time as _t
    hit = _UCL_LAST.get("rows")
    if hit and hit[0] > _t.time():
        return hit[1]
    out: dict = {}
    try:
        auth = getattr(_fotmob_mod(), "_auth", None)
        raw = auth.get(f"/api/data/leagues?id={_UCL_LEAGUE_ID}&season={_UCL_SEASON}") if auth else {}
        for m in ((raw or {}).get("fixtures") or {}).get("allMatches") or []:
            if not (m.get("status") or {}).get("finished"):
                continue
            ts = (m.get("status") or {}).get("utcTime") or ""
            for side in ("home", "away"):
                tid = str((m.get(side) or {}).get("id") or "")
                if not tid:
                    continue
                if tid not in out or ts > out[tid][1]:
                    out[tid] = (m.get("id"), ts, side)
    except Exception:                                      # noqa: BLE001
        out = {}
    if out:
        _UCL_LAST["rows"] = (_t.time() + 6 * 3600, out)
    return out


def _last_used_xi(squad, tid):
    """The eleven this club last started IN THE CHAMPIONS LEAGUE, mapped onto our squad.

    None sends the caller back to the auto XI — when the club played no match in the
    competition, when FotMob never published the teamsheet, or when fewer than eleven of the
    names resolve against our squad (a side with holes in it is worse than a built one).

    No rotation guard: a side that rested players in a dead-rubber league-phase game still
    chose that eleven for this competition, and second-guessing it would put back exactly the
    made-up judgement that using the real teamsheet removes.
    """
    if not tid:
        return None
    rec = _ucl_last_matches().get(str(tid))
    if not rec:
        return None
    eid, _ts, side = rec
    try:
        lu = live_feed.lineups(int(eid))
    except Exception:                                      # noqa: BLE001
        return None
    if not lu.get("available"):
        return None
    sd = lu.get(side) or {}
    names = [p.get("name") for p in (sd.get("starting_xi") or []) if p.get("name")]
    if len(names) < 11:
        return None
    return tactics.build_named_xi(squad, sd.get("formation") or "4-3-3", names[:11])


def _ucl_opponent(d, row):
    """Materialise one club from the field — its real current squad, an auto XI in a stock
    shape, unit strengths and recent form. Only the clubs actually drawn are ever built
    (and `_tac_squad` caches them), so a campaign costs a handful of roster fetches."""
    squad = _tac_squad(d, row["name"], tid=row.get("id"))
    if not squad:
        return None
    xi = _last_used_xi(squad, row.get("id")) or tactics.build_xi(squad, "4-3-3")
    if not any(s.get("player") for s in xi):
        return None
    return {"name": row["name"], "logo": row.get("logo"), "xi": xi,
            "units": tactics.team_units(xi, tactics.DEFAULT_TACTICS),
            "tactics": tactics.DEFAULT_TACTICS,
            "form": (_team_form(d, row["name"]) or {}).get("form") or 0.0}


# ---- how hard is this club's league, really? ------------------------------------- #
# Two clubs on identical squads don't have identical seasons: most of a Ligue 1 fixture list
# is easier than most of a Premier League one, and the bar for winning it is lower. UEFA's
# 5-year country coefficient is the standard measure of that gap; normalised to England =
# 1.00 it gives roughly: Italy .89, Spain .82, Germany .80, France .71. The projection uses
# it for the points a side takes, and the real table (below) for where those points finish.
_LEAGUE_DIFFICULTY = {"ENG-Premier League": 1.00, "ITA-Serie A": 0.89, "ESP-La Liga": 0.82,
                      "GER-Bundesliga": 0.80, "FRA-Ligue 1": 0.71}
_LEAGUE_CTX: dict = {}                                    # cache: team.lower() -> (expiry, ctx)


def _league_ctx(d, team):
    """The club's real league context: its difficulty, and every RIVAL's current pace carried
    to a full season (their own row excluded — you don't play yourself). The projection ranks
    the side against those numbers, which is what the season modal's table already does, so
    the card and the table finally agree. None for national sides. Cached 10 min."""
    import time as _t
    key = (team or "").lower()
    hit = _LEAGUE_CTX.get(key)
    if hit and hit[0] > _t.time():
        return hit[1]
    ctx = None
    try:
        from analytics.queries import FOCUS_SEASON
        tid = d.find_team_id(team)
        row = d.con.execute("SELECT team_name, league_key FROM teams WHERE team_id = ?",
                            [tid]).fetchone() if tid else None
        if row:
            canon, lk = row
            df = d.league_standings(lk, FOCUS_SEASON)
            if not df.empty:
                games = int(max(int(r.mp or 0) for r in df.itertuples()) or 38)
                games = 34 if games <= 34 else 38          # 18-team leagues play 34
                rivals = sorted(((r.pts or 0) / max(1, r.mp or 1) * games
                                 for r in df.itertuples() if r.team != canon), reverse=True)
                ctx = {"key": lk, "league": tactics.LEAGUE_INFO.get(team, (lk, 0, 0))[0],
                       "games": games, "n": len(df),
                       "difficulty": _LEAGUE_DIFFICULTY.get(lk, 1.0),
                       "rivals": [round(x, 1) for x in rivals]}
    except Exception:                                     # noqa: BLE001
        ctx = None
    _LEAGUE_CTX[key] = (_t.time() + 600, ctx)
    return ctx


# ---- what real sides actually play like ------------------------------------------- #
# The style comparison used to run against hand-typed vectors for famous teams. This builds
# the real thing instead: every club-season's average xG for, xG against and PPDA, turned
# into percentiles of all club-seasons, so "your setup plays like X" is a measurement rather
# than somebody's impression of Guardiola. Cached for the process — it is a static query.
_STYLE_REF: dict = {}


def _style_catalogue(d):
    """{'teams': [(label, [pct_xg, pct_xga_inv, pct_press]), ...], 'xg': [...], ...}"""
    if _STYLE_REF.get("teams"):
        return _STYLE_REF
    try:
        rows = d.con.execute("""
            SELECT m.season, t.team_name, avg(m.xg_for), avg(m.xg_against), avg(m.ppda),
                   count(*) n
            FROM team_match_stats m JOIN teams t ON t.team_id = m.team_id
            WHERE m.xg_for IS NOT NULL AND m.ppda IS NOT NULL
            GROUP BY 1, 2 HAVING count(*) >= 25""").fetchall()
    except Exception:                                     # noqa: BLE001
        return {}
    if not rows:
        return {}
    xg = sorted(r[2] for r in rows)
    xga = sorted(r[3] for r in rows)
    ppda = sorted(r[4] for r in rows)

    def pct(v, arr, invert=False):
        lo = sum(1 for x in arr if x < v)
        p = 100.0 * lo / len(arr)
        return 100.0 - p if invert else p
    teams = []
    for season, name, a, b, c, _n in rows:
        label = f"{season[:2]}/{season[2:]} {name}"
        teams.append((label, [pct(a, xg), pct(b, xga, True), pct(c, ppda, True)]))
    _STYLE_REF.update({"teams": teams, "xg": xg, "xga": xga, "ppda": ppda})
    return _STYLE_REF


_TAC_ADVISOR: dict = {}                                   # cache: prompt-hash -> text


def _tactics_advisor(b):
    """AI analyst writeup grounded in the engine's computed numbers (Gemini free tier)."""
    import hashlib
    from webapp import gemini
    team = b.get("team") or "the team"
    m, u = b.get("metrics") or {}, b.get("units") or {}
    tac = b.get("tactics") or {}
    weak = [w.get("title") for w in (b.get("weaknesses") or [])]
    chem = b.get("chemistry") or {}
    chem_links = [f"{l.get('kind')}: {l.get('title')}" for l in (chem.get("links") or [])]
    opp = b.get("opponent_name")
    key = hashlib.md5(jdumps([team, m, u, tac, weak, chem.get("score"), opp]).encode()).hexdigest()
    if key in _TAC_ADVISOR:
        return {"available": True, "text": _TAC_ADVISOR[key], "cached": True}
    if not gemini.available():
        return {"available": False}
    prompt = (
        f"You are an elite football tactical analyst. Given this model output for {team}"
        + (f" (vs {opp})" if opp else "") + ", write a SHORT, sharp scouting read.\n\n"
        f"Unit strengths (0-99): {u}\n"
        f"Projected metrics: {m}\n"
        f"Tactical settings (0-100, 50=neutral): {tac}\n"
        f"Flagged weaknesses: {weak or 'none'}\n"
        f"Playstyle chemistry: {chem.get('score', '?')}/99 ({chem.get('label', 'n/a')}); "
        f"role interactions: {chem_links or 'none'}\n\n"
        "Write 3 tight paragraphs, no headers, ~120 words total: (1) the team's tactical "
        "identity in this setup; (2) the single biggest risk and why; (3) ONE concrete change "
        "with its likely effect. Reference the actual numbers. Confident, specific, no fluff.")
    txt = gemini.generate(prompt, temperature=0.5)
    if not txt:
        return {"available": False}
    txt = txt.strip()
    _TAC_ADVISOR[key] = txt
    return {"available": True, "text": txt}
from webapp import admin  # noqa: E402
from webapp import seo  # noqa: E402

FRONTEND = Path(__file__).resolve().parent / "frontend"
PORT = 8000

# The in-process live refresher writes to the warehouse on a loop. DuckDB shares a
# single instance per file per process and forbids mixing read-only + read-write
# connections, so when the refresher is on we must open request connections
# read-write too -- otherwise the first read-only request blocks every later write
# AND a cached read-only instance never sees the refresher's updates (stale live
# feed). With the refresher off, stay read-only so other processes can use the
# warehouse concurrently.
LIVE_REFRESH = os.environ.get("ATLASTRA_NO_LIVE_REFRESH") != "1"
# Live data can instead be PUSHED in from a machine that can reach SofaScore (this
# host's datacenter IP is bot-blocked). When an ingest token is set, the server
# must be read-write to accept those writes even with the local refresher off.
INGEST_TOKEN = os.environ.get("ATLASTRA_INGEST_TOKEN") or None
# FotMob mode runs a server-side refresher that writes live_matches, so open the DB
# read-write for it too.
DB_READ_ONLY = not (LIVE_REFRESH or INGEST_TOKEN or FOTMOB)
seo.configure(DB_READ_ONLY)                    # align the sitemap/meta DB reads with the server's mode

# Fully-enriched fixture previews are expensive (name-matching ~60 squad players to
# our ratings) and near-static for an upcoming match, so cache the finished result
# per event id. First viewer pays the cost; everyone else (and every recheck) is
# instant for the window.
_PREVIEW_CACHE: dict[int, tuple[float, dict]] = {}
_PREVIEW_TTL = 300
# Background preview warmer (cache-mode/deployed only): keep _PREVIEW_CACHE hot for the
# soonest N upcoming fixtures so the Preview tab is instant -- the key-player enrichment
# (name-matching both squads) is the slow part, precomputed here off the click path.
PREVIEW_WARM_N = int(os.environ.get("ATLASTRA_PREVIEW_WARM_N", "10"))
PREVIEW_WARM_EVERY = int(os.environ.get("ATLASTRA_PREVIEW_WARM_EVERY", "120"))
# How often to warm every national team's SofaScore paths into the persisted cache so
# /nat.html loads even while the relay (scraper machine) is offline.
NAT_WARM_EVERY = int(os.environ.get("ATLASTRA_NAT_WARM_EVERY", "300"))

# Optional "Sign in with Google". Set ATLASTRA_GOOGLE_CLIENT_ID to a Google OAuth
# Web client id to enable it; left unset, the Google button simply never appears
# and username/password sign-in is unaffected.
GOOGLE_CLIENT_ID = os.environ.get("ATLASTRA_GOOGLE_CLIENT_ID") or None


def _verify_google(credential):
    """Validate a Google Identity Services ID token via Google's tokeninfo endpoint
    (which checks the signature + expiry for us, so no crypto lib is needed) and
    confirm it was minted for OUR client. -> {sub,email,name} or None."""
    if not credential or not GOOGLE_CLIENT_ID:
        return None
    try:
        url = "https://oauth2.googleapis.com/tokeninfo?id_token=" + quote(credential)
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read())
    except Exception:  # noqa: BLE001 -- bad/expired token or network blip
        return None
    if d.get("aud") != GOOGLE_CLIENT_ID:
        return None
    if d.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return None
    return {"sub": d.get("sub"), "email": d.get("email"), "name": d.get("name")}


def _finite(o):
    """Recursively replace NaN/Inf floats with None so responses are valid JSON.
    Python's json.dumps emits a bare ``NaN`` token by default, which browsers'
    JSON.parse rejects -- one stray NaN stat would 'break' a whole endpoint."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def jdumps(obj):
    """JSON-encode an API payload, guaranteeing valid JSON (no NaN/Inf tokens)."""
    return json.dumps(_finite(obj), default=str)
CT = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
      ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png",
      ".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


# Live match-detail endpoints proxy SofaScore (server-side TLS bypass) and never
# touch the warehouse, so they bypass the SoccerDB context manager below.
def match_api(path: str, q: dict) -> dict:
    eid = int(q.get("id", [0])[0])
    if path == "/api/match":
        live_feed.prewarm(eid)             # batch-queue all tabs' paths on first open
        h = live_feed.header(eid)
        if h.get("available") and (h.get("home_national") or h.get("away_national")):
            with SoccerDB(read_only=DB_READ_ONLY) as db:   # FIFA rank for national-team sides
                if h.get("home_national"):
                    h["home_rank"] = db.fifa_rank(h.get("home"))
                if h.get("away_national"):
                    h["away_rank"] = db.fifa_rank(h.get("away"))
        return h
    if path == "/api/match/stats":
        return live_feed.statistics(eid)
    if path == "/api/match/lineups":
        d = live_feed.lineups(eid)
        starters, players = [], []
        for side in ("home", "away"):
            s = d.get(side) or {}
            starters += (s.get("starting_xi") or [])
            players += (s.get("starting_xi") or []) + (s.get("substitutes") or [])
        # background-warm each starter's club + heatmap so the player modal is instant
        live_feed.prewarm_players(eid, [p.get("id") for p in starters])
        if players:
            with SoccerDB(read_only=DB_READ_ONLY) as db:
                # Prefer our snapshotted wc_matches (robust on the WAF-blocked cloud
                # host); fall back to the live /event header's uniqueTournament id.
                wc_edition = db.wc_edition_for_event(eid)
                if not wc_edition:
                    hdr = live_feed.header(eid)
                    if hdr.get("ut_id") == 16 and hdr.get("start_ts"):    # FIFA World Cup
                        wc_edition = str(datetime.utcfromtimestamp(hdr["start_ts"]).year)
                if wc_edition:
                    # WC match -> show each player's World Cup rating (0-100) rather
                    # than their top-5-league rating, keyed by SofaScore player id.
                    ids = [p.get("id") for p in players]
                    wcr = db.wc_ratings_by_ids(wc_edition, ids)
                    wct = db.wc_tournament_stats_by_ids(wc_edition, ids)  # G/A/apps for the modal
                    for p in players:
                        r = wcr.get(p.get("id"))
                        if r is not None:
                            p["atlas_rating"], p["atlas_est"], p["atlas_wc"] = r, False, True
                        t = wct.get(p.get("id"))
                        if t is not None:
                            p["tourn"] = {"label": f"World Cup {wc_edition}", **t}
                else:
                    # otherwise our real combined League/UCL rating (name-matched);
                    # players not in our DB simply get no rating badge.
                    rmap = db.ratings_by_name([p.get("name") for p in players])
                    for p in players:
                        r = rmap.get(p.get("name"))
                        if r is not None:
                            p["atlas_rating"], p["atlas_est"] = r, False
        return d
    if path == "/api/match/shotmap":
        return live_feed.shotmap(eid)
    if path == "/api/match/timeline":
        return live_feed.timeline(eid)
    if path == "/api/match/key-moments":
        return live_feed.key_moments(eid)
    if path == "/api/match/player-stats":
        d = live_feed.player_stats(eid)
        names = [p.get("name") for p in d.get("players", [])]
        if names:
            with SoccerDB(read_only=DB_READ_ONLY) as db:
                have = db.have_profiles(names)
            for p in d["players"]:
                p["has_profile"] = p.get("name") in have
        return d
    if path == "/api/match/heatmap":
        return live_feed.player_heatmap(eid, int(q.get("player_id", [0])[0]))
    if path == "/api/match/winprob":          # in-play 1X2 from score + clock
        return live_feed.win_probability(eid)
    if path == "/api/match/prediction":
        d = live_feed.prediction(eid)
        if d.get("available"):
            d["score"] = live_feed.score_prediction(eid, d.get("consensus"))
        return d
    raise KeyError(path)


def _fixture_preview(eid: int, d) -> dict:
    """Build the enriched fixture preview (SofaScore preview + top key players by our
    ratings), served from _PREVIEW_CACHE. Shared by the /api/fixture_preview route and
    the background warmer so their logic can't drift. `d` is an open SoccerDB."""
    hit = _PREVIEW_CACHE.get(eid)
    if hit and time.time() - hit[0] < _PREVIEW_TTL:
        return hit[1]
    # queue every preview path (header + both teams' form/squad + h2h + odds) in ONE
    # relay cycle using the team ids we already store, so a cold preview fills in one
    # pass instead of two (header first, then the rest).
    hid, aid = d.match_team_ids(eid)
    live_feed.prewarm_preview(eid, hid, aid)
    pv = live_feed.fixture_preview(eid)
    if pv.get("available") and not pv.get("pending"):
        # Enrich squad -> top-rated key players ONCE the relay has the full squad
        # (name-matching ~60 names is the slow part). Skip while pending so the client's
        # poll cycles stay fast, then cache the finished result.
        for side in ("home", "away"):
            pv[side]["key"] = d.web_squad_key_players(pv[side].pop("squad", []))
        _PREVIEW_CACHE[eid] = (time.time(), pv)
    elif pv.get("available"):
        for side in ("home", "away"):      # pending: don't run enrichment yet
            pv[side].pop("squad", None)
            pv[side]["key"] = []
    return pv


def api(path: str, q: dict) -> dict | list:
    # match-detail routes are exactly /api/match or /api/match/... — must NOT
    # swallow sibling routes like /api/match_search or /api/match_preview.
    if path == "/api/match" or path.startswith("/api/match/"):
        r = match_api(path, q)
        # if data isn't here yet because the remote scraper hasn't filled the cache,
        # flag it pending so the client waits instead of showing "unavailable".
        if isinstance(r, dict) and r.get("available") is False:
            try:
                r["pending"] = live_feed.queue_has(f"/event/{int(q.get('id', [0])[0])}")
            except (TypeError, ValueError):
                pass
        return r
    if path == "/api/national_team":          # SofaScore live proxy (no DB)
        tid = int(q.get("id", [0])[0])
        live_feed.prewarm_team(tid)           # batch-queue squad/results/fixtures with the header
        r = live_feed.national_team(tid)
        if isinstance(r, dict) and r.get("available") is False:
            r["pending"] = live_feed.queue_has(f"/team/{tid}")
        return r
    if path == "/api/player_club":
        pid = int(q.get("id", [0])[0])
        r = live_feed.player_club(pid)
        if isinstance(r, dict) and r.get("available") is False:
            r["pending"] = live_feed.queue_has(f"/player/{pid}")
        return r
    if path == "/api/coach":                  # coach/manager career + trophies (FotMob)
        return getattr(live_feed, "coach", lambda _: {"available": False})(int(q.get("id", [0])[0]))
    if path == "/api/highlights":             # top clips of the day/week (FotMob, aggregated)
        period = q.get("period", ["day"])[0]
        period = period if period in ("day", "week") else "day"
        return getattr(live_feed, "highlights",
                       lambda **k: {"available": False, "clips": []})(period=period)
    if path == "/api/top_goals":              # best goals of the day/week w/ YouTube clips
        period = q.get("period", ["day"])[0]
        period = period if period in ("day", "week") else "day"
        return getattr(live_feed, "top_goals",
                       lambda **k: {"available": False, "clips": []})(period=period)
    if path == "/api/trending":               # most-viewed football clips on YouTube
        period = q.get("period", ["week"])[0]
        period = period if period in ("day", "week") else "week"
        return getattr(live_feed, "trending",
                       lambda **k: {"available": False, "clips": []})(period=period)
    if path == "/api/shorts":                 # viral football Shorts (skills/edits, TikTok-style)
        return getattr(live_feed, "shorts",
                       lambda: {"available": False, "clips": []})()
    if path == "/api/top_stars":              # top-25 reputation stars + best skills video
        return getattr(live_feed, "top_stars",
                       lambda: {"available": False, "clips": []})()
    if path == "/api/player_video":           # best skills/highlights video for a player (YouTube)
        return getattr(live_feed, "player_video",
                       lambda _n: {"available": False})(q.get("name", [""])[0])
    if path == "/api/player_form":            # recent per-match form (FotMob), keyed by fotmob player id
        return getattr(live_feed, "player_form",
                       lambda _p: {"available": False})(q.get("pid", [""])[0])
    if path == "/api/player_bio":             # foot + height (FotMob), keyed by fotmob player id
        return getattr(live_feed, "player_bio",
                       lambda _p: {"available": False})(q.get("pid", [""])[0])
    if path == "/api/signature_skills":       # Gemini reads the player's reel -> ranked skills
        name = q.get("name", [""])[0]
        pv = getattr(live_feed, "player_video", lambda _n: {})(name) or {}
        url = pv.get("url") if pv.get("available") else None
        return signature_skills.generate(name, url, refresh=q.get("refresh", ["0"])[0] == "1")
    if path == "/api/highlight_players":       # names of players that have highlight reels
        return {"players": signature_skills.cached_players()}
    if path == "/api/blog":                    # blog index, or a single post with ?slug=
        slug = q.get("slug", [""])[0]
        return blog.get_post(slug) if slug else blog.list_posts()
    if path == "/api/weekly_recap":           # AI week-in-review (top performers, goals, results)
        gather = getattr(live_feed, "week_summary_data", None)
        if gather is None:
            return {"available": False, "error": "Recap unavailable."}
        return weekly_recap.generate(gather(), refresh=q.get("refresh", ["0"])[0] == "1")
    if path == "/api/scout_report":           # gather data (DB), then generate via Claude
        with SoccerDB(read_only=DB_READ_ONLY) as d:
            data = d.web_player(q.get("name", ["Pedri"])[0], q.get("career_stat", ["xa"])[0],
                                q.get("season", [None])[0])
        return scout_ai.scout_report(data, refresh=q.get("refresh", ["0"])[0] == "1")
    with SoccerDB(read_only=DB_READ_ONLY) as d:
        if path == "/api/tactics/squad":       # Tactics Lab: squad + auto XI for a formation
            team = q.get("team", [""])[0]
            formation = q.get("formation", ["4-3-3"])[0]
            if formation not in tactics.FORMATIONS:
                formation = "4-3-3"
            # "Build your own XI": an empty shape to fill from the whole player universe,
            # past seasons included. Nothing is pre-picked, so every slot is yours.
            blank = team in ("__custom__", "Custom XI")
            squad = [] if blank else _tac_squad(d, team)
            xi = tactics.build_xi(squad, formation) if squad else (
                [{"id": s["id"], "family": s["family"], "line": s["line"], "x": s["x"],
                  "y": s["y"], "role": tactics.DEFAULT_ROLE.get(s["family"], ""),
                  "player": None} for s in tactics.FORMATIONS[formation]] if blank else [])
            return {"available": bool(squad) or blank, "custom": blank,
                    "team": "Custom XI" if blank else team, "formation": formation,
                    "squad": squad, "xi": _xi_wire(xi),
                    "formations": list(tactics.FORMATIONS.keys()),
                    "roles": tactics.ROLES, "role_defaults": tactics.DEFAULT_ROLE,
                    "tactic_keys": tactics.TACTIC_KEYS, "tactic_defaults": tactics.DEFAULT_TACTICS}
        if path == "/api/tactics/find":        # search any player to add to a squad (what-if)
            return {"results": d.tactics_search(q.get("q", [""])[0])}
        if path == "/api/tactics/seasons":     # which years of a player the Lab can field
            nm = _split_season(q.get("name", [""])[0])[0]
            return {"player": nm, "seasons": d.tactics_player_seasons(nm)}
        if path == "/api/tactics/player":      # resolve one added player into a wire card
            p = _tac_player(d, q.get("name", [""])[0], q.get("season", [""])[0] or None)
            if not p:
                return {"available": False}
            return {"available": True, "player": {
                "player": p["player"], "rating": p["rating"], "position": p.get("position"),
                "photo": p.get("photo"), "breakout": p.get("breakout"),
                "season": p.get("season"), "season_label": p.get("season_label"),
                "team": p.get("team"), "family": p.get("family"),
                "best_role": p.get("best_role"), "best_roles": p.get("best_roles")}}
        if path == "/api/overview":
            return d.web_overview()
        if path == "/api/rankings":
            return d.web_rankings(int(q.get("limit", ["10"])[0]))
        if path == "/api/trending":
            return d.web_trending(int(q.get("limit", ["5"])[0]))
        if path == "/api/position_rankings":
            return d.web_position_rankings(int(q.get("limit", ["20"])[0]),
                                           scope=q.get("scope", ["league"])[0])
        if path == "/api/alltime_seasons":
            return d.web_alltime_seasons(q.get("scope", ["combined"])[0],
                                         int(q.get("limit", ["20"])[0]))
        if path == "/api/national_teams":
            return d.web_national_teams()
        if path == "/api/ucl_seasons":
            return d.web_ucl_seasons()
        if path == "/api/ucl":
            return d.web_ucl_competition(_season(q))
        if path == "/api/ucl_leaders":
            return d.web_ucl_leaders(_season(q))
        if path == "/api/wc_seasons":
            return d.web_wc_seasons()
        if path == "/api/worldcup":
            return d.web_worldcup(q.get("season", ["2026"])[0])
        if path == "/api/wc_leaders":
            return d.web_wc_leaders(q.get("season", ["2026"])[0])
        if path == "/api/players":
            return d.web_players(q.get("group", ["all"])[0],
                                 (q.get("search", [""])[0] or None),
                                 int(q.get("limit", ["30"])[0]),
                                 scope=q.get("scope", ["league"])[0])
        if path == "/api/discover":
            return d.web_discover()
        if path == "/api/spotlight":
            return d.web_spotlight()
        if path == "/api/live":
            res = d.web_live(int(q.get("recent", ["40"])[0]),
                             int(q.get("upcoming", ["40"])[0]))
            for m in res.get("live", []):     # live detail is already warmed
                m["venue"] = live_feed.venue(m["event_id"])
            # upcoming matches: warm the soonest few so their venue fills via the relay
            for i, m in enumerate(res.get("upcoming", [])):
                m["venue"] = live_feed.venue(m["event_id"], warm=(i < 15))
            # finished matches keep their venue too -- the event detail is static, so
            # warm it once and it stays cached (was previously dropped after full-time).
            for m in res.get("recent", []):
                m["venue"] = live_feed.venue(m["event_id"], warm=True)
            return res
        if path == "/api/standings":
            return d.web_standings(q.get("league", ["ENG-Premier League"])[0])
        if path == "/api/player":
            return d.web_player(q.get("name", ["Pedri"])[0],
                                q.get("career_stat", ["xa"])[0],
                                q.get("season", [None])[0])
        if path == "/api/compare":
            names = q.get("name", [])
            stats = q.get("stat") or None
            seasons = q.get("season") or None   # index-tagged "<i>:<code>" per player
            scope = q.get("scope", ["combined"])[0]   # league/ucl/combined/worldcup
            return d.web_compare(names, stats, seasons, scope)
        if path == "/api/leagues":
            return d.web_leagues()
        if path == "/api/seasons":
            return d.web_seasons()
        if path == "/api/league_table":
            return d.web_league_table(q.get("league", ["ENG-Premier League"])[0],
                                      _season(q))
        if path == "/api/league_leaders":
            return d.web_league_leaders(q.get("league", ["ENG-Premier League"])[0],
                                        _season(q))
        if path == "/api/league_fixtures":
            return d.web_league_fixtures(q.get("league", ["ENG-Premier League"])[0],
                                         _season(q))
        if path == "/api/team":
            return d.web_team(q.get("name", ["Arsenal"])[0])
        if path == "/api/search":
            return d.web_search(q.get("q", [""])[0])
        if path == "/api/match_search":
            return d.web_match_search(q.get("a", [""])[0], q.get("b", [""])[0])
        if path == "/api/legends":
            return d.web_legends()
        if path == "/api/find_next":
            return d.web_find_next(q.get("legend", ["xavi"])[0])
        if path == "/api/best_xi":
            return d.web_best_xi(float(q.get("budget", ["200"])[0]),
                                 q.get("formation", ["4-3-3"])[0])
        if path == "/api/card":
            return d.web_card(q.get("name", ["Pedri"])[0], q.get("season", [None])[0])
        if path == "/api/preview":
            return d.web_match_preview(q.get("home", ["Arsenal"])[0], q.get("away", ["Chelsea"])[0])
        if path == "/api/fixture_preview":         # SofaScore preview + key-player enrichment
            return _fixture_preview(int(q.get("id", [0])[0]), d)
        if path == "/api/big_game_board":
            return d.web_big_game_board()
        if path == "/api/big_game":
            return d.web_big_game_player(q.get("name", ["Pedri"])[0])
        if path == "/api/finishing":              # Goals vs xG finishing profile
            return d.web_finishing(q.get("name", ["Pedri"])[0])
        if path == "/api/value_model":            # fair-value model verdict for one player
            return d.web_value_model(q.get("name", ["Pedri"])[0])
        if path == "/api/value_board":            # most under/over-valued leaderboard
            return d.web_value_board(limit=int(q.get("limit", ["15"])[0]))
        if path == "/api/dna_map":
            return d.web_dna_map(int(q.get("min_minutes", ["900"])[0]))
        if path == "/api/archetypes":
            return d.web_archetypes()
        if path == "/api/archetype":
            return d.web_archetype(q.get("name", ["Poacher"])[0])
        if path == "/api/team_of_season":
            return d.web_team_of_season()
        if path == "/api/team_of_week":
            return d.web_team_of_week()
        if path == "/api/scout":
            return d.web_scout(
                q.get("pos", ["all"])[0], q.get("metric", ["rating"])[0],
                float(q.get("max_value", ["0"])[0]), int(q.get("min_minutes", ["450"])[0]),
                int(q.get("max_age", ["0"])[0]), int(q.get("min_rating", ["0"])[0]),
                int(q.get("limit", ["40"])[0]))
        if path == "/api/guess":
            return d.web_guess_rounds(int(q.get("count", ["8"])[0]),
                                      int(q.get("min_minutes", ["1100"])[0]),
                                      int(q.get("min_rating", ["66"])[0]))
        if path == "/api/daily_challenge":
            return d.web_daily_challenge(q.get("date", [""])[0] or "1970-01-01")
        if path == "/api/player_quiz":
            return d.web_player_quiz(q.get("date", [None])[0])
        if path == "/api/draft_pool":
            return d.web_draft_pool(q.get("formation", ["4-3-3"])[0])
        if path == "/api/team_options":
            return d.web_team_options()
        if path == "/api/team_style":
            names = q.get("name", [])
            return [d.web_team_style(n) for n in names] if names else []
        raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # dev server: never let the browser serve a stale JS/CSS/HTML asset
        self.send_header("Cache-Control", "no-store, must-revalidate")
        # stamp a first-time visitor with an anonymous id (usage analytics only)
        nv = getattr(self, "_new_vid", None)
        if nv:
            self.send_header("Set-Cookie",
                             f"atla_vid={nv}; Path=/; Max-Age=31536000; SameSite=Lax")
            self._new_vid = None
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _visitor(self):
        """Anonymous per-browser visitor id (for unique-visitor counts). Minted on
        first visit and set via _send's Set-Cookie; no PII, no login required."""
        vid = self._cookie("atla_vid")
        if not vid:
            vid = secrets.token_urlsafe(9)
            self._new_vid = vid
        return vid

    def _json(self, obj, code=200, extra_headers=None):
        self._send(code, jdumps(obj).encode(), "application/json", extra_headers)

    def _cookie(self, name):
        raw = self.headers.get("Cookie", "") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _body_json(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            d = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:  # noqa: BLE001
            return {}
        # handlers call b.get(...); a non-object body (list/str/number) would AttributeError
        return d if isinstance(d, dict) else {}

    # ---- optional accounts (auth + per-user data sync) ----
    def do_POST(self):
        u = urlparse(self.path)
        b = self._body_json()
        if u.path.startswith("/api/") and not u.path.startswith("/api/ingest/"):
            admin.record_hit(u.path, "api", self._visitor())
        if u.path in ("/api/auth/signup", "/api/auth/login"):
            fn = auth.signup if u.path.endswith("signup") else auth.login
            user, tok = fn(b.get("username"), b.get("password"))
            if not user:
                self._json({"error": tok}, 400)
                return
            cookie = (f"atla_session={tok}; Path=/; HttpOnly; SameSite=Lax; "
                      f"Max-Age={auth.SESSION_DAYS * 86400}")
            self._json({"user": user}, extra_headers=[("Set-Cookie", cookie)])
            return
        if u.path == "/api/auth/google":               # Sign in with Google (ID token)
            info = _verify_google(b.get("credential"))
            if not info:
                self._json({"error": "Google sign-in failed. Please try again."}, 401)
                return
            user, tok = auth.google_login(info["sub"], info.get("email"), info.get("name"))
            if not user:
                self._json({"error": tok}, 400)
                return
            cookie = (f"atla_session={tok}; Path=/; HttpOnly; SameSite=Lax; "
                      f"Max-Age={auth.SESSION_DAYS * 86400}")
            self._json({"user": user, "google_name": info.get("name")},
                       extra_headers=[("Set-Cookie", cookie)])
            return
        if u.path == "/api/auth/logout":
            auth.logout(self._cookie("atla_session"))
            self._json({"ok": True}, extra_headers=[("Set-Cookie", "atla_session=; Path=/; Max-Age=0")])
            return
        if u.path == "/api/user/data":
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user:
                self._json({"error": "Not signed in."}, 401)
                return
            auth.set_data(user["id"], json.dumps(b.get("data")))
            self._json({"ok": True})
            return
        if u.path == "/api/score":                     # post a game score to the leaderboard
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user:
                self._json({"error": "Sign in to post scores to the leaderboard."}, 401)
                return
            try:
                score = float(b.get("score"))
            except (TypeError, ValueError):
                self._json({"error": "Bad score."}, 400)
                return
            game = str(b.get("game", ""))[:32]
            period = str(b.get("period", "alltime"))[:32]
            if not game or not (0 <= score <= 1_000_000):
                self._json({"error": "Invalid submission."}, 400)
                return
            self._json(auth.submit_score(game, period, user["id"], user["username"], score))
            return
        if u.path == "/api/ingest/live":               # live feed pushed from a non-blocked scraper
            if not INGEST_TOKEN or self.headers.get("X-Ingest-Token") != INGEST_TOKEN:
                self._json({"error": "unauthorized"}, 401)
                return
            rows = b.get("rows") or []
            if not isinstance(rows, list):
                self._json({"error": "rows must be a list"}, 400)
                return
            from pipeline import load_live as live
            n_live = live.ingest_rows(rows, prune=bool(b.get("prune")))
            self._json({"ok": True, "received": len(rows), "live": n_live})
            return
        if u.path == "/api/ingest/cache":              # match-detail JSON pushed from the scraper
            if not INGEST_TOKEN or self.headers.get("X-Ingest-Token") != INGEST_TOKEN:
                self._json({"error": "unauthorized"}, 401)
                return
            self._json({"ok": True, "stored": live_feed.cache_put(b.get("items") or [])})
            return
        if u.path == "/api/ingest/wc":                 # World Cup matches/standings/leaders pushed in
            if not INGEST_TOKEN or self.headers.get("X-Ingest-Token") != INGEST_TOKEN:
                self._json({"error": "unauthorized"}, 401)
                return
            from pipeline import load_wc
            self._json({"ok": True, **load_wc.write_wc_rows(b.get("data") or {})})
            return
        if u.path == "/api/comments":                  # post a comment to a thread
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user:
                self._json({"error": "Sign in to post a comment."}, 401)
                return
            comment, err = auth.add_comment(b.get("target"), user["id"],
                                            user["username"], b.get("body"))
            if err:
                self._json({"error": err}, 400)
                return
            self._json({"comment": comment})
            return
        if u.path == "/api/comments/delete":
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user:
                self._json({"error": "Not signed in."}, 401)
                return
            ok = auth.delete_comment(_int(b.get("id")), user["id"])
            self._json({"ok": ok} if ok else {"error": "Can't delete that comment."},
                       200 if ok else 403)
            return
        if u.path == "/api/comments/like":
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user:
                self._json({"error": "Sign in to like comments."}, 401)
                return
            res, err = auth.toggle_like(_int(b.get("id")), user["id"])
            self._json(res if res else {"error": err}, 200 if res else 400)
            return
        if u.path == "/api/tactics/sim":               # Tactics Lab simulation
            with SoccerDB(read_only=DB_READ_ONLY) as d:
                team = b.get("team", "")
                xi, _ = _tac_rebuild(d, team, b.get("xi"))
                opp = None
                fh = fa = None
                ob = b.get("opponent") or {}
                if ob.get("team") and ob.get("xi"):
                    oxi, _ = _tac_rebuild(d, ob["team"], ob["xi"])
                    opp = {"name": ob["team"], "tactics": ob.get("tactics"),
                           "units": tactics.team_units(oxi, ob.get("tactics"))}
                    # recent league + UCL form for each side, to nudge the matchup
                    fh_info = _team_form(d, team)
                    fa_info = _team_form(d, ob["team"])
                    fh, fa = fh_info.get("form"), fa_info.get("form")
                res = tactics.simulate(xi, b.get("tactics"), opponent=opp, team=team,
                                       form_home=fh, form_away=fa,
                                       league_ctx=_league_ctx(d, team),
                                       style_ref=_style_catalogue(d))
                if opp:
                    res["form"] = {"home": fh_info, "away": fa_info}
            self._json(res)
            return
        if u.path == "/api/tactics/match":             # Tactics Lab: play one match out
            ob = b.get("opponent") or {}
            if not (ob.get("team") and ob.get("xi")):
                self._json({"available": False, "error": "No opponent set."})
                return
            with SoccerDB(read_only=DB_READ_ONLY) as d:
                team = b.get("team", "")
                xi, _ = _tac_rebuild(d, team, b.get("xi"))
                oxi, _ = _tac_rebuild(d, ob["team"], ob["xi"])
                opp = {"name": ob["team"], "tactics": ob.get("tactics"),
                       "units": tactics.team_units(oxi, ob.get("tactics"))}
                fh = _team_form(d, team).get("form")
                fa = _team_form(d, ob["team"]).get("form")
                res = tactics.simulate_match(xi, b.get("tactics"), oxi, opp, team=team,
                                             opp_name=ob["team"], form_home=fh, form_away=fa,
                                             seed=_int(b.get("seed")) or None)
            self._json(res)
            return
        if u.path == "/api/tactics/ucl":               # Tactics Lab: play a Champions League run
            field = _ucl_field()
            if not field:
                self._json({"available": False, "error": "Champions League field unavailable."})
                return
            with SoccerDB(read_only=DB_READ_ONLY) as d:
                team = b.get("team", "")
                xi, _ = _tac_rebuild(d, team, b.get("xi"))
                if not any(s.get("player") for s in xi):
                    self._json({"available": False, "error": "Load a squad first."})
                    return
                own = _FOTMOB_TEAM_ID.get(team)
                res = tactics.simulate_ucl(
                    xi, b.get("tactics"), team, field, lambda row: _ucl_opponent(d, row),
                    form_home=_team_form(d, team).get("form"),
                    seed=_int(b.get("seed")) or None,
                    team_logo=(f"https://images.fotmob.com/image_resources/logo/teamlogo/{own}.png"
                               if own else None))
            self._json(res)
            return
        if u.path == "/api/tactics/wc":                # Tactics Lab: play a World Cup
            with SoccerDB(read_only=DB_READ_ONLY) as d:
                team = b.get("team", "")
                field = _wc_field(d)
                if not field:
                    self._json({"available": False, "error": "World Cup field unavailable."})
                    return
                xi, _ = _tac_rebuild(d, team, b.get("xi"))
                if not any(s.get("player") for s in xi):
                    self._json({"available": False, "error": "Load a squad first."})
                    return
                own = _FOTMOB_TEAM_ID.get(team)
                res = tactics.simulate_wc(
                    xi, b.get("tactics"), team, field, lambda row: _wc_opponent(d, row),
                    form_home=(_team_form(d, team) or {}).get("form"),
                    seed=_int(b.get("seed")) or None,
                    host=next((h for h in _WC_HOSTS if h.lower() == team.lower()), None),
                    team_logo=(f"https://images.fotmob.com/image_resources/logo/teamlogo/{own}.png"
                               if own else None))
            self._json(res)
            return
        if u.path == "/api/tactics/season":            # Tactics Lab: full-season simulation
            with SoccerDB(read_only=DB_READ_ONLY) as d:
                team = b.get("team", "")
                xi, _ = _tac_rebuild(d, team, b.get("xi"))
                res = tactics.simulate(xi, b.get("tactics"), team=team,
                                       league_ctx=_league_ctx(d, team))
                proj = res.get("projection") or {}
                names = [s["player"]["player"] for s in xi if s.get("player")]
                games = proj.get("games") or (7 if proj.get("kind") == "national" else 38)
                out = {"available": bool(proj), "team": team, "projection": proj,
                       "leaders": d.tactics_stat_leaders(names, games)}
                if proj.get("kind") == "club":
                    out["standings"] = d.tactics_league_table(team, proj.get("points"), games)
            self._json(out)
            return
        if u.path == "/api/tactics/advisor":           # AI analyst writeup (Gemini free tier)
            self._json(_tactics_advisor(b))
            return
        self._json({"error": "Not found"}, 404)

    def do_GET(self):
        u = urlparse(self.path)
        vid = self._visitor()
        if u.path.startswith("/api/") and not u.path.startswith("/api/ingest/"):
            admin.record_hit(u.path, "api", vid)       # usage log (ingest is machine traffic)
        if u.path == "/api/admin/overview":            # admin dashboard data (admins only)
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user or not user.get("is_admin"):
                self._json({"error": "Admins only."}, 403)
                return
            self._json(admin.overview())
            return
        if u.path == "/api/ingest/queue":              # SofaScore paths the pusher should fetch
            if not INGEST_TOKEN or self.headers.get("X-Ingest-Token") != INGEST_TOKEN:
                self._json({"error": "unauthorized"}, 401)
                return
            self._json({"paths": live_feed.queue_pending()})
            return
        if u.path == "/api/auth/me":
            self._json({"user": auth.user_for_token(self._cookie("atla_session"))})
            return
        if u.path == "/api/auth/config":               # public: enables the Google button
            self._json({"google_client_id": GOOGLE_CLIENT_ID})
            return
        if u.path == "/api/user/data":
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user:
                self._json({"error": "Not signed in."}, 401)
                return
            self._json({"data": json.loads(auth.get_data(user["id"]) or "null")})
            return
        if u.path == "/api/leaderboard":               # public game leaderboard
            qq = parse_qs(u.query)
            self._json(auth.leaderboard(qq.get("game", [""])[0],
                                        qq.get("period", ["alltime"])[0],
                                        _int(qq.get("limit", ["25"])[0], 25)))
            return
        if u.path == "/api/comments":                  # public: read a thread's comments
            qq = parse_qs(u.query)
            viewer = auth.user_for_token(self._cookie("atla_session"))
            self._json(auth.list_comments(qq.get("target", [""])[0],
                                          viewer["id"] if viewer else None,
                                          qq.get("sort", ["new"])[0]))
            return
        if u.path == "/api/img":                       # binary image proxy (not JSON)
            res = fetch_image(parse_qs(u.query).get("u", [""])[0])
            if res:
                self._send(200, res[0], res[1])
            else:
                self._send(404, b"", "text/plain")
            return
        if u.path == "/api/sofa_team_img":             # SofaScore crest -> 302 so the user's browser
            tid = parse_qs(u.query).get("id", [""])[0]  # loads it directly (real Chrome TLS); the
            if tid.isdigit():                           # server itself is WAF-blocked from SofaScore
                self.send_response(302)
                self.send_header("Location", f"{SOFASCORE_BASE}/team/{int(tid)}/image")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
            else:
                self._send(404, b"", "text/plain")
            return
        if u.path.startswith("/api/"):
            try:
                data = api(u.path, parse_qs(u.query))
                self._send(200, jdumps(data).encode(), "application/json")
            except Exception as e:  # noqa: BLE001
                self._send(404 if isinstance(e, KeyError) else 500,
                           json.dumps({"error": str(e)}).encode(), "application/json")
            return
        # SEO: crawl guidance + a sitemap of every player/team page (which are
        # otherwise undiscoverable -- they live behind a JS search box).
        if u.path == "/robots.txt":
            self._send(200, seo.robots_txt(), "text/plain")
            return
        if u.path == "/sitemap.xml":
            self._send(200, seo.sitemap_xml(), "application/xml")
            return
        # static
        rel = u.path.lstrip("/") or "index.html"
        f = (FRONTEND / rel).resolve()
        if not str(f).startswith(str(FRONTEND)) or not f.is_file():
            self._send(404, b"Not found", "text/plain")
            return
        if f.suffix == ".html":                        # count real page views (not JS/CSS/img)
            admin.record_hit("/" + rel, "page", vid)
            # rewrite the JS-rendered shell's <head> with a real title, description,
            # canonical + OG tags so crawlers see meaningful, page-specific content.
            body = seo.inject_head(f.read_bytes(), rel, parse_qs(u.query), u.path)
            self._send(200, body, "text/html")
            return
        self._send(200, f.read_bytes(), CT.get(f.suffix, "application/octet-stream"))


def _live_refresher():
    """Keep live_matches genuinely live while the server runs: re-scrape SofaScore
    on a loop so the /api/live feed (and the page's 30s poll) shows current scores.
    Full window scrape every FULL_EVERY s (catches kickoffs / full-time / new
    fixtures); the cheap in-play overlay in between. Paces itself -- ~25s while
    games are live, slower when idle. Set ATLASTRA_NO_LIVE_REFRESH=1 to disable."""
    import time
    if FOTMOB:
        # FotMob path: rebuild live_matches from FotMob (server-side, no proxy/Mac).
        from pipeline import load_live_fotmob as fm
        LIVE_POLL = int(os.environ.get("ATLASTRA_LIVE_POLL", "45"))
        IDLE_POLL = int(os.environ.get("ATLASTRA_IDLE_POLL", "300"))
        n_live = 0
        while True:
            try:
                _, n_live = fm.refresh()
            except Exception as e:                 # noqa: BLE001
                print(f"fotmob refresher: {type(e).__name__}: {str(e)[:120]}", flush=True)
            time.sleep(LIVE_POLL if n_live else IDLE_POLL)
    from pipeline import load_live as live
    # Gentle cadence so a single (proxy) IP doesn't trip SofaScore's per-IP rate
    # limit: the live experience rides on the cheap 1-call overlay; the heavy
    # all-competitions sweep runs rarely. All tunable via env.
    FULL_EVERY = int(os.environ.get("ATLASTRA_FULL_EVERY", "1800"))   # full sweep every 30 min
    LIVE_POLL = int(os.environ.get("ATLASTRA_LIVE_POLL", "45"))       # overlay while games are live
    IDLE_POLL = int(os.environ.get("ATLASTRA_IDLE_POLL", "300"))      # nothing live -> back off
    # Lite mode: ONLY the single global live-events call (no per-competition sweep),
    # so one proxy IP never bursts and trips SofaScore's per-IP rate limit. Live
    # scores + the real-time bracket still update; upcoming/results stay on the last
    # snapshot. For a single static IP this is the only reliable mode.
    LITE = os.environ.get("ATLASTRA_LIVE_LITE") == "1"
    last_full = 0.0
    n_live = 0
    while True:
        try:
            if not LITE and time.time() - last_full >= FULL_EVERY:
                n_live = live.load_live()
                last_full = time.time()
            elif LITE or n_live:               # 1-call overlay: always in lite mode, else when live
                n_live = live.update_live_overlay()
        except Exception as e:                 # noqa: BLE001 -- network/scrape hiccup
            print(f"live refresher: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(LIVE_POLL if (LITE or n_live) else IDLE_POLL)


def _wc_refresher():
    """Rebuild the World Cup hub tables (standings/leaders/matches/bracket/player
    ratings) from FotMob on a loop — the FotMob replacement for the Mac's WC push."""
    import time
    from pipeline import load_wc_fotmob as wc
    WC_EVERY = int(os.environ.get("ATLASTRA_WC_EVERY", "900"))   # 15 min
    WC_SEASON = os.environ.get("ATLASTRA_WC_SEASON", "2026")
    while True:
        try:
            n = wc.refresh(WC_SEASON)
            print(f"WC refresh (FotMob): {n}", flush=True)
        except Exception as e:                         # noqa: BLE001
            print(f"WC refresher: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(WC_EVERY)


def _preview_warmer():
    """Keep _PREVIEW_CACHE hot for the soonest upcoming fixtures so the Preview tab is
    instant on the first click. The pusher warms each match's SofaScore preview paths
    into the cache; this precomputes the expensive key-player enrichment on top of them
    off the click path. Refresh cadence stays under _PREVIEW_TTL so the cache never
    lapses for those matches. Only meaningful in cache mode (the deployed host)."""
    import time
    while True:
        try:
            with SoccerDB(read_only=DB_READ_ONLY) as d:
                eids = [m["event_id"] for m in d.web_live(0, PREVIEW_WARM_N).get("upcoming", [])]
            for eid in eids:
                with SoccerDB(read_only=DB_READ_ONLY) as d:
                    _fixture_preview(eid, d)       # populates _PREVIEW_CACHE once data is ready
        except Exception as e:                     # noqa: BLE001
            print(f"preview warmer: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(PREVIEW_WARM_EVERY)


def _national_warmer():
    """Keep every national team's SofaScore paths warm in the persisted cache so
    /nat.html loads even when the relay (the scraper machine) is offline. Like the
    preview warmer, this just pulls each team through national_team() so its header,
    squad, results, fixtures (and latest-XI lineups) get queued for the relay and land
    in the persisted snapshot -- the reactive prewarm_team() on a page hit only covers
    teams someone has actually visited. Only meaningful in cache mode (the deployed
    host). Converges over a couple of cycles: prewarm_team() queues the four core paths
    up front, then national_team() queues the deeper lineup paths once they're cached."""
    import time
    while True:
        try:
            with SoccerDB(read_only=DB_READ_ONLY) as d:
                tids = [t["team_id"] for t in d.web_national_teams() if t.get("team_id")]
            for tid in tids:
                live_feed.prewarm_team(tid)
                live_feed.national_team(tid)       # pull-through -> queues + fills the cache
        except Exception as e:                     # noqa: BLE001
            print(f"national warmer: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(NAT_WARM_EVERY)


if __name__ == "__main__":
    print(f"Atlastra UI -> http://localhost:{PORT}  (Ctrl-C to stop)")
    admin.start_writer()
    print("admin usage log: on (buffered writer -> /admin dashboard)")
    if LIVE_REFRESH or FOTMOB:
        threading.Thread(target=_live_refresher, daemon=True).start()
        print(f"live refresher: on ({'FotMob' if FOTMOB else 'SofaScore'}, read-write DB)")
    if FOTMOB:                                     # WC hub straight from FotMob, no Mac
        threading.Thread(target=_wc_refresher, daemon=True).start()
        print("WC refresher: on (FotMob)")
    if live_feed.CACHE_MODE:
        threading.Thread(target=_preview_warmer, daemon=True).start()
        print(f"preview warmer: on (soonest {PREVIEW_WARM_N} upcoming, every {PREVIEW_WARM_EVERY}s)")
        threading.Thread(target=_national_warmer, daemon=True).start()
        print(f"national warmer: on (all national teams, every {NAT_WARM_EVERY}s)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
