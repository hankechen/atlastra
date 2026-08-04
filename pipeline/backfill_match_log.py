"""
Backfill the per-player per-match log across every collected season.

`pipeline/load_player_match_stats.py` already does one season; it has only ever
been run for the current one, which is why `player_match_log` holds 2025/26 and
nothing else. That single season is what limits the Big Game Index to "this
year" and leaves the trajectory model with no way to see rest days or the gaps
that mark an injury.

Two phases, deliberately separated:

  1. **warm**  -- pull every match page into soccerdata's on-disk cache. This is
     the slow part (~20k pages) and it holds NO database connection, so the
     warehouse stays readable and writable by the app and by other jobs while it
     runs. Re-running skips whatever is already cached.
  2. **load**  -- run the existing per-season loader. Every page is a cache hit
     by then, so this is quick, and each season is atomic (the loader deletes and
     re-inserts that season, so a re-run is safe).

Doing it the other way round -- one long job holding the write lock across hours
of scraping -- locks the warehouse for the duration.

Run:
    python -m pipeline.backfill_match_log --warm     # slow, no DB lock; run first
    python -m pipeline.backfill_match_log --load     # fast, writes the table
    python -m pipeline.backfill_match_log            # both, in order
    python -m pipeline.backfill_match_log --seasons 1415,1516
"""
import argparse
import sys
import time
import warnings

try:
    from config import LEAGUES, ALL_SEASONS, FOCUS_SEASON
    from pipeline.load_player_match_stats import load
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import LEAGUES, ALL_SEASONS, FOCUS_SEASON
    from pipeline.load_player_match_stats import load

warnings.filterwarnings("ignore")

# Understat tolerates a steady pull and then stops: the first full run cached four
# seasons and failed the next 29 league-seasons on connection errors. These are the
# knobs that make a re-run get further rather than faster.
MAX_ATTEMPTS = 3
BACKOFF_SEC = 30       # doubled per retry
COOLDOWN_SEC = 5       # between league-seasons, throttled or not


def warm(seasons: list[str], attempts: int = MAX_ATTEMPTS) -> tuple[int, int]:
    """Populate soccerdata's cache. No warehouse connection is opened here.

    Understat throttles a sustained pull: the first run of this got four seasons
    in and then failed 29 league-seasons in a row on connection errors. So each
    league-season is retried with a widening pause, and a run that still cannot
    get through is reported as such rather than leaving a silent hole. The cache
    makes re-running cheap — already-fetched pages are not fetched again.
    """
    import soccerdata as sd

    t0, ok, bad = time.time(), 0, 0
    for i, season in enumerate(seasons, 1):
        for lg in LEAGUES:
            for attempt in range(1, attempts + 1):
                try:
                    reader = sd.Understat(leagues=[lg], seasons=[season])
                    reader.read_schedule()
                    n = len(reader.read_player_match_stats())
                    ok += 1
                    print(f"[warm] {season} {lg}: {n} player-match rows cached "
                          f"({i}/{len(seasons)} seasons, {time.time() - t0:.0f}s)", flush=True)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == attempts:
                        bad += 1
                        print(f"[warm] {season} {lg}: FAILED after {attempts} attempts "
                              f"({type(e).__name__}: {str(e)[:70]})", flush=True)
                    else:
                        back = BACKOFF_SEC * (2 ** (attempt - 1))
                        print(f"[warm] {season} {lg}: attempt {attempt} failed, "
                              f"retrying in {back}s", flush=True)
                        time.sleep(back)
            time.sleep(COOLDOWN_SEC)
    print(f"[warm] {ok} cached, {bad} failed, in {time.time() - t0:.0f}s", flush=True)
    if bad:
        print("[warm] re-run to retry the failures — cached pages are skipped", flush=True)
    return ok, bad


def load_all(seasons: list[str]) -> int:
    """Load only the seasons that are COMPLETE.

    `load()` deletes a season and re-inserts whatever leagues it manages to read,
    skipping any that fail. That is the right behaviour for a single-season run
    you are watching, and quietly corrupting for a bulk backfill: Understat
    throttles, one league of five comes back empty, and the season lands in the
    warehouse looking finished while missing a fifth of its matches. Nothing
    downstream — the Big Game Index splits, per-90s from the log — would show it.

    So each season is verified after loading, and a season that did not bring all
    five leagues is rolled back rather than left half-there.
    """
    import duckdb
    from config import DB_PATH

    total, done, skipped = 0, [], []
    for season in seasons:
        print(f"[load] {season} ...", flush=True)
        n = load(season)
        con = duckdb.connect(str(DB_PATH))
        got = [r[0] for r in con.execute(
            "SELECT DISTINCT league_key FROM player_match_log WHERE season = ?",
            [season]).fetchall()]
        if len(got) < len(LEAGUES):
            missing = sorted(set(LEAGUES) - set(got))
            con.execute("DELETE FROM player_match_log WHERE season = ?", [season])
            con.close()
            skipped.append(season)
            print(f"[load] {season}: ROLLED BACK — only {len(got)}/{len(LEAGUES)} leagues "
                  f"(missing {', '.join(missing)}). Re-run --warm for this season.", flush=True)
            continue
        con.close()
        total += n
        done.append(season)
        print(f"[load] {season}: {n} rows, all {len(LEAGUES)} leagues", flush=True)

    print(f"\n[load] loaded {len(done)} complete season(s): {', '.join(done) or '—'}", flush=True)
    if skipped:
        print(f"[load] INCOMPLETE, not loaded: {', '.join(skipped)}", flush=True)
    print(f"[load] {total} player-match rows", flush=True)
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warm", action="store_true", help="cache match pages only (no DB lock)")
    ap.add_argument("--load", action="store_true", help="write cached seasons into the warehouse")
    ap.add_argument("--seasons", help="comma-separated season codes (default: all but the current)")
    a = ap.parse_args()

    # 2025/26 is already loaded and is still being played, so it is refreshed by
    # the normal pipeline rather than backfilled here
    picked = (a.seasons.split(",") if a.seasons
              else [s for s in ALL_SEASONS if s != FOCUS_SEASON])
    bad = [s for s in picked if s not in ALL_SEASONS]
    if bad:
        raise SystemExit(f"unknown season(s): {', '.join(bad)}")

    do_warm, do_load = (a.warm, a.load) if (a.warm or a.load) else (True, True)
    print(f"seasons: {', '.join(picked)}", flush=True)
    if do_warm:
        warm(picked)
    if do_load:
        load_all(picked)
