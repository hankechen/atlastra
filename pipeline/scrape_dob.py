"""
Birth dates for the historical player panel.

The trajectory model (`ml/train_trajectory.py`) needs a player's age *in a given
season*, across twelve seasons. `player_bio` only carries a birth date for the
~2.1k players in the current FotMob squad pull, which covers 2025/26 but leaves
most of the 2014-2020 panel ageless -- and `player_bio.fotmob_age` is a snapshot
of age TODAY, so it cannot be walked back to a historical season either.

FotMob's `/api/data/playerData` returns `birthDate` at the top level for any
player id, and `player_enrichment` already resolved a `fotmob_player_id` for
~5k Understat players (2020/21 onward -- which, because careers overlap, reaches
most of the players active in the earlier seasons too).

~5k signed requests, so this is **resumable**: results are checkpointed to
data/raw/fotmob/player_dob.parquet and a re-run only fetches ids not cached.

Run:  python -m pipeline.scrape_dob
Then: python -m pipeline.load_dob
"""
import sys
import time
import warnings

import duckdb
import pandas as pd

try:
    from config import RAW_DIR, DB_PATH
    from pipeline.fotmob_auth import FotmobAuth
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import RAW_DIR, DB_PATH
    from pipeline.fotmob_auth import FotmobAuth

warnings.filterwarnings("ignore")

FOTMOB_RAW = RAW_DIR.parent / "fotmob"
DOB_PARQUET = FOTMOB_RAW / "player_dob.parquet"

RATE_LIMIT_SEC = 0.3
CHECKPOINT_EVERY = 100


def _ids_to_fetch() -> list[int]:
    """Every FotMob player id the enrichment ever resolved, across all seasons."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    ids = con.execute("""
        SELECT DISTINCT CAST(fotmob_player_id AS BIGINT) AS fid
        FROM player_enrichment
        WHERE fotmob_player_id IS NOT NULL
        ORDER BY fid
    """).df()["fid"].tolist()
    con.close()
    return [int(i) for i in ids]


def _birth_date(player_data: dict):
    """`birthDate` is {'utcTime': '2000-07-21T00:00:00.000Z', ...} -> date."""
    bd = player_data.get("birthDate")
    if isinstance(bd, dict):
        bd = bd.get("utcTime")
    if not bd:
        return None
    try:
        return pd.to_datetime(bd, utc=True, errors="coerce").date()
    except Exception:
        return None


def scrape() -> None:
    FOTMOB_RAW.mkdir(parents=True, exist_ok=True)
    ids = _ids_to_fetch()

    cached: dict[int, object] = {}
    if DOB_PARQUET.exists():
        prev = pd.read_parquet(DOB_PARQUET)
        cached = {int(r.fotmob_player_id): r.date_of_birth for r in prev.itertuples()}
    todo = [i for i in ids if i not in cached]
    print(f"{len(ids)} fotmob ids; {len(cached)} cached; fetching {len(todo)} ...", flush=True)
    if not todo:
        print("nothing to do.", flush=True)
        return

    auth = FotmobAuth()
    rows = [{"fotmob_player_id": k, "date_of_birth": v} for k, v in cached.items()]
    for n, pid in enumerate(todo, 1):
        try:
            dob = _birth_date(auth.get(f"/api/data/playerData?id={pid}"))
        except Exception as e:
            print(f"  id={pid} failed: {repr(e)[:60]}", flush=True)
            dob = None
        rows.append({"fotmob_player_id": int(pid), "date_of_birth": dob})
        if n % CHECKPOINT_EVERY == 0:
            pd.DataFrame(rows).to_parquet(DOB_PARQUET)
            print(f"  ... {n}/{len(todo)} (checkpointed)", flush=True)
        time.sleep(RATE_LIMIT_SEC)

    df = pd.DataFrame(rows)
    df.to_parquet(DOB_PARQUET)
    have = df["date_of_birth"].notna().sum()
    print(f"\nSaved {len(df)} rows ({have} with a birth date) -> {DOB_PARQUET}", flush=True)


if __name__ == "__main__":
    scrape()
