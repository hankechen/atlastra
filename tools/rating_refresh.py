#!/usr/bin/env python3
"""
Biweekly league-rating refresh — the automated half of "update the rating every
2 weeks" (UCL scope is a separate, not-yet-built FotMob migration; see
[[auto-blog-drafts]]-style memory note for the sibling decision on that).

Runs the specific pipeline steps a rating refresh needs -- Understat scrape,
datamb scrape, their loaders, the rating engine, the stat views, and the
combined ratings -- WITHOUT ever calling pipeline.run_pipeline or
pipeline.init_db(reset=True), which deletes the entire warehouse file. Every
step this script calls only touches its own output table(s) (DROP TABLE IF
EXISTS <its table> + recreate), the same safe pattern every other refresher in
this codebase uses -- verified by reading each one before wiring this up, not
assumed.

Because DuckDB won't allow a second read-write connection while the server
holds the file open, this stops the systemd service, runs the pipeline, then
restarts it -- a real, scheduled downtime window (user confirmed this
tradeoff is acceptable, timed for low-traffic hours). Self-gates to roughly
every 2 weeks via a marker file rather than fighting cron/systemd calendar
math for "every N weeks": meant to be invoked DAILY (by a systemd timer), and
only actually does anything once >= 13 days have passed since the last
SUCCESSFUL run. A failed run does not update the marker, so it's retried the
next day rather than waiting a full two weeks again.

Usage:
    python -m tools.rating_refresh            # normal (self-gated) run
    python -m tools.rating_refresh --force     # ignore the 13-day gate
    python -m tools.rating_refresh --dry-run   # print the plan, touch nothing
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DATA_DIR  # noqa: E402

MARKER = DATA_DIR / "rating_refresh_last_ok.txt"
LOG = Path(__file__).resolve().parent.parent / "logs" / "rating_refresh.log"
MIN_DAYS = 13
HEALTH_URL = "http://127.0.0.1:8000/"
SERVICE = "atlastra"

# Each is `python -m pipeline.<module>` -- run in this order. Skips the
# SofaScore UCL scrape/load entirely, so UCL-scope ratings stay at whatever
# they were last set to (by a manual full pipeline run) -- see the module
# docstring for why that's a deliberate, temporary scope cut, not an oversight.
STEPS = [
    ["pipeline.scrape", "--quick"],   # Understat, focus season + players only -- no need to
                                       # re-pull 12 historical seasons every 2 weeks
    ["pipeline.scrape_datamb"],       # datamb.football Wyscout stats, current season only (DATAMB_SEASONS)
    ["pipeline.load"],                # reload Understat data into the core tables (own tables only)
    ["pipeline.load_datamb"],         # player_wyscout (DROPs + recreates -- wipes the backfill below)
    ["pipeline.load_sofa_domestic"],  # re-applies clearances/errors_per_90 onto player_wyscout from
                                       # the cached raw parquet (NOT a live SofaScore call -- see its
                                       # docstring) -- must run after load_datamb or the DROP above
                                       # wipes these columns every cycle instead of just leaving them
                                       # stale. No network dependency here, so no SofaScore-block issue.
    ["pipeline.rate"],                # player_ratings_v2, rating_weights (the base engine)
    ["pipeline.build_views"],         # v_stats_* views (no data written, just view definitions)
    ["pipeline.rate_combined"],       # player_ratings_combined -- league scope refreshes; UCL
                                       # scope is untouched since its SofaScore input isn't re-scraped
]


def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def _days_since_last_ok() -> float:
    if not MARKER.exists():
        return float("inf")
    try:
        return (time.time() - float(MARKER.read_text().strip())) / 86400
    except (ValueError, OSError):
        return float("inf")


def _run_step(step: list[str]) -> bool:
    mod = step[0]
    _log(f"-> {' '.join(step)}")
    r = subprocess.run([sys.executable, "-m", *step], capture_output=True, text=True)
    if r.stdout.strip():
        _log(r.stdout.strip()[-2000:])   # tail only -- Understat/datamb scrapes are chatty
    if r.returncode != 0:
        _log(f"   FAILED (exit {r.returncode}): {r.stderr.strip()[-1500:]}")
        return False
    return True


def _service(action: str) -> None:
    _log(f"systemctl {action} {SERVICE}")
    subprocess.run(["sudo", "systemctl", action, SERVICE], check=False)


def _wait_healthy(tries: int = 30, delay: int = 2) -> bool:
    import urllib.request
    for _ in range(tries):
        try:
            if urllib.request.urlopen(HEALTH_URL, timeout=3).status == 200:
                return True
        except Exception:                                  # noqa: BLE001
            pass
        time.sleep(delay)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignore the 13-day gate")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    args = ap.parse_args()

    days = _days_since_last_ok()
    if args.dry_run:
        print(f"Last successful run: {days:.1f} days ago (gate: {MIN_DAYS}).")
        print("Would run:" if (args.force or days >= MIN_DAYS) else "Would SKIP (too soon):")
        for s in STEPS:
            print(f"  - python -m {' '.join(s)}")
        return 0

    if not args.force and days < MIN_DAYS:
        _log(f"skip: last successful run was {days:.1f} days ago (< {MIN_DAYS})")
        return 0

    _log(f"=== biweekly rating refresh starting (last ok {days:.1f}d ago) ===")
    _service("stop")
    ok = True
    try:
        for step in STEPS:
            if not _run_step(step):
                ok = False
                _log(f"aborting remaining steps after {step[0]} failed")
                break
    finally:
        _service("start")
        healthy = _wait_healthy()
        _log(f"service restarted, healthy={healthy}")

    if ok and healthy:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(str(time.time()))
        _log("=== refresh OK, marker updated ===")
        return 0
    _log("=== refresh FAILED or service unhealthy after restart -- marker NOT updated, will retry tomorrow ===")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
