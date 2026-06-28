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


def _push(rows: list[dict], prune: bool):
    """POST rows to the server (plain HTTP to OUR server -- no bot bypass needed)."""
    body = json.dumps({"rows": rows, "prune": prune}, default=str).encode()
    req = urllib.request.Request(
        f"{SERVER}/api/ingest/live", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Ingest-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def main():
    if not TOKEN:
        sys.exit("ATLASTRA_INGEST_TOKEN is required (must match the server).")
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
