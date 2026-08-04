"""
What does a transfer do to a player's rating?

The trajectory model has a documented blind spot: it projects a player forward
knowing nothing about where he will be playing. This measures the size of that
hole, and whether it is the kind of hole worth digging out.

The hard part is the counterfactual. Comparing movers to stayers directly answers
the wrong question -- players who move are not a random sample, they are
disproportionately the ones already declining, or young ones already climbing, so
the raw gap mixes the move in with the reason for it.

The trick is that we already have a counterfactual: the trajectory model's own
projection, made without any knowledge of the move. The residual (what he actually
did minus what the model expected of him) is what the move plus everything else
unmodelled is worth. Stayers give the baseline residual; the difference is the
transfer effect.

Everything is scored on transitions the model never trained on.

Run:  python -m tools.transfer_effect
"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

try:
    from config import DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ml.train_trajectory import (  # noqa: E402
    _load_panel, _build, _features, TRAIN_THROUGH)

# How much stronger the new club has to be, in points per game, before the move
# counts as a step up rather than a sideways one.
PPG_STEP = 0.25


def _clubs(con) -> pd.DataFrame:
    """Each player's main club per season, and that club's points per game."""
    return con.execute("""
        SELECT ps.player_id, ps.season,
               ARG_MAX(ps.team_id, ps.minutes) AS team_id
        FROM player_season_stats ps GROUP BY 1, 2
    """).df()


def run() -> dict:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    panel = _build(_load_panel(con))
    panel = panel[panel["age"].notna() & panel["rating_next"].notna()].copy()

    clubs = _clubs(con)
    ppg = con.execute("""
        SELECT team_id, season, points / NULLIF(matches_played, 0) AS ppg
        FROM team_season_stats""").df()
    con.close()

    nxt = clubs.copy()
    nxt["prev"] = nxt["season"].map(lambda s: f"{int(s[:2]) - 1:02d}{int(s[2:]) - 1:02d}")
    d = (panel.merge(clubs, on=["player_id", "season"], how="left")
              .merge(nxt[["player_id", "prev", "team_id"]].rename(
                     columns={"prev": "season", "team_id": "team_next"}),
                     on=["player_id", "season"], how="left"))
    d = d[d["team_id"].notna() & d["team_next"].notna()].copy()

    # Both clubs are graded on the SAME season — the one the player is leaving.
    # That is the comparison a reader would make ("he dropped to a worse side"),
    # and it avoids grading the new club on a season its new signing played in.
    # Joining the new club's ppg on team id alone, without pinning the season,
    # silently pairs each player with an arbitrary year of that club's history.
    d = (d.merge(ppg.rename(columns={"ppg": "ppg_now"}), on=["team_id", "season"], how="left")
          .merge(ppg.rename(columns={"team_id": "team_next", "ppg": "ppg_new"}),
                 on=["team_next", "season"], how="left"))
    d = d.drop_duplicates(subset=["player_id", "season"])
    d["moved"] = (d["team_id"] != d["team_next"]).astype(int)

    # ---- the counterfactual: a model that never saw any of this ---------------
    tr = (d["season"] <= TRAIN_THROUGH).values
    te = ~tr
    _, feats, med = _features(d[tr])
    X, _, _ = _features(d, med)
    y = d["rating_next"].values.astype(float)
    model = GradientBoostingRegressor(n_estimators=400, max_depth=3, learning_rate=0.03,
                                      subsample=0.8, min_samples_leaf=25,
                                      random_state=42).fit(X.values[tr], y[tr])
    d["resid"] = y - model.predict(X.values)
    t = d[te].copy()

    print(f"[transfer] {len(t):,} held-out transitions, {t['moved'].sum():,} of them moves "
          f"({t['moved'].mean():.0%})", flush=True)

    stay = t[t["moved"] == 0]["resid"]
    move = t[t["moved"] == 1]["resid"]
    se = float(np.sqrt(stay.var() / len(stay) + move.var() / len(move)))
    print(f"\n[transfer] residual vs the model's own expectation "
          f"(positive = did better than projected):", flush=True)
    print(f"[transfer]   stayed  {stay.mean():+.2f}  (n={len(stay):,})", flush=True)
    print(f"[transfer]   moved   {move.mean():+.2f}  (n={len(move):,})", flush=True)
    print(f"[transfer]   gap     {move.mean() - stay.mean():+.2f} rating points "
          f"(SE {se:.2f}, {(move.mean() - stay.mean()) / se:+.1f} sigma)", flush=True)

    # ---- does the direction of the move matter? ------------------------------
    m = t[(t["moved"] == 1) & t["ppg_now"].notna() & t["ppg_new"].notna()].copy()
    m["step"] = np.where(m["ppg_new"] - m["ppg_now"] >= PPG_STEP, "up",
                 np.where(m["ppg_new"] - m["ppg_now"] <= -PPG_STEP, "down", "sideways"))
    print(f"\n[transfer] by direction of the move (club points per game, "
          f"±{PPG_STEP} counts as a step):", flush=True)
    rows = []
    for lab in ["up", "sideways", "down"]:
        s = m[m["step"] == lab]["resid"]
        if len(s) < 40:
            continue
        rows.append({"step": lab, "n": len(s), "resid": round(float(s.mean()), 2)})
        print(f"[transfer]   moved {lab:<9} {s.mean():+.2f}  (n={len(s):,})", flush=True)

    # ---- how much of the model's error does this actually explain? -----------
    mae = float(np.abs(t["resid"]).mean())
    adj = t["resid"] - np.where(t["moved"] == 1, move.mean(), stay.mean())
    mae_adj = float(np.abs(adj).mean())
    print(f"\n[transfer] knowing only whether he moved would cut held-out MAE from "
          f"{mae:.3f} to {mae_adj:.3f} ({(mae - mae_adj) / mae * 100:+.2f}%)", flush=True)
    print("[transfer] and it is not knowable at projection time anyway — a transfer that has "
          "not happened yet\n[transfer] cannot be a feature. This measures the blind spot; "
          "it does not close it.", flush=True)

    return {"n": len(t), "gap": float(move.mean() - stay.mean()), "se": se,
            "by_step": rows, "mae": mae, "mae_adj": mae_adj}


if __name__ == "__main__":
    run()
