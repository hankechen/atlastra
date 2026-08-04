"""
Load SofaScore goalkeeper stats -> `gk_season_stats`, keyed by Understat player_id.

The rating engine's GK vector wants four numbers per keeper-season: save %, goals
conceded per 90, clean sheets and saves per 90. `pipeline/scrape_sofa_gk.py`
fetches them for every season; this matches each SofaScore keeper to the
Understat player the rest of the warehouse is keyed on.

Matching is a fuzzy name match, but a far easier one than the general enrichment
case: the candidate pool is only the goalkeepers Understat recorded in that same
league and season -- roughly forty names, not five hundred -- so a keeper matches
his own league-mates or nothing. The threshold is deliberately high and an
Understat id is used at most once per league-season.

Run:  python -m pipeline.load_sofa_gk
"""
import sys
import unicodedata
import warnings

import duckdb
import pandas as pd
from rapidfuzz import fuzz, process

try:
    from config import RAW_DIR, DB_PATH, ALL_SEASONS
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import RAW_DIR, DB_PATH, ALL_SEASONS

warnings.filterwarnings("ignore")

SOFA_RAW = RAW_DIR.parent / "sofascore"
MATCH_THRESHOLD = 82

DDL = """CREATE TABLE IF NOT EXISTS gk_season_stats(
    player_id BIGINT, season VARCHAR, league_key VARCHAR,
    minutes DOUBLE, appearances INTEGER, saves DOUBLE, goals_conceded DOUBLE,
    clean_sheets DOUBLE, save_percentage_pct DOUBLE,
    saves_per_90 DOUBLE, goals_conceded_per_90 DOUBLE,
    sofascore_player_id BIGINT, match_confidence DOUBLE)"""


def _norm(name: str) -> str:
    """Lowercase + strip accents so 'Ter Stegen' and 'ter Stegen' match."""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def load() -> int:
    files = sorted(SOFA_RAW.glob("domestic_gk_*.parquet"))
    if not files:
        print("No domestic_gk_*.parquet -- run `python -m pipeline.scrape_sofa_gk` first.")
        return 0
    sofa = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    con = duckdb.connect(str(DB_PATH))
    con.execute(DDL)
    con.execute("DELETE FROM gk_season_stats")

    # the candidate pool: Understat keepers, per league-season
    us = con.execute("""
        SELECT DISTINCT ps.player_id, p.player_name, ps.league_key, ps.season
        FROM player_season_stats ps JOIN players p USING(player_id)
        WHERE ps.position_group = 'GK'
    """).df()
    pools = {
        key: ([int(r.player_id) for r in g.itertuples()],
              [_norm(r.player_name) for r in g.itertuples()])
        for key, g in us.groupby(["league_key", "season"])
    }

    rows, matched, unmatched = [], 0, 0
    for (league_key, season), grp in sofa.groupby(["league_key", "season"]):
        ids, names = pools.get((league_key, season), ([], []))
        used = set()
        # best-first: strongest name matches claim their player before weaker ones,
        # so a near-duplicate surname cannot steal an id from its rightful owner
        scored = []
        for r in grp.itertuples():
            target = _norm(getattr(r, "player_name", ""))
            if not target or not names:
                unmatched += 1
                continue
            best = process.extractOne(target, names, scorer=fuzz.token_sort_ratio)
            if best and best[1] >= MATCH_THRESHOLD:
                scored.append((best[1], best[2], r))
            else:
                unmatched += 1
        for score, idx, r in sorted(scored, key=lambda t: -t[0]):
            pid = ids[idx]
            if pid in used:
                unmatched += 1
                continue
            used.add(pid)
            matched += 1
            mins = float(r.minutes_played or 0)
            saves = float(r.saves or 0)
            conceded = float(r.goals_conceded or 0)
            faced = saves + conceded
            rows.append((
                pid, season, league_key, mins, int(r.appearances or 0),
                saves, conceded, float(r.clean_sheets or 0),
                round(saves / faced * 100, 2) if faced else 0.0,
                round(saves / mins * 90, 4) if mins else 0.0,
                round(conceded / mins * 90, 4) if mins else 0.0,
                int(r.sofascore_player_id) if pd.notna(r.sofascore_player_id) else None,
                float(score),
            ))

    con.executemany(
        "INSERT INTO gk_season_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("CREATE INDEX IF NOT EXISTS idx_gk ON gk_season_stats(player_id, season)")
    cov = con.execute("""
        SELECT season, count(*) AS keepers, round(avg(match_confidence), 1) AS conf
        FROM gk_season_stats GROUP BY 1 ORDER BY 1""").df()
    con.close()

    pct = matched / max(1, matched + unmatched) * 100
    print(f"[load_sofa_gk] {matched} matched, {unmatched} unmatched ({pct:.1f}%)")
    print(cov.to_string(index=False))
    return matched


if __name__ == "__main__":
    load()
