"""
World Cup stats-based ratings.

Unlike the first cut (a flat linear map of SofaScore's average match rating), this
grades each player on their ACTUAL tournament output -- goals, xG, assists, chances
created, dribbles, tackles, interceptions, passing volume + accuracy, duel win rate
(and, for keepers, save% / goals conceded / clean sheets / saves) -- the same
metric family and machinery as pipeline.rate_combined (the League/UCL common-metric
engine), so a World Cup number reads on the same 0-99 Atlastra scale.

Positions are the COARSE SofaScore lines (G / D / M / F): most World Cup squads are
players outside the top-5 leagues, so we have no fine ST/W/AM/CM/DM/FB/CB split for
them -- everyone is graded against tournament peers in their own line.

A World Cup is a tiny sample (3-7 games), so minutes shrinkage is strong (K below)
and the minutes floor is low: a player is pulled hard toward the pool mean until he
has real tournament minutes, so nobody rates elite off one match.

Pure function `compute(players)`: takes the scraped per-player stat dicts and returns
{(season, player_id): {"rating", "classification", "rank", "n"}}. Runs on the
scraping machine (needs numpy/pandas); the server only stores/reads the result.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd

from pipeline.rate import _zscore, _classify

# ---- tournament awards -------------------------------------------------------
# Winning an individual FIFA World Cup award floors the tournament rating to
# AWARD_FLOOR: an external mark of a standout tournament the box stats can miss --
# e.g. a Golden Glove won largely on penalty-SHOOTOUT saves (which no open-play
# stat captures), so Emi Martínez 2022 no longer grades on open play alone. Winners
# are name-token matched to the scraped rows. 2026 awards are decided at the final,
# so that edition is added once known.
AWARD_FLOOR = 85
AWARDS = {
    "2010": {"Diego Forlán": "Golden Ball", "Thomas Müller": "Golden Boot",
             "Iker Casillas": "Golden Glove"},
    "2014": {"Lionel Messi": "Golden Ball", "James Rodríguez": "Golden Boot",
             "Manuel Neuer": "Golden Glove", "Paul Pogba": "Best Young Player"},
    "2018": {"Luka Modrić": "Golden Ball", "Harry Kane": "Golden Boot",
             "Thibaut Courtois": "Golden Glove", "Kylian Mbappé": "Best Young Player"},
    "2022": {"Lionel Messi": "Golden Ball", "Kylian Mbappé": "Golden Boot",
             "Emiliano Martínez": "Golden Glove", "Enzo Fernández": "Best Young Player"},
}


# Manual per-edition rating overrides (edition -> {name: exact rating}). A hand-set
# value that supersedes BOTH the computed rating and the award floor -- for cases
# neither stats nor awards capture (tiny sample, reputation). Name-token matched.
MANUAL_OVERRIDES = {
    "2026": {"Lamine Yamal": 81, "Folarin Balogun": 73,
             "Cristian Romero": 65, "Enzo Fernández": 75, "Alexis Mac Allister": 71,
             "Rodrigo De Paul": 73, "Lautaro Martínez": 70, "Thiago Almada": 63,
             "Nuno Mendes": 76, "Vitinha": 71, "Thibaut Courtois": 80},
}


def _fold(s: str) -> set:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ø", "o").replace("å", "a").replace("æ", "ae")
    return set(re.sub(r"[^a-z ]", " ", s.lower()).split())


def _apply_awards(out: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Floor each edition's award winners to AWARD_FLOOR (folded name-token match:
    exact, or >=2 shared tokens), then recompute their classification."""
    toks = {}                                    # (season, pid) -> folded tokens
    for r in df.itertuples():
        pid = r.player_id
        if pid is None or (isinstance(pid, float) and np.isnan(pid)):
            continue
        toks[(r.season, int(pid))] = _fold(str(r.player))
    out = out.copy()
    out["player_id"] = pd.to_numeric(out["player_id"], errors="coerce").astype("int64")
    for season, winners in AWARDS.items():
        for full_name in winners:
            want = _fold(full_name)
            for (s, pid), t in toks.items():
                if s == season and (t == want or len(t & want) >= 2):
                    m = (out["season"] == season) & (out["player_id"] == pid)
                    out.loc[m, "rating"] = out.loc[m, "rating"].clip(lower=AWARD_FLOOR)
    for season, ov in MANUAL_OVERRIDES.items():      # exact sets, win over awards
        for full_name, val in ov.items():
            want = _fold(full_name)
            for (s, pid), t in toks.items():
                if s == season and (t == want or len(t & want) >= 2):
                    m = (out["season"] == season) & (out["player_id"] == pid)
                    out.loc[m, "rating"] = int(val)
    out["classification"] = [_classify(int(r), int(k))
                             for r, k in zip(out["rating"], out["rank"])]
    return out

# canonical metric -> (per_90?, invert?).  counts are per-90-blended with the raw
# total (0.5/0.5); rates (%) are used as-is.
METRICS = {
    "goals": (True, False), "assists": (True, False), "xg": (True, False),
    "shots": (True, False), "chances_created": (True, False),
    "big_chances_created": (True, False), "dribbles_completed": (True, False),
    "tackles": (True, False), "interceptions": (True, False),
    "passes_completed": (True, False), "pass_accuracy_pct": (False, False),
    "duels_won_pct": (False, False),
}

# COARSE position weight vectors (auto-normalised). F = all forwards (ST + wingers),
# M = all midfielders, D = all defenders (FB + CB). Balanced so an attacking full-back
# (chances/assists) and a stopper CB (duels/passing) can both score in the D pool.
WEIGHTS = {
    "F": {"goals": .24, "xg": .16, "shots": .10, "dribbles_completed": .10,
          "big_chances_created": .08, "assists": .10, "chances_created": .06,
          "duels_won_pct": .06, "pass_accuracy_pct": .06, "interceptions": .04},
    "M": {"passes_completed": .10, "chances_created": .14, "assists": .12,
          "pass_accuracy_pct": .08, "tackles": .10, "interceptions": .06,
          "duels_won_pct": .08, "xg": .06, "goals": .10, "dribbles_completed": .08,
          "big_chances_created": .08},
    "D": {"duels_won_pct": .22, "pass_accuracy_pct": .16, "passes_completed": .14,
          "tackles": .14, "interceptions": .14, "chances_created": .06,
          "assists": .06, "goals": .04, "dribbles_completed": .04},
}

# Keepers: own vector, led by goals PREVENTED (saves vs post-shot xG) -- the
# team-independent shot-stopping metric -- rather than raw goals conceded / clean
# sheets, which mostly reflect how much the TEAM in front concedes and buried busy
# keepers on deep-running sides. (No open-play stat captures penalty-shootout saves,
# so keepers whose tournament value was shootouts are graded on their open play.)
GK_METRICS = {
    "goals_prevented": (True, False), "save_percentage_pct": (False, False),
    "saves_per_90": (False, False), "clean_sheets": (True, False),
}
GK_WEIGHTS = {"goals_prevented": .42, "save_percentage_pct": .22,
              "saves_per_90": .18, "clean_sheets": .18}

# Per-line calibration gain (spread) -- attackers are measured well by box output,
# defenders more crudely, so defensive lines get a slightly damped spread. Mirrors
# the intent of rate_combined.BASE_GAIN, collapsed to coarse lines.
GAIN = {"F": 1.00, "M": 0.95, "D": 0.85, "G": 1.05}

# Tournament-scale shrinkage. K is small vs a league season (a WC max is ~690 min):
# a 450-min player (≈5 games) keeps ~0.69 of his signal, a one-game player ~0.31.
K_SHRINK = 200
MIN_MINUTES = 90            # need ~one full match before a rating is emitted

# soft-knee 0-99 curve, same shape as rate_combined
CURVE_CENTER, CURVE_SLOPE, CURVE_KNEE, CURVE_COMP = 55.0, 14.0, 78.0, 0.050


def _curve(S):
    base = CURVE_CENTER + CURVE_SLOPE * S
    out = np.where(base > CURVE_KNEE,
                   CURVE_KNEE + (base - CURVE_KNEE) / (1 + CURVE_COMP * (base - CURVE_KNEE)),
                   base)
    return np.clip(np.round(out), 1, 99)


def _rate_group(g: pd.DataFrame, weights: dict, metrics: dict, gain: float) -> pd.DataFrame:
    total = sum(weights.values())
    C = pd.Series(0.0, index=g.index)
    mins = pd.to_numeric(g["minutes"], errors="coerce").fillna(0)
    for m, (per90, inv) in metrics.items():
        if m not in weights:
            continue
        x = pd.to_numeric(g.get(m), errors="coerce")
        if per90:
            # 65% rate + 35% volume: rewards efficient low-minute players (a productive
            # cameo isn't buried by small totals) while the minutes shrinkage below
            # still guards against tiny-sample noise.
            s90 = x / mins.replace(0, np.nan) * 90
            z = 0.65 * _zscore(s90) + 0.35 * _zscore(x)
        else:
            z = _zscore(x)
        C = C + (weights[m] / total) * (-z if inv else z)
    C_adj = mins / (mins + K_SHRINK) * C
    mu, sigma = C_adj.mean(), C_adj.std(ddof=0)
    S = ((C_adj - mu) / sigma if sigma else C_adj * 0) * gain
    out = g[["season", "player_id"]].copy()
    out["rating"] = pd.Series(_curve(np.asarray(S)), index=g.index).astype(int)
    out["_c"] = C_adj.values
    out = out.sort_values("rating", ascending=False).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    out["n"] = len(out)
    out["classification"] = [_classify(r, k) for r, k in zip(out["rating"], out["rank"])]
    return out.drop(columns="_c")


def compute(players: list[dict]) -> dict:
    """players: scraped per-player stat dicts (canonical metric names, plus season /
    position / player_id / minutes). Returns {(season, player_id): {...rating...}}."""
    if not players:
        return {}
    df = pd.DataFrame(players)
    df["minutes"] = pd.to_numeric(df["minutes"], errors="coerce").fillna(0)
    df = df[df["minutes"] >= MIN_MINUTES]
    if df.empty:
        return {}
    frames = []
    for (season, pos), g in df.groupby(["season", "position"]):
        if pos == "G":
            gg = g.copy()
            saves = pd.to_numeric(gg.get("saves"), errors="coerce").fillna(0)
            conc = pd.to_numeric(gg.get("goals_conceded"), errors="coerce").fillna(0)
            faced = (saves + conc).replace(0, np.nan)
            mins = gg["minutes"].replace(0, np.nan)
            gg["save_percentage_pct"] = (saves / faced * 100).fillna(0)
            gg["saves_per_90"] = (saves / mins * 90).fillna(0)
            gg["goals_conceded_per_90"] = (conc / mins * 90).fillna(0)
            if len(gg) >= 2:
                frames.append(_rate_group(gg, GK_WEIGHTS, GK_METRICS, GAIN["G"]))
        elif pos in WEIGHTS and len(g) >= 2:
            frames.append(_rate_group(g, WEIGHTS[pos], METRICS, GAIN[pos]))
    if not frames:
        return {}
    out = _apply_awards(pd.concat(frames, ignore_index=True), df)
    return {(r.season, int(r.player_id)): {"rating": int(r.rating),
            "classification": r.classification, "rank": int(r.rank), "n": int(r.n)}
            for r in out.itertuples()}
