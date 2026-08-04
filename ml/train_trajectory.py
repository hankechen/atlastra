"""
Career Trajectory model — what a player does NEXT season.

Everything else in Atlastra looks backwards: it scores the season that happened.
This is the one model that projects a player forward. It learns, from twelve
seasons of the top-5 panel, how a player's Atlastra rating moves from one season
to the next given his age, his last three seasons, his workload and how much of
his output was finishing luck.

Two heads, because "how good will he be" and "will he still be here" are
different questions and conflating them flatters the projection:

  * availability  P(the player is still a top-5 regular next season)  — classifier
  * projection    his Atlastra league rating next season               — regressor,
                  trained and reported on the players who ARE still there

Availability data (pipeline/build_absences.py) is in the feature set, and what it
turned out to be worth is written down beside it rather than assumed:

  * it does NOT scar the next season's rating. Control for a player's current
    level and those who missed 10+ consecutive matches move almost identically to
    those who missed none (in the 65-80 band, -9.3 against -9.3). Conditional on
    coming back at all, a season lost to injury does not appear to cost rating.
  * it adds almost nothing to the availability head either (+0.001 AUC), because
    minutes-share already proxies most of it.
  * what it does predict is more absence — but barely well enough to model, and
    that is handled below as a measured table rather than a per-player score.

Data (all already in the warehouse):
  panel   = player_ratings_combined (scope='league')   12 seasons, ~20.6k rows
  lags    = the same table at t-1, t-2
  output  = v_player_season_stats per-90s at t
  age     = player_dob / player_bio birth date, evaluated at each season's midpoint

Honesty, in the house style (see tools/backtest.py):
  * the split is by TIME, not at random — train on transitions up to 2021/22,
    test on 2022/23, 2023/24 and 2024/25, which the model never saw
  * **goalkeepers are covered, but they were not at first.** They used to be rated
    in 2025/26 alone, so no GK transition existed to fit or score, and the model
    read a keeper's empty attacking line as an outfielder in freefall. The fix
    belonged upstream, not here: pipeline/scrape_sofa_gk.py backfills keeper
    metrics to 2015/16, and keepers are now modelled on the same footing. Their
    ratings move ~13 points a season against ~9 for a midfielder, and the fitted
    interval picks that up on its own — GK bands come out ~5 points wider
  * **only players whose age we actually know are modelled.** This costs ~20% of
    the training rows and it is not optional. Our birth dates come from FotMob,
    whose coverage starts in 2020/21, so a *missing* birth date very nearly means
    "this player had already left the top-5 by 2020" — which is the availability
    target itself. Left in, that artefact of our own scrape is the single most
    predictive feature (48% present vs 79%, and the gap moves between the train
    and test periods), and it lifts availability AUC from 0.79 to 0.84 by
    reading our data collection rather than football. So the panel is restricted
    to known ages and the weaker, real number is the one reported
  * scored against the baseline that actually matters: PERSISTENCE ("he'll be
    exactly as good as he was"). Ratings are percentile-based and sticky, so
    persistence is hard to beat, and skill-over-persistence is the real number.
    A model that beats only the position mean has learned nothing useful.
  * the projection carries the held-out residual as an error bar, and the UI
    shows it, because a projection without one is a guess wearing a suit.

Persistence (build artifacts, like the rating tables — NOT in git):
  * ml/trajectory_model.joblib      — fitted models + feature spec + medians
  * table player_trajectory         — per-player projection for the next season
  * table player_trajectory_drivers — per-player local feature contributions (why)
  * table trajectory_aging_curve    — measured rating delta by age × position
  * table trajectory_meta           — held-out metrics, for the UI note

Run:  PYTHONPATH=. python -m ml.train_trajectory
      PYTHONPATH=. python -m ml.train_trajectory --report   (metrics only, no write)
"""
import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score, roc_auc_score

try:
    from config import DB_PATH, FOCUS_SEASON, ALL_SEASONS
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON, ALL_SEASONS

ART = Path(__file__).resolve().parent / "trajectory_model.joblib"

# Transitions with a season code <= this train the model; everything after is
# held out. 2122 -> 2223 is the last transition the model is allowed to see, so
# 2223, 2324 and 2425 are scored blind (~3.6k projections).
TRAIN_THROUGH = "2122"

# A projected move of this many rating points or more counts as a "big call" —
# the kind that fills the risers/fallers boards and that a reader is entitled to
# be sceptical of. Scored separately on the held-out seasons.
BIG_MOVE = 15.0

# The projection's error bar is an 80% interval, from two quantile models rather
# than one residual sd applied to everybody. The last training season is spent on
# calibrating that band rather than fitting it (conformalized quantile
# regression) — the held-out seasons must not be used to tune what is then
# reported as held-out coverage.
Q_LO, Q_HI = 0.10, 0.90
CALIB_SEASON = "2122"

# "Lost a long run to one absence" — ten consecutive club matches, roughly two
# and a half months. Long enough that rotation and a short ban are not plausible
# explanations, short enough to be common enough to model (~11% of seasons).
LONG_ABSENCE = 10

# A player is "present" next season if he clears the rating minutes bar, i.e. he
# appears in player_ratings_combined at t+1. Below that he has left the top-5,
# lost his place, or retired — all of which the availability head predicts.
#
# Goalkeepers were excluded here at first, and for a real reason: the engine only
# rated them in 2025/26, so the warehouse held no GK transition at all and the
# model quietly read their empty per-90 attacking line as an outfielder having a
# terrible year — it "corrected" a 19-rated keeper up to 47. That was fixed at the
# source (pipeline/scrape_sofa_gk.py backfilled keeper metrics to 2015/16), so
# they are modelled now, on the same footing as everyone else.
#
# They keep their own group because they are scored on a different vector — save
# %, goals conceded, clean sheets — and none of the attacking per-90 features
# mean anything for them; the position dummy is what lets the model learn that.
POSITION_GROUPS = ["GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"]
EXCLUDED_GROUPS: list[str] = []
LEAGUES = ["ENG-Premier League", "ESP-La Liga", "ITA-Serie A",
           "GER-Bundesliga", "FRA-Ligue 1"]

MEDIAN_FILL = ["age", "team_ppg", "team_pos", "availability_pct"]

LABELS = {
    "age": "Age", "age_sq": "Age", "age_cu": "Age",
    "rating": "Current rating", "rating_lag1": "Last season's rating",
    "rating_lag2": "Two seasons ago", "has_lag1": "Career length",
    "has_lag2": "Career length", "n_seasons": "Career length",
    "d_rating": "Recent trend", "d_rating2": "Recent trend",
    "rating_mean3": "Three-season form", "rating_max": "Career-best rating",
    "minutes": "Minutes played", "minutes_lag1": "Minutes last season",
    "d_minutes": "Change in minutes", "games": "Appearances",
    "goals_per90": "Goal rate", "assists_per90": "Assist rate",
    "xg_per90": "Shot quality (xG)", "xa_per90": "Chance quality (xA)",
    "shots_per90": "Shot volume", "key_passes_per90": "Chances created",
    "finishing": "Finishing vs xG", "creation": "Assists vs xA",
    "team_ppg": "Club's strength", "team_pos": "Club's strength",
    "minutes_share": "Share of club's minutes",
    "availability_pct": "Availability", "longest_spell": "Longest absence",
    "extended_spells": "Spells out", "missed_long": "Longest absence",
    **{f"is_{g}": "Position" for g in POSITION_GROUPS},
    **{f"lg_{l}": "League" for l in LEAGUES},
}

# Deliberately age-neutral wording. "Breakout" belongs to the young-player board,
# which filters on age; as a verdict it would also land on a 32-year-old bouncing
# back from an outlier season, which is a rebound, not a breakout.
VERDICTS = [
    # (min delta, label, css class, blurb)
    (3.5,  "Sharp rise", "great",   "projected to climb sharply"),
    (1.2,  "Rising",     "good",    "projected to improve"),
    (-1.2, "Steady",     "neutral", "projected to hold his level"),
    (-3.5, "Declining",  "warn",    "projected to slip"),
    (-99,  "Sharp fall", "bad",     "projected to drop sharply"),
]


def _exclude_sql() -> str:
    """Position-group exclusion clause. Empty when nothing is excluded — an
    unguarded `NOT IN ()` is a syntax error, not a no-op."""
    if not EXCLUDED_GROUPS:
        return ""
    return ("AND r.position_group NOT IN ("
            + ",".join("'" + g + "'" for g in EXCLUDED_GROUPS) + ")")


def _season_mid(code: str) -> pd.Timestamp:
    """Midpoint of a season, used as the age reference date. '2526' -> 2026-01-01."""
    return pd.Timestamp(year=2000 + int(code[2:]), month=1, day=1)


def _next_season(code: str) -> str | None:
    """'2425' -> '2526'. None past the end of the collected range."""
    nxt = f"{int(code[:2]) + 1:02d}{int(code[2:]) + 1:02d}"
    return nxt


def _load_panel(con) -> pd.DataFrame:
    """One row per (player, season) with rating, output and birth date."""
    dob_tbl = "player_dob" if con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='player_dob'"
    ).fetchone()[0] else None

    dob_sql = (
        "LEFT JOIN player_dob d ON d.player_id = r.player_id"
        if dob_tbl else "LEFT JOIN (SELECT NULL::BIGINT player_id, NULL::DATE date_of_birth) d ON FALSE"
    )
    df = con.execute(f"""
        SELECT r.player_id, r.season, r.position_group AS grp, r.minutes, r.rating,
               pl.player_name,
               COALESCE(CAST(d.date_of_birth AS VARCHAR),
                        CAST(b.date_of_birth AS VARCHAR)) AS dob,
               s.games, s.goals, s.assists, s.xg, s.xa, s.shots, s.key_passes,
               lg.league_key AS lg,
               t.points / NULLIF(t.matches_played, 0) AS team_ppg,
               t.league_position AS team_pos,
               t.matches_played * 90.0 AS team_minutes,
               av.availability_pct, av.longest_spell, av.extended_spells
        FROM player_ratings_combined r
        JOIN players pl USING(player_id)
        {dob_sql}
        LEFT JOIN player_bio b ON b.player_id = r.player_id
        LEFT JOIN v_player_season_stats s
               ON s.player_id = r.player_id AND s.season = r.season
        LEFT JOIN (
            SELECT player_id, season,
                   ARG_MAX(league_key, minutes) AS league_key,
                   ARG_MAX(team_id, minutes)    AS team_id
            FROM player_season_stats GROUP BY 1, 2
        ) lg ON lg.player_id = r.player_id AND lg.season = r.season
        LEFT JOIN team_season_stats t
               ON t.team_id = lg.team_id AND t.season = r.season
        -- availability, derived from the match log (pipeline/build_absences.py).
        -- A player with two clubs in a season has two windows; pool them, because
        -- what matters is the share of his club football he was actually there for.
        LEFT JOIN (
            SELECT player_id, season,
                   round(100.0 * sum(played) / NULLIF(sum(window_matches), 0), 1)
                       AS availability_pct,
                   max(longest_spell)  AS longest_spell,
                   sum(extended_spells) AS extended_spells
            FROM player_availability GROUP BY 1, 2
        ) av ON av.player_id = r.player_id AND av.season = r.season
        WHERE r.scope = 'league'
          {_exclude_sql()}
        ORDER BY r.player_id, r.season
    """).df()
    df["dob"] = pd.to_datetime(df["dob"], errors="coerce")
    return df


def _build(df: pd.DataFrame) -> pd.DataFrame:
    """Add lags, the next-season targets, and age. One row per transition t -> t+1."""
    df = df.sort_values(["player_id", "season"]).reset_index(drop=True)
    df["season_i"] = df["season"].map(ALL_SEASONS.index)

    # lags/leads by position in the player's own season sequence, but only count
    # them as lag-1 when the season really is consecutive (a player who drops out
    # of the top-5 for a year must not have that gap read as continuity).
    g = df.groupby("player_id", sort=False)
    for k in (1, 2):
        df[f"rating_lag{k}"] = g["rating"].shift(k)
        df[f"minutes_lag{k}"] = g["minutes"].shift(k)
        gap = df["season_i"] - g["season_i"].shift(k)
        bad = gap != k
        df.loc[bad, [f"rating_lag{k}", f"minutes_lag{k}"]] = np.nan

    df["rating_next"] = g["rating"].shift(-1)
    lead_gap = g["season_i"].shift(-1) - df["season_i"]
    df.loc[lead_gap != 1, "rating_next"] = np.nan

    # careful: absent from the panel at t+1 means "not a top-5 regular", which is
    # only knowable for transitions whose target season we actually collected.
    df["target_season"] = df["season"].map(lambda s: _next_season(s))
    df["has_target"] = df["target_season"].isin(ALL_SEASONS)
    df["present_next"] = np.where(df["has_target"], df["rating_next"].notna().astype(float), np.nan)

    df["n_seasons"] = g.cumcount() + 1
    df["rating_max"] = g["rating"].cummax()

    # durability target: does he lose a long run to one absence NEXT season? Only
    # answerable for a player who is actually around next season to be absent from,
    # so it is null (not zero) for anyone who leaves — the availability head owns
    # that question, and treating "gone" as "fit" would flatter this one.
    df["spell_next"] = g["longest_spell"].shift(-1)
    df.loc[lead_gap != 1, "spell_next"] = np.nan
    df["long_absence_next"] = np.where(df["spell_next"].notna(),
                                       (df["spell_next"] >= LONG_ABSENCE).astype(float), np.nan)
    # career absence history to date, which is the injury-proneness signal itself
    df["spell_prev"] = g["longest_spell"].shift(1)
    df["spell_career_max"] = g["longest_spell"].cummax()

    ref = df["season"].map(_season_mid)
    df["age"] = (ref - df["dob"]).dt.days / 365.25
    df.loc[(df["age"] < 15) | (df["age"] > 44), "age"] = np.nan
    return df


def _features(df: pd.DataFrame, medians: dict | None = None):
    """Deterministic feature build. Returns (X, feature list, medians)."""
    x = pd.DataFrame(index=df.index)
    mins = df["minutes"].clip(lower=1)

    x["age"] = df["age"].astype(float)
    x["rating"] = df["rating"].astype(float)
    # a missing lag is a real signal (first top-5 season / came back after a gap),
    # so it is flagged rather than silently filled with the current rating
    for k in (1, 2):
        x[f"rating_lag{k}"] = df[f"rating_lag{k}"].fillna(df["rating"]).astype(float)
        x[f"has_lag{k}"] = df[f"rating_lag{k}"].notna().astype(float)
    x["d_rating"] = (df["rating"] - df["rating_lag1"]).fillna(0).astype(float)
    x["d_rating2"] = (df["rating_lag1"] - df["rating_lag2"]).fillna(0).astype(float)
    x["rating_mean3"] = x[["rating", "rating_lag1", "rating_lag2"]].mean(axis=1)
    x["rating_max"] = df["rating_max"].astype(float)
    x["n_seasons"] = df["n_seasons"].astype(float)

    x["minutes"] = df["minutes"].fillna(0).astype(float)
    x["minutes_lag1"] = df["minutes_lag1"].fillna(df["minutes"]).astype(float)
    x["d_minutes"] = (x["minutes"] - x["minutes_lag1"]).astype(float)
    x["games"] = df["games"].fillna(0).astype(float)

    for col in ["goals", "assists", "xg", "xa", "shots", "key_passes"]:
        x[f"{col}_per90"] = (df[col].fillna(0) / mins * 90).astype(float)
    # the mean-reversion signals: output the underlying numbers did not support
    x["finishing"] = x["goals_per90"] - x["xg_per90"]
    x["creation"] = x["assists_per90"] - x["xa_per90"]

    # club context: the same rating means different things at a title winner and
    # at a relegation side, and a big share of a club's minutes means a starter
    x["team_ppg"] = df["team_ppg"].astype(float)
    x["team_pos"] = df["team_pos"].astype(float)
    x["minutes_share"] = (df["minutes"] / df["team_minutes"].clip(lower=1)).clip(0, 1).astype(float)

    # availability: whether he was fit and picked, which minutes alone conflates.
    # A player at 60% because he is a rotation option and one at 60% because he
    # tore an ACL in September have the same minutes and very different next seasons.
    x["availability_pct"] = df["availability_pct"].astype(float)
    x["longest_spell"] = df["longest_spell"].fillna(0).astype(float)
    x["extended_spells"] = df["extended_spells"].fillna(0).astype(float)
    x["missed_long"] = (df["longest_spell"].fillna(0) >= LONG_ABSENCE).astype(float)
    x["spell_prev"] = df["spell_prev"].fillna(0).astype(float)
    x["spell_career_max"] = df["spell_career_max"].fillna(0).astype(float)

    for g in POSITION_GROUPS:
        x[f"is_{g}"] = (df["grp"] == g).astype(float)
    for lg in LEAGUES:
        x[f"lg_{lg}"] = (df["lg"] == lg).astype(float)

    if medians is None:
        medians = {c: float(x[c].median()) for c in MEDIAN_FILL}
    for c in MEDIAN_FILL:
        x[c] = x[c].fillna(medians[c])
    # age enters non-linearly; a career is a curve, not a slope
    x["age_sq"] = x["age"] ** 2
    x["age_cu"] = x["age"] ** 3

    return x, list(x.columns), medians


def _drivers(model, x_row: pd.Series, feats: list, baseline: np.ndarray) -> list:
    """Local ablation explanation: rating-point impact of each feature = the
    projection with the real value minus the projection with that feature set to
    the training median. Reflects THIS model's reasoning, no extra deps."""
    # one ablated row per feature, scored in a single predict — the per-row call
    # this replaces costs minutes across the whole cohort
    grid = np.tile(x_row.values, (len(feats), 1))
    np.fill_diagonal(grid, baseline)
    abl = model.predict(grid)
    full = float(model.predict(x_row.values.reshape(1, -1))[0])
    out: dict[str, float] = {}
    for i, f in enumerate(feats):
        out.setdefault(LABELS.get(f, f), 0.0)
        out[LABELS.get(f, f)] += full - float(abl[i])
    items = [{"label": k, "impact": round(v, 2)} for k, v in out.items() if abs(v) >= 0.15]
    items.sort(key=lambda d: -abs(d["impact"]))
    return items[:5]


def _interval(qlo, qhi, X: np.ndarray, point: np.ndarray, pad: float = 0.0):
    """The 80% band around a projection.

    Two independently fitted quantile models are not ordered by construction, so
    on a handful of rows the 'low' one comes out above the 'high' one; and neither
    is tied to the squared-error model that produces the number on the card, so
    the point estimate can land outside its own interval. Both would be visible
    nonsense in the UI, so both are repaired here: sort the pair, then widen it to
    contain the point. Ratings live on 0-100, so clip to that too.

    `pad` is the conformal correction — a single margin, learned on a calibration
    season, that widens the band until it covers as often as it promises.
    """
    a, b = qlo.predict(X), qhi.predict(X)
    lo, hi = np.minimum(a, b) - pad, np.maximum(a, b) + pad
    lo, hi = np.minimum(lo, point), np.maximum(hi, point)
    return np.clip(lo, 0, 100), np.clip(hi, 0, 100)


def _verdict(delta: float) -> tuple[str, str, str]:
    for lo, label, cls, blurb in VERDICTS:
        if delta >= lo:
            return label, cls, blurb
    return VERDICTS[-1][1], VERDICTS[-1][2], VERDICTS[-1][3]


def _aging_curve(panel: pd.DataFrame) -> pd.DataFrame:
    """The measured thing, before any model: mean season-on-season rating change
    by age and position group. This is data, not a projection."""
    d = panel[panel["rating_next"].notna() & panel["age"].notna()].copy()
    d["age_i"] = d["age"].round().astype(int)
    d["delta"] = d["rating_next"] - d["rating"]
    rows = []
    for grp, sub in [("ALL", d)] + list(d.groupby("grp")):
        agg = (sub.groupby("age_i")["delta"]
                  .agg(["mean", "count", "std"]).reset_index())
        agg = agg[(agg["age_i"].between(17, 38)) & (agg["count"] >= 20)]
        for r in agg.itertuples():
            rows.append((str(grp), int(r.age_i), round(float(r.mean), 3),
                         int(r.count), round(float(r.std or 0), 3)))
    return pd.DataFrame(rows, columns=["position_group", "age", "mean_delta", "n", "sd"])


def train(write: bool = True) -> dict:
    # a --report run only scores the model, so it takes a read-only handle and can
    # share the file with a server (or a test fixture) that already has one open
    con = duckdb.connect(str(DB_PATH), read_only=not write)
    full_panel = _build(_load_panel(con))
    # see the module docstring: an unknown birth date is an artefact of FotMob's
    # 2020/21 coverage start, and it all but announces that the player had
    # already left. Modelling it would be modelling our own scraper.
    panel = full_panel[full_panel["age"].notna()].copy()
    dropped = len(full_panel) - len(panel)

    # ---- rating head: transitions where the player is present at t+1 ----------
    reg_rows = panel[panel["rating_next"].notna()].copy().reset_index(drop=True)
    yr = reg_rows["rating_next"].values.astype(float)
    tr = reg_rows["season"] <= TRAIN_THROUGH
    te = ~tr
    # imputation medians come from the TRAINING seasons only — fitting them on the
    # full panel would leak the held-out seasons into the score, slightly
    _, _, tr_medians = _features(reg_rows[tr.values])
    Xr, feats, _ = _features(reg_rows, tr_medians)

    reg_params = dict(n_estimators=400, max_depth=3, learning_rate=0.03,
                      subsample=0.8, min_samples_leaf=25, random_state=42)
    held = GradientBoostingRegressor(**reg_params).fit(Xr.values[tr.values], yr[tr.values])
    pred_te = held.predict(Xr.values[te.values])
    y_te = yr[te.values]

    # the baseline that matters: he will be exactly as good as he was
    base_te = reg_rows.loc[te, "rating"].values.astype(float)
    mae, base_mae = mean_absolute_error(y_te, pred_te), mean_absolute_error(y_te, base_te)
    rmse = float(np.sqrt(np.mean((y_te - pred_te) ** 2)))
    base_rmse = float(np.sqrt(np.mean((y_te - base_te) ** 2)))
    r2, base_r2 = r2_score(y_te, pred_te), r2_score(y_te, base_te)
    skill = (base_mae - mae) / base_mae * 100

    # per-season held-out detail, so a single lucky season cannot carry the claim
    per_season = []
    for s in sorted(reg_rows.loc[te, "season"].unique()):
        m = (reg_rows["season"] == s).values & te.values
        per_season.append({
            "season": s, "n": int(m.sum()),
            "mae": round(float(mean_absolute_error(yr[m], held.predict(Xr.values[m]))), 3),
            "base_mae": round(float(mean_absolute_error(yr[m], reg_rows.loc[m, "rating"])), 3),
        })
    # direction: of the players who really moved, how often is the sign right?
    moved = np.abs(y_te - base_te) >= 2
    dir_acc = float(np.mean(np.sign(pred_te - base_te)[moved] == np.sign(y_te - base_te)[moved]))
    resid_sd = float(np.std(y_te - pred_te))

    # ---- how wide should the error bar be, for THIS player? ------------------
    # One residual sd for everyone says a 20-year-old with a single season behind
    # him is as predictable as an ever-present 28-year-old, which he plainly is
    # not. Two quantile models give a per-player interval instead. Whether that is
    # an improvement is a measurable question, so it is measured: an 80% interval
    # has to contain the truth ~80% of the time, and beat the constant band for
    # the same coverage by being narrower where the model actually knows more.
    # Raw quantile models come out a little too narrow (77% coverage for a
    # nominal 80%), so they are conformalized: fit on the seasons before the
    # calibration year, measure on that year how far outside the band the truth
    # actually fell, and widen by that margin. Calibrating on the held-out
    # seasons instead would be marking our own homework.
    fit_m = (reg_rows["season"] < CALIB_SEASON).values
    cal_m = (reg_rows["season"] == CALIB_SEASON).values
    qlo = GradientBoostingRegressor(loss="quantile", alpha=Q_LO, **reg_params)
    qhi = GradientBoostingRegressor(loss="quantile", alpha=Q_HI, **reg_params)
    qlo.fit(Xr.values[fit_m], yr[fit_m])
    qhi.fit(Xr.values[fit_m], yr[fit_m])

    pred_cal = held.predict(Xr.values[cal_m])
    l_cal, h_cal = _interval(qlo, qhi, Xr.values[cal_m], pred_cal)
    # conformity score: how far outside the interval the truth landed (negative
    # when comfortably inside). The (1-alpha) quantile of it is the correction.
    scores = np.maximum(l_cal - yr[cal_m], yr[cal_m] - h_cal)
    nc = len(scores)
    q_hat = float(np.quantile(scores, min(1.0, np.ceil((nc + 1) * (Q_HI - Q_LO)) / nc)))

    lo_te, hi_te = _interval(qlo, qhi, Xr.values[te.values], pred_te, pad=q_hat)

    z = 1.2816  # the constant-band interval covering the same nominal 80%
    c_lo, c_hi = pred_te - z * resid_sd, pred_te + z * resid_sd
    cover = float(np.mean((y_te >= lo_te) & (y_te <= hi_te)))
    cover_const = float(np.mean((y_te >= c_lo) & (y_te <= c_hi)))
    width, width_const = float(np.mean(hi_te - lo_te)), float(np.mean(c_hi - c_lo))

    # The band's whole claim is that it varies where the data varies. Where it
    # does is by rating level, not by age: a constant band is far too generous
    # for a 45-rated player and too tight for an 80-rated one, who has more room
    # below him than above. So coverage is reported per band, both ways.
    rate_te = reg_rows.loc[te, "rating"].values.astype(float)
    by_level = []
    for lab, msk in [("<=50", rate_te <= 50), ("50-70", (rate_te > 50) & (rate_te <= 70)),
                     (">70", rate_te > 70)]:
        if not msk.any():
            continue
        by_level.append({
            "level": lab, "n": int(msk.sum()),
            "cover": round(float(np.mean(((y_te >= lo_te) & (y_te <= hi_te))[msk])), 3),
            "cover_const": round(float(np.mean(((y_te >= c_lo) & (y_te <= c_hi))[msk])), 3),
            "width": round(float(np.mean((hi_te - lo_te)[msk])), 2),
            "up": round(float(np.mean((hi_te - pred_te)[msk])), 2),
            "down": round(float(np.mean((pred_te - lo_te)[msk])), 2),
        })

    # The two leaderboards are made of big calls, and a big call is exactly where
    # a mean-reverting model is easiest to distrust: the top of the risers list is
    # players whose season sat far below their own history. So check the boards'
    # own claim on the held-out seasons — when the model shouted, was it right?
    # split by direction: averaging a +20 riser against a -20 faller cancels to
    # nothing and would say the model made no big calls at all
    pred_move, real_move = pred_te - base_te, y_te - base_te

    def _big(mask):
        if not mask.any():
            return {"n": 0, "pred": 0.0, "real": 0.0, "mae": 0.0, "base_mae": 0.0}
        return {"n": int(mask.sum()),
                "pred": round(float(np.mean(pred_move[mask])), 1),
                "real": round(float(np.mean(real_move[mask])), 1),
                "mae": round(float(mean_absolute_error(y_te[mask], pred_te[mask])), 2),
                "base_mae": round(float(mean_absolute_error(y_te[mask], base_te[mask])), 2)}

    big_up = _big(pred_move >= BIG_MOVE)
    big_dn = _big(pred_move <= -BIG_MOVE)

    # ---- availability head ---------------------------------------------------
    clf_rows = panel[panel["present_next"].notna()].copy().reset_index(drop=True)
    Xc, _, _ = _features(clf_rows, tr_medians)
    yc = clf_rows["present_next"].values.astype(int)
    ctr = (clf_rows["season"] <= TRAIN_THROUGH).values
    clf_params = dict(n_estimators=300, max_depth=3, learning_rate=0.05,
                      subsample=0.8, min_samples_leaf=25, random_state=42)
    held_c = GradientBoostingClassifier(**clf_params).fit(Xc.values[ctr], yc[ctr])
    p_te = held_c.predict_proba(Xc.values[~ctr])[:, 1]
    auc = float(roc_auc_score(yc[~ctr], p_te))
    base_rate = float(yc[ctr].mean())

    # ---- durability: measured, NOT modelled ---------------------------------
    # There was a third head here that predicted whether a player would lose ten
    # or more consecutive matches next season. It was cut, and the reason is worth
    # keeping: it scored 0.577 AUC against 0.5616 for simply reading his longest
    # absence last season off the table. A gradient-boosted model that adds 0.015
    # over one column, on a target that is barely predictable at all, has no
    # business being shown to anyone as a per-player "injury risk".
    #
    # What survives is the relationship itself, which is solid and worth stating:
    # prior absence really does predict future absence, at roughly 1.7x from the
    # bottom bucket to the top. So the table is measured and published, and no
    # individual gets a spurious probability attached to him.
    dur_rows = panel[panel["long_absence_next"].notna()]
    yd = dur_rows["long_absence_next"].values.astype(int)
    prior = dur_rows["longest_spell"].fillna(0).values
    dur_table = []
    for lab, lo, hi in [("none", 0, 5), ("5-9", 5, LONG_ABSENCE),
                        (f"{LONG_ABSENCE}+", LONG_ABSENCE, 10 ** 6)]:
        m = (prior >= lo) & (prior < hi)
        if m.sum() >= 100:
            dur_table.append({"prior": lab, "n": int(m.sum()),
                              "rate": round(float(yd[m].mean()), 3)})

    metrics = {
        "n_train": int(tr.sum()), "n_test": int(te.sum()),
        "train_through": TRAIN_THROUGH,
        "test_seasons": sorted(reg_rows.loc[te, "season"].unique().tolist()),
        "mae": round(float(mae), 3), "base_mae": round(float(base_mae), 3),
        "rmse": round(rmse, 3), "base_rmse": round(base_rmse, 3),
        "r2": round(float(r2), 3), "base_r2": round(float(base_r2), 3),
        "skill_pct": round(float(skill), 1),
        "direction_acc": round(dir_acc, 3), "resid_sd": round(resid_sd, 2),
        "per_season": per_season,
        "big_move": BIG_MOVE, "big_up": big_up, "big_dn": big_dn,
        "coverage": round(cover, 3), "coverage_const": round(cover_const, 3),
        "width": round(width, 2), "width_const": round(width_const, 2),
        "conformal_pad": round(q_hat, 2), "by_level": by_level,
        "avail_auc": round(auc, 3), "avail_base_rate": round(base_rate, 3),
        "n_avail": int(len(yc)),
        "dur_table": dur_table, "n_dur": int(len(yd)),
        "long_absence": LONG_ABSENCE,
    }

    print(f"[trajectory] panel {len(panel)} player-seasons with a known age "
          f"({dropped} dropped for having none)", flush=True)
    print(f"[trajectory] rating head  train={metrics['n_train']} (<= {TRAIN_THROUGH})  "
          f"test={metrics['n_test']} ({', '.join(metrics['test_seasons'])})", flush=True)
    print(f"[trajectory]   MAE {metrics['mae']} vs persistence {metrics['base_mae']}  "
          f"-> {metrics['skill_pct']}% skill   R² {metrics['r2']} vs {metrics['base_r2']}", flush=True)
    print(f"[trajectory]   direction correct on real movers: {metrics['direction_acc']:.1%}  "
          f"| residual sd {metrics['resid_sd']}", flush=True)
    for ps in per_season:
        print(f"[trajectory]     {ps['season']}: n={ps['n']:4d}  MAE {ps['mae']}  "
              f"(persistence {ps['base_mae']})", flush=True)
    for lbl, b in [("up", big_up), ("down", big_dn)]:
        print(f"[trajectory]   big {lbl:<4} calls (>= {BIG_MOVE:.0f} pts): n={b['n']:3d}  "
              f"predicted {b['pred']:+.1f}  actually {b['real']:+.1f}  "
              f"MAE {b['mae']} vs persistence {b['base_mae']}", flush=True)
    print(f"[trajectory]   80% interval: covers {metrics['coverage']:.1%} of held-out truths at "
          f"mean width {metrics['width']} (constant band: {metrics['coverage_const']:.1%} "
          f"at {metrics['width_const']}; conformal pad {metrics['conformal_pad']})", flush=True)
    for b in by_level:
        print(f"[trajectory]     rating {b['level']:<6} n={b['n']:4d}  covers {b['cover']:.1%} "
              f"(constant {b['cover_const']:.1%})  width {b['width']} "
              f"(+{b['up']} / -{b['down']})", flush=True)
    print(f"[trajectory] availability head  AUC {metrics['avail_auc']}  "
          f"(base rate {metrics['avail_base_rate']:.1%}, n={metrics['n_avail']})", flush=True)
    print(f"[trajectory] durability (measured, not modelled; n={metrics['n_dur']}): "
          f"chance of losing {LONG_ABSENCE}+ consecutive matches next season, by "
          f"longest absence this season", flush=True)
    for b in dur_table:
        print(f"[trajectory]     missed {b['prior']:<4} this season -> {b['rate']:.1%} "
              f"(n={b['n']})", flush=True)

    if not write:
        con.close()
        return metrics

    # ---- final models on everything, then project FOCUS_SEASON forward -------
    # The models that ship see every season, including the ones held out above —
    # that is the point of a holdout, not a contradiction of it. The numbers
    # reported to the user still come from the blind split.
    Xr_all, feats, medians = _features(reg_rows)
    Xc_all, _, _ = _features(clf_rows, medians)
    model = GradientBoostingRegressor(**reg_params).fit(Xr_all.values, yr)
    model_c = GradientBoostingClassifier(**clf_params).fit(Xc_all.values, yc)
    baseline = Xr_all.median().values

    # The shipping band repeats the evaluated recipe on all the data: quantiles
    # fitted on everything before the most recent transition season, then
    # conformalized on that season. Calibration has to sit on data the quantile
    # models did not fit, or the correction it learns is zero by construction.
    last_season = max(reg_rows["season"])
    f_all = (reg_rows["season"] < last_season).values
    c_all = (reg_rows["season"] == last_season).values
    qlo_f = GradientBoostingRegressor(loss="quantile", alpha=Q_LO, **reg_params)
    qhi_f = GradientBoostingRegressor(loss="quantile", alpha=Q_HI, **reg_params)
    qlo_f.fit(Xr_all.values[f_all], yr[f_all])
    qhi_f.fit(Xr_all.values[f_all], yr[f_all])
    p_cal = model.predict(Xr_all.values[c_all])
    l_c, h_c = _interval(qlo_f, qhi_f, Xr_all.values[c_all], p_cal)
    sc = np.maximum(l_c - yr[c_all], yr[c_all] - h_c)
    pad_ship = float(np.quantile(sc, min(1.0, np.ceil((len(sc) + 1) * (Q_HI - Q_LO)) / len(sc))))

    cur = panel[panel["season"] == FOCUS_SEASON].copy().reset_index(drop=True)
    Xf, _, _ = _features(cur, medians)
    proj = model.predict(Xf.values)
    lo_f, hi_f = _interval(qlo_f, qhi_f, Xf.values, proj, pad=pad_ship)
    p_present = model_c.predict_proba(Xf.values)[:, 1]
    target_season = _next_season(FOCUS_SEASON)

    rows_t, rows_d = [], []
    for i, r in cur.iterrows():
        # derive the delta from the rounded projection, not the raw one, so the
        # three numbers the card shows side by side always add up
        now = int(round(r["rating"]))
        projected = round(float(proj[i]), 1)
        delta = round(projected - now, 1)
        label, cls, blurb = _verdict(delta)
        rows_t.append((
            int(r["player_id"]), FOCUS_SEASON, target_season,
            now, projected, delta,
            round(float(lo_f[i]), 1), round(float(hi_f[i]), 1),
            round(float(hi_f[i] - lo_f[i]) / 2, 1), round(float(p_present[i]), 3),
            None if pd.isna(r["age"]) else round(float(r["age"]), 1),
            label, cls, blurb,
        ))
        for d in _drivers(model, Xf.iloc[i], feats, baseline):
            rows_d.append((int(r["player_id"]), FOCUS_SEASON, d["label"], float(d["impact"])))

    con.execute("DROP TABLE IF EXISTS player_trajectory")
    con.execute("""CREATE TABLE player_trajectory(
        player_id BIGINT, season VARCHAR, target_season VARCHAR,
        rating_now INTEGER, projected DOUBLE, delta DOUBLE,
        lo DOUBLE, hi DOUBLE, band DOUBLE,
        p_present DOUBLE, age DOUBLE,
        verdict VARCHAR, verdict_class VARCHAR, blurb VARCHAR)""")
    con.executemany("INSERT INTO player_trajectory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_t)
    con.execute("CREATE INDEX IF NOT EXISTS idx_traj ON player_trajectory(player_id)")

    con.execute("DROP TABLE IF EXISTS player_trajectory_drivers")
    con.execute("""CREATE TABLE player_trajectory_drivers(
        player_id BIGINT, season VARCHAR, label VARCHAR, impact DOUBLE)""")
    con.executemany("INSERT INTO player_trajectory_drivers VALUES (?,?,?,?)", rows_d)
    con.execute("CREATE INDEX IF NOT EXISTS idx_trajd ON player_trajectory_drivers(player_id)")

    curve = _aging_curve(panel)
    con.execute("DROP TABLE IF EXISTS trajectory_aging_curve")
    con.execute("""CREATE TABLE trajectory_aging_curve(
        position_group VARCHAR, age INTEGER, mean_delta DOUBLE, n INTEGER, sd DOUBLE)""")
    con.executemany("INSERT INTO trajectory_aging_curve VALUES (?,?,?,?,?)",
                    list(curve.itertuples(index=False, name=None)))

    con.execute("DROP TABLE IF EXISTS trajectory_meta")
    con.execute("""CREATE TABLE trajectory_meta(
        season VARCHAR, target_season VARCHAR, n_train INTEGER, n_test INTEGER,
        test_seasons VARCHAR, mae DOUBLE, base_mae DOUBLE, skill_pct DOUBLE,
        r2 DOUBLE, base_r2 DOUBLE, direction_acc DOUBLE, band DOUBLE,
        avail_auc DOUBLE, n_projected INTEGER, big_move DOUBLE,
        big_up_n INTEGER, big_up_pred DOUBLE, big_up_real DOUBLE,
        big_dn_n INTEGER, big_dn_pred DOUBLE, big_dn_real DOUBLE,
        interval_pct DOUBLE, coverage DOUBLE, coverage_const DOUBLE,
        width DOUBLE, width_const DOUBLE)""")
    con.execute("INSERT INTO trajectory_meta VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        FOCUS_SEASON, target_season, metrics["n_train"], metrics["n_test"],
        ", ".join(metrics["test_seasons"]), metrics["mae"], metrics["base_mae"],
        metrics["skill_pct"], metrics["r2"], metrics["base_r2"],
        metrics["direction_acc"], metrics["resid_sd"], metrics["avail_auc"], len(rows_t),
        BIG_MOVE, big_up["n"], big_up["pred"], big_up["real"],
        big_dn["n"], big_dn["pred"], big_dn["real"],
        round((Q_HI - Q_LO) * 100, 0), metrics["coverage"], metrics["coverage_const"],
        metrics["width"], metrics["width_const"],
    ])

    dump({"model": model, "clf": model_c, "qlo": qlo_f, "qhi": qhi_f,
          "conformal_pad": pad_ship, "feats": feats, "medians": medians,
          "baseline": baseline.tolist(), "season": FOCUS_SEASON,
          "metrics": metrics}, ART)
    con.close()

    imp = sorted(zip(feats, model.feature_importances_), key=lambda t: -t[1])
    metrics["top_features"] = [(f, round(w, 3)) for f, w in imp[:8]]
    print(f"[trajectory] top features: {metrics['top_features']}", flush=True)
    print(f"[trajectory] wrote player_trajectory ({len(rows_t)}) + drivers ({len(rows_d)}) + "
          f"aging curve ({len(curve)}); artifact -> {ART.name}", flush=True)
    return metrics


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true",
                    help="score the held-out seasons and print metrics, write nothing")
    a = ap.parse_args()
    train(write=not a.report)
