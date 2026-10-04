#!/usr/bin/env python3
"""
Biweekly league + UCL rating refresh — the automated half of "update the rating
every 2 weeks". Both the combined scopes AND the PRIMARY rating refresh now:
- Combined (player_ratings_combined): pipeline.load_player_season_fotmob fills
  the CURRENT domestic season straight into player_season_stats, and
  pipeline.load_ucl_fotmob fills the CURRENT UCL season into ucl_player_stats
  (SofaScore itself stays Mac-only/manual for history, see
  [[sofascore-ucl-data-source]]). See [[ucl-fotmob-loader]] for the pattern.
- Primary (player_ratings_v2, pipeline.rate + pipeline.profile): datamb.football
  was never actually paywalled -- it was misdiagnosed as one (2026-09ish) when
  its own site had rolled over to serving the 2026/27 season while this project's
  scraper kept requesting the now-gone 2025/26 bucket under
  config.FOCUS_SEASON, which is pinned to whatever Understat has published (a
  season behind). Found and fixed 2026-10-03: config.DATAMB_SEASON is now a
  SEPARATE constant from FOCUS_SEASON (datamb's own rollover is independent of
  Understat's), and pipeline.rate/load_datamb/load_sofa_domestic/
  scrape_sofa_domestic/profile all default to it. See [[datamb-season-fix]] for
  the full story.

Runs the specific pipeline steps a rating refresh needs -- Understat scrape,
datamb scrape, their loaders, the domestic + UCL FotMob loaders, the rating
engine, the stat views, and the combined ratings -- WITHOUT ever calling pipeline.run_pipeline or
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
# SofaScore UCL scrape/load itself (that stays Mac-only/manual for historical
# seasons -- see pipeline/load_ucl.py), but load_ucl_fotmob keeps the CURRENT
# UCL season's rows fresh via FotMob's server-reachable CDN instead.
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
    ["pipeline.load_player_season_fotmob"],  # current domestic season only, straight into
                                       # player_season_stats (Understat itself has zero 2026/27 data --
                                       # see [[player-total-stats-fotmob]]); INSERT OR REPLACE by PK,
                                       # never wiped by a later pipeline.load since that only ever
                                       # touches seasons present in the scraped Understat parquet.
    ["pipeline.load_ucl_fotmob"],     # current UCL season only, into the same ucl_player_stats table
                                       # SofaScore's loader builds (data_source='fotmob' scoped delete
                                       # + insert) -- historical seasons untouched. Must run before
                                       # build_views, which rebuilds the UCL<->player_id crosswalk.
    ["pipeline.rate"],                # player_ratings_v2, rating_weights (the PRIMARY engine --
                                       # datamb-only, config.DATAMB_SEASON, see the 2026-10-03 fix note
                                       # at the top of this file)
    ["pipeline.profile"],             # player_profile_metrics / v_player_profile -- the table that
                                       # actually bridges player_ratings_v2 to a player_id for the
                                       # profile page's rating/rank/percentile. Was NEVER in this list
                                       # before (only ever ran via the full pipeline.run_pipeline,
                                       # which nothing here calls) -- so it had gone stale independently
                                       # of player_ratings_v2 itself. Added so it can't happen again.
    ["pipeline.build_views"],         # v_stats_* views + ucl_understat_xwalk (crosswalk picks up
                                       # whatever load_ucl_fotmob just wrote)
    ["pipeline.rate_combined"],       # player_ratings_combined -- league AND UCL scope both refresh now
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
