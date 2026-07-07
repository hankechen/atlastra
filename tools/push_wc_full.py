"""
Full World Cup re-scrape + push to prod (all editions).

Needed after a wc_player_stats SCHEMA change: the recurring pusher only sends the
current edition (2026), and the server drops+recreates wc_player_stats on a schema
mismatch -- so a 2026-only push would wipe the historical editions. This re-scrapes
every edition (correct tuple shapes for all WC tables), refreshes the local DB, and
POSTs the whole dataset to /api/ingest/wc so prod migrates + repopulates in one shot.

    ATLASTRA_INGEST_TOKEN=... ATLASTRA_SERVER=https://16-59-15-84.sslip.io \
        python -m tools.push_wc_full
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import load_wc   # noqa: E402

SERVER = (os.environ.get("ATLASTRA_SERVER") or "https://16-59-15-84.sslip.io").rstrip("/")
TOKEN = os.environ.get("ATLASTRA_INGEST_TOKEN")


def main() -> None:
    if not TOKEN:
        sys.exit("ATLASTRA_INGEST_TOKEN is required (must match the server).")
    print("scraping all World Cup editions (this hits SofaScore ~100x)…")
    data = load_wc.fetch_wc_rows(None)          # all editions, rich rows
    load_wc.write_wc_rows(data)                  # refresh local DB with the new schema
    print(f"pushing {len(data['players'])} player rows / {len(data['matches'])} matches "
          f"to {SERVER} …")
    body = json.dumps({"data": data}, default=str).encode()
    req = urllib.request.Request(
        f"{SERVER}/api/ingest/wc", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Ingest-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=300) as resp:
        print(resp.status, (resp.read() or b"")[:300].decode(errors="replace"))


if __name__ == "__main__":
    main()
