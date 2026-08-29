"""
Match outcome (Home / Draw / Away) from historical results alone.

The site's live match-page Prediction tab (webapp/tactics.py + webapp/live_feed_fotmob.py)
already runs a fitted Poisson-GLM over TODAY'S auto-selected XI's card ratings -- a strong
model, but one that needs a full 11-player squad it can rate, and knows nothing about a
team's actual recent RESULTS (form, points, goals). This is a second, independent signal:
a gradient-boosted classifier trained purely on each side's result history up to kickoff --
no player data, no cards, just what actually happened on the pitch. Cheap to compute for
every fixture (no squad-building step), and a natural complement to the card-based model
rather than a replacement for it. Deliberately NOT wired into that engine or its UI -- see
memory match-outcome-ml.md for why.

Four feature families, all STRICTLY pre-match (no lookahead):
  elo        an incremental Elo rating (start 1500, K=20 scaled by a goal-difference
             multiplier -- the eloratings.net "World Football" convention: 1.5x for a 2-goal
             margin, (11+gd)/8 beyond that -- so a 4-0 actually moves the rating more than a
             1-0), +65 rating-point home edge folded into the expectation, walked once
             through every match in chronological order across all 12 seasons. Captures
             long-run strength before this season's own results exist to lean on -- exactly
             the cold-start gap the season-to-date features below have in August.
  form       season-to-date and last-5-match rolling averages of points, goals for/against,
             and xG for/against.
  venue form the same season-to-date averages again, but restricted to a team's OWN home
             matches (for the home side) or OWN away matches (for the away side) -- some
             teams' home and away form genuinely diverge from their overall record, which a
             single pooled average can't see.
  rest       days since each side's last match (fixture-congestion signal), clipped at 120
             so an off-season gap doesn't dominate a split.
All computed with SQL window functions over ROWS BETWEEN UNBOUNDED/N PRECEDING AND
1 PRECEDING -- the same causal-window pattern ml/train_match_contribution.py uses, so a
team's Matchday-1 numbers are NULL (native HistGradientBoostingClassifier missing-value
support handles that), never a leaked peek at the match itself.

Hyperparameters are chosen by a small grid, scored with TimeSeriesSplit cross-validation
INSIDE the training window only (the held-out seasons are never touched until the one final
evaluation) -- see _tune().

Scored against the base-rate and always-home baselines on held-out seasons -- see train()'s
printed output for whether it's actually worth anything. Also worth running
`python -m tools.backtest --season 2526` separately afterwards: that's the SAME kind of
log-loss/Brier/top-pick scoring already built for the card-based engine, on real recent
results, and the honest way to judge whether this is competitive with the model already
live on the site.

Persistence:
  * ml/match_outcome.joblib  -- fitted model + feature spec + final Elo ratings (for serving
    a fixture that hasn't been played yet)
  * table match_outcome_meta -- held-out metrics for the UI note

Run:  PYTHONPATH=. python -m ml.train_match_outcome
      PYTHONPATH=. python -m ml.train_match_outcome --report
"""
import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss, brier_score_loss
from sklearn.model_selection import TimeSeriesSplit

try:
    from config import DB_PATH, ALL_SEASONS
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, ALL_SEASONS

ART = Path(__file__).resolve().parent / "match_outcome.joblib"

# Trained on everything up to here; the seasons after are never seen -- same cutoff as
# ml/train_match_contribution.py, so the two models' held-out windows line up.
TRAIN_THROUGH = "2223"
FORM_WINDOW = 5
REST_CAP = 120

ELO_START = 1500.0
ELO_K = 20.0
ELO_HOME_ADV = 65.0    # rating-point edge folded into the home side's expected score

FORM_SQL = f"""
SELECT
    game_id, team_id, season, match_date, is_home,
    avg(points)         OVER wp AS s2d_ppg,
    avg(goals_for)       OVER wp AS s2d_gf,
    avg(goals_against)   OVER wp AS s2d_ga,
    avg(xg_for)          OVER wp AS s2d_xgf,
    avg(xg_against)      OVER wp AS s2d_xga,
    count(*)             OVER wp AS s2d_games,
    avg(points)         OVER wf AS form_ppg,
    avg(goals_for)       OVER wf AS form_gf,
    avg(goals_against)   OVER wf AS form_ga,
    avg(xg_for)          OVER wf AS form_xgf,
    avg(xg_against)      OVER wf AS form_xga,
    count(*)             OVER wf AS form_games,
    avg(points)         OVER wv AS venue_ppg,
    avg(goals_for)       OVER wv AS venue_gf,
    avg(goals_against)   OVER wv AS venue_ga,
    avg(xg_for)          OVER wv AS venue_xgf,
    avg(xg_against)      OVER wv AS venue_xga,
    count(*)             OVER wv AS venue_games,
    date_diff('day', lag(match_date) OVER wall, match_date) AS rest_days
FROM team_match_stats
WHERE goals_for IS NOT NULL
WINDOW
    wp AS (PARTITION BY team_id, season ORDER BY match_date
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
    wf AS (PARTITION BY team_id ORDER BY match_date
           ROWS BETWEEN {FORM_WINDOW} PRECEDING AND 1 PRECEDING),
    wv AS (PARTITION BY team_id, season, is_home ORDER BY match_date
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
    wall AS (PARTITION BY team_id ORDER BY match_date)
"""

MATCH_SQL = """
SELECT game_id, season, league_key, match_date, home_team_id, away_team_id,
       home_goals, away_goals
FROM matches
WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL
ORDER BY match_date, game_id
"""

FORM_COLS = ("s2d_ppg", "s2d_gf", "s2d_ga", "s2d_xgf", "s2d_xga", "s2d_games",
             "form_ppg", "form_gf", "form_ga", "form_xgf", "form_xga", "form_games",
             "venue_ppg", "venue_gf", "venue_ga", "venue_xgf", "venue_xga", "venue_games",
             "rest_days")

FEATS = (["elo_diff", "h_elo", "a_elo"]
         + [f"h_{c}" for c in FORM_COLS]
         + [f"a_{c}" for c in FORM_COLS])


def _k_mult(goal_diff: int) -> float:
    """eloratings.net's "World Football Elo" goal-difference multiplier -- a bigger margin
    moves the rating more than a 1-0 does, up to a point."""
    gd = abs(goal_diff)
    if gd <= 1:
        return 1.0
    if gd == 2:
        return 1.5
    return (11 + gd) / 8.0


def _elo(matches: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One chronological pass. Returns (per-match PRE-match elo for both sides,
    final rating per team_id as of the last match in `matches`)."""
    rating = {}
    rows = []
    for r in matches.itertuples():
        eh = rating.get(r.home_team_id, ELO_START)
        ea = rating.get(r.away_team_id, ELO_START)
        rows.append((r.game_id, eh, ea))
        exp_h = 1.0 / (1.0 + 10 ** (-(eh - ea + ELO_HOME_ADV) / 400.0))
        gd = r.home_goals - r.away_goals
        actual_h = 1.0 if gd > 0 else (0.5 if gd == 0 else 0.0)
        delta = ELO_K * _k_mult(gd) * (actual_h - exp_h)
        rating[r.home_team_id] = eh + delta
        rating[r.away_team_id] = ea - delta
    elo_df = pd.DataFrame(rows, columns=["game_id", "h_elo", "a_elo"])
    return elo_df, rating


def _frame(con) -> tuple[pd.DataFrame, dict]:
    matches = con.execute(MATCH_SQL).df()
    matches = matches[matches["season"].isin(ALL_SEASONS)].reset_index(drop=True)
    elo_df, final_elo = _elo(matches)
    form = con.execute(FORM_SQL).df()
    form["rest_days"] = form["rest_days"].clip(upper=REST_CAP)

    df = matches.merge(elo_df, on="game_id")
    df = df.merge(form.add_prefix("h_").rename(columns={"h_game_id": "game_id"}),
                  left_on=["game_id", "home_team_id"], right_on=["game_id", "h_team_id"], how="left")
    df = df.merge(form.add_prefix("a_").rename(columns={"a_game_id": "game_id"}),
                  left_on=["game_id", "away_team_id"], right_on=["game_id", "a_team_id"], how="left")
    df["elo_diff"] = df["h_elo"] - df["a_elo"]
    df["result"] = np.where(df["home_goals"] > df["away_goals"], "H",
                    np.where(df["home_goals"] < df["away_goals"], "A", "D"))
    return df, final_elo


# A small grid, not an exhaustive search -- scored by TimeSeriesSplit CV inside the training
# window (never the held-out seasons) so picking among these is not itself a form of peeking.
CANDIDATES = [
    dict(max_iter=300, max_depth=5, learning_rate=0.05, min_samples_leaf=80, l2_regularization=1.0),
    dict(max_iter=300, max_depth=4, learning_rate=0.05, min_samples_leaf=60, l2_regularization=1.0),
    dict(max_iter=400, max_depth=3, learning_rate=0.05, min_samples_leaf=100, l2_regularization=1.0),
    dict(max_iter=300, max_depth=5, learning_rate=0.03, min_samples_leaf=100, l2_regularization=2.0),
    dict(max_iter=500, max_depth=4, learning_rate=0.03, min_samples_leaf=80, l2_regularization=1.0),
    dict(max_iter=300, max_depth=6, learning_rate=0.05, min_samples_leaf=50, l2_regularization=0.5),
]


def _tune(X_tr: np.ndarray, y_tr: np.ndarray, n_splits: int = 4) -> dict:
    tscv = TimeSeriesSplit(n_splits=n_splits)
    best_params, best_score = CANDIDATES[0], np.inf
    print(f"[outcome] tuning over {len(CANDIDATES)} candidates, {n_splits}-fold TimeSeriesSplit "
          f"(training window only):", flush=True)
    for params in CANDIDATES:
        scores = []
        for fold_tr, fold_va in tscv.split(X_tr):
            m = HistGradientBoostingClassifier(random_state=42, **params).fit(
                X_tr[fold_tr], y_tr[fold_tr])
            p = m.predict_proba(X_tr[fold_va])
            scores.append(log_loss(y_tr[fold_va], p, labels=list(m.classes_)))
        mean_score = float(np.mean(scores))
        print(f"[outcome]   depth={params['max_depth']} lr={params['learning_rate']} "
              f"leaf={params['min_samples_leaf']} l2={params['l2_regularization']} "
              f"iter={params['max_iter']}  ->  CV log loss {mean_score:.4f}", flush=True)
        if mean_score < best_score:
            best_score, best_params = mean_score, params
    print(f"[outcome] best: {best_params}  (CV log loss {best_score:.4f})", flush=True)
    return best_params


def fit_held_out(con=None):
    """Build the frame and fit a model trained ONLY through TRAIN_THROUGH -- the exact
    held-out setup train() scores its own printed metrics on. Exposed separately so an
    external comparison (e.g. tools/backtest.py's ensemble check against the card-based
    Tactics Lab engine) can score this model fairly, on the SAME unseen seasons, rather
    than against the final artifact -- which is refit on everything including those
    seasons and would be an unfair, optimistic in-sample number in that comparison.
    Returns (model, df, X, y, tr_mask, te_mask, classes, best_params, final_elo)."""
    close = con is None
    con = con or duckdb.connect(str(DB_PATH), read_only=True)
    df, final_elo = _frame(con)
    if close:
        con.close()
    X = df[FEATS].astype(float)
    y = df["result"].values
    tr = (df["season"] <= TRAIN_THROUGH).values
    te = ~tr
    best_params = _tune(X[tr].values, y[tr])
    model = HistGradientBoostingClassifier(random_state=42, **best_params).fit(X[tr], y[tr])
    return model, df, X, y, tr, te, list(model.classes_), best_params, final_elo


def train(write: bool = True) -> dict:
    con = duckdb.connect(str(DB_PATH), read_only=not write)
    model, df, X, y, tr, te, classes, best_params, final_elo = fit_held_out(con)
    print(f"[outcome] {len(df):,} matches  ({tr.sum():,} train <= {TRAIN_THROUGH}, "
          f"{te.sum():,} held out)", flush=True)
    P = model.predict_proba(X[te])
    y_te = y[te]

    from collections import Counter
    c = Counter(y[tr])
    n_tr = tr.sum()
    base_rates = np.array([[c[k] / n_tr for k in classes]] * te.sum())
    always_home = np.array([[1.0 if k == "H" else 1e-6 for k in classes]] * te.sum())
    always_home = always_home / always_home.sum(axis=1, keepdims=True)

    def _ll_brier(probs):
        ll = log_loss(y_te, probs, labels=classes)
        br = np.mean([brier_score_loss((y_te == k).astype(int), probs[:, i])
                      for i, k in enumerate(classes)])
        acc = (np.array(classes)[probs.argmax(axis=1)] == y_te).mean()
        return round(ll, 4), round(float(br), 4), round(float(acc) * 100, 1)

    m = {"n": int(len(df)), "n_test": int(te.sum()), "classes": classes, "params": best_params}
    for label, probs in (("model", P), ("base rate", base_rates), ("always home", always_home)):
        ll, br, acc = _ll_brier(probs)
        m[f"log_loss_{label.replace(' ', '_')}"] = ll
        m[f"brier_{label.replace(' ', '_')}"] = br
        m[f"top_pick_{label.replace(' ', '_')}"] = acc
        print(f"[outcome] {label:<12} log loss {ll:>7.4f}  Brier {br:>7.4f}  top-pick {acc:>5.1f}%",
              flush=True)

    # calibration: of the matches called P(home)=60-70%, how often did home actually win?
    ph = P[:, classes.index("H")]
    bins = np.clip((ph * 10).astype(int), 0, 9)
    is_home_win = (y_te == "H").astype(int)
    cal = [{"band": f"{b*10}-{b*10+10}%", "n": int((bins == b).sum()),
            "predicted": round(float(ph[bins == b].mean()), 3),
            "actual": round(float(is_home_win[bins == b].mean()), 3)}
           for b in range(10) if (bins == b).sum() >= 30]
    m["calibration"] = cal
    print("[outcome] calibration (P home win vs actual):", flush=True)
    for row in cal:
        print(f"[outcome]   {row['band']:<8} n={row['n']:5d}  predicted {row['predicted']:.3f}  "
              f"actual {row['actual']:.3f}", flush=True)

    if not write:
        con.close()
        return m

    con.execute("DROP TABLE IF EXISTS match_outcome_meta")
    con.execute("""CREATE TABLE match_outcome_meta(
        n INTEGER, n_test INTEGER,
        log_loss_model DOUBLE, log_loss_base_rate DOUBLE, log_loss_always_home DOUBLE,
        brier_model DOUBLE, top_pick_model DOUBLE, top_pick_base_rate DOUBLE,
        train_through VARCHAR)""")
    con.execute("INSERT INTO match_outcome_meta VALUES (?,?,?,?,?,?,?,?,?)",
                [m["n"], m["n_test"], m["log_loss_model"], m["log_loss_base_rate"],
                 m["log_loss_always_home"], m["brier_model"], m["top_pick_model"],
                 m["top_pick_base_rate"], TRAIN_THROUGH])
    con.close()

    final_model = HistGradientBoostingClassifier(random_state=42, **best_params).fit(X, y)
    dump({"model": final_model, "feats": FEATS, "elo": final_elo, "metrics": m}, ART)
    print(f"[outcome] artifact -> {ART.name}", flush=True)
    return m


# --------------------------------------------------------------------------- #
# Serving
# --------------------------------------------------------------------------- #
def predict_fixture(con, home_team_id: int, away_team_id: int, model=None, feats=None, elo=None):
    """P(H)/P(D)/P(A) for a fixture that hasn't been played -- each side's CURRENT
    season-to-date/form/venue-form state (as of their last recorded match) plus their latest
    Elo rating. Same feature columns as training; a serving/training skew here would be
    invisible and would quietly ruin the predictions."""
    if model is None:
        from joblib import load
        art = load(ART)
        model, feats, elo = art["model"], art["feats"], art["elo"]

    def _latest(team_id, is_home):
        # the venue-form columns are venue-specific (this team's OWN home or away matches),
        # so ask for the side matching how they'd appear in the fixture being predicted
        form = con.execute(
            FORM_SQL + " QUALIFY row_number() OVER "
            "(PARTITION BY team_id, is_home ORDER BY match_date DESC) = 1 "
            "AND team_id = ? AND is_home = ?", [team_id, is_home]).df()
        if not form.empty:
            return form.iloc[0]
        # this side has never played at this venue in the window -- fall back to their
        # most recent match of EITHER venue for the non-venue-specific columns at least
        form = con.execute(
            FORM_SQL + " QUALIFY row_number() OVER "
            "(PARTITION BY team_id ORDER BY match_date DESC) = 1 "
            "AND team_id = ?", [team_id]).df()
        return form.iloc[0] if not form.empty else None

    h, a = _latest(home_team_id, True), _latest(away_team_id, False)
    row = {"h_elo": elo.get(home_team_id, ELO_START), "a_elo": elo.get(away_team_id, ELO_START)}
    row["elo_diff"] = row["h_elo"] - row["a_elo"]
    for side, data in (("h", h), ("a", a)):
        for c in FORM_COLS:
            v = data[c] if data is not None else np.nan
            row[f"{side}_{c}"] = min(v, REST_CAP) if c == "rest_days" and v is not None and not pd.isna(v) else v
    X = pd.DataFrame([row])[feats].astype(float)
    p = model.predict_proba(X)[0]
    return dict(zip(model.classes_, (float(round(v, 4)) for v in p)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="score only, write nothing")
    a = ap.parse_args()
    train(write=not a.report)
