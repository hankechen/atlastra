"""
Keep `live_matches` fresh so /live.html updates live.

The web UI (webapp/frontend/js/live.js) already re-polls /api/live every 30s, but
/api/live just reads the live_matches table -- so the scores only move if the
table is refreshed. This daemon re-runs the SofaScore scrape (pipeline.load_live)
on an ADAPTIVE interval: fast while matches are in play, slow when nothing is on,
so it's live during games without hammering SofaScore overnight.

Run it alongside the web server (separate process):
    python -m webapp.server          # the UI
    python -m pipeline.live_refresh  # this -- keeps the feed live

Ctrl-C to stop. DB write-lock clashes with the server's brief per-request reads
are rare and self-heal on the next tick (load_live opens its own connection).
"""
import sys
import time

try:
    from pipeline.load_live import load_live
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pipeline.load_live import load_live

LIVE_INTERVAL = 30      # seconds between refreshes while >=1 match is in play
IDLE_INTERVAL = 300     # seconds between refreshes when nothing is live
ERROR_BACKOFF = 20      # after a failed tick (e.g. DB lock / network blip)


def main() -> None:
    print(f"live_refresh: polling every {LIVE_INTERVAL}s when live, "
          f"{IDLE_INTERVAL}s when idle. Ctrl-C to stop.")
    while True:
        try:
            n_live = load_live()
            delay = LIVE_INTERVAL if n_live else IDLE_INTERVAL
        except KeyboardInterrupt:
            print("live_refresh: stopped."); return
        except Exception as e:  # noqa: BLE001 -- keep the daemon alive
            print(f"live_refresh: tick failed ({repr(e)[:100]}); retrying soon.")
            delay = ERROR_BACKOFF
        time.sleep(delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("live_refresh: stopped.")
