"""
Per-season fine positions for every player (so the profile's season selector can
show a CONCRETE position that changes over a career, not just Understat's coarse
GK/D/M/F or the carried-back current position).

No source gives per-season DETAILED positions directly: SofaScore/FotMob detail
is career-aggregate, and the only per-season signal is Understat's coarse code.
So we DERIVE the fine group from each season's own stat profile, reusing the
rating engine's split logic ([[rating-engine]]):

  coarse (Understat per-season position)         refined into
  --------------------------------------         ------------------------------
  GK                                       ->     GK
  M (Midfielder)                           ->     DM / CM / AM   (attack-minus-defence terciles)
  F (Forward)                              ->     ST / W         (wide-creation vs box-scoring)
  D (Defender)                             ->     CB / FB        (attacking involvement)

The DM/CM/AM and ST/W splits need FotMob defensive/dribble stats, which only
exist from 2020/21 (`player_enrichment`); earlier seasons (or players with no
FotMob row) keep the coarse label. CB/FB uses Understat creation stats so it
works for all 12 seasons. No L/R sides (no per-season source has them). The label
is INFERRED, not official -- documented in the UI.

Output: table `player_position_history(player_id, season, coarse_group,
fine_position, minutes)`. fine_position is a fine code (ST/W/AM/CM/DM/CB/FB/GK)
when split, else NULL (UI then shows the readable coarse word).

Run after load_enrich (needs player_enrichment):
    python -m pipeline.positions_history
"""
import sys
import warnings

import duckdb
import numpy as np
import pandas as pd

try:
    from config import DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH

warnings.filterwarnings("ignore")

MIN_MINUTES_COHORT = 450        # players below this still get classified, but the
                                # z-score cohort (mean/std/thresholds) uses only
                                # those at/above it, so the splits aren't noisy.

# coarse Understat group -> the coarse label shown when we can't split
COARSE_LABEL = {"GK": "Goalkeeper", "DEF": "Defender", "MID": "Midfielder", "FWD": "Forward"}

_AGG = """
WITH us AS (   -- Understat season totals (all 12 seasons), + the main coarse group
    SELECT player_id, season,
           SUM(minutes) AS mins, SUM(goals) AS goals, SUM(assists) AS assists,
           SUM(key_passes) AS kp, SUM(xa) AS xa, SUM(np_xg) AS npxg, SUM(shots) AS shots,
           arg_max(position_group, minutes) AS coarse   -- most-played row's group
    FROM player_season_stats GROUP BY player_id, season),
fm AS (        -- FotMob enrichment totals (2020/21+ only)
    SELECT player_id, season,
           SUM(minutes_played) AS mins, SUM(tackles) AS tk, SUM(interceptions) AS intc,
           SUM(recoveries) AS rec, SUM(dribbles_completed) AS drib,
           SUM(chances_created) AS cc
    FROM player_enrichment GROUP BY player_id, season)
SELECT u.player_id, u.season, u.mins, u.coarse,
       u.goals, u.assists, u.kp, u.xa, u.npxg, u.shots,
       f.mins AS fm_mins, f.tk, f.intc, f.rec, f.drib, f.cc
FROM us u LEFT JOIN fm f USING (player_id, season)
WHERE u.coarse IS NOT NULL AND u.mins > 0
"""


def _per90(total, mins):
    m = pd.to_numeric(mins, errors="coerce")
    return pd.to_numeric(total, errors="coerce") / m.where(m > 0) * 90


def _z(series, cohort):
    """z-score of `series` using mean/std of the `cohort`-masked subset."""
    s = pd.to_numeric(series, errors="coerce")
    ref = s[cohort]
    mu, sd = ref.mean(), ref.std(ddof=0)
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return ((s - mu) / sd).fillna(0.0)


def _classify_season(df: pd.DataFrame, st_w_anchor: dict) -> pd.Series:
    """fine_position per player for one season's frame (NaN -> couldn't split).
    st_w_anchor: player_id -> 'ST'/'W' official current position, used to stabilise
    the ST/W call (a striker-vs-winger identity barely changes over a career, and
    dribbly strikers like Mbappé look identical to scoring wingers like Salah in the
    stats -- so we trust the official label unless a season is decisively wide/central)."""
    fine = pd.Series(index=df.index, dtype="object")
    qual = df["mins"] >= MIN_MINUTES_COHORT
    has_fm = df["fm_mins"].notna() & (df["fm_mins"] > 0)

    # per-90 rates
    xa90, kp90, npxg90 = _per90(df.xa, df.mins), _per90(df.kp, df.mins), _per90(df.npxg, df.mins)
    g90, a90, sh90 = _per90(df.goals, df.mins), _per90(df.assists, df.mins), _per90(df.shots, df.mins)
    tk90, in90 = _per90(df.tk, df.fm_mins), _per90(df.intc, df.fm_mins)
    rec90, dr90, cc90 = _per90(df.rec, df.fm_mins), _per90(df.drib, df.fm_mins), _per90(df.cc, df.fm_mins)

    # GK
    fine[df.coarse == "GK"] = "GK"

    # MID -> DM/CM/AM (needs FotMob defensive stats). The AM signal is BOX/GOAL
    # THREAT (npxg/goals/shots), NOT passing volume -- deep playmakers (Kimmich,
    # Rodri) create a lot from deep, so creation-led indices wrongly tag them AM.
    # attack-minus-defence index, terciles within the season's qualified mids.
    mid = (df.coarse == "MID") & has_fm
    if mid.any():
        cohort = mid & qual
        attack = (_z(npxg90, cohort) + _z(g90, cohort) + _z(sh90, cohort)
                  + 0.5 * _z(xa90, cohort))
        defend = (_z(tk90, cohort) + _z(in90, cohort) + _z(rec90, cohort))
        diff = (attack - defend)[mid]
        ref = diff[qual[mid]] if qual[mid].any() else diff
        lo, hi = ref.quantile(1 / 3), ref.quantile(2 / 3)
        fine[mid] = np.where(diff >= hi, "AM", np.where(diff <= lo, "DM", "CM"))

    # FWD -> ST/W. The stat signal (dribbles + creation - box-scoring) can't
    # reliably separate dribbly strikers from scoring wingers, so it's ANCHORED to
    # the official current position: an ST stays ST unless that season is decisively
    # wide (top 40% of the wide-index), a W stays W unless decisively central
    # (bottom 40%); the 40-60 dead-zone keeps the official label. Players with no
    # anchor (retired pre-2025/26) use a plain median split.
    fwd = (df.coarse == "FWD") & has_fm
    if fwd.any():
        cohort = fwd & qual
        widx = (_z(dr90, cohort) + _z(cc90, cohort) - _z(npxg90, cohort))
        ref = widx[cohort] if cohort.any() else widx[fwd]
        med, hi, lo = ref.median(), ref.quantile(0.6), ref.quantile(0.4)
        anchor = df["player_id"].map(lambda p: st_w_anchor.get(int(p)))
        def _stw(i):
            a, w = anchor.iloc[i], widx.iloc[i]
            if a == "ST":  return "W" if w >= hi else "ST"
            if a == "W":   return "ST" if w <= lo else "W"
            return "W" if w >= med else "ST"          # no anchor -> plain split
        fine[fwd] = [ _stw(i) for i in np.where(fwd.values)[0] ]

    # DEF -> CB/FB (Understat creation stats; works all seasons). FB = more
    # attacking involvement. median split within the season's qualified defenders.
    dfn = df.coarse == "DEF"
    if dfn.any():
        cohort = dfn & qual
        fbidx = (_z(a90, cohort) + _z(kp90, cohort) + _z(xa90, cohort))
        if has_fm.any():                         # add dribbles/chances where present
            fbidx = fbidx + _z(dr90, cohort) + _z(cc90, cohort)
        fbidx = fbidx[dfn]
        thr = fbidx[qual[dfn]].median() if qual[dfn].any() else fbidx.median()
        fine[dfn] = np.where(fbidx >= thr, "FB", "CB")

    return fine


def build_positions_history() -> None:
    con = duckdb.connect(str(DB_PATH))
    df = con.execute(_AGG).df()
    # official current ST/W label (FotMob, via player_position) -> anchors the
    # otherwise-unreliable per-season ST/W split. Empty dict if the table is absent.
    try:
        st_w_anchor = {int(p): g for p, g in con.execute(
            "SELECT player_id, fotmob_group FROM player_position "
            "WHERE fotmob_group IN ('ST', 'W')").fetchall()}
    except Exception:
        st_w_anchor = {}
    out = []
    for season, g in df.groupby("season"):
        g = g.reset_index(drop=True)
        g["fine_position"] = _classify_season(g, st_w_anchor)
        out.append(g[["player_id", "season", "coarse", "mins", "fine_position"]])
    res = pd.concat(out, ignore_index=True).rename(
        columns={"coarse": "coarse_group", "mins": "minutes"})
    res["minutes"] = res["minutes"].astype(int)

    con.execute("DROP TABLE IF EXISTS player_position_history")
    con.execute("""CREATE TABLE player_position_history
        (player_id BIGINT, season VARCHAR, coarse_group VARCHAR,
         fine_position VARCHAR, minutes INTEGER)""")
    con.executemany(
        "INSERT INTO player_position_history VALUES (?,?,?,?,?)",
        [(int(r.player_id), r.season, r.coarse_group,
          (None if pd.isna(r.fine_position) else r.fine_position), int(r.minutes))
         for r in res.itertuples()])
    con.execute("CREATE INDEX IF NOT EXISTS idx_pph ON player_position_history(player_id, season)")
    n_fine = res["fine_position"].notna().sum()
    con.close()
    print(f"player_position_history: {len(res)} player-seasons, {n_fine} with a fine "
          f"split ({res['fine_position'].dropna().value_counts().to_dict()}).")


if __name__ == "__main__":
    build_positions_history()
