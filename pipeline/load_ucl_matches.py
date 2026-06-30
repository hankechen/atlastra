"""Backfill historical Champions League match results into `ucl_matches`.

We have no UCL fixtures in the warehouse (the `matches` table is domestic-only and
`ucl_player_stats` is season-aggregated). SofaScore is our UCL source, so we page
through the Champions League unique-tournament (id 7) season-by-season and store
each finished match, mapping the two clubs to our `teams.team_id` where possible
(top-5 clubs map; other UCL clubs stay name-only). Head-to-head then unions this
with the domestic `matches`.

Run:  python -m pipeline.load_ucl_matches
"""
from __future__ import annotations

import re
import sys
import time
import unicodedata
from pathlib import Path

import tls_requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.queries import SoccerDB

BASE = "https://api.sofascore.com/api/v1"
UCL = 7                       # SofaScore unique-tournament id for the Champions League
MAX_PAGES = 40               # safety cap per season

# tokens dropped when normalising club names (FC/CF prefixes, sponsor numbers, …)
_FILLER = {"fc", "cf", "afc", "ac", "sc", "ssc", "as", "rc", "sl", "sd", "ud", "cd",
           "club", "de", "calcio", "bsc", "vfb", "vfl", "tsg", "rb", "bk", "if", "sk",
           "fk", "ogc", "rcd", "rco", "aj", "spvgg", "us", "ss"}
_SYN = {"munchen": "munich", "internazionale": "inter", "moenchengladbach": "gladbach",
        "monchengladbach": "gladbach", "hotspur": "", "wanderers": ""}


def _norm(s: str) -> tuple:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z ]", " ", s).lower()
    return tuple(t for raw in s.split()
                 if raw not in _FILLER for t in [_SYN.get(raw, raw)] if t)


def build_resolver(con):
    """SofaScore club name -> our teams.team_id via normalized matching.

    Exact normalized match wins; otherwise a unique subset match, but a single-token
    team (e.g. 'Inter') only matches exactly so it can't swallow 'Inter d'Escaldes'.
    """
    teams = con.execute("SELECT team_id, team_name FROM teams").fetchall()
    exact, sets = {}, []
    for tid, nm in teams:
        k = _norm(nm)
        exact.setdefault(k, tid)
        sets.append((set(k), tid))

    def resolve(name: str):
        k = _norm(name)
        if k in exact:
            return exact[k]
        ks = set(k)
        cands = {tid for toks, tid in sets if len(toks) >= 2 and toks <= ks}      # our multi-token name inside sofa
        cands |= {tid for toks, tid in sets if toks and ks <= toks}               # sofa inside our name
        return next(iter(cands)) if len(cands) == 1 else None
    return resolve


def _get(path: str) -> dict:
    for attempt in range(3):
        try:
            r = tls_requests.get(f"{BASE}{path}", timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return {}
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5 * (attempt + 1))
    return {}


def _season_code(year: str) -> str | None:
    """'24/25' -> '2425'."""
    if "/" not in year:
        return None
    a, b = year.split("/")
    return f"{a.zfill(2)}{b.zfill(2)}"


def main() -> None:
    db = SoccerDB(read_only=False)
    con = db.con
    con.execute("""
        CREATE TABLE IF NOT EXISTS ucl_matches (
            event_id      BIGINT PRIMARY KEY,
            season        VARCHAR,
            match_date    TIMESTAMP,
            home_team_id  BIGINT,          -- our teams.team_id (nullable: non-top-5 clubs)
            away_team_id  BIGINT,
            home_name     VARCHAR,         -- SofaScore display name
            away_name     VARCHAR,
            home_goals    INTEGER,
            away_goals    INTEGER,
            round         VARCHAR
        )
    """)

    seasons = (_get(f"/unique-tournament/{UCL}/seasons") or {}).get("seasons") or []
    # keep the last 12 completed seasons + current (14/15 .. now)
    wanted = []
    for s in seasons:
        code = _season_code(s.get("year", ""))
        if not code:
            continue
        yy = int(code[:2])
        year = (2000 if yy < 50 else 1900) + yy      # '14'->2014, '97'->1997
        if year >= 2014:                              # last 12 seasons (2014/15+)
            wanted.append((code, s["id"], s["year"]))
    wanted.sort()

    resolve = build_resolver(con)

    grand = 0
    for code, sid, year in wanted:
        events: dict[int, dict] = {}
        for page in range(MAX_PAGES):
            evs = (_get(f"/unique-tournament/{UCL}/season/{sid}/events/last/{page}") or {}).get("events") or []
            if not evs:
                break
            for e in evs:
                events[e["id"]] = e
        rows = []
        for e in events.values():
            # finished includes FT (100), after-extra-time (110) and after-penalties
            # (120) -- knockout ties that go to ET/pens must NOT be dropped.
            if (e.get("status") or {}).get("type") != "finished":
                continue
            hs, as_ = (e.get("homeScore") or {}), (e.get("awayScore") or {})
            # `display` is the regulation/ET goals score WITHOUT the shootout;
            # `current` folds penalties in (a 1-1 final reads as 5-4). Prefer display.
            hg = hs.get("display") if hs.get("display") is not None else hs.get("current")
            ag = as_.get("display") if as_.get("display") is not None else as_.get("current")
            if hg is None or ag is None:
                continue
            h, a = e["homeTeam"], e["awayTeam"]
            ri = e.get("roundInfo") or {}
            rows.append((
                e["id"], code,
                e.get("startTimestamp"),
                resolve(h["name"]), resolve(a["name"]),
                h["name"], a["name"],
                int(hg), int(ag),
                ri.get("name") or (f"Round {ri['round']}" if ri.get("round") else None),
            ))
        for r in rows:
            con.execute(
                "INSERT OR REPLACE INTO ucl_matches VALUES (?,?,to_timestamp(?),?,?,?,?,?,?,?)", r)
        grand += len(rows)
        print(f"  {year}: {len(rows)} finished UCL matches")

    mapped = con.execute(
        "SELECT COUNT(*) FROM ucl_matches WHERE home_team_id IS NOT NULL AND away_team_id IS NOT NULL").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM ucl_matches").fetchone()[0]
    print(f"\nucl_matches: {total} rows ({mapped} with both clubs mapped to our team_ids)")
    db.close()


if __name__ == "__main__":
    main()
