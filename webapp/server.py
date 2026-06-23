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
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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
from config import FOCUS_SEASON  # noqa: E402
from webapp import auth  # noqa: E402


def _season(q):
    """Season code from the query string, defaulting to the current season."""
    return (q.get("season", [FOCUS_SEASON])[0] or FOCUS_SEASON)
from webapp import live_feed  # noqa: E402
from webapp import scout_ai  # noqa: E402

FRONTEND = Path(__file__).resolve().parent / "frontend"
PORT = 8000
CT = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
      ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png"}


# Live match-detail endpoints proxy SofaScore (server-side TLS bypass) and never
# touch the warehouse, so they bypass the SoccerDB context manager below.
def match_api(path: str, q: dict) -> dict:
    eid = int(q.get("id", [0])[0])
    if path == "/api/match":
        return live_feed.header(eid)
    if path == "/api/match/stats":
        return live_feed.statistics(eid)
    if path == "/api/match/lineups":
        d = live_feed.lineups(eid)
        players = []
        for side in ("home", "away"):
            s = d.get(side) or {}
            players += (s.get("starting_xi") or []) + (s.get("substitutes") or [])
        if players:
            with SoccerDB(read_only=True) as db:
                rmap = db.ratings_by_name([p.get("name") for p in players])
            # estimate (SofaScore season form) for players not in our DB — in
            # parallel, since each is a couple of network calls.
            need = {p.get("id") for p in players if rmap.get(p.get("name")) is None and p.get("id")}
            ests = {}
            if need:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {ex.submit(live_feed.season_estimate, pid): pid for pid in need}
                    for f in as_completed(futs):
                        ests[futs[f]] = f.result()
            for p in players:
                r = rmap.get(p.get("name"))
                if r is not None:                       # our combined League/UCL rating
                    p["atlas_rating"], p["atlas_est"] = r, False
                elif ests.get(p.get("id")) is not None:  # estimated from SofaScore
                    p["atlas_rating"], p["atlas_est"] = ests[p["id"]], True
        return d
    if path == "/api/match/shotmap":
        return live_feed.shotmap(eid)
    if path == "/api/match/timeline":
        return live_feed.timeline(eid)
    if path == "/api/match/player-stats":
        d = live_feed.player_stats(eid)
        names = [p.get("name") for p in d.get("players", [])]
        if names:
            with SoccerDB(read_only=True) as db:
                have = db.have_profiles(names)
            for p in d["players"]:
                p["has_profile"] = p.get("name") in have
        return d
    if path == "/api/match/heatmap":
        return live_feed.player_heatmap(eid, int(q.get("player_id", [0])[0]))
    if path == "/api/match/prediction":
        return live_feed.prediction(eid)
    raise KeyError(path)


def api(path: str, q: dict) -> dict | list:
    # match-detail routes are exactly /api/match or /api/match/... — must NOT
    # swallow sibling routes like /api/match_search or /api/match_preview.
    if path == "/api/match" or path.startswith("/api/match/"):
        return match_api(path, q)
    if path == "/api/national_team":          # SofaScore live proxy (no DB)
        return live_feed.national_team(int(q.get("id", [0])[0]))
    if path == "/api/player_club":
        return live_feed.player_club(int(q.get("id", [0])[0]))
    if path == "/api/scout_report":           # gather data (DB), then generate via Claude
        with SoccerDB(read_only=True) as d:
            data = d.web_player(q.get("name", ["Pedri"])[0], q.get("career_stat", ["xa"])[0],
                                q.get("season", [None])[0])
        return scout_ai.scout_report(data, refresh=q.get("refresh", ["0"])[0] == "1")
    with SoccerDB(read_only=True) as d:
        if path == "/api/overview":
            return d.web_overview()
        if path == "/api/rankings":
            return d.web_rankings(int(q.get("limit", ["10"])[0]))
        if path == "/api/position_rankings":
            return d.web_position_rankings(int(q.get("limit", ["20"])[0]),
                                           scope=q.get("scope", ["league"])[0])
        if path == "/api/alltime_seasons":
            return d.web_alltime_seasons(q.get("scope", ["combined"])[0],
                                         int(q.get("limit", ["20"])[0]))
        if path == "/api/national_teams":
            return d.web_national_teams()
        if path == "/api/players":
            return d.web_players(q.get("group", ["all"])[0],
                                 (q.get("search", [""])[0] or None),
                                 int(q.get("limit", ["30"])[0]),
                                 scope=q.get("scope", ["league"])[0])
        if path == "/api/spotlight":
            return d.web_spotlight()
        if path == "/api/live":
            return d.web_live(int(q.get("recent", ["40"])[0]),
                              int(q.get("upcoming", ["40"])[0]))
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
            pv = live_feed.fixture_preview(int(q.get("id", [0])[0]))
            if pv.get("available"):
                for side in ("home", "away"):
                    pv[side]["key"] = d.web_squad_key_players(pv[side].pop("squad", []))
            return pv
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
        if path == "/api/scout":
            return d.web_scout(
                q.get("pos", ["all"])[0], q.get("metric", ["rating"])[0],
                float(q.get("max_value", ["0"])[0]), int(q.get("min_minutes", ["450"])[0]),
                int(q.get("max_age", ["0"])[0]), int(q.get("min_rating", ["0"])[0]),
                int(q.get("limit", ["40"])[0]))
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
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200, extra_headers=None):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json", extra_headers)

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
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except Exception:  # noqa: BLE001
            return {}

    # ---- optional accounts (auth + per-user data sync) ----
    def do_POST(self):
        u = urlparse(self.path)
        b = self._body_json()
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
        self._json({"error": "Not found"}, 404)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/auth/me":
            self._json({"user": auth.user_for_token(self._cookie("atla_session"))})
            return
        if u.path == "/api/user/data":
            user = auth.user_for_token(self._cookie("atla_session"))
            if not user:
                self._json({"error": "Not signed in."}, 401)
                return
            self._json({"data": json.loads(auth.get_data(user["id"]) or "null")})
            return
        if u.path == "/api/img":                       # binary image proxy (not JSON)
            res = fetch_image(parse_qs(u.query).get("u", [""])[0])
            if res:
                self._send(200, res[0], res[1])
            else:
                self._send(404, b"", "text/plain")
            return
        if u.path == "/api/sofa_team_img":             # SofaScore crest via TLS bypass
            tid = parse_qs(u.query).get("id", [""])[0]
            res = live_feed.team_image(int(tid)) if tid.isdigit() else None
            if res:
                self._send(200, res[0], res[1])
            else:
                self._send(404, b"", "text/plain")
            return
        if u.path.startswith("/api/"):
            try:
                data = api(u.path, parse_qs(u.query))
                self._send(200, json.dumps(data, default=str).encode(), "application/json")
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
        self._send(200, f.read_bytes(), CT.get(f.suffix, "application/octet-stream"))


if __name__ == "__main__":
    print(f"Atlastra UI -> http://localhost:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
