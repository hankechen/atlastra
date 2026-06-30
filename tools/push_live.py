"""
Push the live SofaScore feed to the Atlastra cloud server.

The cloud host's datacenter IP is bot-blocked by SofaScore, but a normal machine
(e.g. a home Mac) can scrape it fine. This script does that scrape here and POSTs
the rows to the server's /api/ingest/live endpoint, so the deployed app shows live
scores + a real-time bracket without the server ever touching SofaScore.

Run it wherever SofaScore is reachable. Config via env:
    ATLASTRA_SERVER         server base URL   (default https://16-59-15-84.sslip.io)
    ATLASTRA_INGEST_TOKEN   shared secret (must match the server's)   [required]
    PUSH_FULL_EVERY         full window scrape interval, s   (default 1800)
    PUSH_LIVE_POLL          in-play overlay interval while live, s   (default 60)
    PUSH_IDLE_POLL          poll interval when nothing is live, s    (default 300)

    python -m tools.push_live          # loop forever (Ctrl-C to stop)
"""
import json
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import load_live as live   # noqa: E402  (scrapes SofaScore via tls_requests)

SERVER = (os.environ.get("ATLASTRA_SERVER") or "https://16-59-15-84.sslip.io").rstrip("/")
TOKEN = os.environ.get("ATLASTRA_INGEST_TOKEN")
FULL_EVERY = int(os.environ.get("PUSH_FULL_EVERY", "1800"))
LIVE_POLL = int(os.environ.get("PUSH_LIVE_POLL", "60"))
IDLE_POLL = int(os.environ.get("PUSH_IDLE_POLL", "300"))
QUEUE_POLL = int(os.environ.get("PUSH_QUEUE_POLL", "3"))   # on-demand match-detail relay interval
WARM_LIVE = int(os.environ.get("PUSH_WARM_LIVE", "5"))     # pre-warm this many in-play matches' detail
WARM_EVERY = int(os.environ.get("PUSH_WARM_EVERY", "60"))  # how often to re-warm live matches
_WARMED_CLUBS = set()                                      # player ids whose (static) club is cached
WC_EVERY = int(os.environ.get("PUSH_WC_EVERY", "900"))     # World Cup leaders/standings refresh
WC_SEASON = os.environ.get("PUSH_WC_SEASON", "2026")       # current edition to keep fresh


def _post(endpoint: str, payload: dict):
    """POST JSON to OUR server (plain HTTP -- no bot bypass needed)."""
    body = json.dumps(payload, default=str).encode()
    req = urllib.request.Request(
        f"{SERVER}{endpoint}", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Ingest-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def _push(rows: list[dict], prune: bool):
    return _post("/api/ingest/live", {"rows": rows, "prune": prune})


def _fetch(path: str):
    """Fetch one SofaScore path directly (this machine isn't blocked). None on error
    so the server negative-caches 404s (e.g. a heatmap for a player who didn't play)."""
    try:
        return live._get(path)
    except Exception:                                     # noqa: BLE001
        return None


def _warm_matches(eids):
    """Pre-fetch + push detail for these matches so they open INSTANTLY on the site --
    the core match detail (header/lineups/timeline/stats/shotmap) AND each starter's
    club (static, fetched once) + heatmap, so the lineup player modal has no on-click
    lag. Returns the number of paths warmed."""
    items = []
    for eid in eids:
        lu = None
        for suf in ("", "/lineups", "/incidents", "/statistics", "/shotmap"):
            body = _fetch(f"/event/{eid}{suf}")
            if suf == "/lineups":
                lu = body
            items.append({"path": f"/event/{eid}{suf}", "body": body})
        for side in ("home", "away"):                     # starters' club + heatmap
            for pl in ((lu or {}).get(side) or {}).get("players", []) or []:
                if pl.get("substitute"):
                    continue
                pid = (pl.get("player") or {}).get("id")
                if not pid:
                    continue
                hp = f"/event/{eid}/player/{pid}/heatmap"
                items.append({"path": hp, "body": _fetch(hp)})
                if pid not in _WARMED_CLUBS:              # club is static -> fetch once
                    items.append({"path": f"/player/{pid}", "body": _fetch(f"/player/{pid}")})
                    _WARMED_CLUBS.add(pid)
    for i in range(0, len(items), 30):                    # push in modest chunks
        try:
            _post("/api/ingest/cache", {"items": items[i:i + 30]})
        except Exception:                                 # noqa: BLE001
            pass
    return len(items)


def _service_warm():
    """Keep the in-play matches hot (core detail + starters' club/heatmap) so they --
    and their player modals -- open instantly. Independent of the live-score push so
    scores stay snappy."""
    while True:
        try:
            eids = [r["event_id"] for r in live.fetch_live_only()][:WARM_LIVE]
            if eids:
                n = _warm_matches(eids)
                print(f"{datetime.now():%H:%M:%S} warmed {len(eids)} live match(es) ({n} paths)", flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"{datetime.now():%H:%M:%S} warm error: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(WARM_EVERY)


def _service_queue():
    """Relay loop: fetch the SofaScore paths the server needs (match detail,
    lineups, national teams, ...) and push the JSON back, so the WAF-blocked
    server can serve match pages from cache."""
    req = urllib.request.Request(f"{SERVER}/api/ingest/queue",
                                 headers={"X-Ingest-Token": TOKEN})
    while True:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                paths = (json.loads(resp.read() or b"{}")).get("paths") or []
            if paths:
                items = [{"path": p, "body": _fetch(p)} for p in paths[:40]]
                status, r = _post("/api/ingest/cache", {"items": items})
                print(f"{datetime.now():%H:%M:%S} relayed {len(items)} match-detail path(s) -> {status}", flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"{datetime.now():%H:%M:%S} queue error: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(QUEUE_POLL)


def _service_wc():
    """Periodically re-scrape the current World Cup (matches/standings/stat leaders)
    and push it -- those are warehouse tables the server can't refresh itself."""
    from pipeline import load_wc
    while True:
        try:
            data = load_wc.fetch_wc_rows(WC_SEASON)
            status, r = _post("/api/ingest/wc", {"data": data})
            print(f"{datetime.now():%H:%M:%S} WC pushed {len(data['leaders'])} leaders / "
                  f"{len(data['matches'])} matches / {len(data['standings'])} standings / "
                  f"{len(data.get('players') or [])} players / "
                  f"{len(data.get('bracket') or [])} bracket -> {status}", flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"{datetime.now():%H:%M:%S} WC push error: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(WC_EVERY)


def main():
    if not TOKEN:
        sys.exit("ATLASTRA_INGEST_TOKEN is required (must match the server).")
    threading.Thread(target=_service_queue, daemon=True).start()   # match-detail relay
    threading.Thread(target=_service_warm, daemon=True).start()    # pre-warm live matches + players
    threading.Thread(target=_service_wc, daemon=True).start()      # World Cup hub data
    last_full = 0.0
    n_live = 0
    while True:
        try:
            full = time.time() - last_full >= FULL_EVERY
            rows = live.fetch_rows() if full else live.fetch_live_only()
            if full:
                last_full = time.time()
            n_live = sum(1 for r in rows if r.get("status_type") == "inprogress")
            status, resp = _push(rows, prune=full)
            print(f"{datetime.now():%H:%M:%S} {'FULL' if full else 'live'} "
                  f"pushed {len(rows)} rows ({n_live} in-play) -> {status} {resp}", flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"{datetime.now():%H:%M:%S} push error: {type(e).__name__}: {str(e)[:140]}", flush=True)
        time.sleep(LIVE_POLL if n_live else IDLE_POLL)


if __name__ == "__main__":
    main()
