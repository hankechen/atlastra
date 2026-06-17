"""
Join SofaScore Top-5 clearances + errors onto `player_wyscout`.

datamb has no clearances/errors, so the rating engine's CB/DM/FB vectors dropped
them. This backfills both (as per-90 rates) by fuzzy-matching SofaScore players to
datamb players, adding `clearances_per_90` and `errors_per_90` columns. Unmatched
players keep NULL -> the engine treats a missing metric as neutral (z=0), so this
only ever helps.

Matching is hard because datamb abbreviates first names ("V. van Dijk") while
SofaScore spells them out ("Virgil van Dijk"), so we match on a
(first-initial, last-name) key with team as a tie-breaker, then a fuzzy fallback
within the same team for the stragglers.

Run after pipeline.scrape_sofa_domestic + pipeline.load_datamb:
    python -m pipeline.load_sofa_domestic
"""
import sys
import unicodedata
import warnings
from collections import defaultdict

import duckdb
import pandas as pd
from rapidfuzz import fuzz

try:
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, RAW_DIR, FOCUS_SEASON

warnings.filterwarnings("ignore")

SOFA_RAW = RAW_DIR.parent / "sofascore"


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _key(name: str):
    """(first-initial, last-name) key. 'V. van Dijk' and 'Virgil van Dijk' ->
    'v|van dijk'; single names map to themselves."""
    toks = [t for t in _fold(name).replace("-", " ").replace(".", " . ").split() if t]
    if toks and toks[0] == ".":
        toks = toks[1:]
    if len(toks) >= 2 and len(toks[0]) == 1:        # "V." style initial
        ini, last = toks[0], [t for t in toks[1:] if t != "."]
    elif toks:
        ini, last = toks[0][0], [t for t in toks[1:] if t != "."] or [toks[0]]
    else:
        return None
    return f"{ini}|{' '.join(last)}"


def _team(t: str) -> str:
    t = _fold(t)
    for suf in [" fc", " cf", " fk", " sc", " ac", " calcio 1913", " 1913"]:
        t = t.replace(suf, "")
    return t.replace(".", "").replace("-", " ").strip()


def _per90(total, minutes):
    if pd.isna(total) or not minutes or pd.isna(minutes):
        return None
    return round(float(total) / float(minutes) * 90, 4)


def load_sofa_domestic(season: str = FOCUS_SEASON) -> None:
    src = SOFA_RAW / f"domestic_defense_{season}.parquet"
    if not src.exists():
        print(f"No {src.name} -- run `python -m pipeline.scrape_sofa_domestic` first. Skipping.")
        return

    sofa = pd.read_parquet(src)
    sofa["errors"] = (sofa["error_lead_to_goal"].fillna(0)
                      + sofa["error_lead_to_shot"].fillna(0))

    con = duckdb.connect(str(DB_PATH))
    dm = con.execute(
        "SELECT DISTINCT player, team_within_selected_timeframe AS team "
        "FROM player_wyscout WHERE season = ?", [season]).df()

    by_key = defaultdict(list)
    for r in sofa.itertuples():
        by_key[_key(r.player_name)].append(r)

    out, matched, fuzzy = [], 0, 0
    for d in dm.itertuples():
        cands = by_key.get(_key(d.player), [])
        rec = None
        if len(cands) == 1:
            rec = cands[0]
        elif len(cands) > 1:                         # tie-break on team
            rec = max(cands, key=lambda c: fuzz.token_set_ratio(_team(c.team_name), _team(d.team)))
            if fuzz.token_set_ratio(_team(rec.team_name), _team(d.team)) < 60:
                rec = None
        if rec is None:                              # fuzzy fallback within team
            dt = _team(d.team)
            pool = [c for c in sofa.itertuples()
                    if fuzz.token_set_ratio(_team(c.team_name), dt) >= 80]
            if pool:
                best = max(pool, key=lambda c: fuzz.token_sort_ratio(_fold(c.player_name), _fold(d.player)))
                if fuzz.token_sort_ratio(_fold(best.player_name), _fold(d.player)) >= 80:
                    rec, fuzzy = best, fuzzy + 1
        if rec is None:
            continue
        matched += 1
        out.append({"player": d.player, "team": d.team,
                    "clearances_per_90": _per90(rec.clearances, rec.minutes_played),
                    "errors_per_90": _per90(rec.errors, rec.minutes_played)})

    add = pd.DataFrame(out)
    for col in ("clearances_per_90", "errors_per_90"):
        con.execute(f"ALTER TABLE player_wyscout ADD COLUMN IF NOT EXISTS {col} DOUBLE")
    con.register("add_df", add)
    con.execute("""
        UPDATE player_wyscout AS w SET
            clearances_per_90 = a.clearances_per_90,
            errors_per_90     = a.errors_per_90
        FROM add_df a
        WHERE w.player = a.player AND w.team_within_selected_timeframe = a.team
    """)
    con.unregister("add_df")
    con.close()
    print(f"player_wyscout: backfilled clearances+errors for {matched}/{len(dm)} "
          f"players ({fuzzy} via fuzzy fallback).")


if __name__ == "__main__":
    load_sofa_domestic()
