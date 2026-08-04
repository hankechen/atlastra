"""
Expected goal involvement in a specific fixture.

Match previews currently list a club's best players by season rating, which is the
same answer every week and takes no account of who they are playing, where, or
whether the player has been on the pitch lately. With twelve seasons of match logs
the actual question is answerable: what is the chance THIS player records a goal or
an assist in THIS match?

Modelled over the whole window grid -- every club match inside a player's spell
there, whether he appeared or not (pipeline/build_absences.py builds the same
grid) -- so a player who is being rotated out or is injured carries that in his
number rather than needing a separate caveat. A preview that lists a man who will
not start has failed regardless of how well it models the ones who do.

Features are all knowable before kick-off:
  ability   season-to-date G+A and xG+xA per 90, from earlier matches only
  form      the last five appearances (worth including, and worth not
            overselling -- see `python -m tools.form_test`: form beats a properly
            estimated ability by a real but small margin)
  role      minutes and appearances in the club's last five matches, which is what
            separates a starter from a squad player and a fit player from an absent one
  fixture   opponent's goals conceded per match to date, opponent league position,
            home or away, days of rest since his last appearance

Scored against the baselines that matter -- the club's season rating order, which
is what previews do today, and the player's own season rate -- on held-out seasons.

Persistence:
  * ml/match_contribution.joblib   -- fitted model + feature spec
  * table player_match_contribution_meta -- held-out metrics for the UI note

Run:  PYTHONPATH=. python -m ml.train_match_contribution
      PYTHONPATH=. python -m ml.train_match_contribution --report
"""
import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, log_loss, brier_score_loss

try:
    from config import DB_PATH, ALL_SEASONS
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, ALL_SEASONS

ART = Path(__file__).resolve().parent / "match_contribution.joblib"

# Trained on everything up to here; the seasons after are never seen.
TRAIN_THROUGH = "2223"
FORM_WINDOW = 5
MIN_PRIOR = 5          # earlier appearances needed before a player is predictable

FRAME_SQL = f"""
WITH spell AS (
    SELECT player_id, season, team_id, min(match_date) AS first_app
    FROM player_match_log GROUP BY 1, 2, 3
),
nxt AS (
    SELECT s.*, lead(first_app) OVER (
               PARTITION BY player_id, season ORDER BY first_app) AS next_club_from
    FROM spell s
),
club_end AS (
    SELECT team_id, season, max(match_date) AS last_match
    FROM team_match_stats GROUP BY 1, 2
),
win AS (
    SELECT n.player_id, n.season, n.team_id, n.first_app AS win_from,
           LEAST(COALESCE(n.next_club_from - INTERVAL 1 DAY, DATE '9999-12-31'),
                 c.last_match) AS win_to
    FROM nxt n JOIN club_end c ON c.team_id = n.team_id AND c.season = n.season
),
grid AS (              -- every club match in the window, appeared or not
    SELECT w.player_id, w.season, w.team_id, t.game_id, t.match_date,
           t.is_home, t.opponent_team_id,
           COALESCE(l.minutes, 0) AS minutes,
           COALESCE(l.goals, 0) + COALESCE(l.assists, 0) AS ga,
           COALESCE(l.xg, 0) + COALESCE(l.xa, 0) AS xga,
           CASE WHEN l.player_id IS NULL THEN 0 ELSE 1 END AS played
    FROM win w
    JOIN team_match_stats t
      ON t.team_id = w.team_id AND t.season = w.season
     AND t.match_date BETWEEN w.win_from AND w.win_to
    LEFT JOIN player_match_log l
      ON l.player_id = w.player_id AND l.game_id = t.game_id
),
seq AS (
    SELECT *,
           row_number() OVER w AS rn,
           -- everything below is strictly BEFORE this match
           sum(ga)      OVER wp AS ga_prior,
           sum(xga)     OVER wp AS xga_prior,
           sum(minutes) OVER wp AS mins_prior,
           sum(played)  OVER wp AS apps_prior,
           sum(ga)      OVER wf AS ga_form,
           sum(xga)     OVER wf AS xga_form,
           sum(minutes) OVER wf AS mins_form,
           sum(played)  OVER wf AS apps_form,
           max(CASE WHEN played = 1 THEN match_date END) OVER wp AS last_app_date
    FROM grid
    WINDOW w  AS (PARTITION BY player_id, season, team_id ORDER BY match_date),
           wp AS (PARTITION BY player_id, season, team_id ORDER BY match_date
                  ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
           wf AS (PARTITION BY player_id, season, team_id ORDER BY match_date
                  ROWS BETWEEN {FORM_WINDOW} PRECEDING AND 1 PRECEDING)
),
opp AS (               -- how leaky the opponent has been to date this season
    SELECT team_id, season, match_date,
           avg(goals_against) OVER (PARTITION BY team_id, season ORDER BY match_date
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
               AS opp_ga_conceded,
           avg(xg_against)    OVER (PARTITION BY team_id, season ORDER BY match_date
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
               AS opp_xga_conceded
    FROM team_match_stats
)
SELECT s.player_id, s.season, s.game_id, s.match_date, s.is_home,
       s.ga, s.played, s.minutes,
       s.ga_prior, s.xga_prior, s.mins_prior, s.apps_prior,
       s.ga_form, s.xga_form, s.mins_form, s.apps_form,
       date_diff('day', s.last_app_date, s.match_date) AS rest_days,
       o.opp_ga_conceded, o.opp_xga_conceded,
       ts.league_position AS opp_pos,
       c.rating, c.position_group AS grp
FROM seq s
LEFT JOIN opp o ON o.team_id = s.opponent_team_id AND o.season = s.season
               AND o.match_date = s.match_date
LEFT JOIN team_season_stats ts ON ts.team_id = s.opponent_team_id AND ts.season = s.season
LEFT JOIN player_ratings_combined c ON c.player_id = s.player_id AND c.season = s.season
                                   AND c.scope = 'league'
WHERE s.apps_prior >= {MIN_PRIOR}
"""

POS = ["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]


def _features(df: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    x = pd.DataFrame(index=df.index)
    mp = df["mins_prior"].clip(lower=1)
    mf = df["mins_form"].clip(lower=1)

    x["ability_ga90"] = df["ga_prior"] / mp * 90
    x["ability_xga90"] = df["xga_prior"] / mp * 90
    x["form_ga90"] = np.where(df["mins_form"] > 0, df["ga_form"] / mf * 90, np.nan)
    x["form_xga90"] = np.where(df["mins_form"] > 0, df["xga_form"] / mf * 90, np.nan)
    # role and fitness: minutes in the club's recent matches is what separates a
    # starter from a bench option, and a fit player from one who is not there
    x["mins_form"] = df["mins_form"].astype(float)
    x["apps_form"] = df["apps_form"].astype(float)
    x["mins_per_app"] = df["mins_prior"] / df["apps_prior"].clip(lower=1)
    x["apps_prior"] = df["apps_prior"].astype(float)
    x["rest_days"] = df["rest_days"].clip(upper=120).astype(float)
    x["is_home"] = df["is_home"].astype(float)
    x["opp_ga_conceded"] = df["opp_ga_conceded"].astype(float)
    x["opp_xga_conceded"] = df["opp_xga_conceded"].astype(float)
    x["opp_pos"] = df["opp_pos"].astype(float)
    x["rating"] = df["rating"].astype(float)
    for g in POS:
        x[f"is_{g}"] = (df["grp"] == g).astype(float)
    return x, list(x.columns)


def train(write: bool = True) -> dict:
    con = duckdb.connect(str(DB_PATH), read_only=not write)
    df = con.execute(FRAME_SQL).df()
    df = df[df["season"].isin(ALL_SEASONS)].reset_index(drop=True)
    X, feats = _features(df)
    y = (df["ga"] >= 1).astype(int).values

    tr = (df["season"] <= TRAIN_THROUGH).values
    te = ~tr
    print(f"[contrib] {len(df):,} player-fixtures  ({tr.sum():,} train <= {TRAIN_THROUGH}, "
          f"{te.sum():,} held out)  base rate {y.mean():.1%}", flush=True)

    params = dict(max_iter=300, max_depth=6, learning_rate=0.06,
                  min_samples_leaf=60, l2_regularization=1.0, random_state=42)
    model = HistGradientBoostingClassifier(**params).fit(X.values[tr], y[tr])
    p = model.predict_proba(X.values[te])[:, 1]

    # Baselines. The one that matters is what a preview does today: rank a club's
    # players by season rating and call the top ones the danger men.
    base_rating = df.loc[te, "rating"].fillna(df["rating"].median()).values
    base_rate = (df.loc[te, "ga_prior"] / df.loc[te, "mins_prior"].clip(lower=1) * 90).values

    auc = roc_auc_score(y[te], p)
    m = {
        "n": int(len(df)), "n_test": int(te.sum()), "base_rate": round(float(y.mean()), 4),
        "auc": round(float(auc), 4),
        "auc_rating": round(float(roc_auc_score(y[te], base_rating)), 4),
        "auc_season_rate": round(float(roc_auc_score(y[te], base_rate)), 4),
        "log_loss": round(float(log_loss(y[te], p)), 4),
        "log_loss_base": round(float(log_loss(y[te], np.full(te.sum(), y[tr].mean()))), 4),
        "brier": round(float(brier_score_loss(y[te], p)), 4),
    }
    print(f"[contrib] AUC {m['auc']}  vs season rating {m['auc_rating']}  "
          f"vs his own season rate {m['auc_season_rate']}", flush=True)
    print(f"[contrib] log loss {m['log_loss']} vs {m['log_loss_base']} for the base rate; "
          f"Brier {m['brier']}", flush=True)

    # The number a reader actually cares about: pick the three likeliest players in
    # each fixture -- how often does one of them deliver?
    t = df.loc[te, ["game_id"]].copy()
    t["p"], t["y"] = p, y[te]
    t["r"] = base_rating
    top_model = t.sort_values("p", ascending=False).groupby("game_id").head(3)
    top_rating = t.sort_values("r", ascending=False).groupby("game_id").head(3)
    m["hit_top3"] = round(float(top_model["y"].mean()), 4)
    m["hit_top3_rating"] = round(float(top_rating["y"].mean()), 4)
    print(f"[contrib] of the 3 players it flags per fixture, {m['hit_top3']:.1%} record a goal or "
          f"assist — against {m['hit_top3_rating']:.1%} picking by season rating", flush=True)

    # calibration: a 20% call should land 20% of the time
    bins = np.clip((p * 10).astype(int), 0, 9)
    cal = [{"band": f"{b * 10}-{b * 10 + 10}%", "n": int((bins == b).sum()),
            "predicted": round(float(p[bins == b].mean()), 3),
            "actual": round(float(y[te][bins == b].mean()), 3)}
           for b in range(10) if (bins == b).sum() >= 200]
    m["calibration"] = cal
    print("[contrib] calibration:", flush=True)
    for c in cal:
        print(f"[contrib]   {c['band']:<8} n={c['n']:6d}  predicted {c['predicted']:.3f}  "
              f"actual {c['actual']:.3f}", flush=True)

    if not write:
        con.close()
        return m

    con.execute("DROP TABLE IF EXISTS player_match_contribution_meta")
    con.execute("""CREATE TABLE player_match_contribution_meta(
        n INTEGER, n_test INTEGER, base_rate DOUBLE, auc DOUBLE, auc_rating DOUBLE,
        auc_season_rate DOUBLE, log_loss DOUBLE, brier DOUBLE,
        hit_top3 DOUBLE, hit_top3_rating DOUBLE, train_through VARCHAR)""")
    con.execute("INSERT INTO player_match_contribution_meta VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [m["n"], m["n_test"], m["base_rate"], m["auc"], m["auc_rating"],
                 m["auc_season_rate"], m["log_loss"], m["brier"],
                 m["hit_top3"], m["hit_top3_rating"], TRAIN_THROUGH])
    con.close()

    final = HistGradientBoostingClassifier(**params).fit(X.values, y)
    dump({"model": final, "feats": feats, "metrics": m}, ART)
    print(f"[contrib] artifact -> {ART.name}", flush=True)
    return m


# --------------------------------------------------------------------------- #
# Serving
# --------------------------------------------------------------------------- #
# A fixture that has not been played cannot come out of the match log, so the
# serving path rebuilds each player's state as of his club's LAST recorded match
# and swaps in the opponent and venue being asked about. Same feature code as
# training -- importing it rather than restating it is the whole point, because a
# serving/training skew here would be invisible and would quietly ruin the
# predictions.
SERVE_SQL = FRAME_SQL.replace(
    f"WHERE s.apps_prior >= {MIN_PRIOR}",
    f"""QUALIFY row_number() OVER (PARTITION BY s.player_id ORDER BY s.match_date DESC) = 1
        AND s.season = ? AND s.apps_prior >= {MIN_PRIOR}"""
)


def predict_fixture(con, team_id: int, opponent_id: int, is_home: bool,
                    season: str, model=None, feats=None) -> pd.DataFrame:
    """Chance of a goal involvement for each of a club's players in one fixture."""
    if model is None:
        from joblib import load
        art = load(ART)
        model, feats = art["model"], art["feats"]

    df = con.execute(SERVE_SQL, [season]).df()
    df = df[df["player_id"].isin(
        con.execute("""SELECT DISTINCT player_id FROM player_match_log
                       WHERE team_id = ? AND season = ?""", [team_id, season]).df()["player_id"]
    )].copy()
    if df.empty:
        return df

    # the fixture being asked about, not the last one he played
    opp = con.execute("""
        SELECT avg(goals_against), avg(xg_against) FROM team_match_stats
        WHERE team_id = ? AND season = ?""", [opponent_id, season]).fetchone()
    pos = con.execute("""SELECT league_position FROM team_season_stats
                         WHERE team_id = ? AND season = ?""", [opponent_id, season]).fetchone()
    df["is_home"] = bool(is_home)
    df["opp_ga_conceded"] = opp[0] if opp else None
    df["opp_xga_conceded"] = opp[1] if opp else None
    df["opp_pos"] = pos[0] if pos else None
    # rest is measured to the next fixture, which we take as a week out; the model
    # is barely sensitive to it and inventing a date would be worse
    df["rest_days"] = df["rest_days"].fillna(7).clip(upper=120)

    X, _ = _features(df)
    df["p"] = model.predict_proba(X[feats].values)[:, 1]
    return df.sort_values("p", ascending=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="score only, write nothing")
    a = ap.parse_args()
    train(write=not a.report)
