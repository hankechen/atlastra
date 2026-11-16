"""
Manager Career Mode -- play through a real season, matchday by matchday,
controlling one club's lineup/tactics while every other club's result that
matchday is auto-simulated. No new prediction model: the user's own match
reuses the exact same Tactics Lab match engine already live (see
webapp/server.py's _play_tactics_match, shared with POST /api/tactics/match
-- that route was factored specifically so there's one implementation, not
a fork), and every OTHER club's result for the matchday is drawn ONCE from
the exact same blended Elo/form + card-based model the Monte Carlo season
simulator (webapp/season_sim.py) already draws from 10,000x per fixture --
here, just once.

Persistence: one row per user in a dedicated `manager_career` sqlite table
(DATA_DIR -- same file-per-feature convention as scout_reports.sqlite/
weekly_recap.sqlite), not the shared `user_data` blob auth.py already has --
this is a growing structured log (a season's worth of results), not a
small preference blob. ONE active career per user for v1; starting a new
one overwrites.

Matchday grouping: team_fixtures_fotmob carries no round number, so rounds
are computed structurally, once, at career creation, and frozen into the
save: sort each team's own remaining fixtures by kickoff timestamp
(match_ts), and round N = the Nth fixture in that order for EVERY team --
deduped by fotmob_match_id, since a fixture is simultaneously both sides'
"index N" fixture. Verified against real Premier League data (252 remaining
fixtures, 19 resolvable teams): round 0 came back as exactly 10 clean
fixtures, matching a 20-club round-robin. Not guaranteed to match FotMob's
own matchday numbering exactly (a postponed/rescheduled match can shift one
pairing out of a round) -- acceptable for a game feature, not chased
further.

Standings fork from the REAL live table (team_standings_fotmob) once, at
creation, and are never re-read from live standings again -- today's real
results should never retroactively change an in-progress career. Everything
after that point evolves purely from this module's own simulated results.

Tactics Lab's match engine is venue-neutral (webapp/tactics.py's
_base_xg gives BOTH sides an equal flat home term, by design -- it's a
sandbox for "side A vs side B", not a real-fixture predictor with a venue
edge; that's /api/match/prediction's job, a separate model). Career mode
inherits that: who's "really" home/away in the fixture list only affects
which side a result is credited to in the standings, never the simulation
inputs. Consistent with how "Simulate in Tactics Lab" already treats any
two sides.
"""
import datetime
import json
import random
import sqlite3
import sys
from pathlib import Path

try:
    from config import DATA_DIR, DATAMB_SEASON
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DATA_DIR, DATAMB_SEASON

DB_PATH = DATA_DIR / "manager_career.sqlite"


def _conn():
    con = sqlite3.connect(str(DB_PATH))
    con.execute("""CREATE TABLE IF NOT EXISTS manager_career (
        user_id INTEGER PRIMARY KEY, team_id INTEGER, team_name TEXT,
        league_key TEXT, season TEXT, matchday INTEGER DEFAULT 0,
        state TEXT, created_at TEXT, updated_at TEXT)""")
    return con


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _build_rounds(con, league_key: str) -> list:
    """[[ (fotmob_match_id, home_team_id, away_team_id), ... ], ...] -- see
    module docstring for the grouping rule."""
    rows = con.execute(
        "SELECT fotmob_match_id, home_team_id, away_team_id, match_ts "
        "FROM team_fixtures_fotmob WHERE league_key = ?", [league_key]).fetchall()
    per_team: dict = {}
    for fid, h, a, ts in rows:
        per_team.setdefault(h, []).append((ts, fid, h, a))
        per_team.setdefault(a, []).append((ts, fid, h, a))
    for lst in per_team.values():
        lst.sort(key=lambda r: (r[0] is None, r[0]))     # unknown ts sorts last, not crashes
    max_rounds = max((len(lst) for lst in per_team.values()), default=0)
    rounds = []
    for i in range(max_rounds):
        this_round: dict = {}
        for lst in per_team.values():
            if i < len(lst):
                _, fid, h, a = lst[i]
                this_round[fid] = (h, a)
        rounds.append([(fid, h, a) for fid, (h, a) in this_round.items()])
    return rounds


def _apply_result(standings: dict, home_id: str, away_id: str, hg: int, ag: int) -> None:
    """Fold one fixture's result into the save's standings dict in place."""
    sh, sa = standings.get(home_id), standings.get(away_id)
    if sh is None or sa is None:                       # a side with no live-standings row
        return                                          # -- "absent rather than wrong"
    sh["gf"] += hg; sh["ga"] += ag; sh["gd"] = sh["gf"] - sh["ga"]; sh["played"] = sh.get("played", 0) + 1
    sa["gf"] += ag; sa["ga"] += hg; sa["gd"] = sa["gf"] - sa["ga"]; sa["played"] = sa.get("played", 0) + 1
    if hg > ag:
        sh["pts"] += 3; rh, ra = "W", "L"
    elif hg < ag:
        sa["pts"] += 3; rh, ra = "L", "W"
    else:
        sh["pts"] += 1; sa["pts"] += 1; rh, ra = "D", "D"
    sh["form"] = (sh.get("form") or [])[-4:] + [rh]
    sa["form"] = (sa.get("form") or [])[-4:] + [ra]


def _sorted_standings(standings: dict) -> list:
    table = sorted(standings.items(), key=lambda kv: (-kv[1]["pts"], -kv[1]["gd"]))
    return [{"team_id": int(tid), **row} for tid, row in table]


def start_career(d, user_id: int, team_name: str) -> dict:
    """Fork a new career from the REAL live standings/fixtures for
    team_name's league, as of right now. Overwrites any existing career for
    this user (one active career per user, v1 scope)."""
    con = d.con
    tid = d.find_team_id(team_name)
    if not tid:
        return {"available": False, "error": f"No club matching {team_name!r}."}
    row = con.execute(
        "SELECT t.team_name, tl.league_key FROM teams t JOIN team_logos tl USING(team_id) "
        "WHERE t.team_id = ?", [tid]).fetchone()
    if not row or not row[1]:
        return {"available": False, "error": f"{team_name!r} has no league on record."}
    real_name, league_key = row

    cur = con.execute("""
        SELECT tl.team_id, t.team_name, f.points, f.goal_difference, f.goals_for, f.goals_against,
               f.played
        FROM team_standings_fotmob f
        JOIN team_logos tl ON tl.fotmob_team_id = f.fotmob_team_id AND tl.league_key = f.league_key
        JOIN teams t ON t.team_id = tl.team_id
        WHERE f.league_key = ?
    """, [league_key]).fetchall()
    if not cur:
        return {"available": False, "error": f"No live standings for {league_key!r} right now."}
    standings = {str(r[0]): {"team": r[1], "pts": r[2], "gd": r[3], "gf": r[4], "ga": r[5],
                             "played": r[6], "form": []} for r in cur}
    if str(tid) not in standings:
        return {"available": False,
                "error": f"{real_name} has no live standings row yet -- try again shortly."}

    rounds = _build_rounds(con, league_key)
    if not rounds:
        return {"available": False, "error": f"No remaining fixtures on record for {league_key!r}."}

    # The real season this career forks from -- DATAMB_SEASON is the live
    # current season (team_standings_fotmob/team_fixtures_fotmob are both
    # current-season-only data, same convention as webapp/season_sim.py).
    season = DATAMB_SEASON

    state = {"standings": standings, "rounds": rounds, "results_log": [],
             "last_xi": None, "last_tactics": None}
    now = _now()
    scon = _conn()
    scon.execute(
        "INSERT OR REPLACE INTO manager_career VALUES (?,?,?,?,?,0,?,?,?)",
        [user_id, tid, real_name, league_key, season, json.dumps(state), now, now])
    scon.commit()
    scon.close()
    return get_career(user_id)


def get_career(user_id: int) -> dict:
    scon = _conn()
    row = scon.execute(
        "SELECT team_id, team_name, league_key, season, matchday, state, updated_at "
        "FROM manager_career WHERE user_id = ?", [user_id]).fetchone()
    scon.close()
    if not row:
        return {"available": False}
    team_id, team_name, league_key, season, matchday, state_raw, updated_at = row
    state = json.loads(state_raw)
    rounds = state["rounds"]
    season_complete = matchday >= len(rounds)
    next_fixture = None
    if not season_complete:
        for fid, h, a in rounds[matchday]:
            if h == team_id or a == team_id:
                is_home = h == team_id
                opp_id = a if is_home else h
                next_fixture = {"fotmob_match_id": fid, "is_home": is_home,
                                "opponent_team_id": opp_id,
                                "opponent": (state["standings"].get(str(opp_id)) or {}).get("team")}
                break
    return {"available": True, "team_id": team_id, "team": team_name, "league_key": league_key,
            "season": season, "matchday": matchday, "total_matchdays": len(rounds),
            "season_complete": season_complete, "standings": _sorted_standings(state["standings"]),
            "next_fixture": next_fixture, "results_log": state["results_log"],
            "last_xi": state.get("last_xi"), "last_tactics": state.get("last_tactics"),
            "updated_at": updated_at}


def delete_career(user_id: int) -> bool:
    scon = _conn()
    cur = scon.execute("DELETE FROM manager_career WHERE user_id = ?", [user_id])
    scon.commit()
    ok = cur.rowcount > 0
    scon.close()
    return ok


def advance_matchday(d, user_id: int, xi_wire: list, tactics_body: dict) -> dict:
    """Play the user's club's fixture this round with their chosen xi/tactics,
    draw every OTHER fixture this round once, fold every result into the
    saved standings, and persist. Returns the user's full rich match result
    (score/events/story/MOTM, same shape /api/tactics/match returns) plus
    the round's other scorelines and the updated table."""
    con = d.con
    scon = _conn()
    row = scon.execute(
        "SELECT team_id, team_name, league_key, matchday, state FROM manager_career "
        "WHERE user_id = ?", [user_id]).fetchone()
    if not row:
        scon.close()
        return {"available": False, "error": "No active career. Start one first."}
    team_id, team_name, league_key, matchday, state_raw = row
    state = json.loads(state_raw)
    rounds = state["rounds"]
    if matchday >= len(rounds):
        scon.close()
        return {"available": False, "error": "Season complete."}
    round_fixtures = rounds[matchday]

    # Shared with POST /api/tactics/match and GET /api/tactics/squad -- one real
    # implementation of "play one match" / "a club's current auto XI", not a
    # fork. Lazy import: webapp.server imports this module to wire the
    # /api/career/* routes, so a module-top import here would be circular.
    from webapp.server import _play_tactics_match, _auto_xi_wire

    from joblib import load
    from webapp.season_sim import ART, _prefetch_form, simulate_one_fixture
    art = load(ART)
    model, feats, elo = art["model"], art["feats"], art["elo"]
    venue_map, any_map = _prefetch_form(con)
    tid_to_fotmob = dict(con.execute(
        "SELECT team_id, fotmob_team_id FROM team_logos WHERE league_key = ?",
        [league_key]).fetchall())
    rng = random.Random()

    user_result, other_results = None, []
    for fid, h, a in round_fixtures:
        if team_id not in (h, a):
            hg, ag, _outcome = simulate_one_fixture(
                model, feats, elo, venue_map, any_map, h, a, tid_to_fotmob, rng=rng)
            other_results.append({"fotmob_match_id": fid, "home_team_id": h,
                                  "away_team_id": a, "home_goals": hg, "away_goals": ag})
            _apply_result(state["standings"], str(h), str(a), hg, ag)
            continue
        is_home = h == team_id
        opp_id = a if is_home else h
        opp_name = (state["standings"].get(str(opp_id)) or {}).get("team") or "Opponent"
        opp_xi, opp_tactics = _auto_xi_wire(d, opp_name)
        res = _play_tactics_match(d, team_name, xi_wire, tactics_body,
                                  {"team": opp_name, "xi": opp_xi, "tactics": opp_tactics})
        if not res:
            scon.close()
            return {"available": False, "error": f"Could not build {opp_name}'s side."}
        user_goals, opp_goals = res["score"]["home"], res["score"]["away"]
        if is_home:
            _apply_result(state["standings"], str(team_id), str(opp_id), user_goals, opp_goals)
        else:
            _apply_result(state["standings"], str(opp_id), str(team_id), opp_goals, user_goals)
        user_result = {**res, "fotmob_match_id": fid, "is_home": is_home, "opponent": opp_name}

    if user_result is None:
        # Shouldn't happen on a clean round-robin schedule (verified on real data --
        # see module docstring), but a postponed/rescheduled fixture could in theory
        # leave this round without the user's club in it. Fail loudly rather than
        # silently advancing the matchday with nothing played.
        scon.close()
        return {"available": False,
               "error": "No fixture found for your club this matchday -- schedule gap."}

    state["results_log"].append({"matchday": matchday, "user_result": user_result,
                                 "other_results": other_results})
    state["last_xi"], state["last_tactics"] = xi_wire, tactics_body
    new_matchday = matchday + 1
    scon.execute("UPDATE manager_career SET matchday = ?, state = ?, updated_at = ? "
                "WHERE user_id = ?", [new_matchday, json.dumps(state), _now(), user_id])
    scon.commit()
    scon.close()
    return {"available": True, "matchday": matchday, "user_result": user_result,
            "other_results": other_results, "standings": _sorted_standings(state["standings"]),
            "season_complete": new_matchday >= len(rounds)}
