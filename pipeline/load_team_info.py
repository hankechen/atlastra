"""
Team manager + venue + squad from FotMob -> tables `team_meta` / `team_squad_fotmob`
(use case 7).

Understat (our domestic spine) has no manager/stadium and its squad snapshot is
only as fresh as the last manual scrape, but FotMob's team endpoint has all of
it live: overview.coachHistory[-1] is the current manager, overview.venue carries
the stadium name/city/capacity/opened/surface, and `squad` is today's actual
roster (transfers included). We already store each club's FotMob id in
`team_logos` (see load_team_logos), so we hit /api/data/teams?id=<fotmob_id>
once per linked club (~96 calls) and pull both out of that single response.

Run after load_team_logos:  python -m pipeline.load_team_info
"""
import sys
import time

try:
    from config import DB_PATH
    from analytics.queries import connect_retry
    from pipeline.fotmob_auth import FotmobAuth
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH
    from analytics.queries import connect_retry
    from pipeline.fotmob_auth import FotmobAuth

RATE_LIMIT_SEC = 0.4

# FotMob squad group title -> our position_group convention (skip "coach": that's
# the manager, captured separately in team_meta).
_POS_GROUP = {"keepers": "GK", "defenders": "DEF", "midfielders": "MID", "attackers": "FWD"}


def _extract(d: dict) -> dict:
    ov = d.get("overview", {}) or {}
    ch = ov.get("coachHistory") or []
    manager = ch[-1].get("name") if ch else None          # latest season = current
    ven = (ov.get("venue") or {})
    w = ven.get("widget", {}) or {}
    stat = {k: v for k, v in (ven.get("statPairs") or [])}
    def _int(x):
        try:
            return int(x)
        except (TypeError, ValueError):
            return None
    return {
        "manager": manager,
        "venue": w.get("name"),
        "city": w.get("city"),
        "capacity": _int(stat.get("Capacity")),
        "opened": _int(stat.get("Opened")),
        "surface": stat.get("Surface"),
    }


def _extract_squad(d: dict, team_id: int) -> list:
    """Squad rows for one team from the same /api/data/teams response.
    Note the nesting: d["squad"] is {"squad": [...groups], "isNationalTeam": bool},
    not the group list itself."""
    rows = []
    for grp in ((d.get("squad") or {}).get("squad") or []):
        pg = _POS_GROUP.get(grp.get("title"))
        if not pg:
            continue                                     # skip "coach"
        for m in (grp.get("members") or []):
            fid = m.get("id")
            if fid is None:
                continue
            rows.append((team_id, int(fid), m.get("name"), pg, m.get("shirtNumber"),
                         m.get("age"), m.get("cname"), m.get("goals"), m.get("assists"),
                         m.get("ycards"), m.get("rcards"), bool(m.get("injury"))))
    return rows


def load_team_info() -> None:
    # Read the team list and release the connection immediately -- the ~96
    # throttled FotMob calls below take ~40s, and holding a DuckDB connection
    # open for that long would lock out the server/other refreshers (same
    # reasoning as pipeline/load_live_fotmob.py's fetch/write split). Opened
    # read_only=False (even though this call only reads) because DuckDB
    # refuses a second in-process connection to the same file whose config
    # differs from one already open -- the server's own pool holds it
    # read-write whenever this runs as a background thread.
    con = connect_retry(DB_PATH, read_only=False)
    try:
        teams = con.execute(
            "SELECT team_id, fotmob_team_id, team_name FROM team_logos "
            "WHERE team_id IS NOT NULL AND fotmob_team_id IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    auth = FotmobAuth()
    rows, squad_rows, ok = [], [], 0
    for tid, fid, tname in teams:
        try:
            d = auth.get(f"/api/data/teams?id={int(fid)}")
            m = _extract(d)
        except Exception as e:  # noqa: BLE001
            print(f"  {tname}: failed ({repr(e)[:50]})")
            continue
        rows.append((int(tid), m["manager"], m["venue"], m["city"],
                     m["capacity"], m["opened"], m["surface"]))
        squad_rows.extend(_extract_squad(d, int(tid)))
        ok += 1
        time.sleep(RATE_LIMIT_SEC)

    con = connect_retry(DB_PATH, read_only=False)
    con.execute("DROP TABLE IF EXISTS team_meta")
    con.execute("""CREATE TABLE team_meta
        (team_id BIGINT, manager VARCHAR, venue VARCHAR, city VARCHAR,
         capacity BIGINT, opened BIGINT, surface VARCHAR)""")
    if rows:
        con.executemany("INSERT INTO team_meta VALUES (?,?,?,?,?,?,?)", rows)

    con.execute("DROP TABLE IF EXISTS team_squad_fotmob")
    con.execute("""CREATE TABLE team_squad_fotmob
        (team_id BIGINT, fotmob_player_id BIGINT, player_name VARCHAR,
         position_group VARCHAR, shirt_number INTEGER, age INTEGER,
         nationality VARCHAR, goals INTEGER, assists INTEGER,
         yellow_cards INTEGER, red_cards INTEGER, injured BOOLEAN)""")
    if squad_rows:
        con.executemany(
            "INSERT INTO team_squad_fotmob VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", squad_rows)

    n_mgr = sum(1 for r in rows if r[1])
    n_ven = sum(1 for r in rows if r[2])
    con.close()
    print(f"team_meta: {ok} teams ({n_mgr} with manager, {n_ven} with venue); "
          f"team_squad_fotmob: {len(squad_rows)} players.")


if __name__ == "__main__":
    load_team_info()
