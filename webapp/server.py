"""
Atlastra web UI -- zero-dependency server (Python stdlib only).

Serves the static frontend (webapp/frontend) and a small JSON API backed by
analytics.queries.SoccerDB (real DuckDB data). Anything the warehouse doesn't
have (live matches, Ballon d'Or predictor, team-of-season, heatmap, technique
analysis, nationality, contract) is a clearly-labelled placeholder in the
frontend, per the design mock.

Run:  python -m webapp.server     ->  http://localhost:8000
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analytics.queries import SoccerDB  # noqa: E402

FRONTEND = Path(__file__).resolve().parent / "frontend"
PORT = 8000
CT = {".html": "text/html", ".css": "text/css", ".js": "application/javascript",
      ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png"}


def api(path: str, q: dict) -> dict | list:
    with SoccerDB(read_only=True) as d:
        if path == "/api/overview":
            return d.web_overview()
        if path == "/api/rankings":
            return d.web_rankings(int(q.get("limit", ["10"])[0]))
        if path == "/api/players":
            return d.web_players(q.get("group", ["all"])[0],
                                 (q.get("search", [""])[0] or None),
                                 int(q.get("limit", ["30"])[0]))
        if path == "/api/spotlight":
            return d.web_spotlight()
        if path == "/api/standings":
            return d.web_standings(q.get("league", ["ENG-Premier League"])[0])
        if path == "/api/player":
            return d.web_player(q.get("name", ["Pedri"])[0],
                                q.get("career_stat", ["xa"])[0])
        if path == "/api/compare":
            names = q.get("name", [])
            stats = q.get("stat") or None
            return d.web_compare(names, stats)
        if path == "/api/leagues":
            return d.web_leagues()
        if path == "/api/league_table":
            return d.web_league_table(q.get("league", ["ENG-Premier League"])[0])
        if path == "/api/team":
            return d.web_team(q.get("name", ["Arsenal"])[0])
        if path == "/api/search":
            return d.web_search(q.get("q", [""])[0])
        if path == "/api/match_search":
            return d.web_match_search(q.get("a", [""])[0], q.get("b", [""])[0])
        if path == "/api/archetypes":
            return d.web_archetypes()
        if path == "/api/archetype":
            return d.web_archetype(q.get("name", ["Poacher"])[0])
        if path == "/api/team_of_season":
            return d.web_team_of_season()
        raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # dev server: never let the browser serve a stale JS/CSS/HTML asset
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
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
