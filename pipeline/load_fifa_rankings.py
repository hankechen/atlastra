"""Snapshot the FIFA/Coca-Cola Men's World Ranking into `fifa_rankings`.

SofaScore exposes it at /rankings/type/2 (211 teams). We key by the SofaScore team
NAME — the same canonical name that appears in `live_matches` and `wc_matches` — so
national-team match displays can show each side's world ranking by a simple name
join. FIFA refresh the ranking only a handful of times a year, so run this
occasionally (it's also cheap to re-run alongside load_wc).

Run:  python -m pipeline.load_fifa_rankings
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import tls_requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, SOFASCORE_BASE  # noqa: E402
import duckdb  # noqa: E402

FIFA_RANKING_TYPE = 2     # SofaScore ranking type id for the FIFA men's ranking

DDL = """
CREATE TABLE IF NOT EXISTS fifa_rankings (
    team_name        VARCHAR PRIMARY KEY,   -- SofaScore canonical name (join key)
    ranking          INTEGER,               -- world ranking position (1 = best)
    points           DOUBLE,
    previous_ranking INTEGER
);
"""


def _get(path: str) -> dict:
    for attempt in range(3):
        try:
            r = tls_requests.get(f"{SOFASCORE_BASE}{path}", timeout=25)
            if r.status_code == 200:
                return r.json()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5 * (attempt + 1))
    return {}


def main() -> None:
    rows = []
    for r in (_get(f"/rankings/type/{FIFA_RANKING_TYPE}") or {}).get("rankings") or []:
        name = (r.get("team") or {}).get("name") or r.get("rowName")
        if name and r.get("ranking") is not None:
            rows.append((name, r.get("ranking"), r.get("points"), r.get("previousRanking")))

    con = duckdb.connect(str(DB_PATH))
    con.execute(DDL)
    con.execute("DELETE FROM fifa_rankings")
    con.executemany("INSERT OR REPLACE INTO fifa_rankings VALUES (?,?,?,?)", rows)
    con.close()
    print(f"fifa_rankings: {len(rows)} national teams")


if __name__ == "__main__":
    main()
