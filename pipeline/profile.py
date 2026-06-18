"""
Player-profile analytics for use case 3: Strengths / Weaknesses / Areas of
Improvement, derived from the rating engine's own metric vectors.

For every rated player we already know which metrics define quality at their
position (pipeline.rate VECTORS, renormalised in `rating_weights`). Here we score
each player on EACH of those metrics as a percentile within their position group
(sign-flipped so higher percentile is always better), then label:

  strength  -- percentile >= STRONG_PCT
  weakness  -- percentile <= WEAK_PCT
  (else neutral)

"Areas of improvement" are the weaknesses ranked by the metric's WEIGHT in the
position vector: fixing a high-weight weakness raises the player's rating most.

Same population as the rating engine (datamb 2025/26, Top-5, >=600 min, main
position). Writes:
  player_profile_metrics  -- one row per (player, metric): value, percentile, label
  v_player_profile        -- one row per player: position, rating, and the
                             comma-joined top strengths / weaknesses / improvements

Run after pipeline.rate:
    python -m pipeline.profile
"""
import sys
import warnings
from collections import defaultdict

import duckdb
import pandas as pd
from rapidfuzz import fuzz, process

from pipeline.rate import (VECTORS, BUCKET_TO_GROUPS, _split_cm, _metric_series,
                           _norm_weights)
from pipeline.load_enrich import (_norm, _name_compatible, MATCH_THRESHOLD,
                                  RECOVER_THRESHOLD)

try:
    from config import DB_PATH, FOCUS_SEASON, MIN_MINUTES_FOR_RATING
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON, MIN_MINUTES_FOR_RATING

warnings.filterwarnings("ignore")

STRONG_PCT = 75   # percentile at/above which a metric is a strength
WEAK_PCT = 25     # percentile at/below which a metric is a weakness
TOP_N = 4         # how many of each to surface in the summary view

# metric -> (expr, invert), deduped across all position vectors -> lets us score
# any player on any metric, not just the ones weighted for their position.
ALL_METRIC_EXPR = {}
for _grp, _vec in VECTORS.items():
    for _m, _expr, _w, _inv in _vec:
        ALL_METRIC_EXPR.setdefault(_m, (_expr, _inv))

# the metrics behind the profile radar's 6 axes (see queries.RADAR_AXES) -- scored
# for EVERY player into player_radar_metrics so no axis falls back to a fake 50.
RADAR_METRICS = [
    "SCA", "key_passes", "GCA", "xA", "crosses_to_box",                 # Chance Creation
    "PrgPasses", "PrgCarries", "PrgRecv", "prg_pass_acc",               # Progression
    "pass_pct", "fwd_pass_pct", "acc_passes_f3",                        # Passing
    "npxG", "npGoals", "finishing", "SoT", "touches_in_box",            # Finishing
    "Tkl_Int", "def_duel_pct", "recoveries", "Int_padj", "blocks_clearances",  # Defending
    "take_ons", "take_on_pct", "aerial_pct",                           # Dribbling
]

# Human-readable metric labels (the VECTORS use terse keys).
PRETTY = {
    "npxG": "non-penalty xG", "npGoals": "non-penalty goals", "finishing": "finishing",
    "SoT": "shots on target", "key_passes": "key passes", "SCA": "shot creation",
    "touches_in_box": "touches in box", "take_ons": "take-ons", "take_on_pct": "take-on %",
    "aerial_pct": "aerial duels %", "dispossessed": "ball retention", "xA": "expected assists",
    "PrgCarries": "progressive carries", "PrgPasses": "progressive passes",
    "PrgRecv": "progressive receptions", "pass_pct": "pass completion %", "GCA": "goal creation",
    "Tkl_Int": "tackles + interceptions", "recoveries": "recoveries", "blocks": "blocks",
    "blocks_clearances": "blocks + clearances", "clearances": "clearances", "errors": "avoiding errors",
    "fouls": "discipline (low fouls)", "crosses_to_box": "crosses to box", "def_duel_pct": "tackle win %",
    "prg_pass_acc": "progressive pass accuracy", "fwd_pass_pct": "forward pass %",
    "acc_passes_f3": "passes into final third", "Int_padj": "interceptions (adj)",
    "Tkl_padj": "tackles (adj)", "PrgPasses_padj": "progressive passes (adj)",
    "PSxG_GA": "shot-stopping (PSxG-GA)", "save_pct": "save %", "GA": "goals prevented",
    "def_outside_box": "sweeping", "launched_pct": "long-pass accuracy",
}


def _ikey(name: str):
    """(first-initial, surname) key -- bridges datamb's abbreviated first names
    ('A. Bastoni') to Understat's full names ('Alessandro Bastoni')."""
    t = _norm(name).split()
    return (t[0][0], t[-1]) if t else None


def _tokens2(name: str):
    """Accent-folded tokens with punctuation split (not stripped), so compound
    surnames separate: 'Mbappe-Lottin' -> ['mbappe', 'lottin'] (vs _norm which
    joins to 'mbappelottin')."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return "".join(c if c.isalnum() else " " for c in s).split()


def _datamb_to_understat(con: duckdb.DuckDBPyConnection, pairs, season: str) -> dict:
    """Map (datamb_player, datamb_team) -> Understat player_id for one season.
    Team disambiguates same-named players (e.g. the two 'J. Bellingham' brothers).
    Phases: A unique (initial, surname), B shared surname token, C fuzzy."""
    us = con.execute(
        "SELECT ps.player_id, any_value(p.player_name) AS player_name, "
        "arg_max(t.team_name, ps.minutes) AS team_name "
        "FROM player_season_stats ps JOIN players p USING (player_id) "
        "JOIN teams t USING (team_id) WHERE ps.season = ? GROUP BY ps.player_id",
        [season]).df()
    by_key = defaultdict(list)
    tok_idx = defaultdict(list)        # surname token -> [(player_id, first_initial)]
    us_team = {}
    for r in us.itertuples():
        pid = int(r.player_id)
        us_team[pid] = _norm(str(r.team_name))
        by_key[_ikey(r.player_name)].append(pid)
        toks = _tokens2(r.player_name)
        if toks:
            init = toks[0][0]
            for tk in set(toks[1:] or toks):
                if len(tk) >= 4:
                    tok_idx[tk].append((pid, init))
    us_ids = [int(i) for i in us.player_id]
    us_norm = [_norm(n) for n in us.player_name]

    def _by_team(cands, dteam):
        """Pick the candidate whose Understat team best matches the datamb team."""
        cands = [c for c in cands if c not in used]
        if len(cands) <= 1:
            return cands[0] if cands else None
        dt = _norm(str(dteam))
        score, pid = max((fuzz.token_set_ratio(dt, us_team.get(c, "")), c) for c in cands)
        return pid if score >= 60 else None

    mapping, used, leftover = {}, set(), []
    for nm, tm in pairs:                               # phase A: unique initial+surname
        cands = [i for i in by_key.get(_ikey(nm), []) if i not in used]
        pick = cands[0] if len(cands) == 1 else (_by_team(cands, tm) if cands else None)
        if pick is not None:
            mapping[(nm, tm)] = pick
            used.add(pick)
        else:
            leftover.append((nm, tm))

    still = []                                         # phase B: shared surname token
    for nm, tm in leftover:
        toks = _tokens2(nm)
        cands = list({pid for tk in (toks[1:] or toks) if len(tk) >= 4
                      for pid, ui in tok_idx.get(tk, []) if ui == toks[0][0] and pid not in used}) \
            if toks else []
        pick = cands[0] if len(cands) == 1 else (_by_team(cands, tm) if cands else None)
        if pick is not None:
            mapping[(nm, tm)] = pick
            used.add(pick)
        else:
            still.append((nm, tm))
    leftover = still

    for nm, tm in leftover:                            # phase C: fuzzy fallback
        target = _norm(nm)
        avail = [(i, n) for i, n in zip(us_ids, us_norm) if i not in used]
        if not target or not avail:
            continue
        ids_a, names_a = [a[0] for a in avail], [a[1] for a in avail]
        best = process.extractOne(target, names_a, scorer=fuzz.token_sort_ratio)
        if best and best[1] >= MATCH_THRESHOLD:
            mapping[(nm, tm)] = ids_a[best[2]]
            used.add(ids_a[best[2]])
            continue
        cand = [(fuzz.token_set_ratio(target, n), i) for i, n in avail
                if fuzz.token_set_ratio(target, n) >= RECOVER_THRESHOLD
                and _name_compatible(target, n)]
        if cand:
            score, pid = max(cand)
            mapping[(nm, tm)] = pid
            used.add(pid)
    return mapping


def _assign_groups(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["position_group"] = None
    cm = df["datamb_position"] == "CM"
    df.loc[~cm, "position_group"] = df.loc[~cm, "datamb_position"].map(
        lambda b: BUCKET_TO_GROUPS[b][0])
    if cm.any():
        df.loc[cm, "position_group"] = _split_cm(df[cm]).values
    return df


def build_profiles(season: str = FOCUS_SEASON,
                   min_minutes: int = MIN_MINUTES_FOR_RATING) -> None:
    con = duckdb.connect(str(DB_PATH))
    df = con.execute(f"""
        SELECT * FROM player_wyscout
        WHERE season = '{season}' AND datamb_position = main_position
          AND minutes_played >= {min_minutes} AND in_top5
    """).df()
    df = _assign_groups(df)                  # df["position_group"] = datamb group
    tcol = "team_within_selected_timeframe"
    xwalk = _datamb_to_understat(con, list(zip(df["player"], df[tcol])), season)
    df["player_id"] = [xwalk.get((p, t)) for p, t in zip(df["player"], df[tcol])]
    print(f"  datamb->understat: {df['player_id'].notna().sum()}/{len(df)} players linked to player_id")

    # overlay FotMob position (same rule as rate.py) so SWOT pools match the
    # ratings; datamb only decides DM vs CM. See positions-use-fotmob.
    df["datamb_group"] = df["position_group"]
    try:
        pp = con.execute("SELECT player_id, fotmob_group FROM player_position").df()
        fmg = dict(zip(pp.player_id, pp.fotmob_group))
    except Exception:
        fmg = {}

    def _final(player_id, dg):
        fm = fmg.get(player_id)
        if fm in ("GK", "CB", "ST", "AM", "W", "FB"):
            return fm
        if fm == "CMID":
            return "DM" if dg == "DM" else "CM"
        return dg
    df["position_group"] = [_final(pid, dg)
                            for pid, dg in zip(df["player_id"], df["datamb_group"])]

    rows = []
    for grp, g in df.groupby("position_group"):
        for metric, expr, weight, invert in _norm_weights(VECTORS[grp]):
            vals = _metric_series(g, expr)
            pr = vals.rank(pct=True)                      # 0..1 within group
            good = (1 - pr) if invert else pr             # higher = better, always
            pct = (good * 100).round(1)
            for player, team, v, p in zip(g["player"],
                                          g["team_within_selected_timeframe"],
                                          vals.round(3), pct):
                label = ("strength" if p >= STRONG_PCT else
                         "weakness" if p <= WEAK_PCT else "neutral")
                rows.append((xwalk.get((player, team)), season, player, team, grp, metric,
                             PRETTY.get(metric, metric), float(v), float(p),
                             round(weight, 4), invert, label))

    cols = ["player_id", "season", "player", "team", "position_group", "metric",
            "metric_label", "value", "percentile", "weight", "invert", "label"]
    out = pd.DataFrame(rows, columns=cols)
    con.execute("DROP TABLE IF EXISTS player_profile_metrics")
    con.execute("CREATE TABLE player_profile_metrics AS SELECT * FROM out")

    # Radar metrics: percentile every player on the FULL radar metric set (not just
    # their position vector), still ranked within their position group -- so the
    # profile radar shows a real number on every axis (e.g. a striker's Defending
    # vs other strikers) instead of a fake neutral 50 for unmeasured axes.
    radar_rows = []
    for grp, g in df.groupby("position_group"):
        for metric in RADAR_METRICS:
            expr, invert = ALL_METRIC_EXPR[metric]
            vals = _metric_series(g, expr)
            pr = vals.rank(pct=True)
            pct = (((1 - pr) if invert else pr) * 100).round(1)
            for player, team, p in zip(g["player"], g["team_within_selected_timeframe"], pct):
                if pd.isna(p):
                    continue
                radar_rows.append((xwalk.get((player, team)), season, grp, metric,
                                   PRETTY.get(metric, metric), float(p)))
    rout = pd.DataFrame(radar_rows, columns=["player_id", "season", "position_group",
                                             "metric", "metric_label", "percentile"])
    con.execute("DROP TABLE IF EXISTS player_radar_metrics")
    con.execute("CREATE TABLE player_radar_metrics AS SELECT * FROM rout")

    # Summary view: top strengths (by percentile), weaknesses & improvements
    # (improvements = weaknesses ordered by metric weight) per player.
    con.execute(f"""
        CREATE OR REPLACE VIEW v_player_profile AS
        WITH s AS (
            SELECT *,
                row_number() OVER (PARTITION BY season, player
                    ORDER BY percentile DESC, weight DESC) AS strong_rk,
                row_number() OVER (PARTITION BY season, player
                    ORDER BY percentile ASC, weight DESC) AS weak_rk,
                row_number() OVER (PARTITION BY season, player
                    ORDER BY (label='weakness') DESC, weight DESC, percentile ASC) AS impr_rk
            FROM player_profile_metrics
        )
        SELECT
            s.player_id, r.season, r.player, r.team, r.position_group,
            r.detailed_position, r.rating, r.rank_in_group, r.classification,
            string_agg(CASE WHEN s.strong_rk <= {TOP_N} AND s.label='strength'
                       THEN s.metric_label END, ', ' ORDER BY s.strong_rk) AS strengths,
            string_agg(CASE WHEN s.weak_rk <= {TOP_N} AND s.label='weakness'
                       THEN s.metric_label END, ', ' ORDER BY s.weak_rk) AS weaknesses,
            string_agg(CASE WHEN s.impr_rk <= {TOP_N} AND s.label='weakness'
                       THEN s.metric_label END, ', ' ORDER BY s.impr_rk) AS areas_of_improvement
        FROM s
        JOIN player_ratings_v2 r
          ON r.season = s.season AND r.player = s.player
         AND r.position_group = s.position_group AND r.team = s.team
        GROUP BY s.player_id, r.season, r.player, r.team, r.position_group,
                 r.detailed_position, r.rating, r.rank_in_group, r.classification
    """)

    # Unified profile (use case 3): position + market value + career + SWOT,
    # keyed on Understat player_id. Career detail stays in v_player_career.
    con.execute("""
        CREATE OR REPLACE VIEW v_player_profile_full AS
        SELECT
            pp.player_id, pp.player, pp.team, pp.position_group AS main_position,
            pp.detailed_position, pp.rating, pp.rank_in_group, pp.classification,
            mv.market_value_eur,
            car.seasons AS career_seasons, car.career_games,
            car.career_goals, car.career_assists,
            pp.strengths, pp.weaknesses, pp.areas_of_improvement
        FROM v_player_profile pp
        LEFT JOIN player_market_value mv
               ON mv.player_id = pp.player_id AND mv.season = pp.season
        LEFT JOIN (
            SELECT player_id, COUNT(*) AS seasons, SUM(games) AS career_games,
                   SUM(goals) AS career_goals, SUM(assists) AS career_assists
            FROM v_player_career GROUP BY player_id
        ) car ON car.player_id = pp.player_id
        WHERE pp.player_id IS NOT NULL
    """)
    n = con.execute("SELECT COUNT(*) FROM player_profile_metrics").fetchone()[0]
    np_ = con.execute("SELECT COUNT(*) FROM v_player_profile").fetchone()[0]
    con.close()
    print(f"player_profile_metrics: {n} rows; v_player_profile: {np_} players "
          f"(strength>={STRONG_PCT}pct, weakness<={WEAK_PCT}pct).")


if __name__ == "__main__":
    build_profiles()
