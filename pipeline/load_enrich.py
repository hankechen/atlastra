"""
Load FotMob enrichment Parquet into `player_enrichment`, resolving each FotMob
player to an Understat `player_id` by fuzzy (accent-folded) name matching,
scoped per league.

Safe to run even if no enrichment file exists yet -- it no-ops with a message.
Run after pipeline.scrape_enrich (and after the main load):
    python -m pipeline.load_enrich
"""
import sys
import unicodedata
import warnings

import duckdb
import pandas as pd
from rapidfuzz import fuzz, process

try:
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON

warnings.filterwarnings("ignore")

FOTMOB_RAW = RAW_DIR.parent / "fotmob"
MATCH_THRESHOLD = 80  # min fuzzy score to accept a name match

# columns in the parquet that map 1:1 into player_enrichment
ENRICH_COLS = [
    "big_chances_created", "big_chances_missed", "chances_created",
    "dribbles_completed", "tackles", "interceptions", "recoveries",
    "passes_completed", "duels_won", "dribbles_per90", "dribble_success_pct",
    "tackles_per90", "interceptions_per90", "recoveries_per90",
    "pass_accuracy_pct", "duels_won_pct", "fotmob_rating",
    "minutes_played", "matches_played",
]
INT_COLS = {"big_chances_created", "big_chances_missed", "chances_created",
            "dribbles_completed", "tackles", "interceptions", "recoveries",
            "passes_completed", "duels_won", "minutes_played", "matches_played"}


def _norm(name: str) -> str:
    """Lowercase + strip accents so 'Mbappé' and 'Mbappe' match."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def _val(v, col):
    if pd.isna(v):
        return None
    return int(round(float(v))) if col in INT_COLS else float(v)


def _load_enrichment_frames() -> pd.DataFrame:
    """Concatenate every per-season player_enrichment_<season>.parquet."""
    files = sorted(FOTMOB_RAW.glob("player_enrichment_*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    fot = pd.concat(frames, ignore_index=True)
    # back-compat: older focus-only files had no season column
    if "season" not in fot.columns:
        fot["season"] = FOCUS_SEASON
    return fot


def _load_duels() -> pd.DataFrame:
    """Concatenate per-season duels files; tag season if absent."""
    files = sorted(FOTMOB_RAW.glob("player_duels_*.parquet"))
    frames = []
    for f in files:
        d = pd.read_parquet(f)
        if "season" not in d.columns:
            # filename is player_duels_<season>.parquet
            d["season"] = f.stem.rsplit("_", 1)[-1]
        frames.append(d)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_enrich() -> None:
    fot = _load_enrichment_frames()
    if fot.empty:
        print("No enrichment files found -- run `python -m pipeline.scrape_enrich` first. Skipping.")
        return

    duels = _load_duels()
    if not duels.empty:
        fot = fot.merge(duels, on=["fotmob_player_id", "season"], how="left")
        print(f"merged duels for {duels['duels_won'].notna().sum()} player-seasons")
    else:
        fot["duels_won"] = None
        fot["duels_won_pct"] = None
        print("no duels parquet yet -- run `python -m pipeline.scrape_duels` (duels will be NULL)")

    con = duckdb.connect(str(DB_PATH))

    # Understat players per (league, season), for scoped fuzzy matching.
    us = con.execute(
        """
        SELECT DISTINCT ps.player_id, p.player_name, ps.league_key, ps.season
        FROM player_season_stats ps JOIN players p USING(player_id)
        """
    ).df()
    pools = {
        key: ([int(r.player_id) for r in g.itertuples()],
              [_norm(r.player_name) for r in g.itertuples()])
        for key, g in us.groupby(["league_key", "season"])
    }

    rows, matched, unmatched = [], 0, 0
    for (league_key, season), grp in fot.groupby(["league_key", "season"]):
        ids, names = pools.get((league_key, season), ([], []))
        used = set()
        for r in grp.itertuples():
            target = _norm(getattr(r, "player_name", ""))
            if not target or not names:
                unmatched += 1
                continue
            best = process.extractOne(target, names, scorer=fuzz.token_sort_ratio)
            if not best or best[1] < MATCH_THRESHOLD:
                unmatched += 1
                continue
            player_id = ids[best[2]]
            if player_id in used:   # avoid two FotMob rows mapping to one player
                continue
            used.add(player_id)
            matched += 1
            vals = [_val(getattr(r, c, None), c) for c in ENRICH_COLS]
            rows.append((
                player_id, league_key, season,
                int(r.fotmob_player_id) if pd.notna(r.fotmob_player_id) else None,
                float(best[1]), *vals,
            ))

    con.execute("DELETE FROM player_enrichment WHERE source='fotmob'")
    if rows:
        cols = "player_id, league_key, season, fotmob_player_id, match_confidence, " + ", ".join(ENRICH_COLS)
        ph = ",".join(["?"] * (5 + len(ENRICH_COLS)))
        con.executemany(
            f"INSERT OR REPLACE INTO player_enrichment ({cols}, source) VALUES ({ph}, 'fotmob')",
            rows,
        )
    con.close()
    print(f"player_enrichment: matched {matched}, unmatched {unmatched} "
          f"(threshold {MATCH_THRESHOLD}, {len(fot)} FotMob rows across {fot['season'].nunique()} seasons).")


if __name__ == "__main__":
    load_enrich()
