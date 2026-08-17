"""
Build a Player — a create-a-player game that runs a fictional build through the SAME
rating engine as every real player on the site (pipeline.rate), rather than a fake
formula dressed up to look real.

How it stays honest: rate.py's math (VECTORS, z-scores, minutes shrinkage, standardise-
within-group) is a pure function of a table of real per-90 stats. Nothing stops that
table from having one row that isn't a real player. Insert a synthetic "Your Creation"
row into the same 2025/26 field pipeline.rate scores everyone else against, run
pipeline.rate._rate_group unmodified, and the rating that comes back is not an estimate
of what the engine WOULD say — it is what the engine DOES say, computed the identical
way, in the identical field.

Two implications worth being upfront about:
  - Attribute sliders don't take raw units (nobody has intuition for "3.1 progressive
    carries per 90"). Each slider is READ as the desired percentile within the real
    position's field this season, and converted to a raw value via that field's own
    quantile function -- so slider 90 means "as good as the 90th-percentile real
    winger", not an arbitrary 0-100 scale. See _slider_to_value.
  - Adding one row shifts the real field's mean/std by a hair (n+1 instead of n). That
    is truthful rather than a bug: a created "Best In Position" 99 really would nudge
    where average sits, by the same tiny amount a real signing would.

Position groups, their per-90 vectors and weights are pipeline.rate.VECTORS -- this
module adds NO new weights or metrics, only a UI layer (labels, slider bounds, a
points budget) and the synthetic-row plumbing.
"""
import sys

import pandas as pd

try:
    from config import FOCUS_SEASON, MIN_MINUTES_FOR_RATING
    from pipeline.rate import (VECTORS, BUCKET_TO_GROUPS, _split_cm, _rate_group,
                               _norm_weights, _metric_series, _zscore, _is_rate)
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import FOCUS_SEASON, MIN_MINUTES_FOR_RATING
    from pipeline.rate import (VECTORS, BUCKET_TO_GROUPS, _split_cm, _rate_group,
                               _norm_weights, _metric_series, _zscore, _is_rate)

# Position, in the order the game presents them, and the readable name for each of
# pipeline.rate's eight groups.
POSITIONS = [
    ("ST", "Striker"), ("W", "Winger"), ("AM", "Attacking Mid"), ("CM", "Central Mid"),
    ("DM", "Defensive Mid"), ("FB", "Full-Back"), ("CB", "Centre-Back"), ("GK", "Goalkeeper"),
]

# One human label per underlying datamb column that appears in ANY vector, so a
# column shared by several metrics (e.g. npxg_per_90 feeds both "npxG" and the
# derived "finishing" metric on ST) still gets exactly one slider. `invert=True`
# stats are framed positively here (fewer errors, not "more errors") -- the slider
# maps to the real field's quantile in the direction that makes 99 the good end.
COLUMN_LABELS = {
    "npxg_per_90": "Shot Quality (npxG)",
    "non_penalty_goals_per_90": "Finishing Output",
    "shots_on_target_per_90": "Shots on Target",
    "key_passes_per_90": "Key Passes",
    "shot_assists_per_90": "Shot-Creating Actions",
    "touches_in_box_per_90": "Box Presence",
    "successful_dribbles_per_90": "Take-Ons",
    "aerial_duels_won_pct": "Aerial Ability",
    "possessions_lost_per_90": "Ball Security",
    "xa_per_90": "Creativity (xA)",
    "dribble_success_rate_pct": "Dribbling Success",
    "progressive_carries_per_90": "Ball Carrying",
    "pre_assists_per_90": "Build-Up Creativity",
    "progressive_passes_per_90": "Progressive Passing",
    "passes_received_per_90": "Link-Up Movement",
    "defensive_duels_won_per_90": "Tackling",
    "interceptions_per_90": "Interceptions",
    "pass_completion_pct": "Passing Accuracy",
    "possessions_won_per_90": "Ball Recovery",
    "shots_blocked_per_90": "Shot-Blocking",
    "clearances_per_90": "Clearances",
    "fouls_per_90": "Discipline",
    "errors_per_90": "Composure Under Pressure",
    "crosses_to_box_per_90": "Crossing",
    "defensive_duels_won_pct": "Tackling Success",
    "progressive_pass_accuracy_pct": "Progressive Pass Accuracy",
    "forward_pass_completion_pct": "Forward-Pass Accuracy",
    "accurate_passes_to_final_third_per_90": "Build-Up Volume",
    "interceptions_padj": "Reading the Game",
    "prevented_goals_per_90": "Shot-Stopping",
    "save_percentage_pct": "Save %",
    "goals_conceded_per_90": "Goals Prevented",
    "exits_per_90": "Sweeping",
    "long_pass_accuracy_pct": "Distribution Range",
}

# Minutes tiers offered in the UI. Real, not arbitrary: the engine's own shrinkage
# constant (K=600 in pipeline.rate) means minutes change the rating on their own --
# the same per-90 build trusted less on 900 minutes than on 2900. lambda is shown so
# that effect is visible rather than hidden inside a number.
MINUTE_TIERS = [
    {"key": "impact", "label": "Impact Sub", "minutes": 900},
    {"key": "starter", "label": "Regular Starter", "minutes": 1800},
    {"key": "ironman", "label": "Ever-Present", "minutes": 2900},
]
_K = 600  # mirrors pipeline.rate.K

# Points-per-slider average. Set so a player who spreads evenly lands solidly
# above-average (attribute 60ish) but cannot max every slider -- the budget is what
# turns "type in some numbers" into a build with trade-offs.
_BUDGET_PER_SLIDER = 62
_ATTR_MIN, _ATTR_MAX, _ATTR_DEFAULT = 1, 99, 50


def _group_columns(group: str) -> list[tuple[str, bool]]:
    """Distinct (column, invert) pairs referenced anywhere in VECTORS[group], in
    first-seen order. A column used by more than one metric (ST's npxg_per_90 feeds
    both npxG and finishing) gets exactly one slider that drives every metric built
    from it, same as a real player's one stat line does."""
    seen: dict[str, bool] = {}
    for _label, expr, _w, inv in VECTORS[group]:
        for col, _sign in expr:
            seen[col] = seen.get(col, False) or inv
    return list(seen.items())


def config(con) -> dict:
    """Everything the frontend needs to build the picker + sliders, with real
    calibration points (10th/50th/90th of the actual field) so a slider tooltip can
    say what "90" costs in the units real per-90 stats come in.

    `con` is the caller's existing DuckDB connection (SoccerDB.con) -- reused rather
    than opened fresh, because a second `duckdb.connect()` to the same file with a
    different read_only mode raises when the live refresher holds the file
    read-write (`ConnectionException: ... different configuration`)."""
    universe = _load_universe(con)
    positions = []
    for key, label in POSITIONS:
        cols = _group_columns(key)
        n = len(cols)
        group_df = universe[universe["position_group"] == key]
        sliders = []
        for col, inv in cols:
            real = pd.to_numeric(group_df[col], errors="coerce").dropna()
            unit = "%" if col.endswith("_pct") else " /90"
            anchors = ([round(float(real.quantile(q)), 2) for q in (0.1, 0.5, 0.9)]
                       if len(real) >= 5 else [None, None, None])
            if inv:
                anchors = anchors[::-1]  # so "low attribute" still reads left-to-right worse->better
            sliders.append({"column": col, "label": COLUMN_LABELS.get(col, col),
                            "unit": unit, "invert": inv,
                            "p10": anchors[0], "p50": anchors[1], "p90": anchors[2]})
        positions.append({"key": key, "label": label, "n_sliders": n,
                          "budget": n * _BUDGET_PER_SLIDER, "sliders": sliders,
                          "field_size": int(len(group_df))})
    return {"available": True, "positions": positions, "minute_tiers": MINUTE_TIERS,
            "attr_min": _ATTR_MIN, "attr_max": _ATTR_MAX, "attr_default": _ATTR_DEFAULT,
            "season": FOCUS_SEASON}


# ---- the fictional field: same query + overlay pipeline.rate.rate() uses ----------
_UNIVERSE_CACHE: pd.DataFrame | None = None


def _load_universe(con) -> pd.DataFrame:
    """The exact real-player field pipeline.rate scores, with position_group already
    assigned. Cached for the process's life: this table only changes on a pipeline
    rebuild, which comes with a server restart (same reasoning as the DB connection
    pool -- see analytics.queries). Queried through the caller's connection -- see
    config()'s docstring for why this module never opens its own."""
    global _UNIVERSE_CACHE
    if _UNIVERSE_CACHE is not None:
        return _UNIVERSE_CACHE
    df = con.execute(f"""
        SELECT * FROM player_wyscout
        WHERE season = '{FOCUS_SEASON}' AND datamb_position = main_position
          AND minutes_played >= {MIN_MINUTES_FOR_RATING} AND in_top5
    """).df()
    df["datamb_group"] = None
    cm_mask = df["datamb_position"] == "CM"
    df.loc[~cm_mask, "datamb_group"] = df.loc[~cm_mask, "datamb_position"].map(
        lambda b: BUCKET_TO_GROUPS[b][0])
    if cm_mask.any():
        df.loc[cm_mask, "datamb_group"] = _split_cm(df[cm_mask]).values
    try:
        pp = con.execute("SELECT datamb_player, datamb_team, fotmob_group, side "
                         "FROM player_position WHERE datamb_player IS NOT NULL").df()
        fmg = {(r.datamb_player, r.datamb_team): r.fotmob_group for r in pp.itertuples()}
    except Exception:  # noqa: BLE001 -- table not built yet; datamb grouping still works
        fmg = {}

    def _final(player, team, dg):
        fm = fmg.get((player, team))
        if fm in ("GK", "CB", "ST", "AM", "W", "FB", "CM", "DM"):
            return fm
        if fm == "CMID":
            return "DM" if dg == "DM" else "CM"
        return dg
    teams = df["team_within_selected_timeframe"]
    df["position_group"] = [_final(p, t, dg) for p, t, dg
                            in zip(df["player"], teams, df["datamb_group"])]
    _UNIVERSE_CACHE = df
    return df


def _slider_to_value(real: pd.Series, attr: int, invert: bool) -> float:
    """attr (1-99) -> a raw per-90/pct value, read as a percentile of the REAL field.
    invert flips the direction so a HIGH attribute always means "good" on the
    slider (e.g. attribute 90 on Ball Security = few losses = low raw value),
    matching how pipeline.rate itself flips these before summing."""
    if real.empty:
        return 0.0
    q = (100 - attr) / 100.0 if invert else attr / 100.0
    return float(real.quantile(min(max(q, 0.0), 1.0)))


def _row_contributions(df: pd.DataFrame, group: str, idx: int) -> list[dict]:
    """Mirrors pipeline.rate._rate_group's steps 2-3 for ONE row, to show which
    sliders actually moved the rating. Reads the same VECTORS/weights _rate_group
    uses; the authoritative rating still comes from calling _rate_group itself, so
    this is exposition, not a second implementation of the scoring."""
    vec = _norm_weights(VECTORS[group])
    mins = pd.to_numeric(df["minutes_played"], errors="coerce").fillna(0)
    out = []
    for label, expr, w, inv in vec:
        s90 = _metric_series(df, expr)
        z = _zscore(s90) if _is_rate(expr) else (
            0.5 * _zscore(s90) + 0.5 * _zscore(s90 * mins / 90.0))
        zval = float((-z if inv else z).iloc[idx])
        out.append({"label": label, "weight": round(w, 3), "z": round(zval, 2),
                    "contribution": round(w * zval, 3)})
    out.sort(key=lambda r: -abs(r["contribution"]))
    return out


def build_rating(con, group: str, minutes: int, attrs: dict[str, int]) -> dict:
    """Score a fictional build. attrs: {column: attribute 1-99}, unspecified columns
    default to _ATTR_DEFAULT. Returns the rating, classification, percentile and rank
    EXACTLY as pipeline.rate would compute them with this row in the field, plus a
    contribution breakdown and the nearest real players by rank."""
    if group not in VECTORS:
        return {"available": False, "error": "Unknown position."}
    minutes = max(1, min(int(minutes), 3420))          # a season is ~38 x 90
    universe = _load_universe(con)
    group_df = universe[universe["position_group"] == group].reset_index(drop=True)
    if len(group_df) < 5:
        return {"available": False, "error": "Not enough real players to compare against."}

    cols = _group_columns(group)
    row = {"player": "Your Creation", "team_within_selected_timeframe": "Custom XI",
           "minutes_played": minutes}
    slider_out = []
    for col, inv in cols:
        attr = int(max(_ATTR_MIN, min(_ATTR_MAX, attrs.get(col, _ATTR_DEFAULT))))
        real = pd.to_numeric(group_df[col], errors="coerce").dropna()
        val = _slider_to_value(real, attr, inv)
        row[col] = val
        slider_out.append({"column": col, "label": COLUMN_LABELS.get(col, col),
                           "attribute": attr, "value": round(val, 2)})

    combined = pd.concat([group_df, pd.DataFrame([row])], ignore_index=True)
    my_idx = len(combined) - 1
    result = _rate_group(combined, group)               # the real engine, unmodified
    me_i = result.index[result["player"] == "Your Creation"][0]
    me = result.loc[me_i]

    others = result.drop(index=me_i).reset_index(drop=True)
    # re-rank the field WITHOUT the creation for "vs the real 2025/26 top-5 field"
    lo, hi = max(0, me_i - 3), min(len(result), me_i + 4)
    comparables = [{"player": r.player, "team": r.team, "rating": int(r.rating),
                    "classification": r.classification, "rank_in_group": int(r.rank_in_group)}
                   for r in result.iloc[lo:hi].itertuples() if r.player != "Your Creation"]

    lam = round(minutes / (minutes + _K), 3)
    return {
        "available": True, "position": group, "minutes": minutes, "shrinkage": lam,
        "rating": int(me.rating), "classification": me.classification,
        "percentile": float(me.percentile), "rank_in_group": int(me.rank_in_group),
        "n_in_group": int(len(others)) + 1,     # field size including the creation
        "sliders": slider_out,
        "contributions": _row_contributions(combined, group, my_idx),
        "comparables": comparables,
        "budget": len(cols) * _BUDGET_PER_SLIDER,
        "spent": sum(s["attribute"] for s in slider_out),
    }
