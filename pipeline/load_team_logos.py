"""
Team crest IDs from FotMob -> table `team_logos`.

FotMob serves every club crest at a stable URL keyed by its team id:
    https://images.fotmob.com/image_resources/logo/teamlogo/<fotmob_team_id>.png
We already hit the league-table endpoint in scrape_fotmob_positions to discover
team ids; here we persist them. Each league's table gives {fotmob_id: name}; we
fuzzy-match that name to our canonical `teams` row (same league) so the crest is
keyed by our team_id. Player photos need no scrape -- player_enrichment already
carries fotmob_player_id (playerimages/<id>.png).

Run after the teams table exists (any time):  python -m pipeline.load_team_logos
"""
import sys
import unicodedata

import duckdb
from rapidfuzz import fuzz, process

try:
    from config import DB_PATH, FOTMOB_LEAGUE_IDS, FOCUS_SEASON, fotmob_season
    from pipeline.fotmob_auth import FotmobAuth
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOTMOB_LEAGUE_IDS, FOCUS_SEASON, fotmob_season
    from pipeline.fotmob_auth import FotmobAuth

MATCH_THRESHOLD = 78


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    # fold all punctuation (hyphens, &, .) to spaces so "Paris Saint-Germain"
    # tokenises the same as "Paris Saint Germain"
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s)
    for junk in (" fc", " cf", " afc", " calcio", " 1899"):
        s = s.replace(junk, " ")
    return " ".join(s.split())


def _team_table(auth: FotmobAuth, league_id: int, season: str) -> dict:
    yr = fotmob_season(season).replace("/", "%2F")
    lg = auth.get(f"/api/data/leagues?id={league_id}&season={yr}")
    return {t["id"]: t["name"] for t in lg["table"][0]["data"]["table"]["all"]}


def load_team_logos(season: str = FOCUS_SEASON) -> None:
    con = duckdb.connect(str(DB_PATH))
    auth = FotmobAuth()
    rows = []
    for lk, lid in FOTMOB_LEAGUE_IDS.items():
        fm = _team_table(auth, lid, season)                       # {fotmob_id: name}
        ours = con.execute(
            "SELECT team_id, team_name FROM teams WHERE league_key = ?", [lk]).fetchall()
        norm_to_team = {_norm(n): (tid, n) for tid, n in ours}
        keys = list(norm_to_team)
        # GLOBAL best assignment: token_set handles subset names (Brighton in
        # "Brighton & Hove Albion"), but ties on token_set (Paris FC vs Paris
        # Saint-Germain both score 100 against each other once "FC" is stripped)
        # are broken by token_sort, which penalises length mismatch -> the exact
        # pair wins. Score = token_set*1000 + token_sort, claimed greedily by
        # descending score so each club takes its true counterpart, not a swap.
        cands = []
        for fid, fname in fm.items():
            fn = _norm(fname)
            for k in keys:
                setr = fuzz.token_set_ratio(fn, k)
                if setr >= MATCH_THRESHOLD:
                    cands.append((setr * 1000 + fuzz.token_sort_ratio(fn, k),
                                  setr, int(fid), fname, k))
        cands.sort(key=lambda c: -c[0])
        used_fm, used_k, matched = set(), set(), 0
        for _, setr, fid, fname, k in cands:
            if fid in used_fm or k in used_k:
                continue
            used_fm.add(fid); used_k.add(k)
            tid, tname = norm_to_team[k]
            matched += 1
            rows.append((tid, tname, fid, fname, lk, float(setr)))
        for fid, fname in fm.items():               # unmatched FotMob clubs
            if int(fid) not in used_fm:
                rows.append((None, None, int(fid), fname, lk, 0.0))
        print(f"  {lk}: matched {matched}/{len(fm)} crests")

    con.execute("DROP TABLE IF EXISTS team_logos")
    con.execute("""CREATE TABLE team_logos
        (team_id BIGINT, team_name VARCHAR, fotmob_team_id BIGINT,
         fotmob_name VARCHAR, league_key VARCHAR, match_confidence DOUBLE)""")
    con.executemany("INSERT INTO team_logos VALUES (?,?,?,?,?,?)", rows)
    n = con.execute("SELECT COUNT(*) FROM team_logos WHERE team_id IS NOT NULL").fetchone()[0]
    con.close()
    print(f"team_logos: {n} crests linked to a team_id "
          f"({len(rows)} FotMob teams seen).")


if __name__ == "__main__":
    load_team_logos()
