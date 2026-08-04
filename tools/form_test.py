"""
Is form real? A hot-hand test on twelve seasons of match logs.

Every football broadcast treats form as a fact: a player is "in form" and
therefore likely to score. The claim is testable, and now that `player_match_log`
covers 21,586 matches it can be tested properly rather than asserted.

The question has to be posed carefully, because the lazy version answers itself.
"Players who scored a lot recently score a lot next match" is true and tells you
nothing -- good players score a lot in every window. Form only means something if
it predicts output **beyond what the player's own baseline already predicts**.

So each appearance is scored against two numbers built only from matches BEFORE
it:

    baseline  his per-90 across this season's earlier appearances, EXCLUDING the
              recent window -- so the same matches never feed both terms
    form      his per-90 across the last FORM_WINDOW appearances

and the test is whether `form` carries information about the next appearance once
`baseline` is in the model. Two ways of asking, because they can disagree:

  1. does adding form to a baseline-only regression reduce held-out error?
  2. split players into hot / cold at matched baselines -- do they differ?

Run:  python -m tools.form_test
      python -m tools.form_test --metric ga     (goals+assists rather than xg+xa)
"""
import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

try:
    from config import DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH

FORM_WINDOW = 5        # appearances counted as "recent form"
MIN_PRIOR = 8          # appearances before the window, so the baseline means something
MIN_MINUTES = 45       # a cameo is not an appearance for this purpose


def _load(con, metric: str) -> pd.DataFrame:
    num = {"xg": "xg + xa", "ga": "CAST(goals + assists AS DOUBLE)"}[metric]
    df = con.execute(f"""
        SELECT player_id, season, match_date, minutes, opp_top_half, is_home,
               ({num}) AS out
        FROM player_match_log
        WHERE minutes >= {MIN_MINUTES}
        ORDER BY player_id, season, match_date
    """).df()
    df["per90"] = df["out"] / df["minutes"] * 90
    return df


def _build(df: pd.DataFrame) -> pd.DataFrame:
    """Attach a leak-free baseline and form to every appearance."""
    g = df.groupby(["player_id", "season"], sort=False)["per90"]
    # everything strictly before this match
    csum = g.cumsum() - df["per90"]
    cnt = g.cumcount()
    # the recent window, also strictly before this match
    roll = g.apply(lambda s: s.shift(1).rolling(FORM_WINDOW).sum()).reset_index(level=[0, 1], drop=True)

    df = df.copy()
    df["form"] = roll / FORM_WINDOW
    # baseline excludes the window, so a match never appears in both terms
    df["baseline"] = (csum - roll) / (cnt - FORM_WINDOW).replace(0, np.nan)
    df["n_prior"] = cnt

    # The confound that decides this question. `baseline` above is built from a
    # dozen-odd earlier matches, so it is a NOISY estimate of how good the player
    # is -- and `form` is five more matches of the same thing. A model will improve
    # when form is added even if recency means nothing at all, simply because two
    # samples estimate ability better than one. Anyone testing the hot hand against
    # a short baseline is mostly rediscovering that.
    #
    # So build a second, much better ability estimate: the player's whole season
    # EXCEPT the window and the match being predicted. It uses later matches too,
    # which would be cheating in a forecast but is exactly right here -- the
    # question is whether recency carries information beyond ability, not whether
    # we could have known at the time.
    tot = df.groupby(["player_id", "season"])["per90"].transform("sum")
    ntot = df.groupby(["player_id", "season"])["per90"].transform("size")
    df["baseline_full"] = (tot - roll - df["per90"]) / (ntot - FORM_WINDOW - 1).replace(0, np.nan)

    return df[(df["n_prior"] >= MIN_PRIOR + FORM_WINDOW)
              & df["form"].notna() & df["baseline"].notna()
              & df["baseline_full"].notna()].copy()


def run(metric: str = "xg") -> dict:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    d = _build(_load(con, metric))
    con.close()
    n = len(d)
    y = d["per90"].values
    base, form = d["baseline"].values, d["form"].values

    print(f"[form] metric={metric}  {n:,} appearances with "
          f"{MIN_PRIOR}+ prior matches and a {FORM_WINDOW}-match window", flush=True)
    print(f"[form] correlations with the NEXT appearance: "
          f"baseline {np.corrcoef(base, y)[0, 1]:.3f}, form {np.corrcoef(form, y)[0, 1]:.3f}", flush=True)

    # ---- 1. does form add anything to a baseline-only model? -----------------
    # split by time so the answer is held out, not fitted
    cut = d["match_date"].quantile(0.7)
    tr, te = (d["match_date"] <= cut).values, (d["match_date"] > cut).values

    def fit(cols):
        X = np.column_stack([np.ones(n)] + [c for c in cols])
        coef, *_ = np.linalg.lstsq(X[tr], y[tr], rcond=None)
        pred = X[te] @ coef
        return coef, float(np.mean(np.abs(y[te] - pred))), float(np.sqrt(np.mean((y[te] - pred) ** 2)))

    c_b, mae_b, rmse_b = fit([base])
    c_bf, mae_bf, rmse_bf = fit([base, form])
    print(f"[form] held-out MAE  baseline only {mae_b:.4f}  |  baseline+form {mae_bf:.4f}  "
          f"({(mae_b - mae_bf) / mae_b * 100:+.2f}%)", flush=True)
    print(f"[form] held-out RMSE baseline only {rmse_b:.4f}  |  baseline+form {rmse_bf:.4f}  "
          f"({(rmse_b - rmse_bf) / rmse_b * 100:+.2f}%)", flush=True)
    print(f"[form] fitted weights: baseline {c_bf[1]:.3f}, form {c_bf[2]:.3f} "
          f"(baseline-only model: {c_b[1]:.3f})", flush=True)

    # ---- 1b. the decisive one: form against a WELL-estimated ability ---------
    full = d["baseline_full"].values
    c_f, mae_f, rmse_f = fit([full])
    c_ff, mae_ff, rmse_ff = fit([full, form])
    print(f"\n[form] against a full-season ability estimate (the confound-free test):", flush=True)
    print(f"[form] held-out MAE  ability only {mae_f:.4f}  |  ability+form {mae_ff:.4f}  "
          f"({(mae_f - mae_ff) / mae_f * 100:+.2f}%)", flush=True)
    print(f"[form] fitted weights: ability {c_ff[1]:.3f}, form {c_ff[2]:.3f}", flush=True)

    # ---- 2. hot vs cold at a matched baseline -------------------------------
    # the honest version of "he's in form": among players of the SAME underlying
    # level, do the ones running hot do better next time out?
    # banded on the full-season ability estimate, for the same reason as above
    d["b_band"] = pd.qcut(d["baseline_full"], 5, labels=False, duplicates="drop")
    d["excess"] = d["form"] - d["baseline_full"]
    rows = []
    for b, sub in d.groupby("b_band"):
        hot = sub[sub["excess"] >= sub["excess"].quantile(0.8)]
        cold = sub[sub["excess"] <= sub["excess"].quantile(0.2)]
        if len(hot) < 100 or len(cold) < 100:
            continue
        rows.append({"ability_band": int(b),
                     "ability": round(float(sub["baseline_full"].mean()), 3),
                     "hot_next": round(float(hot["per90"].mean()), 3),
                     "cold_next": round(float(cold["per90"].mean()), 3),
                     "gap": round(float(hot["per90"].mean() - cold["per90"].mean()), 3),
                     "n_hot": len(hot), "n_cold": len(cold)})
    tab = pd.DataFrame(rows)
    print("\n[form] hot vs cold, within matched baseline bands "
          "(next appearance's per-90):", flush=True)
    print(tab.to_string(index=False), flush=True)

    # a standard error on the pooled gap, so "small" can be told from "zero"
    hot_all = d[d["excess"] >= d.groupby("b_band")["excess"].transform(lambda s: s.quantile(0.8))]
    cold_all = d[d["excess"] <= d.groupby("b_band")["excess"].transform(lambda s: s.quantile(0.2))]
    gap = hot_all["per90"].mean() - cold_all["per90"].mean()
    se = float(np.sqrt(hot_all["per90"].var() / len(hot_all) + cold_all["per90"].var() / len(cold_all)))
    print(f"\n[form] pooled gap {gap:+.4f} per 90 (SE {se:.4f}, "
          f"{gap / se:+.1f} sigma) on {len(hot_all):,} hot vs {len(cold_all):,} cold", flush=True)

    # the verdict hangs on the confound-free comparison, not the naive one
    verdict = ("form carries signal beyond a well-estimated ability"
               if abs(gap / se) > 3 and mae_ff < mae_f
               else "no usable signal once ability is properly estimated -- "
                    "the naive gain is mostly form being extra sample")
    print(f"[form] VERDICT: {verdict}", flush=True)
    return {"n": n, "mae_base": mae_b, "mae_form": mae_bf, "gap": float(gap),
            "se": se, "sigma": float(gap / se), "table": rows, "verdict": verdict}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metric", default="xg", choices=["xg", "ga"],
                    help="xg = xG+xA per 90 (process), ga = goals+assists per 90 (outcome)")
    a = ap.parse_args()
    run(a.metric)
