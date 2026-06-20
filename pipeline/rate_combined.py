"""
Combined-data ratings: ONE League rating and ONE UCL rating per player, both
computed from the metrics common to Understat+FotMob (domestic) and SofaScore
(UCL) -- so the two numbers are directly comparable.

Why a separate engine: the main rating (pipeline.rate / player_ratings_v2) uses
datamb progressive/SCA metrics that exist ONLY for the domestic leagues, so it
cannot be computed for the UCL. This engine instead uses the ~13 stats present in
BOTH sources (goals, assists, xG, shots, chances created, big chances created,
dribbles, tackles, interceptions, passes completed, and the pass/dribble/duel
success rates) off the canonical `v_stats_combined` view. Coarser than the main
rating, but it works identically for league and UCL.

Scope:
  league = the 5 domestic competitions (summed per player), min 600 min
  ucl    = the UEFA Champions League,                      min 270 min
Position groups are the FINE groups (ST/W/AM/CM/DM/FB/CB) from datamb's position,
falling back to a coarse->fine guess if datamb has no position. GKs are rated on
a separate keeper vector (save% / goals conceded / clean sheets / saves) sourced
outside v_stats_combined -- league from datamb player_wyscout, UCL derived from
ucl_player_stats -- since the canonical outfield metric set has no GK stats.

Writes `player_ratings_combined` (player_id, season, scope, ...). Run after
pipeline.build_views:
    python -m pipeline.rate_combined
"""
import sys

import duckdb
import numpy as np
import pandas as pd

from pipeline.rate import _zscore, _classify

try:
    from config import DB_PATH, FOCUS_SEASON
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON

# scope -> (shrinkage K, minimum minutes)
SCOPES = {"league": (600, 600), "ucl": (300, 270)}

# canonical metric -> (per_90?, invert?)
METRICS = {
    "goals": (True, False), "assists": (True, False), "xg": (True, False),
    "shots": (True, False), "chances_created": (True, False),
    "big_chances_created": (True, False), "dribbles_completed": (True, False),
    "tackles": (True, False), "interceptions": (True, False),
    "passes_completed": (True, False), "pass_accuracy_pct": (False, False),
    "dribble_success_pct": (False, False), "duels_won_pct": (False, False),
}

# FINE-position weight vectors over the common metrics (auto-normalised in code).
# Wingers split from strikers; AM from CM from DM; FB from CB. GK not rated.
WEIGHTS = {
    "ST": {"goals": .26, "xg": .18, "shots": .12, "dribbles_completed": .08,
           "big_chances_created": .08, "assists": .06, "chances_created": .06,
           "duels_won_pct": .06, "pass_accuracy_pct": .06, "dribble_success_pct": .04},
    "W":  {"dribbles_completed": .16, "chances_created": .14, "assists": .14, "xg": .12,
           "goals": .10, "big_chances_created": .08, "dribble_success_pct": .08,
           "shots": .08, "pass_accuracy_pct": .06, "duels_won_pct": .04},
    "AM": {"chances_created": .16, "assists": .16, "big_chances_created": .10, "xg": .10,
           "dribbles_completed": .10, "goals": .08, "passes_completed": .08,
           "pass_accuracy_pct": .08, "dribble_success_pct": .06, "interceptions": .04, "duels_won_pct": .04},
    "CM": {"passes_completed": .14, "chances_created": .12, "assists": .12, "pass_accuracy_pct": .12,
           "tackles": .10, "interceptions": .10, "duels_won_pct": .08, "xg": .06,
           "goals": .06, "dribbles_completed": .06, "big_chances_created": .04},
    "DM": {"tackles": .18, "interceptions": .18, "passes_completed": .16, "pass_accuracy_pct": .14,
           "duels_won_pct": .12, "chances_created": .06, "dribbles_completed": .06,
           "big_chances_created": .04, "assists": .04, "goals": .02},
    "FB": {"tackles": .14, "interceptions": .12, "chances_created": .12, "assists": .12,
           "dribbles_completed": .12, "duels_won_pct": .10, "passes_completed": .10,
           "pass_accuracy_pct": .08, "big_chances_created": .06, "goals": .04},
    # Rebalanced 2026-06-18: raw tackles/interceptions VOLUME (was .40 combined)
    # buried CBs on possession-dominant teams (Saliba/Gabriel/van Dijk make few
    # defensive actions because their team concedes little) and inflated weak-team
    # CBs (Eric García). Now leans on the duel-WIN RATE and build-up passing; raw
    # defensive volume cut to .20. Mirrors the same fix made to the datamb CB vector.
    "CB": {"duels_won_pct": .26, "pass_accuracy_pct": .22, "passes_completed": .18,
           "tackles": .10, "interceptions": .10, "chances_created": .06,
           "goals": .04, "assists": .04},
}

# Per-scope weight overrides: a fine position can use a different vector in the UCL
# than in the league. UCL wingers lean more on goals and less on dribble/shot volume
# (knockout finishing matters more than the league sample suggests). Falls back to
# WEIGHTS[grp] for any (scope, group) not listed here.
SCOPE_WEIGHTS = {
    "ucl": {
        "W": {"dribbles_completed": .14, "chances_created": .14, "assists": .14, "xg": .12,
              "goals": .14, "big_chances_created": .08, "dribble_success_pct": .08,
              "shots": .06, "pass_accuracy_pct": .06, "duels_won_pct": .04},
    },
}

# GKs can't be rated on the outfield common metrics, so they get their own vector
# over keeper stats that exist in BOTH sources: save% / goals conceded / clean
# sheets / saves (league from player_wyscout, UCL derived from ucl_player_stats).
# These columns are pre-computed as rates in _gk_league_df / _gk_ucl_df, so the
# only per-90 (rate+volume blend) metric here is clean_sheets.
GK_METRICS = {
    "save_percentage_pct": (False, False), "goals_conceded_per_90": (False, True),
    "clean_sheets": (True, False), "saves_per_90": (False, False),
}
GK_WEIGHTS = {"save_percentage_pct": .35, "goals_conceded_per_90": .30,
              "clean_sheets": .20, "saves_per_90": .15}

# --- rating scale (recalibrated 2026-06-18) -----------------------------------
# Map the within-group z-score S -> 0..99 via a soft-knee curve: linear in the
# body (so the 80s fill out), then hyperbolically compressed above the knee so
# 95+ is scarce and 99 is reserved for a singular season. Replaces the old flat
# `50 + 15*S` which clipped a pile of players at 99 and left the 80s near-empty.
CURVE_CENTER, CURVE_SLOPE, CURVE_KNEE, CURVE_COMP = 55.0, 14.0, 78.0, 0.050

# Per-position calibration: the common metrics measure attackers well (goals/xG/
# assists) but defenders crudely (tackles/passes/duels), so defensive leaders
# pile up at the top. These BASE gains scale each group's S so a rating means
# roughly the same across positions (gain<1 dampens an over-rewarded group's
# spread, >1 widens an under-spread one). Hand-tuned so the 95+ tier is elite
# attackers only and the best FB/CB/DM/GK top out ~88-92 instead of 95-99. At
# runtime these are then LIGHTLY nudged by market value (see _market_gain_mult).
BASE_GAIN = {"ST": 1.00, "W": 1.00, "AM": 0.98, "CM": 0.94,
             "DM": 0.86, "FB": 0.80, "CB": 0.80, "GK": 1.10}

# Market-value calibration: multiply each group's gain by (its elite market value
# / the cross-position geomean) ^ MV_CALIB_ALPHA, so positions the market prizes
# (W/AM/ST) rate a touch higher and cheap ones (GK/DM) a touch lower -- anchored
# to an external reference, not pure judgment. ALPHA is small on purpose: this
# shifts ratings a little, it doesn't redraw them. The signal is the median value
# of each group's top-N most valuable players (top-by-VALUE, so it's independent
# of our own ratings); a group with too few priced players is left at mult 1.0.
MV_CALIB_ALPHA = 0.15
MV_CALIB_TOPN = 15
MV_CALIB_MIN_SAMPLES = 5


def _market_gain_mult(con, season: str, pid2fine: dict) -> dict:
    mv = con.execute(
        "SELECT player_id, market_value_eur AS mv FROM player_market_value "
        "WHERE season = ? AND market_value_eur IS NOT NULL", [season]).df()
    mv["grp"] = [pid2fine.get(int(p)) for p in mv["player_id"]]
    mv = mv.dropna(subset=["grp"])
    meds = {}
    for g in BASE_GAIN:
        top = mv.loc[mv["grp"] == g, "mv"].sort_values(ascending=False).head(MV_CALIB_TOPN)
        if len(top) >= MV_CALIB_MIN_SAMPLES:
            meds[g] = float(top.median())
    if len(meds) < 2:                                    # not enough data -> no-op
        return {g: 1.0 for g in BASE_GAIN}
    geo = float(np.exp(np.mean(np.log(list(meds.values())))))
    return {g: (meds[g] / geo) ** MV_CALIB_ALPHA if g in meds else 1.0 for g in BASE_GAIN}


def _curve(S):
    """Soft-knee map from standardized score S to a 1-99 rating."""
    base = CURVE_CENTER + CURVE_SLOPE * S
    out = np.where(base > CURVE_KNEE,
                   CURVE_KNEE + (base - CURVE_KNEE) / (1 + CURVE_COMP * (base - CURVE_KNEE)),
                   base)
    return np.clip(np.round(out), 1, 99)

AGG = """
WITH base AS (
    SELECT player_id,
           CASE WHEN competition = 'UCL' THEN 'ucl' ELSE 'league' END AS scope,
           games, minutes, goals, assists, xg, shots, chances_created,
           big_chances_created, dribbles_completed, tackles, interceptions,
           passes_completed, pass_accuracy_pct, dribble_success_pct, duels_won_pct
    FROM v_stats_combined
    WHERE player_id IS NOT NULL AND season = ?
)
SELECT b.player_id, b.scope, p.player_name,
       p.position_group AS understat_group, f.fine AS datamb_fine,
       SUM(b.minutes) AS minutes, SUM(b.games) AS games,
       SUM(b.goals) AS goals, SUM(b.assists) AS assists, SUM(b.xg) AS xg,
       SUM(b.shots) AS shots, SUM(b.chances_created) AS chances_created,
       SUM(b.big_chances_created) AS big_chances_created,
       SUM(b.dribbles_completed) AS dribbles_completed, SUM(b.tackles) AS tackles,
       SUM(b.interceptions) AS interceptions, SUM(b.passes_completed) AS passes_completed,
       SUM(b.pass_accuracy_pct * b.minutes) / NULLIF(SUM(b.minutes), 0) AS pass_accuracy_pct,
       SUM(b.dribble_success_pct * b.minutes) / NULLIF(SUM(b.minutes), 0) AS dribble_success_pct,
       SUM(b.duels_won_pct * b.minutes) / NULLIF(SUM(b.minutes), 0) AS duels_won_pct
FROM base b JOIN players p USING (player_id)
LEFT JOIN (SELECT DISTINCT player_id, position_group AS fine
           FROM player_profile_metrics) f USING (player_id)
GROUP BY b.player_id, b.scope, p.player_name, p.position_group, f.fine
"""

# Fallback when a player has no datamb fine position (rare: in Understat but not
# datamb) -> map Understat's coarse group to a representative fine group.
COARSE_FALLBACK = {"FWD": "ST", "MID": "CM", "DEF": "CB"}


def _gk_league_df(con, season: str, min_min: int) -> pd.DataFrame:
    """League keepers from datamb player_wyscout (the only domestic source with
    GK stats), name+team crosswalked to Understat player_id. Returns one row per
    player_id with the GK_METRICS columns already as rates."""
    from pipeline.profile import _datamb_to_understat  # reuse the datamb->id matcher
    df = con.execute("""
        SELECT player, team_within_selected_timeframe AS team,
               minutes_played AS minutes, save_percentage_pct,
               goals_conceded_per_90, clean_sheets, saves_per_90
        FROM player_wyscout
        WHERE season = ? AND main_position = 'GK' AND datamb_position = main_position
          AND in_top5 AND minutes_played >= ? AND save_percentage_pct IS NOT NULL
    """, [season, min_min]).df()
    if df.empty:
        return df
    xwalk = _datamb_to_understat(con, list(zip(df["player"], df["team"])), season)
    df["player_id"] = [xwalk.get((p, t)) for p, t in zip(df["player"], df["team"])]
    df = df[df["player_id"].notna()].copy()
    df["player_id"] = df["player_id"].astype("int64")
    # one row per id (keepers who switched clubs): keep the max-minutes spell
    return df.sort_values("minutes", ascending=False).drop_duplicates("player_id")


def _gk_ucl_df(con, season: str, min_min: int) -> pd.DataFrame:
    """UCL keepers from ucl_player_stats. saves/clean_sheet/goals_conceded exist
    for every player there (team stats for outfielders), so restrict to GKs via
    the crosswalk -> players.position_group, and derive the rate metrics."""
    df = con.execute("""
        SELECT x.player_id,
               CAST(u.minutes_played AS DOUBLE) AS minutes,
               CAST(u.saves AS DOUBLE) AS saves,
               CAST(u.clean_sheet AS DOUBLE) AS clean_sheets,
               CAST(COALESCE(u.goals_conceded_inside_the_box, 0)
                  + COALESCE(u.goals_conceded_outside_the_box, 0) AS DOUBLE) AS goals_conceded
        FROM ucl_player_stats u
        JOIN ucl_understat_xwalk x
          ON x.sofascore_player_id = u.sofascore_player_id AND x.season = u.season
        JOIN players p ON p.player_id = x.player_id AND p.position_group = 'GK'
        WHERE u.season = ? AND u.minutes_played >= ? AND u.saves IS NOT NULL
    """, [season, min_min]).df()
    if df.empty:
        return df
    mins = df["minutes"].replace(0, np.nan)
    faced = df["saves"] + df["goals_conceded"]
    df["save_percentage_pct"] = (df["saves"] / faced.replace(0, np.nan) * 100).fillna(0)
    df["saves_per_90"] = df["saves"] / mins * 90
    df["goals_conceded_per_90"] = df["goals_conceded"] / mins * 90
    return df


def _rate_group(g: pd.DataFrame, group_label: str, weights: dict,
                metrics: dict, K: int, gain: float = 1.0) -> pd.DataFrame:
    w = weights
    total = sum(w.values())
    C = pd.Series(0.0, index=g.index)
    mins = pd.to_numeric(g["minutes"], errors="coerce").fillna(0)
    for m, (per90, inv) in metrics.items():
        if m not in w:
            continue
        x = pd.to_numeric(g[m], errors="coerce")     # totals for counts, % for rates
        if per90:                                    # 50% per-90 + 50% cumulative
            s90 = x / mins.replace(0, np.nan) * 90
            z = 0.5 * _zscore(s90) + 0.5 * _zscore(x)
        else:
            z = _zscore(x)
        C = C + (w[m] / total) * (-z if inv else z)
    C_adj = mins / (mins + K) * C
    mu, sigma = C_adj.mean(), C_adj.std(ddof=0)
    # per-position gain scales the spread (calibration); soft-knee curve maps to 1-99
    S = ((C_adj - mu) / sigma if sigma else C_adj * 0) * gain
    rating = pd.Series(_curve(S.values), index=g.index)
    out = pd.DataFrame({
        "player_id": g["player_id"].values, "player": g["player_name"].values,
        "position_group": group_label, "minutes": mins.astype(int).values,
        "rating": rating.astype(int).values, "composite_adj": C_adj.round(4).values,
    }).sort_values("rating", ascending=False).reset_index(drop=True)
    out["rank_in_group"] = np.arange(1, len(out) + 1)
    n = len(out)
    pr = out["composite_adj"].rank(method="min") - 1
    out["percentile"] = (pr / (n - 1) * 100).round(1) if n > 1 else 50.0
    out["classification"] = [_classify(r, k) for r, k in zip(out["rating"], out["rank_in_group"])]
    return out.drop(columns="composite_adj")


# Manual overrides for the DISPLAYED (combined-League) rating, keyed by Understat
# player_id -> rating. Reputation boosts the common-metric engine can't capture
# (e.g. elite defenders on possession-dominant teams). Classification is recomputed.
LEAGUE_RATING_OVERRIDES = {
    6888: 83,   # William Saliba
    8824: 83,   # Jude Bellingham
}


def _rate_one_season(con, season: str) -> pd.DataFrame:
    """Build the League + UCL common-metric ratings for ONE season. Position:
    the CURRENT season uses player_profile_metrics (official FotMob fine, aligned
    with the rest of the current-season analysis); PAST seasons use the stat-derived
    per-season fine position from `player_position_history` so a player is rated in
    the role he actually played that year (e.g. Mbappé as a Winger in 21/22, not the
    Striker he is now) -- fixing the old carry-back mismatch. Where a past season has
    no fine split (pre-2020/21), it falls back to the carried-back current fine, then
    a coarse->fine guess. Market-value calibration falls back to 1.0 for seasons with
    no market data (see _market_gain_mult)."""
    df = con.execute(AGG, [season]).df()
    if df.empty:
        return pd.DataFrame()
    # past seasons: override the carried-back current position with that season's
    # actual (stat-derived) fine position where we have one.
    if season != FOCUS_SEASON:
        pph = con.execute(
            "SELECT player_id, fine_position FROM player_position_history "
            "WHERE season = ? AND fine_position IS NOT NULL", [season]).df()
        pmap = {int(p): f for p, f in zip(pph["player_id"], pph["fine_position"])}
        df["datamb_fine"] = [pmap.get(int(p), f)
                             for p, f in zip(df["player_id"], df["datamb_fine"])]
    # prefer the fine position; fall back to a coarse->fine guess if missing
    df["position_group"] = [fine if fine in WEIGHTS else COARSE_FALLBACK.get(ug)
                            for fine, ug in zip(df["datamb_fine"], df["understat_group"])]
    # player_id -> fine group (outfield from datamb; GKs straight from players),
    # used to attach a position to each priced player for the market calibration.
    pid2fine = {int(p): g for p, g in zip(df["player_id"], df["position_group"]) if g}
    for (pid,) in con.execute(
            "SELECT player_id FROM players WHERE position_group = 'GK'").fetchall():
        pid2fine[int(pid)] = "GK"
    mv_mult = _market_gain_mult(con, season, pid2fine)
    gain = {g: BASE_GAIN[g] * mv_mult[g] for g in BASE_GAIN}

    frames = []
    for scope, (K, min_min) in SCOPES.items():
        s = df[(df["scope"] == scope) & (pd.to_numeric(df["minutes"]) >= min_min)
               & df["position_group"].isin(WEIGHTS.keys())]
        for grp, g in s.groupby("position_group"):
            w = SCOPE_WEIGHTS.get(scope, {}).get(grp, WEIGHTS[grp])
            r = _rate_group(g, grp, w, METRICS, K, gain.get(grp, 1.0))
            r["scope"] = scope
            frames.append(r)
        # GKs: own vector over keeper stats, sourced per scope (see _gk_*_df)
        gk = (_gk_league_df(con, season, min_min) if scope == "league"
              else _gk_ucl_df(con, season, min_min))
        if len(gk) >= 2:
            names = dict(con.execute(
                "SELECT player_id, player_name FROM players").fetchall())
            gk = gk.copy()
            gk["player_name"] = [names.get(int(p)) for p in gk["player_id"]]
            r = _rate_group(gk, "GK", GK_WEIGHTS, GK_METRICS, K, gain["GK"])
            r["scope"] = scope
            frames.append(r)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["season"] = season
    return out


def rate_combined(season: str = FOCUS_SEASON, all_seasons: bool = True) -> None:
    """Rate every season present in v_stats_combined_player (all_seasons=True, the
    default, so the player profile's season selector can show comparable gauges
    for past seasons) or just `season`."""
    con = duckdb.connect(str(DB_PATH))
    if all_seasons:
        seasons = [r[0] for r in con.execute(
            "SELECT DISTINCT season FROM v_stats_combined_player ORDER BY season").fetchall()]
    else:
        seasons = [season]
    parts = [o for s in seasons if not (o := _rate_one_season(con, s)).empty]
    out = pd.concat(parts, ignore_index=True)

    # Reputation pins apply ONLY to the current season (they're a "this season"
    # judgement, not a claim about the player's whole history).
    if LEAGUE_RATING_OVERRIDES:
        m = ((out["season"] == FOCUS_SEASON) & (out["scope"] == "league")
             & out["player_id"].isin(LEAGUE_RATING_OVERRIDES))
        out.loc[m, "rating"] = out.loc[m, "player_id"].map(LEAGUE_RATING_OVERRIDES).astype(int)
        out.loc[m, "classification"] = [_classify(r, k) for r, k in
                                        zip(out.loc[m, "rating"], out.loc[m, "rank_in_group"])]

    con.execute("DROP TABLE IF EXISTS player_ratings_combined")
    con.execute("""CREATE TABLE player_ratings_combined AS SELECT
        player_id, season, scope, position_group, minutes, rating,
        rank_in_group, percentile, classification FROM out""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_prc ON player_ratings_combined(player_id, season)")
    n_l = (out["scope"] == "league").sum()
    n_u = (out["scope"] == "ucl").sum()
    con.close()
    n_gk = (out["position_group"] == "GK").sum()
    print(f"player_ratings_combined: {n_l} league + {n_u} UCL ratings across "
          f"{out['season'].nunique()} seasons "
          f"(common-metric, fine ST/W/AM/CM/DM/FB/CB + GK; {n_gk} GK rows).")


if __name__ == "__main__":
    rate_combined()
