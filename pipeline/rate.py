"""
Position-weighted player rating + classification engine (the 7-step spec).

Runs on `player_wyscout` (datamb / Wyscout, 2025/26) -- the only source carrying
the progressive / carry / shot-creation metrics the vectors need. One rating per
player at their MAIN position (datamb's index decides it). datamb's six buckets
map to the spec's eight position groups; the merged "CM" bucket is sub-classified
into DM / CM / AM by stat profile (terciles of an attack-minus-defence index).

Pipeline (per position group):
  1. metrics are taken from datamb's per-90 / % columns as-is (already per-90)
  2. z = clip((x-mu)/sigma, -3, 3) within group; sign-flipped for "lower is better"
  3. C   = sum(w_m * z_m)            (weights per position, renormalised to 1)
  4. C_adj = lambda * C,  lambda = minutes/(minutes+K),  K=600
  5. S   = (C_adj - mu_C)/sigma_C    within group
  6. Rating = clip(round(50 + 15*S), 1, 99)
  7. Rank by Rating desc; Percentile = percent_rank(C_adj) within group * 100

Metric -> datamb column mapping (proxies / drops documented in METRIC_NOTES).
Writes `rating_weights` (transparent vectors) and `player_ratings_v2` (results).

Run after pipeline.load_datamb:
    python -m pipeline.rate
"""
import sys
import warnings

import duckdb
import numpy as np
import pandas as pd

try:
    from config import DB_PATH, FOCUS_SEASON, MIN_MINUTES_FOR_RATING
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON, MIN_MINUTES_FOR_RATING

warnings.filterwarnings("ignore")

K = 600                       # shrinkage constant (step 4)
RATING_VERSION = "v2-datamb"

# Manual rating overrides (hacks), keyed by (player, team) -> rating. These pin a
# player's final rating regardless of the formula -- use sparingly. Rank and
# classification within the group are recomputed to stay consistent.
RATING_OVERRIDES = {
    ("J. Bellingham", "Real Madrid"): 83,   # reputation override (down statistical season)
}

# --- metric -> datamb column expression -------------------------------------
# Each metric is sum(sign * column). Proxies and (inv) noted in METRIC_NOTES.
NPXG   = [("npxg_per_90", 1)]
NPG    = [("non_penalty_goals_per_90", 1)]
FINISH = [("non_penalty_goals_per_90", 1), ("npxg_per_90", -1)]   # npG - npxG
SOT    = [("shots_on_target_per_90", 1)]
KEYP   = [("key_passes_per_90", 1)]
SCA    = [("shot_assists_per_90", 1)]            # proxy: shot assists
GCA    = [("pre_assists_per_90", 1)]             # proxy: pre-assists
TIB    = [("touches_in_box_per_90", 1)]
TAKEON = [("successful_dribbles_per_90", 1)]
TAKEPC = [("dribble_success_rate_pct", 1)]
AERIAL = [("aerial_duels_won_pct", 1)]
DISP   = [("possessions_lost_per_90", 1)]        # proxy: possessions lost
XA     = [("xa_per_90", 1)]
PRGC   = [("progressive_carries_per_90", 1)]
PRGP   = [("progressive_passes_per_90", 1)]
PRGR   = [("passes_received_per_90", 1)]         # proxy: passes received
PASSPC = [("pass_completion_pct", 1)]
TKLINT = [("defensive_duels_won_per_90", 1), ("interceptions_per_90", 1)]  # proxy Tkl+Int
RECOV  = [("possessions_won_per_90", 1)]         # proxy: recoveries
BLOCKS = [("shots_blocked_per_90", 1)]
CLEAR  = [("clearances_per_90", 1)]              # from SofaScore domestic
# CB quality/rate metrics (possession-neutral) + build-up passing.
# clearances/blocks/duels have no PAdj, so per-90 versions reward CBs under more
# pressure (weaker teams) -- the CB vector leans on success-rates instead. NOTE:
# progressive_passes uses per-90, NOT _padj: PAdj scales DOWN for high-possession
# teams (it's built for defensive actions), which perversely penalises exactly the
# ball-dominant CBs we want to rate up. interceptions_padj IS correct (defensive).
PRGPACC  = [("progressive_pass_accuracy_pct", 1)]   # ball-progression quality
DEFDUEL  = [("defensive_duels_won_pct", 1)]         # tackle success rate
FWDPASPC = [("forward_pass_completion_pct", 1)]     # progressive passing accuracy
ACCF3    = [("accurate_passes_to_final_third_per_90", 1)]  # build-up volume
INTPADJ  = [("interceptions_padj", 1)]              # possession-adjusted (defensive)
ERRORS = [("errors_per_90", 1)]                  # from SofaScore domestic (inv)
BLKCLR = [("shots_blocked_per_90", 1), ("clearances_per_90", 1)]  # DM "blocks+clearances"
FOULS  = [("fouls_per_90", 1)]
CROSSB = [("crosses_to_box_per_90", 1)]
# GK
PREV   = [("prevented_goals_per_90", 1)]         # PSxG - GA
SAVEPC = [("save_percentage_pct", 1)]
GA     = [("goals_conceded_per_90", 1)]
GKOUT  = [("exits_per_90", 1)]                   # proxy: defensive actions outside box
LAUNCH = [("long_pass_accuracy_pct", 1)]         # proxy: launched-pass %

# (metric_label, expr, weight, invert).  Weights are the spec's; dropped metrics
# (no datamb column) are simply absent and the vector is renormalised to sum 1.
VECTORS = {
    "ST": [("npxG", NPXG, .20, False), ("npGoals", NPG, .15, False),
           ("finishing", FINISH, .08, False), ("SoT", SOT, .10, False),
           ("key_passes", KEYP, .12, False), ("SCA", SCA, .08, False),
           ("touches_in_box", TIB, .10, False), ("take_ons", TAKEON, .09, False),
           ("aerial_pct", AERIAL, .05, False), ("dispossessed", DISP, .03, True)],
    "W":  [("xA", XA, .16, False), ("npxG", NPXG, .14, False), ("SCA", SCA, .12, False),
           ("take_ons", TAKEON, .12, False), ("take_on_pct", TAKEPC, .06, False),
           ("PrgCarries", PRGC, .12, False), ("key_passes", KEYP, .10, False),
           ("npGoals", NPG, .10, False), ("dispossessed", DISP, .08, True)],
    # Rebalanced 2026-06 into three ~equal pillars (was ~0.52 on creativity):
    #   creativity 0.34 (xA/key_passes/SCA/GCA) + scoring 0.33 (npxG/npGoals/
    #   touches_in_box) + ball-carrying 0.33 (PrgCarries/take_ons/PrgPasses/PrgRecv).
    "AM": [("xA", XA, .14, False), ("key_passes", KEYP, .10, False),
           ("SCA", SCA, .05, False), ("GCA", GCA, .05, False),
           ("npxG", NPXG, .15, False), ("npGoals", NPG, .12, False),
           ("touches_in_box", TIB, .06, False),
           ("PrgCarries", PRGC, .13, False), ("take_ons", TAKEON, .10, False),
           ("PrgPasses", PRGP, .06, False), ("PrgRecv", PRGR, .04, False)],
    "CM": [("PrgPasses", PRGP, .16, False), ("Tkl_Int", TKLINT, .14, False),
           ("xA", XA, .12, False), ("key_passes", KEYP, .10, False),
           ("pass_pct", PASSPC, .10, False), ("PrgCarries", PRGC, .10, False),
           ("recoveries", RECOV, .10, False), ("npxG", NPXG, .08, False),
           ("SCA", SCA, .06, False), ("dispossessed", DISP, .04, True)],
    "DM": [("Tkl_Int", TKLINT, .20, False), ("PrgPasses", PRGP, .16, False),
           ("pass_pct", PASSPC, .14, False), ("recoveries", RECOV, .14, False),
           ("blocks_clearances", BLKCLR, .10, False), ("aerial_pct", AERIAL, .08, False),
           ("fouls", FOULS, .06, True), ("errors", ERRORS, .06, True),
           ("dispossessed", DISP, .06, True)],
    "FB": [("Tkl_Int", TKLINT, .16, False), ("PrgCarries", PRGC, .12, False),
           ("PrgPasses", PRGP, .12, False), ("xA", XA, .12, False),
           ("key_passes", KEYP, .10, False), ("crosses_to_box", CROSSB, .08, False),
           ("take_ons", TAKEON, .08, False), ("recoveries", RECOV, .08, False),
           ("pass_pct", PASSPC, .06, False), ("errors", ERRORS, .04, True),
           ("dispossessed", DISP, .04, True)],
    # Rebuilt 2026-06: build-up / quality-rate-dominant, NOT per-90 defensive
    # volume. The spec's volume vector inverted the ranking (weak-team CBs make
    # more actions/90); this leans on success-rates + build-up passing so modern
    # ball-playing CBs rate correctly. See METRIC_NOTES / rating-engine memory.
    "CB": [("pass_pct", PASSPC, .16, False), ("def_duel_pct", DEFDUEL, .14, False),
           ("aerial_pct", AERIAL, .12, False), ("PrgPasses", PRGP, .12, False),
           ("prg_pass_acc", PRGPACC, .12, False), ("fwd_pass_pct", FWDPASPC, .08, False),
           ("acc_passes_f3", ACCF3, .08, False), ("Int_padj", INTPADJ, .08, False),
           ("errors", ERRORS, .06, True), ("fouls", FOULS, .04, True)],
    "GK": [("PSxG_GA", PREV, .30, False), ("save_pct", SAVEPC, .18, False),
           ("GA", GA, .12, True), ("pass_pct", PASSPC, .12, False),
           ("def_outside_box", GKOUT, .10, False), ("launched_pct", LAUNCH, .08, False)],
    # cross_stop/claims 0.10 dropped -> renormalised
}

# datamb's six buckets -> which spec vectors apply. CM is split downstream.
BUCKET_TO_GROUPS = {"GK": ["GK"], "CB": ["CB"], "FB": ["FB"],
                    "ST": ["ST"], "FW": ["W"], "CM": ["DM", "CM", "AM"]}

METRIC_NOTES = (
    "proxies: SCA<-shot_assists, GCA<-pre_assists, PrgRecv<-passes_received, "
    "Tkl+Int<-defensive_duels_won+interceptions, recoveries<-possessions_won, "
    "dispossessed<-possessions_lost, GK def_outside_box<-exits, launched%<-long_pass_acc. "
    "clearances + errors backfilled from SofaScore domestic (pipeline.load_sofa_domestic). "
    "still dropped (no source): GK cross-stop/claims, renormalised into the GK vector. "
    "CB vector rebuilt to build-up/rates (pass%, def-duel-win%, aerial%, prog-passes/90, "
    "prg-pass-acc, fwd-pass%, accurate-final-third-passes, interceptions_padj); raw per-90 "
    "volume (clearances/blocks/recoveries) dropped -- it rewarded CBs under more pressure "
    "(weaker teams) and inverted the ranking. progressive_passes uses per-90 NOT padj (PAdj "
    "wrongly scales attacking output down for high-possession teams)."
)


def _norm_weights(vec):
    total = sum(w for _, _, w, _ in vec)
    return [(m, e, w / total, inv) for m, e, w, inv in vec]


def _metric_series(df, expr):
    s = pd.Series(0.0, index=df.index)
    for col, sign in expr:
        s = s + sign * pd.to_numeric(df[col], errors="coerce")
    return s


def _zscore(s):
    """z within group; NaN -> 0 (neutral); clipped to [-3, 3]."""
    mu, sigma = s.mean(), s.std(ddof=0)
    if not sigma or np.isnan(sigma):
        return pd.Series(0.0, index=s.index)
    z = (s - mu) / sigma
    return z.fillna(0.0).clip(-3, 3)


def _classify(rating, rank):
    if rank == 1:
        return "Best In Position"
    if rating >= 90:
        return "World-Class"
    if rating >= 80:
        return "Elite"
    if rating >= 65:
        return "Above Average"
    if rating >= 50:
        return "Average"
    return "Below Average"


def _split_cm(cm: pd.DataFrame) -> pd.Series:
    """Sub-classify the merged CM bucket into DM / CM / AM by terciles of an
    attack-minus-defence index (z-summed within the bucket)."""
    att = (_zscore(_metric_series(cm, XA)) + _zscore(_metric_series(cm, KEYP))
           + _zscore(_metric_series(cm, TIB)) + _zscore(_metric_series(cm, NPXG)))
    dfn = (_zscore(_metric_series(cm, TKLINT)) + _zscore(_metric_series(cm, RECOV))
           + _zscore(_metric_series(cm, BLOCKS)))
    diff = att - dfn
    lo, hi = diff.quantile([1 / 3, 2 / 3])
    return diff.apply(lambda d: "AM" if d >= hi else ("DM" if d <= lo else "CM"))


def _is_rate(expr) -> bool:
    """A %/rate metric (e.g. pass_completion_pct) -- has no cumulative version."""
    return all(c.endswith("_pct") for c, _ in expr)


def _rate_group(df: pd.DataFrame, group: str) -> pd.DataFrame:
    vec = _norm_weights(VECTORS[group])
    mins = pd.to_numeric(df["minutes_played"], errors="coerce").fillna(0)
    # step 2-3: composite. Counting metrics blend 50% per-90 + 50% cumulative
    # (season total = per90 * minutes/90), so the rating rewards both rate and
    # volume; rate/% metrics have no cumulative form and use the per-90 z alone.
    C = pd.Series(0.0, index=df.index)
    for _, expr, w, inv in vec:
        s90 = _metric_series(df, expr)
        if _is_rate(expr):
            z = _zscore(s90)
        else:
            z = 0.5 * _zscore(s90) + 0.5 * _zscore(s90 * mins / 90.0)
        C = C + w * (-z if inv else z)
    # step 4: minutes shrinkage
    lam = mins / (mins + K)
    C_adj = lam * C
    # step 5-6: standardise within group -> rating
    mu, sigma = C_adj.mean(), C_adj.std(ddof=0)
    S = (C_adj - mu) / sigma if sigma else C_adj * 0
    rating = (50 + 15 * S).round().clip(1, 99)
    # step 7: rank + percentile (percent_rank of C_adj)
    out = pd.DataFrame({
        "player": df["player"].values,
        "team": df["team_within_selected_timeframe"].values,
        "position_group": group,
        "minutes": mins.astype(int).values,
        "composite": C.round(4).values,
        "composite_adj": C_adj.round(4).values,
        "standardized": S.round(4).values,
        "rating": rating.astype(int).values,
    })
    out = out.sort_values("rating", ascending=False).reset_index(drop=True)
    out["rank_in_group"] = np.arange(1, len(out) + 1)
    n = len(out)
    pr = out["composite_adj"].rank(method="min") - 1
    out["percentile"] = (pr / (n - 1) * 100).round(1) if n > 1 else 50.0
    out["classification"] = [_classify(r, k) for r, k in
                             zip(out["rating"], out["rank_in_group"])]
    return out


def rate(season: str = FOCUS_SEASON, min_minutes: int = MIN_MINUTES_FOR_RATING) -> None:
    con = duckdb.connect(str(DB_PATH))
    df = con.execute(f"""
        SELECT * FROM player_wyscout
        WHERE season = '{season}' AND datamb_position = main_position
          AND minutes_played >= {min_minutes}
          AND in_top5
    """).df()
    print(f"rating {len(df)} Top-5 players (>= {min_minutes} min) for {season}")

    # datamb-derived group (kept only for the DM-vs-CM decision)
    df["datamb_group"] = None
    cm_mask = df["datamb_position"] == "CM"
    df.loc[~cm_mask, "datamb_group"] = df.loc[~cm_mask, "datamb_position"].map(
        lambda b: BUCKET_TO_GROUPS[b][0])
    if cm_mask.any():
        df.loc[cm_mask, "datamb_group"] = _split_cm(df[cm_mask]).values

    # position comes from FotMob (player_position); datamb only decides DM vs CM.
    # See positions-use-fotmob: FotMob is authoritative for GK/CB/FB/ST/AM/W; the
    # central-mid band ('CMID') falls back to datamb's DM/CM split (AM -> CM).
    try:
        pp = con.execute("SELECT datamb_player, datamb_team, fotmob_group, side "
                         "FROM player_position WHERE datamb_player IS NOT NULL").df()
        fmg = {(r.datamb_player, r.datamb_team): r.fotmob_group for r in pp.itertuples()}
        side = {(r.datamb_player, r.datamb_team): r.side for r in pp.itertuples()}
    except Exception:
        fmg, side = {}, {}

    def _final(player, team, dg):
        fm = fmg.get((player, team))                 # keyed by (name, team)
        # CM/DM are authoritative only as a manual override (DESC_TO_GROUP emits
        # CMID for FotMob's own CM/DM tags, so these come from OVERRIDES).
        if fm in ("GK", "CB", "ST", "AM", "W", "FB", "CM", "DM"):
            return fm
        if fm == "CMID":
            return "DM" if dg == "DM" else "CM"
        return dg                                    # no FotMob position -> datamb
    teams = df["team_within_selected_timeframe"]
    df["position_group"] = [_final(p, t, dg) for p, t, dg
                            in zip(df["player"], teams, df["datamb_group"])]
    df["pos_side"] = [side.get((p, t)) for p, t in zip(df["player"], teams)]
    print("  position groups:", df["position_group"].value_counts().sort_index().to_dict())

    results = [_rate_group(g, grp) for grp, g in df.groupby("position_group")]
    out = pd.concat(results, ignore_index=True)
    out["season"] = season
    out["rating_version"] = RATING_VERSION
    # detailed position for display: W/FB get a side (LW/RW, LB/RB)
    side_map = {(p, t): s for p, t, s in
                zip(df["player"], df["team_within_selected_timeframe"], df["pos_side"])}

    def _detail(grp, player, team):
        s = side_map.get((player, team))
        if grp == "W":
            return {"R": "RW", "L": "LW"}.get(s, "W")
        if grp == "FB":
            return {"R": "RB", "L": "LB"}.get(s, "FB")
        return grp
    out["detailed_position"] = [_detail(g, p, t) for g, p, t
                                in zip(out["position_group"], out["player"], out["team"])]

    # apply manual rating overrides, then recompute rank + classification per group
    if RATING_OVERRIDES:
        for (pl, tm), rv in RATING_OVERRIDES.items():
            mask = (out["player"] == pl) & (out["team"] == tm)
            if mask.any():
                out.loc[mask, "rating"] = rv
            else:
                print(f"  override: no match for {pl} / {tm}")
        out = out.sort_values(["position_group", "rating", "composite_adj"],
                              ascending=[True, False, False]).reset_index(drop=True)
        out["rank_in_group"] = out.groupby("position_group").cumcount() + 1
        out["classification"] = [_classify(r, k) for r, k in
                                 zip(out["rating"], out["rank_in_group"])]

    # weights table (renormalised, with mapping) for transparency
    wrows = []
    for grp, vec in VECTORS.items():
        for m, expr, w, inv in _norm_weights(vec):
            wrows.append((grp, m, "+".join(f"{s:+d}*{c}" for c, s in expr),
                          round(w, 4), inv))
    weights = pd.DataFrame(wrows, columns=["position_group", "metric",
                                           "datamb_expr", "weight", "invert"])

    con.execute("DROP TABLE IF EXISTS player_ratings_v2")
    con.execute("""CREATE TABLE player_ratings_v2 AS SELECT
        season, player, team, position_group, detailed_position, minutes, composite,
        composite_adj, standardized, rating, rank_in_group, percentile, classification,
        rating_version FROM out""")
    con.execute("DROP TABLE IF EXISTS rating_weights")
    con.execute("CREATE TABLE rating_weights AS SELECT * FROM weights")
    con.execute("CREATE INDEX IF NOT EXISTS idx_pr2_grp ON player_ratings_v2(position_group, rating)")
    con.close()
    print(f"player_ratings_v2: {len(out)} players across "
          f"{out['position_group'].nunique()} position groups.")
    print(f"rating_weights: {len(weights)} rows. {METRIC_NOTES}")


if __name__ == "__main__":
    rate()
