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
from concurrent.futures import ThreadPoolExecutor
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
FAIL_POLL = int(os.environ.get("PUSH_FAIL_POLL", "15"))    # retry the main scrape soon after a failed cycle
WARM_LIVE = int(os.environ.get("PUSH_WARM_LIVE", "5"))     # pre-warm this many in-play matches' detail
WARM_EVERY = int(os.environ.get("PUSH_WARM_EVERY", "60"))  # how often to re-warm live matches
# Also pre-warm the soonest UPCOMING fixtures (preview + lineup paths) and most RECENT
# results (core detail) so those match pages open from cache -- no relay round-trip on
# the click path. These change slowly, so a gentler cadence than the live warm.
WARM_UPCOMING = int(os.environ.get("PUSH_WARM_UPCOMING", "10"))
WARM_RECENT = int(os.environ.get("PUSH_WARM_RECENT", "6"))
WARM_FIXT_EVERY = int(os.environ.get("PUSH_WARM_FIXT_EVERY", "240"))
_WARMED_CLUBS = set()                                      # player ids whose (static) club is cached
_FIXTURES = {"upcoming": [], "recent": []}                 # soonest/most-recent rows (from the full scrape)
# bookmaker odds feeds for the preview projection (mirror live_feed._ODDS_PROVIDERS)
_ODDS_PROVIDERS = (1, 5, 8, 11, 14, 16)
WC_EVERY = int(os.environ.get("PUSH_WC_EVERY", "900"))     # World Cup leaders/standings refresh
WC_SEASON = os.environ.get("PUSH_WC_SEASON", "2026")       # current edition to keep fresh

# tls_requests leaks ONE socket per request -- its Go tls-client binding never frees
# the connection (confirmed: neither response.close, client.close, nor a with-block
# reclaims it), and every socket holds an ephemeral local port. macOS has only ~16k
# ephemeral ports (49152-65535), so after enough requests this process can't open ANY
# new socket -- not even the UDP socket the DNS resolver needs -- and every scrape
# fails with "listen udp :0: bind: resource temporarily unavailable", leaving the live
# feed empty. There is no in-process fix, so recycle BEFORE we get close: a guard
# thread exits when our open-fd count crosses a threshold, and launchd's KeepAlive
# relaunches a fresh process (which re-warms the feed in ~1 min).
FD_RECYCLE = int(os.environ.get("PUSH_FD_RECYCLE", "10000"))   # ~6k ports of headroom
FD_CHECK_EVERY = int(os.environ.get("PUSH_FD_CHECK_EVERY", "120"))


def _fd_count() -> int:
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return 0


def _service_fdguard():
    while True:
        time.sleep(FD_CHECK_EVERY)
        n = _fd_count()
        if n >= FD_RECYCLE:
            print(f"{datetime.now():%H:%M:%S} fd guard: {n} fds open (>= {FD_RECYCLE}) "
                  f"-- recycling process; launchd KeepAlive will relaunch", flush=True)
            os._exit(0)   # from a daemon thread: os._exit kills the WHOLE process


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


# SofaScore serves a single home IP fine at a modest fan-out; fetching a batch of
# queued paths concurrently (instead of one-at-a-time) cuts a relay cycle from
# several seconds to ~one, so cold match pages / previews open much faster.
_FETCH_POOL = ThreadPoolExecutor(max_workers=int(os.environ.get("PUSH_FETCH_WORKERS", "8")))


def _fetch_items(paths):
    """Fetch many paths concurrently -> [{path, body}] (order-independent)."""
    return [{"path": p, "body": b}
            for p, b in zip(paths, _FETCH_POOL.map(_fetch, paths))]


def _push_items(items):
    """POST warmed {path, body} rows to the server's cache in modest chunks."""
    for i in range(0, len(items), 30):
        try:
            _post("/api/ingest/cache", {"items": items[i:i + 30]})
        except Exception:                                 # noqa: BLE001
            pass
    return len(items)


def _warm_matches(eids, lite: bool = False):
    """Pre-fetch + push match detail for these events so they open INSTANTLY on the
    site -- the core detail (header/lineups/timeline/stats/shotmap), and unless `lite`,
    each starter's club (static, fetched once) + heatmap so the lineup player modal has
    no on-click lag. Fetched concurrently. Returns the number of paths warmed."""
    core = [f"/event/{eid}{suf}" for eid in eids
            for suf in ("", "/lineups", "/incidents", "/statistics", "/shotmap")]
    items = _fetch_items(core)
    lus = {it["path"]: it["body"] for it in items}        # lineups keyed by path
    if not lite:                                          # starters' heatmap + club
        extra = []
        for eid in eids:
            lu = lus.get(f"/event/{eid}/lineups")
            for side in ("home", "away"):
                for pl in ((lu or {}).get(side) or {}).get("players", []) or []:
                    if pl.get("substitute"):
                        continue
                    pid = (pl.get("player") or {}).get("id")
                    if not pid:
                        continue
                    extra.append(f"/event/{eid}/player/{pid}/heatmap")
                    if pid not in _WARMED_CLUBS:          # club is static -> fetch once
                        extra.append(f"/player/{pid}")
                        _WARMED_CLUBS.add(pid)
        items += _fetch_items(extra)
    return _push_items(items)


def _warm_previews(rows):
    """Pre-fetch + push each upcoming fixture's PREVIEW paths (both teams' recent form &
    squad, head-to-head, bookmaker odds) so the Preview tab renders from cache instead
    of triggering a relay round-trip. Team ids come from the scraped row, so no wait on
    the event header. Fetched concurrently. Returns the number of paths warmed."""
    paths = []
    for r in rows:
        eid = r["event_id"]
        paths.append(f"/event/{eid}/h2h")
        paths += [f"/event/{eid}/odds/{p}/featured" for p in _ODDS_PROVIDERS]
        for tid in (r.get("home_team_id"), r.get("away_team_id")):
            if tid:
                paths += [f"/team/{tid}/events/last/0", f"/team/{tid}/players"]
    return _push_items(_fetch_items(paths))


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


def _service_warm_fixtures():
    """Keep the soonest UPCOMING fixtures (core detail + preview) and the most RECENT
    results (core detail) warm so their match pages -- Preview, Lineups, Stats, ... --
    open from cache with no relay round-trip. The fixture list comes from the main
    loop's full scrape (_FIXTURES); these change slowly, hence the gentle cadence."""
    while True:
        try:
            up = _FIXTURES["upcoming"][:WARM_UPCOMING]
            rc = _FIXTURES["recent"][:WARM_RECENT]
            n = 0
            if up:
                n += _warm_matches([r["event_id"] for r in up], lite=True)
                n += _warm_previews(up)
            if rc:
                n += _warm_matches([r["event_id"] for r in rc], lite=True)
            if up or rc:
                print(f"{datetime.now():%H:%M:%S} warmed {len(up)} upcoming + {len(rc)} "
                      f"recent fixture(s) ({n} paths)", flush=True)
            else:
                time.sleep(10)                            # list not populated yet -- retry soon
                continue
        except Exception as e:                            # noqa: BLE001
            print(f"{datetime.now():%H:%M:%S} fixture-warm error: {type(e).__name__}: {str(e)[:120]}", flush=True)
        time.sleep(WARM_FIXT_EVERY)


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
                items = _fetch_items(paths[:40])
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
    threading.Thread(target=_service_warm_fixtures, daemon=True).start()  # upcoming previews + recent
    threading.Thread(target=_service_wc, daemon=True).start()      # World Cup hub data
    threading.Thread(target=_service_fdguard, daemon=True).start() # recycle before port exhaustion
    last_full = 0.0
    n_live = 0
    while True:
        ok = True
        try:
            full = time.time() - last_full >= FULL_EVERY
            if full:
                rows = live.fetch_rows()
            else:
                # A fixture whose kickoff has just passed won't be re-scraped as live
                # until the next (30-min) full sweep, and the global live feed may not
                # carry it -- so direct-poll any soonest-upcoming fixture that should be
                # in play now, catching the kickoff within one live cycle.
                now = int(time.time())
                due = [r["event_id"] for r in _FIXTURES["upcoming"]
                       if live._should_be_live(r, now)]
                rows = live.fetch_live_only(extra_ids=due)
            if full:
                last_full = time.time()
                # refresh the fixture-warm lists: soonest upcoming, most-recent finished
                up = sorted((r for r in rows if r.get("status_type") in ("notstarted", "delayed")
                             and r.get("start_timestamp")), key=lambda r: r["start_timestamp"])
                rc = sorted((r for r in rows if r.get("status_type") == "finished"
                             and r.get("start_timestamp")), key=lambda r: -r["start_timestamp"])
                _FIXTURES["upcoming"] = up[:WARM_UPCOMING]
                _FIXTURES["recent"] = rc[:WARM_RECENT]
            n_live = sum(1 for r in rows if r.get("status_type") == "inprogress")
            status, resp = _push(rows, prune=full)
            print(f"{datetime.now():%H:%M:%S} {'FULL' if full else 'live'} "
                  f"pushed {len(rows)} rows ({n_live} in-play) -> {status} {resp}", flush=True)
        except Exception as e:                            # noqa: BLE001
            ok = False
            print(f"{datetime.now():%H:%M:%S} push error: {type(e).__name__}: {str(e)[:140]}", flush=True)
        # A failed cycle is usually a transient network blip -- retry soon instead of
        # sleeping the full idle interval, so the live feed recovers in seconds.
        time.sleep((LIVE_POLL if n_live else IDLE_POLL) if ok else FAIL_POLL)


if __name__ == "__main__":
    main()
