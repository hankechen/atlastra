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
falling back to a coarse->fine guess if datamb has no position; GK is skipped
(the canonical schema carries no GK-specific stats).

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
    "CB": {"tackles": .20, "interceptions": .20, "duels_won_pct": .18, "passes_completed": .16,
           "pass_accuracy_pct": .16, "goals": .04, "assists": .03, "chances_created": .03},
}

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


def _rate_scope_group(g: pd.DataFrame, group: str, K: int) -> pd.DataFrame:
    w = WEIGHTS[group]
    total = sum(w.values())
    C = pd.Series(0.0, index=g.index)
    mins = pd.to_numeric(g["minutes"], errors="coerce").fillna(0)
    for m, (per90, inv) in METRICS.items():
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
    S = (C_adj - mu) / sigma if sigma else C_adj * 0
    rating = (50 + 15 * S).round().clip(1, 99)
    out = pd.DataFrame({
        "player_id": g["player_id"].values, "player": g["player_name"].values,
        "position_group": group, "minutes": mins.astype(int).values,
        "rating": rating.astype(int).values, "composite_adj": C_adj.round(4).values,
    }).sort_values("rating", ascending=False).reset_index(drop=True)
    out["rank_in_group"] = np.arange(1, len(out) + 1)
    n = len(out)
    pr = out["composite_adj"].rank(method="min") - 1
    out["percentile"] = (pr / (n - 1) * 100).round(1) if n > 1 else 50.0
    out["classification"] = [_classify(r, k) for r, k in zip(out["rating"], out["rank_in_group"])]
    return out.drop(columns="composite_adj")


def rate_combined(season: str = FOCUS_SEASON) -> None:
    con = duckdb.connect(str(DB_PATH))
    df = con.execute(AGG, [season]).df()
    # prefer datamb's fine position; fall back to a coarse->fine guess if missing
    df["position_group"] = [fine if fine in WEIGHTS else COARSE_FALLBACK.get(ug)
                            for fine, ug in zip(df["datamb_fine"], df["understat_group"])]
    frames = []
    for scope, (K, min_min) in SCOPES.items():
        s = df[(df["scope"] == scope) & (pd.to_numeric(df["minutes"]) >= min_min)
               & df["position_group"].isin(WEIGHTS.keys())]
        for grp, g in s.groupby("position_group"):
            r = _rate_scope_group(g, grp, K)
            r["scope"] = scope
            frames.append(r)
    out = pd.concat(frames, ignore_index=True)
    out["season"] = season

    con.execute("DROP TABLE IF EXISTS player_ratings_combined")
    con.execute("""CREATE TABLE player_ratings_combined AS SELECT
        player_id, season, scope, position_group, minutes, rating,
        rank_in_group, percentile, classification FROM out""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_prc ON player_ratings_combined(player_id, scope)")
    n_l = (out["scope"] == "league").sum()
    n_u = (out["scope"] == "ucl").sum()
    con.close()
    print(f"player_ratings_combined: {n_l} league + {n_u} UCL ratings "
          f"(common-metric, fine ST/W/AM/CM/DM/FB/CB, GK skipped).")


if __name__ == "__main__":
    rate_combined()
