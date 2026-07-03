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
from webapp import scout_ai  # noqa: E402
from webapp import admin  # noqa: E402

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
DB_READ_ONLY = not (LIVE_REFRESH or INGEST_TOKEN)

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
      ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png"}


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
    if path == "/api/scout_report":           # gather data (DB), then generate via Claude
        with SoccerDB(read_only=DB_READ_ONLY) as d:
            data = d.web_player(q.get("name", ["Pedri"])[0], q.get("career_stat", ["xa"])[0],
                                q.get("season", [None])[0])
        return scout_ai.scout_report(data, refresh=q.get("refresh", ["0"])[0] == "1")
    with SoccerDB(read_only=DB_READ_ONLY) as d:
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
            return d.web_compare(names, stats)
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
        # static
        rel = u.path.lstrip("/") or "index.html"
        f = (FRONTEND / rel).resolve()
        if not str(f).startswith(str(FRONTEND)) or not f.is_file():
            self._send(404, b"Not found", "text/plain")
            return
        if f.suffix == ".html":                        # count real page views (not JS/CSS/img)
            admin.record_hit("/" + rel, "page", vid)
        self._send(200, f.read_bytes(), CT.get(f.suffix, "application/octet-stream"))


def _live_refresher():
    """Keep live_matches genuinely live while the server runs: re-scrape SofaScore
    on a loop so the /api/live feed (and the page's 30s poll) shows current scores.
    Full window scrape every FULL_EVERY s (catches kickoffs / full-time / new
    fixtures); the cheap in-play overlay in between. Paces itself -- ~25s while
    games are live, slower when idle. Set ATLASTRA_NO_LIVE_REFRESH=1 to disable."""
    import time
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
    if LIVE_REFRESH:
        threading.Thread(target=_live_refresher, daemon=True).start()
        print("live refresher: on (read-write DB; ATLASTRA_NO_LIVE_REFRESH=1 to disable)")
    if live_feed.CACHE_MODE:
        threading.Thread(target=_preview_warmer, daemon=True).start()
        print(f"preview warmer: on (soonest {PREVIEW_WARM_N} upcoming, every {PREVIEW_WARM_EVERY}s)")
        threading.Thread(target=_national_warmer, daemon=True).start()
        print(f"national warmer: on (all national teams, every {NAT_WARM_EVERY}s)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
