"""
Duels enrichment for 2025/26.

FotMob's season "deep stats" CDN does NOT expose duels, but each player's
`/api/data/playerData` payload carries them under `firstSeasonStats`
(verified to be the player's current domestic season -- 2025/26). So we fetch
duels per player, for the FotMob player ids already resolved by
pipeline.scrape_enrich.

This is ~2.3k signed requests, so the scraper is **resumable**: results are
checkpointed to data/raw/fotmob/player_duels_<season>.parquet and a re-run only
fetches ids not already cached.

Run (after scrape_enrich):
    python -m pipeline.scrape_duels
"""
import sys
import time
import warnings

import pandas as pd

try:
    from config import RAW_DIR, FOCUS_SEASON
    from pipeline.fotmob_auth import FotmobAuth
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import RAW_DIR, FOCUS_SEASON
    from pipeline.fotmob_auth import FotmobAuth

warnings.filterwarnings("ignore")

FOTMOB_RAW = RAW_DIR.parent / "fotmob"
ENRICH_PARQUET = FOTMOB_RAW / f"player_enrichment_{FOCUS_SEASON}.parquet"
DUELS_PARQUET = FOTMOB_RAW / f"player_duels_{FOCUS_SEASON}.parquet"

RATE_LIMIT_SEC = 0.3
CHECKPOINT_EVERY = 100


def _extract_duels(player_data: dict) -> dict:
    """Pull duel_won (total) and duel_won_percent from firstSeasonStats."""
    out = {"duels_won": None, "duels_won_pct": None}
    fss = player_data.get("firstSeasonStats") or {}
    section = (fss.get("statsSection") or {}).get("items") or []
    for group in section:
        for item in group.get("items", []):
            tid = item.get("localizedTitleId")
            if tid == "duel_won":
                out["duels_won"] = _num(item.get("statValue"))
            elif tid == "duel_won_percent":
                out["duels_won_pct"] = _num(item.get("statValue"))
    return out


def _num(v):
    try:
        f = float(str(v).replace(",", ""))
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def scrape() -> None:
    if not ENRICH_PARQUET.exists():
        print(f"No {ENRICH_PARQUET} -- run `python -m pipeline.scrape_enrich` first.")
        return

    ids = (
        pd.read_parquet(ENRICH_PARQUET)[["fotmob_player_id"]]
        .dropna().astype({"fotmob_player_id": "int64"})
        .drop_duplicates()["fotmob_player_id"].tolist()
    )

    cached = {}
    if DUELS_PARQUET.exists():
        prev = pd.read_parquet(DUELS_PARQUET)
        cached = {int(r.fotmob_player_id): r for r in prev.itertuples()}
    todo = [i for i in ids if i not in cached]
    print(f"{len(ids)} players; {len(cached)} cached; fetching {len(todo)} ...")

    auth = FotmobAuth()
    rows = [
        {"fotmob_player_id": int(r.fotmob_player_id),
         "duels_won": r.duels_won, "duels_won_pct": r.duels_won_pct}
        for r in cached.values()
    ]
    for n, pid in enumerate(todo, 1):
        try:
            data = auth.get(f"/api/data/playerData?id={pid}")
            rec = _extract_duels(data)
        except Exception as e:
            print(f"  id={pid} failed: {repr(e)[:60]}")
            rec = {"duels_won": None, "duels_won_pct": None}
        rows.append({"fotmob_player_id": int(pid), **rec})
        if n % CHECKPOINT_EVERY == 0:
            pd.DataFrame(rows).to_parquet(DUELS_PARQUET)
            print(f"  ... {n}/{len(todo)} (checkpointed)")
        time.sleep(RATE_LIMIT_SEC)

    df = pd.DataFrame(rows)
    df["season"] = FOCUS_SEASON
    df.to_parquet(DUELS_PARQUET)
    have = df["duels_won"].notna().sum()
    print(f"\nSaved {len(df)} rows ({have} with duels) -> {DUELS_PARQUET}")


if __name__ == "__main__":
    scrape()
