"""
Reduced per-season percentile radar + strengths/weaknesses for PAST seasons
(2020/21+), so the profile's season selector shows real (if cruder) analysis for
history. The full datamb radar/SWOT (pipeline/profile.py) only exists for the
current season; here we use the metrics that DO have history -- Understat
(xG/xA/goals/shots/key passes, all 12 seasons) + FotMob enrichment (chances,
dribbles, tackles, interceptions, recoveries, passes, duels, pass%, from 2020/21)
via v_player_season_stats. No Progression axis or SCA/GCA (those need datamb or
FBref -- see [[atlastra-webapp]]). Percentiles are computed within each
(season, coarse position group) cohort, so they read "vs same position that year".

Output:
  player_radar_hist(player_id, season, axis, value)   -- 5 axes, 0-100, ordered
  player_swot_hist(player_id, season, position_group,
                   strengths, weaknesses, areas_of_improvement)

Run after build_views + positions_history:
    python -m pipeline.profile_history
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

SEASONS = ["2021", "2122", "2223", "2324", "2425"]   # 2020/21+ (FotMob), excl current
MIN_MINUTES = 600                                     # cohort floor for percentiles

# axis -> metric keys (a mix of v_player_season_stats rate/% columns and *_p90
# totals derived below). Order matches the radar's drawn order.
RADAR_AXES = {
    "Finishing":       ["xg_per90", "goals_per90", "shots_p90"],
    "Chance Creation": ["xa_per90", "key_passes_p90", "chances_created_p90", "big_chances_created_p90"],
    "Dribbling":       ["dribbles_p90", "dribble_success_pct"],
    "Passing":         ["passes_completed_p90", "pass_accuracy_pct"],
    "Defending":       ["tackles_p90", "interceptions_p90", "recoveries_p90", "duels_won_pct"],
}
LABELS = {
    "xg_per90": "xG", "goals_per90": "Goals", "shots_p90": "Shots",
    "xa_per90": "xA", "key_passes_p90": "Key passes", "chances_created_p90": "Chances created",
    "big_chances_created_p90": "Big chances created",
    "dribbles_p90": "Dribbling", "dribble_success_pct": "Dribble %",
    "passes_completed_p90": "Passing volume", "pass_accuracy_pct": "Pass accuracy",
    "tackles_p90": "Tackling", "interceptions_p90": "Interceptions",
    "recoveries_p90": "Ball recovery", "duels_won_pct": "Duels won %",
}
_METRICS = sorted({m for axis in RADAR_AXES.values() for m in axis})


def build_profile_history() -> None:
    con = duckdb.connect(str(DB_PATH))
    df = con.execute(f"""
        SELECT v.player_id, v.season, v.minutes,
               v.xg_per90, v.goals_per90, v.xa_per90, v.shots, v.key_passes,
               v.chances_created, v.big_chances_created, v.dribbles_completed,
               v.dribble_success_pct, v.passes_completed, v.pass_accuracy_pct,
               v.tackles, v.interceptions, v.recoveries, v.duels_won_pct,
               h.coarse_group
        FROM v_player_season_stats v
        JOIN player_position_history h
          ON h.player_id = v.player_id AND h.season = v.season
        WHERE v.season IN ({','.join(['?'] * len(SEASONS))})
          AND h.coarse_group IN ('FWD', 'MID', 'DEF')
          AND v.minutes >= {MIN_MINUTES}
    """, SEASONS).df()

    m = df["minutes"].replace(0, np.nan)
    df["shots_p90"] = df["shots"] / m * 90
    df["key_passes_p90"] = df["key_passes"] / m * 90
    df["chances_created_p90"] = df["chances_created"] / m * 90
    df["big_chances_created_p90"] = df["big_chances_created"] / m * 90
    df["dribbles_p90"] = df["dribbles_completed"] / m * 90
    df["passes_completed_p90"] = df["passes_completed"] / m * 90
    df["tackles_p90"] = df["tackles"] / m * 90
    df["interceptions_p90"] = df["interceptions"] / m * 90
    df["recoveries_p90"] = df["recoveries"] / m * 90

    radar_rows, swot_rows = [], []
    for (season, grp), g in df.groupby(["season", "coarse_group"]):
        if len(g) < 6:
            continue
        pct = pd.DataFrame({mk: pd.to_numeric(g[mk], errors="coerce").rank(pct=True) * 100
                            for mk in _METRICS}, index=g.index)
        for idx, pid in zip(g.index, g["player_id"]):
            for axis, mks in RADAR_AXES.items():
                vals = [pct.at[idx, mk] for mk in mks if pd.notna(pct.at[idx, mk])]
                if vals:
                    radar_rows.append((int(pid), season, axis, round(float(np.mean(vals)))))
            ranked = pct.loc[idx].dropna().sort_values(ascending=False)
            strengths = [LABELS[mk] for mk in ranked.index if ranked[mk] >= 70][:3]
            weak = [LABELS[mk] for mk in reversed(ranked.index) if ranked[mk] <= 30][:3]
            swot_rows.append((int(pid), season, grp, ", ".join(strengths),
                              ", ".join(weak), ", ".join(weak)))

    con.execute("DROP TABLE IF EXISTS player_radar_hist")
    con.execute("CREATE TABLE player_radar_hist "
                "(player_id BIGINT, season VARCHAR, axis VARCHAR, value INTEGER)")
    con.executemany("INSERT INTO player_radar_hist VALUES (?,?,?,?)", radar_rows)
    con.execute("CREATE INDEX IF NOT EXISTS idx_rh ON player_radar_hist(player_id, season)")

    con.execute("DROP TABLE IF EXISTS player_swot_hist")
    con.execute("CREATE TABLE player_swot_hist (player_id BIGINT, season VARCHAR, "
                "position_group VARCHAR, strengths VARCHAR, weaknesses VARCHAR, "
                "areas_of_improvement VARCHAR)")
    con.executemany("INSERT INTO player_swot_hist VALUES (?,?,?,?,?,?)", swot_rows)
    con.execute("CREATE INDEX IF NOT EXISTS idx_sh ON player_swot_hist(player_id, season)")
    con.close()
    print(f"player_radar_hist: {len(radar_rows)} axis rows; "
          f"player_swot_hist: {len(swot_rows)} player-seasons (2020/21+, reduced set).")


if __name__ == "__main__":
    build_profile_history()
