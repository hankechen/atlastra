"""
Team manager + venue from FotMob -> table `team_meta` (use case 7).

Understat (our domestic spine) has no manager/stadium, but FotMob's team endpoint
does: overview.coachHistory[-1] is the current manager, overview.venue carries the
stadium name/city/capacity/opened/surface. We already store each club's FotMob id
in `team_logos` (see load_team_logos), so we hit /api/data/teams?id=<fotmob_id>
once per linked club (~96 calls) and key the result by our team_id.

Run after load_team_logos:  python -m pipeline.load_team_info
"""
import sys
import time

import duckdb

try:
    from config import DB_PATH
    from pipeline.fotmob_auth import FotmobAuth
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH
    from pipeline.fotmob_auth import FotmobAuth

RATE_LIMIT_SEC = 0.4


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


def load_team_info() -> None:
    con = duckdb.connect(str(DB_PATH))
    teams = con.execute(
        "SELECT team_id, fotmob_team_id, team_name FROM team_logos "
        "WHERE team_id IS NOT NULL AND fotmob_team_id IS NOT NULL"
    ).fetchall()
    auth = FotmobAuth()
    rows, ok = [], 0
    for tid, fid, tname in teams:
        try:
            d = auth.get(f"/api/data/teams?id={int(fid)}")
            m = _extract(d)
        except Exception as e:  # noqa: BLE001
            print(f"  {tname}: failed ({repr(e)[:50]})")
            continue
        rows.append((int(tid), m["manager"], m["venue"], m["city"],
                     m["capacity"], m["opened"], m["surface"]))
        ok += 1
        time.sleep(RATE_LIMIT_SEC)

    con.execute("DROP TABLE IF EXISTS team_meta")
    con.execute("""CREATE TABLE team_meta
        (team_id BIGINT, manager VARCHAR, venue VARCHAR, city VARCHAR,
         capacity BIGINT, opened BIGINT, surface VARCHAR)""")
    if rows:
        con.executemany("INSERT INTO team_meta VALUES (?,?,?,?,?,?,?)", rows)
    n_mgr = sum(1 for r in rows if r[1])
    n_ven = sum(1 for r in rows if r[2])
    con.close()
    print(f"team_meta: {ok} teams ({n_mgr} with manager, {n_ven} with venue).")


if __name__ == "__main__":
    load_team_info()
