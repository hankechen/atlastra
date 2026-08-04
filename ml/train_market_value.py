"""
Fair Market Value model.

Learns what a player's market value "should" be from AGE + our Atlastra rating +
per-90 performance + position, then compares that model estimate to the player's
ACTUAL Transfermarkt value to flag over- / under-valued players (a scouting signal).

Data (all already in the warehouse, FOCUS_SEASON):
  target   = player_market_value.market_value_eur   (~488 players, €15m–€200m)
  features = age (player_bio/wyscout), Atlastra league rating (player_ratings_combined),
             per-90 output + involvement (v_player_season_stats), position group.

Model: GradientBoostingRegressor on log1p(value in €m). Small/shallow to avoid overfit.
Reports 5-fold CV R² + MAE (in €m) and a held-out test split — honestly.

Persistence (build artifacts, like the rating tables — NOT in git):
  * ml/market_value_model.joblib          — fitted model + feature spec + medians
  * table  player_value_model             — per-player predicted vs actual + verdict
  * table  player_value_drivers           — per-player local feature contributions (why)

Run:  PYTHONPATH=. python -m ml.train_market_value
"""
import json
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import KFold, cross_val_predict, train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

try:
    from config import DB_PATH, FOCUS_SEASON
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON

ART = Path(__file__).resolve().parent / "market_value_model.joblib"

# per-90 involvement stats (imputed to 0 when the FotMob enrichment is missing)
PER90_FILL0 = ["chances_created", "big_chances_created", "dribbles_completed",
               "tackles", "interceptions"]
# rate stats imputed to the column median when missing
MEDIAN_FILL = ["rating", "duels_won_pct", "pass_accuracy_pct", "fotmob_rating"]
# league is a strong value signal (EPL premium) — fixed one-hot order for reproducibility
LEAGUES = ["ENG-Premier League", "ESP-La Liga", "ITA-Serie A",
           "GER-Bundesliga", "FRA-Ligue 1"]

# human labels for the explainability drivers shown in the UI
LABELS = {
    "age": "Age", "age_sq": "Age",
    "rating": "Atlastra rating", "fotmob_rating": "Avg match rating",
    "games": "Appearances", "minutes": "Minutes played",
    "goals_per90": "Goal rate", "assists_per90": "Assist rate",
    "xg_per90": "Shot quality (xG)", "xa_per90": "Chance quality (xA)",
    "shots_per90": "Shot volume", "chances_created_per90": "Chances created",
    "big_chances_created_per90": "Big chances created", "dribbles_completed_per90": "Dribbling",
    "tackles_per90": "Tackling", "interceptions_per90": "Interceptions",
    "duels_won_pct": "Duel win %", "pass_accuracy_pct": "Passing accuracy",
    "is_DEF": "Position (defender)", "is_MID": "Position (midfielder)",
    "is_FWD": "Position (forward)", "is_GK": "Position (goalkeeper)",
    "goals_tot": "Goals (total)", "assists_tot": "Assists (total)",
    "lg_ENG-Premier League": "Premier League", "lg_ESP-La Liga": "La Liga",
    "lg_ITA-Serie A": "Serie A", "lg_GER-Bundesliga": "Bundesliga",
    "lg_FRA-Ligue 1": "Ligue 1",
}


def _load(con) -> pd.DataFrame:
    df = con.execute("""
        SELECT mv.player_id, pl.player_name, pl.position_group AS grp,
               mv.league_key AS lg, mv.market_value_eur AS val,
               COALESCE(b.fotmob_age, w.age) AS age,
               c.rating,
               s.minutes, s.games, s.goals, s.assists, s.xg, s.xa, s.shots,
               s.chances_created, s.big_chances_created, s.dribbles_completed,
               s.tackles, s.interceptions, s.duels_won_pct, s.pass_accuracy_pct,
               s.fotmob_rating
        FROM player_market_value mv
        JOIN players pl USING(player_id)
        LEFT JOIN player_bio b ON b.player_id = mv.player_id
        LEFT JOIN (SELECT player_id, MIN(age) age FROM player_wyscout GROUP BY 1) w
               ON w.player_id = mv.player_id
        LEFT JOIN player_ratings_combined c
               ON c.player_id = mv.player_id AND c.scope='league' AND c.season = ?
        LEFT JOIN v_player_season_stats s
               ON s.player_id = mv.player_id AND s.season = ?
        WHERE mv.season = ? AND COALESCE(b.fotmob_age, w.age) IS NOT NULL
        ORDER BY mv.player_id
    """, [FOCUS_SEASON, FOCUS_SEASON, FOCUS_SEASON]).df()
    return df


def _features(df: pd.DataFrame, medians: dict | None = None):
    """Return (X DataFrame, feature list, medians dict). Deterministic feature build."""
    x = pd.DataFrame(index=df.index)
    mins = df["minutes"].clip(lower=1)
    x["age"] = df["age"].astype(float)
    x["age_sq"] = x["age"] ** 2
    x["games"] = df["games"].fillna(0).astype(float)
    x["minutes"] = df["minutes"].fillna(0).astype(float)
    for col in ["goals", "assists", "xg", "xa", "shots"]:
        x[f"{col}_per90"] = (df[col].fillna(0) / mins * 90).astype(float)
    for col in PER90_FILL0:
        x[f"{col}_per90"] = (df[col].fillna(0) / mins * 90).astype(float)
    for col in ["goals", "assists"]:                 # absolute season production
        x[f"{col}_tot"] = df[col].fillna(0).astype(float)
    for col in MEDIAN_FILL:
        x[col] = df[col].astype(float)
    for g in ["DEF", "MID", "FWD", "GK"]:
        x[f"is_{g}"] = (df["grp"] == g).astype(float)
    for lg in LEAGUES:                               # league premium (EPL etc.)
        x[f"lg_{lg}"] = (df["lg"] == lg).astype(float)
    # median imputation (fit-time medians reused at score-time)
    if medians is None:
        medians = {c: float(x[c].median()) for c in MEDIAN_FILL}
    for c in MEDIAN_FILL:
        x[c] = x[c].fillna(medians[c])
    feats = list(x.columns)
    return x, feats, medians


def _drivers(model, x_row: pd.Series, feats: list, baseline: np.ndarray) -> list:
    """Local, ablation-based explanation: € impact of each feature = model estimate
    with the real value minus the estimate with that feature set to the training
    baseline (median). Reflects THIS model's reasoning, no extra deps."""
    full = float(np.expm1(model.predict(x_row.values.reshape(1, -1))[0]))
    out = {}
    for i, f in enumerate(feats):
        xa = x_row.values.copy()
        xa[i] = baseline[i]
        abl = float(np.expm1(model.predict(xa.reshape(1, -1))[0]))
        out.setdefault(LABELS.get(f, f), 0.0)
        out[LABELS.get(f, f)] += full - abl          # merge age/age_sq, position dummies
    items = [{"label": k, "impact_m": round(v, 1)} for k, v in out.items() if abs(v) >= 0.5]
    items.sort(key=lambda d: -abs(d["impact_m"]))
    return items[:5]


def train() -> dict:
    con = duckdb.connect(str(DB_PATH))
    df = _load(con).reset_index(drop=True)
    n = len(df)
    X, feats, medians = _features(df)
    y = np.log1p(df["val"].values / 1e6)          # log of value in € millions

    params = dict(n_estimators=500, max_depth=2, learning_rate=0.03,
                  subsample=0.8, min_samples_leaf=10, random_state=42)

    # honest evaluation: 5-fold CV (out-of-fold predictions) + a held-out split
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    oof = cross_val_predict(GradientBoostingRegressor(**params), X.values, y, cv=kf)
    cv_r2 = r2_score(y, oof)
    cv_mae_m = mean_absolute_error(np.expm1(y), np.expm1(oof))       # € millions
    Xtr, Xte, ytr, yte = train_test_split(X.values, y, test_size=0.2, random_state=42)
    hold = GradientBoostingRegressor(**params).fit(Xtr, ytr)
    te_r2 = r2_score(yte, hold.predict(Xte))
    te_mae_m = mean_absolute_error(np.expm1(yte), np.expm1(hold.predict(Xte)))

    # final model on ALL data (used for the persisted per-player estimates)
    model = GradientBoostingRegressor(**params).fit(X.values, y)
    baseline = X.median().values

    pred_m = np.expm1(model.predict(X.values))                       # € millions
    df["pred"] = (pred_m * 1e6).round(-5)                            # to nearest €0.1m
    df["ratio"] = df["pred"] / df["val"]

    def verdict(ratio):
        if ratio >= 1.25:  return "Undervalued", "good"
        if ratio <= 0.80:  return "Overvalued", "bad"
        return "Fairly valued", "neutral"

    rows_v, rows_d = [], []
    for i, r in df.iterrows():
        v, cls = verdict(r["ratio"])
        rows_v.append((int(r["player_id"]), FOCUS_SEASON, int(r["val"]), int(r["pred"]),
                       round(float(r["ratio"]), 3), v, cls))
        for d in _drivers(model, X.iloc[i], feats, baseline):
            rows_d.append((int(r["player_id"]), FOCUS_SEASON, d["label"], float(d["impact_m"])))

    con.execute("DROP TABLE IF EXISTS player_value_model")
    con.execute("""CREATE TABLE player_value_model(
        player_id BIGINT, season VARCHAR, actual_eur BIGINT, predicted_eur BIGINT,
        ratio DOUBLE, verdict VARCHAR, verdict_class VARCHAR)""")
    con.executemany("INSERT INTO player_value_model VALUES (?,?,?,?,?,?,?)", rows_v)
    con.execute("DROP TABLE IF EXISTS player_value_drivers")
    con.execute("""CREATE TABLE player_value_drivers(
        player_id BIGINT, season VARCHAR, label VARCHAR, impact_m DOUBLE)""")
    con.executemany("INSERT INTO player_value_drivers VALUES (?,?,?,?)", rows_d)

    # persist honest headline metrics for the UI note (single-row meta table)
    con.execute("DROP TABLE IF EXISTS player_value_meta")
    con.execute("""CREATE TABLE player_value_meta(
        season VARCHAR, n INTEGER, cv_r2 DOUBLE, cv_mae_m DOUBLE,
        test_r2 DOUBLE, test_mae_m DOUBLE)""")
    con.execute("INSERT INTO player_value_meta VALUES (?,?,?,?,?,?)",
                [FOCUS_SEASON, n, round(cv_r2, 3), round(cv_mae_m, 1),
                 round(te_r2, 3), round(te_mae_m, 1)])

    # global feature importance (for reporting / a possible UI note)
    imp = sorted(zip(feats, model.feature_importances_), key=lambda t: -t[1])
    dump({"model": model, "feats": feats, "medians": medians,
          "baseline": baseline.tolist(), "season": FOCUS_SEASON}, ART)
    con.close()

    metrics = {"n": n, "cv_r2": round(cv_r2, 3), "cv_mae_m": round(cv_mae_m, 1),
               "test_r2": round(te_r2, 3), "test_mae_m": round(te_mae_m, 1),
               "top_features": [(f, round(w, 3)) for f, w in imp[:8]]}
    print(f"[market_value] n={n}  CV R²={metrics['cv_r2']}  CV MAE=€{metrics['cv_mae_m']}m  "
          f"| holdout R²={metrics['test_r2']}  MAE=€{metrics['test_mae_m']}m", flush=True)
    print("[market_value] top features:", metrics["top_features"], flush=True)
    print(f"[market_value] wrote player_value_model ({len(rows_v)}) + "
          f"player_value_drivers ({len(rows_d)}); artifact -> {ART.name}", flush=True)
    return metrics


if __name__ == "__main__":
    train()
