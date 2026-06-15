"""Populate team_name_map and (re)build the standings_all bridge view.

Folds football-data.co.uk historical club spellings onto their Understat
spelling so a single club is one identity across the 08/09-13/14 <-> 14/15-
era boundary. Clubs that played only in the historical era (and so have no
Understat spelling to match) are intentionally NOT mapped -- they keep their
own name. Verified by diffing distinct names in both standings tables.

    python -m pipeline.load_name_map
"""
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config import DB_PATH, SCHEMA_PATH  # noqa: E402

# football-data spelling -> Understat (canonical) spelling.
RENAMES = {
    "Ath Bilbao": "Athletic Club",
    "Ath Madrid": "Atletico Madrid",
    "Bastia": "SC Bastia",
    "Betis": "Real Betis",
    "Bielefeld": "Arminia Bielefeld",
    "Celta": "Celta Vigo",
    "Dortmund": "Borussia Dortmund",
    "Ein Frankfurt": "Eintracht Frankfurt",
    "Espanol": "Espanyol",
    "FC Koln": "FC Cologne",
    "Fortuna Dusseldorf": "Fortuna Duesseldorf",
    "Greuther Furth": "Greuther Fuerth",
    "Hamburg": "Hamburger SV",
    "Hannover": "Hannover 96",
    "Hertha": "Hertha Berlin",
    "La Coruna": "Deportivo La Coruna",
    "Leverkusen": "Bayer Leverkusen",
    "M'gladbach": "Borussia M.Gladbach",
    "Mainz": "Mainz 05",
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Milan": "AC Milan",
    "Newcastle": "Newcastle United",
    "Nurnberg": "Nuernberg",
    "Paris SG": "Paris Saint Germain",
    "QPR": "Queens Park Rangers",
    "Sociedad": "Real Sociedad",
    "Sp Gijon": "Sporting Gijon",
    "St Etienne": "Saint-Etienne",
    "St Pauli": "St. Pauli",
    "Stuttgart": "VfB Stuttgart",
    "Valladolid": "Real Valladolid",
    "Vallecano": "Rayo Vallecano",
    "West Brom": "West Bromwich Albion",
    "Wolves": "Wolverhampton Wanderers",
}


def _stmt(ddl: str, marker: str, end: str) -> str:
    start = ddl.index(marker)
    return ddl[start: ddl.index(end, start) + len(end)]


def load() -> None:
    con = duckdb.connect(str(DB_PATH))
    ddl = SCHEMA_PATH.read_text()

    con.execute(_stmt(ddl, "CREATE TABLE IF NOT EXISTS team_name_map", ");"))
    con.execute("DELETE FROM team_name_map")
    con.executemany("INSERT INTO team_name_map VALUES (?,?)",
                    list(RENAMES.items()))
    print(f"  team_name_map: {len(RENAMES)} renames")

    # Recreate the view (ends at the JOIN ... team_name_map m ... ; line).
    view = _stmt(ddl, "CREATE OR REPLACE VIEW standings_all",
                 "ON m.raw_name = t.team_name;")
    con.execute(view)

    # Sanity: how many distinct clubs still split across the boundary?
    split = con.execute("""
        SELECT count(*) FROM (
            SELECT team_name FROM standings_all
            GROUP BY team_name
            HAVING count(DISTINCT era) = 2
        )
    """).fetchone()[0]
    print(f"  clubs now spanning both eras: {split}")
    con.close()
    print(f"\nDone -> {DB_PATH}")


if __name__ == "__main__":
    load()
