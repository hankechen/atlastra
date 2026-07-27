"""Re-derive every fitted constant in the engine from the warehouse.

    python -m tools.fit                    # refit everything against 2025/26, print a diff
    python -m tools.fit --season 2425
    python -m tools.fit --only xg          # xg | press | shape | points | shares

This is the other half of tools/backtest.py. The backtest can tell you a constant is wrong;
this tells you what it should be. Until both existed the engine's "fitted, not chosen"
constants came out of throwaway scripts that no longer exist — they could be scored but not
reproduced, so a new season of data could not be folded in without redoing the derivation
from memory.

Nothing here writes to tactics.py. It prints each constant beside the value currently in the
source and the delta, and you copy across what you want to keep. That is deliberate: a fit
worth adopting is worth looking at first, and several of these constants are knowingly held
away from their fitted value (see _GOAL_EXP) for reasons the data cannot see.

WHAT THIS DOES NOT REFIT, so the gap is written down rather than implied:
  * the attack/midfield/defence weights inside _units. They define the features every fit
    below regresses on, so refitting them here would be circular — it needs a joint fit over
    raw player attributes, which is a different and larger job.
  * _MIN_BLOCKS (goals by 15-minute block). We hold no goal timings; the shape came from
    published distributions.
  * every role, chemistry and weakness constant. Those need positional data the fit path
    does not read — player_heatmap is the way in, and it is still unused here.
"""
import argparse
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                          # noqa: E402
from sklearn.linear_model import PoissonRegressor           # noqa: E402

from analytics.queries import SoccerDB                      # noqa: E402
from webapp import server, tactics as T                     # noqa: E402
from tools.backtest import _matches, _score, _squads        # noqa: E402

# Every fit prints against what the source currently holds, so a drift shows up as a number
# rather than as a feeling. Keyed to the module attribute where one exists.
CURRENT = {
    "_XG_INTERCEPT": T._XG_INTERCEPT, "_XG_ATTACK": T._XG_ATTACK,
    "_XG_MIDFIELD": T._XG_MIDFIELD, "_XG_PRESS_RESIST": T._XG_PRESS_RESIST,
    "_XG_OPP_DEFENSE": T._XG_OPP_DEFENSE, "_XG_OPP_GK": T._XG_OPP_GK,
    "_XG_OPP_AERIAL": T._XG_OPP_AERIAL, "_XG_HOME": T._XG_HOME,
    "_UCL_HOME_XG": T._UCL_HOME_XG, "_UCL_AWAY_XG": T._UCL_AWAY_XG,
    "_PPDA_MID": T._PPDA_MID, "_PPDA_XG_B": T._PPDA_XG_B,
    "_XG_SHAPE": T._XG_SHAPE,
    "_PPG_A": T._PPG_A, "_PPG_B": T._PPG_B, "_PPG_ERR": T._PPG_ERR,
    "_GOAL_EXP": T._GOAL_EXP, "_ASSIST_EXP": T._ASSIST_EXP,
}


def _row(name, fitted, note=""):
    cur = CURRENT.get(name)
    if cur is None:
        print(f"  {name:<20}{fitted:>12.4f}{'':>12}{'':>10}  {note}")
        return
    d = fitted - cur
    flag = "  <-- differs" if abs(d) > max(0.02 * abs(cur), 0.005) else ""
    print(f"  {name:<20}{fitted:>12.4f}{cur:>12.4f}{d:>+10.4f}{flag}  {note}")


def _head(title):
    print(f"\n=== {title} ===")
    print(f"  {'constant':<20}{'fitted':>12}{'in source':>12}{'delta':>10}")


# --------------------------------------------------------------------- features ----- #
def _design(db, season):
    """Two rows per match — each side's own view of it. Features are exactly the terms in
    _base_xg, so a coefficient here IS the constant there: own attack/midfield/press
    resistance, the opponent's defence/keeper/aerial, and the venue as +/-0.5 (which is why
    _XG_HOME reads as the full home-to-away swing rather than half of it)."""
    rows = _matches(db, season)
    units = _squads(db, rows)
    X, y, meta = [], [], []
    for r in rows:
        u, ou = units.get(r["home"]), units.get(r["away"])
        if not u or not ou:
            continue
        for me, opp, goals, home in ((u, ou, r["gf"], 0.5), (ou, u, r["ga"], -0.5)):
            X.append([me["attack"] / 100.0, me["midfield"] / 100.0, me["press_resist"] / 100.0,
                      opp["defense"] / 100.0, opp["gk"] / 100.0, opp["aerial"] / 100.0, home])
            y.append(goals)
            meta.append((r["home"] if home > 0 else r["away"], r["home"], r["away"]))
    return np.array(X, float), np.array(y, float), meta, units, rows


def _fit_xg(X, y):
    """Poisson GLM with a log link — Maher / Dixon-Coles, and the distribution the simulator
    actually draws from. alpha=0 because we want the maximum-likelihood estimate, not a
    regularised one: there are seven features and thousands of rows."""
    g = PoissonRegressor(alpha=0.0, fit_intercept=True, max_iter=5000, tol=1e-10)
    g.fit(X, y)
    return float(g.intercept_), [float(c) for c in g.coef_]


def fit_xg(db, season):
    X, y, _meta, _units, _rows = _design(db, season)
    b0, c = _fit_xg(X, y)
    _head(f"xG core — Poisson GLM on goals, {len(y)} team-matches of {season}")
    _row("_XG_INTERCEPT", b0)
    for name, v in zip(("_XG_ATTACK", "_XG_MIDFIELD", "_XG_PRESS_RESIST",
                        "_XG_OPP_DEFENSE", "_XG_OPP_GK", "_XG_OPP_AERIAL", "_XG_HOME"), c):
        _row(name, v)
    # The venue split the engine multiplies by is this coefficient, not a separate estimate.
    _row("_UCL_HOME_XG", math.exp(0.5 * c[-1]), "= exp(+0.5 x _XG_HOME)")
    _row("_UCL_AWAY_XG", math.exp(-0.5 * c[-1]), "= exp(-0.5 x _XG_HOME)")
    mu = np.exp(b0 + X @ np.array(c))
    print(f"\n  mean goals: fitted {mu.mean():.3f} vs actual {y.mean():.3f} · "
          f"deviance/row {_dev(y, mu) / len(y):.4f}")
    _collinearity(X)
    return X, y, mu


_FEAT = ("attack", "midfield", "press_resist", "opp_defense", "opp_gk", "opp_aerial", "home")


def _collinearity(X):
    """READ THIS BEFORE COPYING A SINGLE COEFFICIENT ACROSS.

    The three opponent terms move together — a good defence tends to have a good keeper and
    win headers — so the fit can trade weight between them almost freely. Their SUM is well
    determined and each one on its own is not: refitting 2024/25 instead of 2025/26 sends
    _XG_OPP_AERIAL from -1.26 to +2.19 while the predictions barely move.

    That is a property of the data, not a bug, and it has a practical consequence: judge a
    refit by the predictions and the backtest, never by whether an individual coefficient
    looks sensible. If you want stable individual terms, pool several seasons or drop one of
    the three."""
    C = np.corrcoef(X.T)
    pairs = [(abs(C[i, j]), _FEAT[i], _FEAT[j], C[i, j])
             for i in range(len(_FEAT)) for j in range(i + 1, len(_FEAT))]
    pairs.sort(reverse=True)
    cond = float(np.linalg.cond(np.column_stack([np.ones(len(X)), X])))
    print(f"  design condition number {cond:,.0f}"
          + ("  — coefficients are individually unstable" if cond > 100 else ""))
    for _a, f1, f2, r in pairs[:3]:
        print(f"    corr({f1}, {f2}) = {r:+.2f}")


def _dev(y, mu):
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(np.where(y > 0, y / mu, 1)), 0.0)
    return float(2 * np.sum(t - (y - mu)))


# ------------------------------------------------------------------- pressing ------- #
def fit_press(db, season):
    """_PPDA_MID is a median, not a fit. _PPDA_XG_B is the coefficient on log(PPDA) with the
    squad-quality terms already in the model — i.e. what pressing is worth ONCE you know how
    good the two sides are, which is the only version of the question worth asking. The raw
    correlation has the opposite sign, because good teams press and good teams score.

    PPDA is joined to the match it was recorded in, by game_id. An earlier version of this
    function matched it only by club, which silently paired every team-match with every PPDA
    that club ever posted and made pressing look decisive in both directions.
    """
    mid = db.con.execute("SELECT median(ppda), count(*) FROM team_match_stats "
                         "WHERE ppda IS NOT NULL AND ppda > 0").fetchone()
    _head(f"pressing — median over {mid[1]} team-matches, coefficient on log(PPDA)")
    _row("_PPDA_MID", float(mid[0]))

    rows = db.con.execute(
        """SELECT th.team_name, ta.team_name, m.goals_for, m.goals_against, m.ppda
           FROM team_match_stats m
           JOIN teams th ON th.team_id = m.team_id
           JOIN teams ta ON ta.team_id = m.opponent_team_id
           WHERE m.season = ? AND m.ppda IS NOT NULL AND m.ppda > 0
             AND m.goals_for IS NOT NULL AND m.goals_against IS NOT NULL""",
        [season]).fetchall()
    mrows = _matches(db, season)
    units = _squads(db, mrows)
    built = []
    for me_nm, opp_nm, gf, ga, p in rows:
        me, opp = units.get(me_nm), units.get(opp_nm)
        if not me or not opp:
            continue
        built.append(([me["attack"] / 100.0, me["midfield"] / 100.0, me["press_resist"] / 100.0,
                       opp["defense"] / 100.0, opp["gk"] / 100.0, opp["aerial"] / 100.0,
                       math.log(p / float(mid[0]))], gf, ga))
    if len(built) < 200:
        print(f"  too few rows with a PPDA reading ({len(built)})")
        return
    X = np.array([b[0] for b in built], float)
    print(f"  {len(built)} team-matches with PPDA joined by game")
    for target, idx in (("scored", 1), ("conceded", 2)):
        _b0, c = _fit_xg(X, np.array([b[idx] for b in built], float))
        if target == "scored":
            _row("_PPDA_XG_B", c[-1], "positive = pressing harder scores less")
        else:
            # Kept in the output because the engine ACTS on it: there is no press term on
            # the conceding side at all, and that absence is only defensible while this
            # reads ~0. Printed as an effect size, since a raw log coefficient invites the
            # wrong reaction — a hard press is about a third below the median PPDA.
            eff = math.exp(c[-1] * math.log(7.0 / float(mid[0])))
            print(f"  {'(conceding)':<20}{c[-1]:>12.4f}{'':>12}{'':>10}  "
                  f"a hard press (PPDA 7) concedes x{eff:.3f} — "
                  f"{'negligible, so the omission holds' if abs(1 - eff) < 0.05 else 'worth a term'}")


# ---------------------------------------------------------------- dispersion -------- #
def fit_shape(db, season, X, y, mu):
    """_XG_SHAPE is used for two different jobs that want two different values, which is
    worth knowing before anyone "improves" it:

      the xG DRAW  (_xg_draw)  — how much a team's xG swings match to match around its own
                                 season level. Fitted here against real per-match xG.
      the ODDS     (_win_probs) — how much GOALS scatter around the model's expectation for
                                 that fixture. Fitted here by likelihood, and cross-checked
                                 by scoring the odds themselves at each candidate value.

    They do not agree, and the honest reading is that the second job wants something close to
    a plain Poisson. Reported rather than resolved: collapsing them into one constant is a
    modelling decision, not an arithmetic one.
    """
    from scipy.optimize import minimize_scalar

    _head("over-dispersion, job 1: a team's xG swing between matches")
    rows = db.con.execute(
        """SELECT t.team_name, m.xg_for FROM team_match_stats m
           JOIN teams t ON t.team_id = m.team_id
           WHERE m.season = ? AND m.xg_for IS NOT NULL AND m.xg_for > 0""",
        [season]).fetchall()
    by = defaultdict(list)
    for nm, v in rows:
        by[nm].append(float(v))
    # Gamma(k, mean/k) has cv = 1/sqrt(k), so the coefficient of variation of a club's own
    # match xG identifies the shape directly — no reference to the model's mean needed.
    cvs = [st.pstdev(v) / st.mean(v) for v in by.values() if len(v) >= 20 and st.mean(v) > 0]
    if cvs:
        cv = st.mean(cvs)
        _row("_XG_SHAPE", 1.0 / cv ** 2, f"from cv {cv:.3f} over {len(cvs)} clubs — DRAW path")

    _head("over-dispersion, job 2: goals around the model's expectation")

    def nll(logk):
        k = math.exp(logk)
        return -float(np.sum(
            np.array([math.lgamma(yi + k) - math.lgamma(k) - math.lgamma(yi + 1)
                      + k * math.log(k / (k + m)) + yi * math.log(m / (k + m))
                      for yi, m in zip(y, np.maximum(mu, 1e-9))])))
    r = minimize_scalar(nll, bounds=(math.log(0.5), math.log(400)), method="bounded")
    k = math.exp(r.x)
    pois = float(np.sum(y * np.log(np.maximum(mu, 1e-9)) - mu
                        - np.array([math.lgamma(v + 1) for v in y])))
    print(f"  maximum likelihood wants shape {k:,.0f} — i.e. it runs to the bound, which is")
    print(f"  the Poisson limit. Negative binomial {-r.fun:.1f} vs plain Poisson {pois:.1f}: "
          f"{-r.fun - pois:+.1f}.")
    print("  Match goals, GIVEN a per-fixture mean, are not over-dispersed. The extra spread")
    print("  people expect is already carried by the mean varying between fixtures.")

    _head("over-dispersion, job 2 cross-check: score the odds at each shape")
    print(f"  {'shape':<20}{'log loss':>12}{'Brier':>12}")
    m_rows = _matches(db, season)
    units = _squads(db, m_rows)
    D = T.DEFAULT_TACTICS
    for cand in (2.0, 3.0, 5.0, 8.0, 15.0, 40.0, None):
        preds = []
        for r2 in m_rows:
            u, ou = units.get(r2["home"]), units.get(r2["away"])
            if not u or not ou:
                continue
            act = "H" if r2["gf"] > r2["ga"] else ("D" if r2["gf"] == r2["ga"] else "A")
            xh = T._metrics(u, D, ou, D)["xg"] * T._UCL_HOME_XG
            xa = T._metrics(ou, D, u, D)["xg"] * T._UCL_AWAY_XG
            w = T._win_probs(xh, xa, cand)
            preds.append((w["home"] / 100, w["draw"] / 100, w["away"] / 100, act))
        if not preds:
            continue
        ll, br, _acc = _score(preds)
        tag = "plain Poisson" if cand is None else ("<-- in source" if cand == T._XG_SHAPE else "")
        print(f"  {str(cand):<20}{ll:>12.4f}{br:>12.4f}  {tag}")


# -------------------------------------------------------------- points curve -------- #
def fit_points(db, season):
    """Points per game against squad strength S. S comes out of the xG core, so this has to
    be refitted whenever the core moves — which is exactly the coupling that made the
    original hand-set line under-project every club in Europe by six points a season."""
    pts = []
    for lk in ("ENG-Premier League", "ESP-La Liga", "ITA-Serie A", "GER-Bundesliga",
               "FRA-Ligue 1"):
        try:
            df = db.league_standings(lk, season)
        except Exception:                                   # noqa: BLE001
            continue
        for r in df.itertuples():
            row = db.con.execute("SELECT fotmob_team_id FROM team_logos WHERE team_name = ?",
                                 [r.team]).fetchone()
            if not row or not row[0]:
                continue
            squad = server._tac_squad(db, r.team, tid=int(row[0]))
            xi = T.build_xi(squad, "4-3-3") if squad else []
            if not xi or not any(s.get("player") for s in xi):
                continue
            u = T._units(xi)
            m = T._metrics(u, T.DEFAULT_TACTICS, T._BASE_OPP, T.DEFAULT_TACTICS)
            xp = T._xpts(round(m["xg"], 2), m["xga"])
            S = T._clamp_f(round((u.get("avg_rating", 74) - 74) * 2.5 + 55 + (xp - 1.4) * 4),
                           1, 90)
            games = int(getattr(r, "played", 0) or 0) or 38
            ctx = server._league_ctx(db, r.team) or {}
            mult = 1 + 0.25 * (1 - ctx.get("difficulty", 1.0))
            # Divide the league effect out of the observation, so the line is fitted on the
            # like-for-like rate the engine multiplies back up.
            pts.append((S, (int(r.pts or 0) / games) / mult, r.team, int(r.pts or 0), games))
    if len(pts) < 20:
        print("\n  points: too few clubs")
        return
    x = np.array([p[0] - 50 for p in pts], float)
    y = np.array([p[1] for p in pts], float)
    b, a = np.polyfit(x, y, 1)
    _head(f"points curve — {len(pts)} club-seasons, ppg on (S - 50)")
    _row("_PPG_A", float(a), "ppg for a league-average squad")
    _row("_PPG_B", float(b), "per point of strength")
    errs = [abs(round(T._clamp_f(a + (S - 50) * b, 0.4, 2.4) * g) - actual)
            for S, _r, _t, actual, g in pts]
    _row("_PPG_ERR", float(st.mean(errs)), "mean absolute miss, in points")


# ------------------------------------------------------- goal / assist shares ------- #
def fit_shares(db, season):
    """The target _GOAL_BASE and _ASSIST_BASE were fitted against: each position's real share
    of its club's goals and assists. Measured here straight off player_match_log, which is
    the half of the derivation that IS reproducible.

    The half that is not: closing the loop back to the engine's eight families. The warehouse
    knows eight coarse position GROUPS and the engine works in families that do not line up
    with them — there is no WM group at all, and the auto-XI it would have to score against
    never fields an AM in 4-3-3, so two families come out at a structural zero rather than a
    measured one. Bridging that needs per-match lineups (which XI actually played, in which
    shape); player_position_detail is the nearest thing we hold and it covers about a quarter
    of the players involved.

    So: the shares below are the evidence. Turning them back into bases is still a manual
    step, and saying so is better than printing a number that looks derived and is not.
    """
    rows = db.con.execute("""
        SELECT c.position_group, sum(l.goals), sum(l.assists), count(DISTINCT l.player_id)
        FROM player_match_log l
        JOIN player_ratings_combined c
          ON c.player_id = l.player_id AND c.season = l.season AND c.scope = 'league'
        WHERE l.season = ? AND c.position_group IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC""", [season]).fetchall()
    if not rows:
        print(f"\n  shares: no player_match_log rows for {season}")
        return
    tg = sum(float(r[1] or 0) for r in rows) or 1.0
    ta = sum(float(r[2] or 0) for r in rows) or 1.0
    print(f"\n=== goal & assist shares — {season} player_match_log, "
          f"{sum(r[3] for r in rows)} players ===")
    print(f"  {'group':<10}{'goals':>10}{'share':>9}{'assists':>10}{'share':>9}"
          f"{'players':>9}")
    for grp, g, a, n in rows:
        print(f"  {grp:<10}{float(g or 0):>10.0f}{100 * float(g or 0) / tg:>8.1f}%"
              f"{float(a or 0):>10.0f}{100 * float(a or 0) / ta:>8.1f}%{n:>9}")
    print("\n  engine families with no warehouse group: WM (and W/AM split differently).")
    print("  These shares are the fit target; mapping them onto _GOAL_BASE / _ASSIST_BASE is")
    print("  still done by hand — see the docstring for why.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2526")
    ap.add_argument("--only", default="all",
                    choices=["all", "xg", "press", "shape", "points", "shares"])
    a = ap.parse_args()
    with SoccerDB(read_only=True) as db:
        X = y = mu = None
        if a.only in ("all", "xg", "shape"):
            X, y, mu = fit_xg(db, a.season)
        if a.only in ("all", "press"):
            fit_press(db, a.season)
        if a.only in ("all", "shape") and y is not None:
            fit_shape(db, a.season, X, y, mu)
        if a.only in ("all", "points"):
            fit_points(db, a.season)
        if a.only in ("all", "shares"):
            fit_shares(db, a.season)
    print("\nNothing was written. Copy across what you want, then re-run tools.backtest "
          "before keeping it.")


if __name__ == "__main__":
    main()
