"""
Pre-warm the Signature Skills cache for the top-rated players, keyed by their REAL
site names (the DB names that profiles and listings use), so the cache actually
hits — a previous version keyed off a hand-written star list ("Vinícius Jr") which
didn't match the profile name ("Vinícius Júnior"), so it never warmed the profiles.

Pulls the top-N names from the live rankings API (avoids a second DuckDB lock),
then warms each. Idempotent — already-cached players are skipped.

Run on the server (needs GEMINI_API_KEY + FotMob feed in the env):
  set -a; . /opt/atlastra/.env; set +a
  /opt/atlastra/venv/bin/python tools/prewarm_signatures.py [N]
"""
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp import live_feed_fotmob as lf      # noqa: E402
from webapp import signature_skills as ss       # noqa: E402

BASE = os.environ.get("ATLASTRA_SITE_URL", "https://atlastra.dedyn.io").rstrip("/")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100


def top_players(n):
    d = json.load(urllib.request.urlopen(f"{BASE}/api/rankings?limit={n}", timeout=30))
    return [p["player"] for p in d if p.get("player")]


def warm(name):
    pv = lf.player_video(name) or {}
    url = pv.get("url") if pv.get("available") else None
    r = ss.generate(name, url)
    if not r.get("available"):
        return f"✗ {name} (no video/result)"
    return f"✓ {name} {'(cached)' if r.get('cached') else str(len(r.get('skills', []))) + ' skills'}"


def main():
    players = top_players(N)
    print(f"pre-warming signature skills for {len(players)} players (by DB name)…", flush=True)
    with ThreadPoolExecutor(max_workers=5) as pool:
        for line in pool.map(warm, players):
            print("  " + line, flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
