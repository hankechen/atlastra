"""
Keep computing the primary rating engine for the new season every biweekly
cycle (so the data isn't lost / keeps accumulating as the 600-min pool grows),
but keep the SITE showing last season's primary rating until there's enough
2026/27 data for it to be reliable.

pipeline.rate / pipeline.profile are single-season, DROP-and-recreate tables
(player_ratings_v2, player_profile_metrics, player_radar_metrics,
player_tendencies) -- there's no "season" column to filter by, whatever they
just computed IS what the site reads. So this runs AFTER rate+profile in the
refresh order and does two things:

  1. Snapshots what rate/profile just wrote (the CURRENT season's fresh
     computation) into "_live" tables -- not read by the site yet, just kept
     around and overwritten fresh every cycle, so the moment the user wants to
     cut over, that data is already there and current.
  2. Restores the frozen 2025/26 snapshot (`*_2526_snapshot`, taken 2026-10-04
     -- see [[primary-rating-display-freeze]]) back into the real table names
     the site reads, undoing what rate/profile just overwrote.

To cut over for real later: stop pointing step 2 at the 2526 snapshot (point
it at the current *_live tables instead, or just delete this script from
STEPS and let rate/profile's own output stand).

    python -m tools.freeze_primary_rating
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH
from analytics.queries import connect_retry

TABLES = ["player_ratings_v2", "player_profile_metrics",
          "player_radar_metrics", "player_tendencies"]
FROZEN_SUFFIX = "_2526_snapshot"
LIVE_SUFFIX = "_live"


def freeze() -> None:
    con = connect_retry(DB_PATH, read_only=False)
    try:
        for t in TABLES:
            frozen = f"{t}{FROZEN_SUFFIX}"
            exists = con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                [frozen]).fetchone()[0]
            if not exists:
                print(f"  ! no {frozen} -- skipping {t} (nothing to restore)")
                continue
            # 1. stash this cycle's fresh new-season computation, so it's never lost
            live = f"{t}{LIVE_SUFFIX}"
            con.execute(f"DROP TABLE IF EXISTS {live}")
            con.execute(f"CREATE TABLE {live} AS SELECT * FROM {t}")
            # 2. restore the frozen season back onto the table the site reads
            con.execute(f"DROP TABLE IF EXISTS {t}")
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM {frozen}")
            n_live = con.execute(f"SELECT count(*) FROM {live}").fetchone()[0]
            n_disp = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"  {t}: stashed {n_live} fresh rows -> {live}, "
                  f"restored {n_disp} rows from {frozen} for display")
        # v_player_profile / v_player_profile_full are VIEWs over
        # player_profile_metrics (see pipeline.profile) -- they read correctly
        # automatically now that the underlying table is restored, no action needed.
    finally:
        con.close()
    print("freeze_primary_rating: display reverted to the 2025/26 snapshot; "
          "new-season computation preserved in *_live tables.")


if __name__ == "__main__":
    freeze()
