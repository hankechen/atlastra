"""
Monte Carlo season simulator.

Every other "projected table" on the site is a single deterministic point
estimate (see analytics.queries.web_projected_table -- a fitted linear
regression, real but a lone number per team). This is the other half: run
the REMAINING SEASON forward thousands of times, sampling each match's
outcome from a real probability distribution, and report how often each
team actually finishes where -- title odds, top-4 odds, relegation odds,
not just an expected points total.

No new model. This reuses the exact two prediction engines already fitted,
backtested, and live on every match page's Odds tab (see memory
match-outcome-ml):
  * ml.train_match_outcome.predict_fixture -- a results-only Elo/form
    HistGradientBoostingClassifier, {"H","D","A"} probabilities, held-out
    log loss 0.9865 vs 1.0758 base-rate.
  * webapp.tactics' fitted xG engine, fed each side's live auto-XI via
    webapp.live_feed_fotmob._squad_units -- the SAME card-based model the
    Odds tab's scoreline grid comes from.
Blended 50/50, same weight already validated (not just assumed) by
tools/backtest.py --ensemble to beat BOTH components on log loss, Brier
score and top-pick accuracy simultaneously.

Two-stage sampling per fixture per trial:
  1. draw W/D/L from the blended {H,D,A} probabilities -- the best single
     signal available, same one already live on the match page.
  2. draw a SPECIFIC scoreline conditional on that outcome from the card
     model's own scoreline grid (renormalized within the drawn outcome's
     cells) -- gives a real, internally consistent goal difference for
     tie-breaking, not just points. `_card_grid` below mirrors
     webapp.live_feed_fotmob._model's own grid-building line for line
     rather than importing it, so this stays purely additive and never
     touches that live, already-deployed code path.

A fixture where a credible XI can't be built for one side (rare for a
top-5-league club -- _squad_units' own 0.6-coverage gate usually passes)
falls back to the ML probabilities alone for the outcome draw, and a
measured (not guessed) average goal margin by outcome type for the GD
contribution -- see _FALLBACK_MARGIN.

Expensive work (model inference) happens ONCE per fixture; cheap random
draws happen N times per fixture (the standard efficient Monte Carlo
pattern) -- ~300 fixtures x 10,000 trials runs in low single-digit seconds.

Run (prints a table, writes nothing):
    PYTHONPATH=. python -m webapp.season_sim --league "ENG-Premier League"
"""
import argparse
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from config import DB_PATH
    from analytics.queries import connect_retry
    from ml.train_match_outcome import (ART, ELO_START, FORM_COLS, FORM_SQL,
                                        REST_CAP)
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH
    from analytics.queries import connect_retry
    from ml.train_match_outcome import (ART, ELO_START, FORM_COLS, FORM_SQL,
                                        REST_CAP)

N_TRIALS_DEFAULT = 10_000

# Measured, not guessed: average goal margin by result type, home perspective,
# every top-5-league match on record (team_match_stats). Used only for the rare
# fixture the card model can't build a grid for.
_FALLBACK_MARGIN = {"H": 1.91, "D": 0.0, "A": -1.74}


def _card_grid(hu: dict, au: dict) -> dict:
    """9x9 {(home_goals, away_goals): probability} from two teams' unit
    strengths -- the same fitted engine + over-dispersed PMF
    webapp.live_feed_fotmob._model uses for the live match Odds tab."""
    from webapp import tactics as _T
    hx = max(0.2, _T._base_xg(hu, au) * _T._UCL_HOME_XG)
    ax = max(0.2, _T._base_xg(au, hu) * _T._UCL_AWAY_XG)
    return {(i, j): _T._goal_pmf(i, hx, _T._XG_SHAPE) * _T._goal_pmf(j, ax, _T._XG_SHAPE)
            for i in range(9) for j in range(9)}


def _prefetch_form(con) -> tuple[dict, dict]:
    """Every team's latest form-state row, for both the venue-specific and
    venue-agnostic fallback ml.train_match_outcome.predict_fixture's own
    _latest() uses -- that function re-runs FORM_SQL's full window-function
    scan over the ENTIRE match history from scratch on EVERY call, which is
    fine for a single live-match-page prediction but measured at 115-150s for
    one league's ~270 fixtures here (540+ redundant full-table scans). Same
    SQL, same two-tier fallback, computed ONCE for every team up front
    instead of once per fixture -- _predict_batch below reads from these maps
    and reproduces predict_fixture's own row-assembly exactly, so results are
    identical, just not re-derived from scratch 500+ times."""
    venue = con.execute(
        FORM_SQL + " QUALIFY row_number() OVER "
        "(PARTITION BY team_id, is_home ORDER BY match_date DESC) = 1").df()
    venue_map = {(int(r.team_id), bool(r.is_home)): r for r in venue.itertuples()}
    any_ = con.execute(
        FORM_SQL + " QUALIFY row_number() OVER "
        "(PARTITION BY team_id ORDER BY match_date DESC) = 1").df()
    any_map = {int(r.team_id): r for r in any_.itertuples()}
    return venue_map, any_map


def _predict_batch(model, feats, elo, home_id: int, away_id: int,
                   venue_map: dict, any_map: dict) -> dict:
    """predict_fixture()'s own row-assembly + predict_proba, reading from the
    prefetched maps above instead of querying -- see _prefetch_form."""
    h = venue_map.get((home_id, True)) or any_map.get(home_id)
    a = venue_map.get((away_id, False)) or any_map.get(away_id)
    row = {"h_elo": elo.get(home_id, ELO_START), "a_elo": elo.get(away_id, ELO_START)}
    row["elo_diff"] = row["h_elo"] - row["a_elo"]
    for side, data in (("h", h), ("a", a)):
        for c in FORM_COLS:
            v = getattr(data, c) if data is not None else np.nan
            row[f"{side}_{c}"] = (min(v, REST_CAP)
                                   if c == "rest_days" and v is not None and not pd.isna(v)
                                   else v)
    X = pd.DataFrame([row])[feats].astype(float)
    p = model.predict_proba(X)[0]
    return dict(zip(model.classes_, (float(round(v, 4)) for v in p)))


def _fixture_distribution(model, feats, elo, venue_map, any_map, home_id: int,
                          away_id: int, tid_to_fotmob: dict):
    """One fixture's (blended {H,D,A} probs, scoreline grid or None)."""
    ml_probs = _predict_batch(model, feats, elo, home_id, away_id, venue_map, any_map)

    # Private helper, cross-module -- same precedent as _model() itself (which
    # reaches into webapp.tactics the same way); see module docstring.
    from webapp.live_feed_fotmob import _squad_units
    hfid, afid = tid_to_fotmob.get(home_id), tid_to_fotmob.get(away_id)
    hu = _squad_units(hfid) if hfid else None
    au = _squad_units(afid) if afid else None
    if not (hu and au):
        return ml_probs, None

    grid = _card_grid(hu, au)
    card_probs = {"H": 0.0, "D": 0.0, "A": 0.0}
    for (i, j), p in grid.items():
        card_probs["H" if i > j else "D" if i == j else "A"] += p
    blended = {k: 0.5 * ml_probs[k] + 0.5 * card_probs[k] for k in ("H", "D", "A")}
    return blended, grid


def _draw_outcome(rng: random.Random, probs: dict) -> str:
    x, cum = rng.random(), 0.0
    for k in ("H", "D", "A"):
        cum += probs[k]
        if x <= cum:
            return k
    return "A"                                 # floating-point rounding guard


def _draw_scoreline(rng: random.Random, grid: dict, outcome: str):
    """A (home_goals, away_goals) sample from `grid`, conditioned on matching
    the already-drawn `outcome` -- so the scoreline and the result it's
    reported under are never inconsistent with each other."""
    cells = [(k, p) for k, p in grid.items()
             if (outcome == "H" and k[0] > k[1])
             or (outcome == "D" and k[0] == k[1])
             or (outcome == "A" and k[0] < k[1])]
    tot = sum(p for _, p in cells)
    if tot <= 0:
        return None
    x, cum = rng.random() * tot, 0.0
    for key, p in cells:
        cum += p
        if x <= cum:
            return key
    return cells[-1][0]


def simulate_league(con, league_key: str, n_trials: int = N_TRIALS_DEFAULT,
                    seed: int | None = None) -> dict:
    """Run the Monte Carlo season simulation for one league's remaining
    fixtures. `con` is the caller's own open DuckDB connection (read-only is
    fine -- nothing here writes) so this can run either as a standalone
    script or from inside the live server without a connection-mode clash
    (see memory reliability-infra-oct13 for why that matters)."""
    # Current real record: the live standings (team_standings_fotmob via
    # team_logos) -- same source league_standings()'s own live branch uses --
    # is the simulation's STARTING point, not zero. A team we can't resolve to
    # a live standings row (no team_logos link, e.g. a newly-promoted club
    # before it's crest-matched) is simply excluded, same "absent rather than
    # wrong" convention used everywhere else this table is read.
    cur = con.execute("""
        SELECT tl.team_id, t.team_name, f.points, f.goal_difference
        FROM team_standings_fotmob f
        JOIN team_logos tl ON tl.fotmob_team_id = f.fotmob_team_id AND tl.league_key = f.league_key
        JOIN teams t ON t.team_id = tl.team_id
        WHERE f.league_key = ?
    """, [league_key]).fetchall()
    if not cur:
        return {"available": False, "error": f"No live standings for {league_key!r}."}
    teams = {tid: {"name": name, "points": pts, "gd": gd} for tid, name, pts, gd in cur}

    tid_to_fotmob = dict(con.execute(
        "SELECT team_id, fotmob_team_id FROM team_logos WHERE league_key = ?",
        [league_key]).fetchall())

    fixtures = con.execute(
        "SELECT home_team_id, away_team_id FROM team_fixtures_fotmob WHERE league_key = ?",
        [league_key]).fetchall()
    fixtures = [(h, a) for h, a in fixtures if h in teams and a in teams]
    if not fixtures:
        return {"available": False,
                "error": f"No remaining fixtures on record for {league_key!r}."}

    from joblib import load
    art = load(ART)
    model, feats, elo = art["model"], art["feats"], art["elo"]
    venue_map, any_map = _prefetch_form(con)       # one scan, not 500+ -- see _prefetch_form

    # Expensive step (model inference, including live squad-building HTTP
    # calls the first time a team's seen) happens ONCE here, not per trial.
    dists = [(h, a, *_fixture_distribution(model, feats, elo, venue_map, any_map,
                                           h, a, tid_to_fotmob))
              for h, a in fixtures]

    rng = random.Random(seed)
    team_ids = list(teams.keys())
    finishes = {tid: {"pts": [], "pos": []} for tid in team_ids}

    for _ in range(n_trials):
        pts = {tid: teams[tid]["points"] for tid in team_ids}
        gd = {tid: teams[tid]["gd"] for tid in team_ids}
        for h, a, probs, grid in dists:
            outcome = _draw_outcome(rng, probs)
            sc = _draw_scoreline(rng, grid, outcome) if grid is not None else None
            if sc is not None:
                hg, ag = sc
                gd[h] += hg - ag
                gd[a] += ag - hg
            else:
                m = _FALLBACK_MARGIN[outcome]
                gd[h] += m
                gd[a] -= m
            if outcome == "H":
                pts[h] += 3
            elif outcome == "A":
                pts[a] += 3
            else:
                pts[h] += 1
                pts[a] += 1
        order = sorted(team_ids, key=lambda tid: (-pts[tid], -gd[tid]))
        for pos, tid in enumerate(order, start=1):
            finishes[tid]["pts"].append(pts[tid])
            finishes[tid]["pos"].append(pos)

    n = len(team_ids)
    table = []
    for tid in team_ids:
        pos = np.array(finishes[tid]["pos"])
        pts = np.array(finishes[tid]["pts"])
        table.append({
            "team_id": tid, "team": teams[tid]["name"],
            "current_points": teams[tid]["points"],
            "title_pct": round(100 * float((pos == 1).mean()), 1),
            "top4_pct": round(100 * float((pos <= 4).mean()), 1),
            "relegation_pct": round(100 * float((pos > n - 3).mean()), 1),
            "median_points": int(np.median(pts)),
            "p10_points": int(np.percentile(pts, 10)),
            "p90_points": int(np.percentile(pts, 90)),
            "median_position": float(np.median(pos)),
        })
    table.sort(key=lambda r: -r["median_points"])
    return {"available": True, "league": league_key, "n_trials": n_trials,
            "n_fixtures": len(dists), "n_teams": n, "table": table}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", default="ENG-Premier League")
    ap.add_argument("--trials", type=int, default=N_TRIALS_DEFAULT)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()

    # Standalone/CLI use only -- read-only, and assumes nothing else holds the
    # warehouse read-write at the same time (stop the server first if it's up).
    _con = connect_retry(DB_PATH, read_only=True)
    try:
        r = simulate_league(_con, a.league, a.trials, a.seed)
    finally:
        _con.close()

    if not r["available"]:
        print(r["error"])
        sys.exit(1)
    print(f"{r['league']} -- {r['n_fixtures']} remaining fixtures, "
          f"{r['n_trials']:,} trials, {r['n_teams']} teams\n")
    print(f"{'Team':<24}{'Pts':>5}{'Title%':>8}{'Top4%':>8}{'Releg%':>8}"
          f"{'MedPts':>8}{'Range':>12}")
    for row in r["table"]:
        print(f"{row['team']:<24}{row['current_points']:>5}{row['title_pct']:>7.1f}%"
              f"{row['top4_pct']:>7.1f}%{row['relegation_pct']:>7.1f}%"
              f"{row['median_points']:>8}  [{row['p10_points']}-{row['p90_points']}]")
