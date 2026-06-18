"""
FotMob detailed-position scraper -- the one thing datamb lacks: left/right.

datamb only has coarse buckets (no LW/RW or LB/RB). FotMob's team-squad endpoint
exposes each player's `positionIds` (a detailed position grid), so we pull every
Top-5 squad and keep (fotmob_player_id, positionIds). One request per team
(~96 teams), team ids read from each league's table.

FotMob positionId grid (verified June 2026 vs known players):
  GK 11 | CB 34/36 | RB 32 RWB 33 | LB 38 LWB 37 | DM 76 | CM 64.. | AM 85/103
  RW 87 (also 83 right-inside) | LW 107 (also 105 left-inside) | ST 104/115

Output: data/raw/fotmob/positions_<season>.parquet
Run:    python -m pipeline.scrape_fotmob_positions
"""
import sys
import time
import warnings

import pandas as pd

try:
    from config import FOTMOB_LEAGUE_IDS, RAW_DIR, FOCUS_SEASON, fotmob_season
    from pipeline.fotmob_auth import FotmobAuth
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import FOTMOB_LEAGUE_IDS, RAW_DIR, FOCUS_SEASON, fotmob_season
    from pipeline.fotmob_auth import FotmobAuth

warnings.filterwarnings("ignore")

FOTMOB_RAW = RAW_DIR.parent / "fotmob"
RATE_LIMIT_SEC = 0.4


def _team_ids(auth: FotmobAuth, league_id: int, season: str) -> dict:
    yr = fotmob_season(season).replace("/", "%2F")
    lg = auth.get(f"/api/data/leagues?id={league_id}&season={yr}")
    table = lg["table"][0]["data"]["table"]["all"]
    return {t["id"]: t["name"] for t in table}


def scrape(season: str = FOCUS_SEASON) -> None:
    auth = FotmobAuth()
    rows = []
    for lk, lid in FOTMOB_LEAGUE_IDS.items():
        teams = _team_ids(auth, lid, season)
        for tid, tname in teams.items():
            try:
                d = auth.get(f"/api/data/teams?id={tid}")
            except Exception as e:  # noqa: BLE001
                print(f"  {tname}: squad failed ({repr(e)[:50]})")
                continue
            for grp in d["squad"]["squad"]:
                if grp.get("title") == "coach":
                    continue
                for m in grp.get("members", []):
                    pid = m.get("id")
                    if pid is None:
                        continue
                    # positionIds is already a comma-string (e.g. "83,85"); a list
                    # in some payloads -- normalise to a comma-string either way.
                    pids = m.get("positionIds")
                    if isinstance(pids, (list, tuple)):
                        pids = ",".join(str(x) for x in pids)
                    # positionIdsDesc is FotMob's own text, primary-first (e.g.
                    # "ST,RW,CAM") -- authoritative, unlike parsing the numeric ids.
                    rows.append((int(pid), m.get("name"), str(pids or ""),
                                 m.get("positionIdsDesc"),
                                 m.get("cname"), m.get("ccode"), m.get("age"),
                                 m.get("dateOfBirth"), lk, season))
            time.sleep(RATE_LIMIT_SEC)
        print(f"  {lk}: {len(teams)} squads")
    df = pd.DataFrame(rows, columns=["fotmob_player_id", "player_name", "position_ids",
                                     "position_ids_desc", "nationality", "country_code",
                                     "age", "date_of_birth", "league_key", "season"])
    df = df.drop_duplicates("fotmob_player_id")
    FOTMOB_RAW.mkdir(parents=True, exist_ok=True)
    path = FOTMOB_RAW / f"positions_{season}.parquet"
    df.to_parquet(path, index=False)
    print(f"fotmob positions: {len(df)} players -> {path}")


if __name__ == "__main__":
    scrape()
