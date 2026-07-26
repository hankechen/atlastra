"""
Tactics Lab — a transparent, explainable tactical engine.

NOT a black-box ML model: every projected number is a documented function of real
player stats (per-90 output, progression, duels, passing — from v_stats_combined_player)
and the user's tactical settings. That traceability is the whole point — it lets the Lab
say *why* a setup succeeds or fails, and show what each change does ("What Changed?").

Flow:  squad -> pick XI in a formation -> assign roles -> set tactic sliders
       -> simulate() -> unit strengths, projected metrics, weaknesses, style match,
          and (vs an opponent) win probability + tactical battles
       -> simulate_match() -> one match played out of those same odds: scoreline,
          scorers, assists, bookings and a timeline. Re-seed to re-simulate.
"""
from __future__ import annotations

import math
import random

# ------------------------------------------------------------------ formations #
# Slot: id, family (role menu + unit membership), pitch coords x(0-100 L→R),
# y(0 own goal → 100 opp goal), line. Attacking third at the top (high y).
def _slot(sid, fam, x, y, line):
    return {"id": sid, "family": fam, "x": x, "y": y, "line": line}


FORMATIONS = {
    "4-3-3": [
        _slot("GK", "GK", 50, 6, "GK"),
        _slot("LB", "FB", 15, 26, "DEF"), _slot("LCB", "CB", 38, 17, "DEF"),
        _slot("RCB", "CB", 62, 17, "DEF"), _slot("RB", "FB", 85, 26, "DEF"),
        _slot("DM", "DM", 50, 40, "MID"), _slot("LCM", "CM", 33, 53, "MID"),
        _slot("RCM", "CM", 67, 53, "MID"),
        _slot("LW", "W", 17, 77, "ATT"), _slot("ST", "ST", 50, 85, "ATT"),
        _slot("RW", "W", 83, 77, "ATT"),
    ],
    "4-2-3-1": [
        _slot("GK", "GK", 50, 6, "GK"),
        _slot("LB", "FB", 15, 26, "DEF"), _slot("LCB", "CB", 38, 17, "DEF"),
        _slot("RCB", "CB", 62, 17, "DEF"), _slot("RB", "FB", 85, 26, "DEF"),
        _slot("LDM", "DM", 38, 40, "MID"), _slot("RDM", "DM", 62, 40, "MID"),
        _slot("LAM", "W", 20, 64, "ATT"), _slot("CAM", "AM", 50, 62, "MID"),
        _slot("RAM", "W", 80, 64, "ATT"), _slot("ST", "ST", 50, 85, "ATT"),
    ],
    "4-4-2": [
        _slot("GK", "GK", 50, 6, "GK"),
        _slot("LB", "FB", 15, 26, "DEF"), _slot("LCB", "CB", 38, 17, "DEF"),
        _slot("RCB", "CB", 62, 17, "DEF"), _slot("RB", "FB", 85, 26, "DEF"),
        _slot("LM", "WM", 17, 52, "MID"), _slot("LCM", "CM", 40, 46, "MID"),
        _slot("RCM", "CM", 60, 46, "MID"), _slot("RM", "WM", 83, 52, "MID"),
        _slot("LST", "ST", 40, 82, "ATT"), _slot("RST", "ST", 60, 82, "ATT"),
    ],
    "3-5-2": [
        _slot("GK", "GK", 50, 6, "GK"),
        _slot("LCB", "CB", 28, 18, "DEF"), _slot("CB", "CB", 50, 15, "DEF"),
        _slot("RCB", "CB", 72, 18, "DEF"),
        _slot("LWB", "FB", 12, 46, "MID"), _slot("LCM", "CM", 37, 48, "MID"),
        _slot("CM", "DM", 50, 42, "MID"), _slot("RCM", "CM", 63, 48, "MID"),
        _slot("RWB", "FB", 88, 46, "MID"),
        _slot("LST", "ST", 40, 82, "ATT"), _slot("RST", "ST", 60, 82, "ATT"),
    ],
    "3-4-3": [
        _slot("GK", "GK", 50, 6, "GK"),
        _slot("LCB", "CB", 28, 18, "DEF"), _slot("CB", "CB", 50, 15, "DEF"),
        _slot("RCB", "CB", 72, 18, "DEF"),
        _slot("LM", "FB", 13, 50, "MID"), _slot("LCM", "CM", 40, 46, "MID"),
        _slot("RCM", "CM", 60, 46, "MID"), _slot("RM", "FB", 87, 50, "MID"),
        _slot("LW", "W", 20, 78, "ATT"), _slot("ST", "ST", 50, 85, "ATT"),
        _slot("RW", "W", 80, 78, "ATT"),
    ],
}

# Which positions can fill each family (for auto-picking the best XI). Includes FotMob's
# detailed club codes AND the coarse G/D/M/F codes national-team (World Cup) data uses.
FAMILY_POS = {
    "GK": {"GK", "G"},
    "CB": {"CB", "LCB", "RCB", "D"},
    "FB": {"LB", "RB", "LWB", "RWB", "WB", "D"},
    "DM": {"DM", "CDM", "CM", "M"},
    "CM": {"CM", "LCM", "RCM", "DM", "AM", "CAM", "M"},
    "AM": {"AM", "CAM", "CM", "M"},
    # WM = a wide MIDFIELDER (RMF/LMF): a flank player in the midfield line, not the front
    # three. Wingers and central midfielders can fill in, but a natural LM/RM belongs here.
    "WM": {"LM", "RM", "LW", "RW", "W", "CM", "LCM", "RCM", "AM", "CAM", "M"},
    "W": {"LW", "RW", "LM", "RM", "W", "AM", "CAM", "F", "M"},
    "ST": {"ST", "CF", "FW", "F"},
}

# Canonical position-code -> role family (the reverse of FAMILY_POS, which overlaps for
# eligibility). Used to auto-slot a user-added "what-if" player into the right position.
_POS_FAMILY = {
    "GK": "GK", "G": "GK",
    "CB": "CB", "LCB": "CB", "RCB": "CB", "D": "CB",
    "LB": "FB", "RB": "FB", "LWB": "FB", "RWB": "FB", "WB": "FB",
    "CDM": "DM", "DM": "DM",
    "CM": "CM", "LCM": "CM", "RCM": "CM", "M": "CM",
    "CAM": "AM", "AM": "AM",
    "LM": "WM", "RM": "WM", "LMF": "WM", "RMF": "WM",
    "LW": "W", "RW": "W", "W": "W", "F": "W",
    "ST": "ST", "CF": "ST", "FW": "ST",
}


def family_for_position(pos: str) -> str:
    return _POS_FAMILY.get((pos or "").upper(), "CM")


# Which flank a position code belongs to. Only codes that actually name a side count — a
# plain CB, CM or ST has no natural flank and should never be pushed to one.
_POS_SIDE = {"LB": "L", "LWB": "L", "LM": "L", "LW": "L", "LCM": "L",
             "RB": "R", "RWB": "R", "RM": "R", "RW": "R", "RCM": "R"}
# How much being on his own side is worth, per family. A fullback on the wrong flank is a
# real cost (he defends and crosses off the wrong foot); a winger inverting is a choice, so
# the nudge there is gentle enough that a clearly better player still gets the shirt.
_SIDE_WEIGHT = {"FB": 7.0, "WM": 4.0, "W": 4.0}


def _slot_side(slot) -> str:
    """'L', 'R' or '' for a central slot — from the slot id, falling back to its x."""
    sid = (slot.get("id") or "").upper()
    if sid.startswith("L"):
        return "L"
    if sid.startswith("R"):
        return "R"
    x = slot.get("x", 50)
    return "" if 34 <= x <= 66 else ("L" if x < 50 else "R")


def _side_bonus(player, slot) -> float:
    """Rating-equivalent bonus for a player lining up on his natural flank (and the matching
    penalty for the wrong one). Zero for anyone whose position names no side."""
    w = _SIDE_WEIGHT.get(slot.get("family"))
    if not w:
        return 0.0
    ps, ss = _POS_SIDE.get((player.get("position") or "").upper(), ""), _slot_side(slot)
    if not ps or not ss:
        return 0.0
    return w if ps == ss else -w

# ------------------------------------------------------------------- roles ---- #
# Each role nudges how a player's quality feeds the team's units, plus side-effects
# (flank_risk raises transition exposure; buildup helps play out; press adds
# pressure). Deltas are small and additive to a base of 0.
def _role(att=0.0, mid=0.0, dfn=0.0, flank=0.0, buildup=0.0, press=0.0, aerial=0.0, note=""):
    return {"att": att, "mid": mid, "def": dfn, "flank": flank,
            "buildup": buildup, "press": press, "aerial": aerial, "note": note}


ROLES = {
    "GK": {
        "Sweeper Keeper": _role(buildup=0.12, note="steps out, starts moves"),
        "Traditional": _role(note="stays on the line"),
    },
    "CB": {
        "Ball-Playing": _role(mid=0.06, buildup=0.14, note="progresses from the back"),
        "Stopper": _role(dfn=0.06, aerial=0.04, note="steps up, aggressive"),
        "Cover": _role(dfn=0.05, note="drops off, covers space"),
    },
    "FB": {
        "Fullback (Support)": _role(note="balanced"),
        "Attacking Wing-Back": _role(att=0.10, flank=0.40, note="bombs on, leaves space"),
        "Inverted Fullback": _role(mid=0.12, flank=0.16, buildup=0.06, note="tucks into midfield"),
        "Defensive Fullback": _role(dfn=0.07, flank=-0.22, note="stays home"),
    },
    "DM": {
        "Anchor": _role(dfn=0.08, note="screens the defence"),
        "Deep-Lying Playmaker": _role(mid=0.12, buildup=0.08, note="dictates from deep"),
        "Ball-Winner": _role(dfn=0.05, press=0.10, note="hunts the ball"),
    },
    "CM": {
        "Box-to-Box": _role(att=0.05, dfn=0.05, note="covers every blade of grass"),
        "Playmaker": _role(mid=0.10, note="orchestrates"),
        "Mezzala": _role(att=0.07, flank=0.10, note="drifts wide & high"),
        "Carrier": _role(att=0.04, mid=0.05, note="drives with the ball"),
    },
    # The 10 is the position with the widest range of interpretations in the modern game —
    # from a static classic playmaker to a second striker to the first man in the press.
    "AM": {
        "Advanced Playmaker": _role(mid=0.06, att=0.05, note="links play"),
        "Shadow Striker": _role(att=0.10, mid=-0.04, note="attacks the box"),
        "Trequartista": _role(mid=0.12, att=0.04, press=-0.08,
                              note="free role between the lines, no defensive duty"),
        "Enganche": _role(mid=0.15, att=-0.04, buildup=0.08, press=-0.10,
                          note="static classic 10 — everything goes through him"),
        "Pressing Ten": _role(att=0.03, press=0.13, mid=0.02,
                              note="triggers the press from the front"),
        "Box Crasher": _role(att=0.09, aerial=0.05, mid=-0.02,
                             note="late runs into the box, arrives on the cross"),
        "Wide-Drifting Ten": _role(att=0.06, mid=0.04, flank=0.14,
                                   note="drifts to the flank to overload it"),
    },
    # WM (RMF/LMF) — a wide midfielder is NOT a winger: he starts in the midfield line, so
    # his roles trade attacking width against midfield bodies and flank protection.
    "WM": {
        "Wide Midfielder (Support)": _role(att=0.03, mid=0.05, dfn=0.03,
                                           note="balanced — supports both boxes"),
        "Touchline Winger": _role(att=0.08, flank=0.14, note="stays chalk on his boots, crosses"),
        "Half-Space Midfielder": _role(mid=0.13, flank=-0.10, buildup=0.05,
                                       note="tucks into the half-space, makes a box midfield"),
        "Wide Playmaker": _role(mid=0.15, att=0.02, buildup=0.04,
                                note="drifts inside to dictate from wide"),
        "Defensive Winger": _role(dfn=0.11, press=0.07, flank=-0.26, att=-0.04,
                                  note="doubles up on their fullback, kills the flank"),
        "Raumdeuter": _role(att=0.13, mid=-0.06, flank=-0.18,
                            note="gives up width entirely to poach in the box"),
    },
    "W": {
        "Inside Forward": _role(att=0.08, note="cuts inside to shoot"),
        "Winger (Wide)": _role(att=0.03, flank=0.06, note="hugs the line, crosses"),
        "Inverted Winger": _role(att=0.05, mid=0.05, note="comes inside to create"),
    },
    "ST": {
        "Advanced Forward": _role(att=0.06, note="runs in behind"),
        "Poacher": _role(att=0.09, mid=-0.10, note="lives in the box"),
        "False 9": _role(att=-0.10, mid=0.16, note="drops in, overloads midfield"),
        "Target Man": _role(att=0.03, aerial=0.10, mid=-0.04, note="holds it up, aerial"),
        "Pressing Forward": _role(att=0.0, press=0.12, mid=0.03, note="leads the press"),
        "Complete Forward": _role(att=0.06, mid=0.05, note="does everything"),
    },
}
DEFAULT_ROLE = {"GK": "Sweeper Keeper", "CB": "Ball-Playing", "FB": "Fullback (Support)",
                "DM": "Deep-Lying Playmaker", "CM": "Box-to-Box", "AM": "Advanced Playmaker",
                "WM": "Wide Midfielder (Support)", "W": "Inside Forward",
                "ST": "Advanced Forward"}

# Role suitability: each specialised role demands certain attributes at a level. A player
# below the demand is miscast (contribution penalised); above it, a small boost. Roles not
# listed are undemanding (fit ~1). e.g. a Deep-Lying Playmaker needs elite passing — play
# Valverde (good but not a deep dictator) there and his build-up suffers.
ROLE_REQ = {
    "Deep-Lying Playmaker": [("passing", 87, 0.7), ("progression", 84, 0.3)],
    "Playmaker": [("passing", 83, 0.6), ("creativity", 80, 0.4)],
    "Advanced Playmaker": [("creativity", 82, 0.6), ("passing", 80, 0.4)],
    "Ball-Winner": [("defending", 78, 0.7), ("pressing", 76, 0.3)],
    "Anchor": [("defending", 80, 0.7), ("aerial", 72, 0.3)],
    "Box-to-Box": [("progression", 74, 0.5), ("defending", 70, 0.5)],
    "Mezzala": [("dribbling", 78, 0.5), ("creativity", 76, 0.5)],
    "Carrier": [("dribbling", 80, 0.6), ("progression", 76, 0.4)],
    "Shadow Striker": [("shooting", 80, 0.6), ("dribbling", 76, 0.4)],
    "Poacher": [("shooting", 82, 1.0)],
    "Advanced Forward": [("shooting", 77, 0.5), ("pace", 79, 0.5)],
    "False 9": [("creativity", 80, 0.4), ("passing", 78, 0.3), ("dribbling", 78, 0.3)],
    "Target Man": [("aerial", 78, 0.7), ("shooting", 74, 0.3)],
    "Pressing Forward": [("pressing", 76, 0.6), ("pace", 78, 0.4)],
    "Complete Forward": [("shooting", 80, 0.4), ("creativity", 76, 0.3), ("dribbling", 78, 0.3)],
    "Inside Forward": [("shooting", 77, 0.4), ("dribbling", 80, 0.6)],
    "Winger (Wide)": [("pace", 80, 0.5), ("dribbling", 78, 0.5)],
    "Inverted Winger": [("creativity", 78, 0.5), ("dribbling", 78, 0.5)],
    # the 10s
    "Trequartista": [("creativity", 84, 0.6), ("dribbling", 78, 0.4)],
    "Enganche": [("passing", 84, 0.6), ("creativity", 82, 0.4)],
    "Pressing Ten": [("pressing", 74, 0.6), ("pace", 76, 0.4)],
    "Box Crasher": [("shooting", 78, 0.6), ("progression", 74, 0.4)],
    "Wide-Drifting Ten": [("dribbling", 78, 0.5), ("creativity", 78, 0.5)],
    # the wide midfielders
    "Touchline Winger": [("pace", 79, 0.5), ("dribbling", 77, 0.5)],
    "Half-Space Midfielder": [("passing", 77, 0.5), ("progression", 76, 0.5)],
    "Wide Playmaker": [("creativity", 81, 0.6), ("passing", 79, 0.4)],
    "Defensive Winger": [("defending", 68, 0.5), ("pressing", 74, 0.5)],
    "Raumdeuter": [("shooting", 77, 0.6), ("progression", 73, 0.4)],
    "Inverted Fullback": [("passing", 77, 1.0)],
    "Attacking Wing-Back": [("pace", 79, 0.5), ("dribbling", 74, 0.5)],
    "Ball-Playing": [("passing", 76, 1.0)],
    "Stopper": [("defending", 80, 0.6), ("aerial", 76, 0.4)],
}


def _role_fit(role_name, attrs):
    """0.75–1.15 multiplier for how well a player's attributes suit a role."""
    req = ROLE_REQ.get(role_name)
    if not req:
        return 1.0
    score = wsum = 0.0
    for attr, demand, w in req:
        score += w * (1 + (attrs.get(attr, 70) - demand) / 22.0)
        wsum += w
    return _clamp_f(score / wsum if wsum else 1.0, 0.75, 1.15)


def _best_role(family, player):
    """The role in this position family that best suits the player's attributes — so the
    auto-XI already profiles players correctly (a limited passer isn't cast as a playmaker)."""
    roles = ROLES.get(family)
    if not roles:
        return DEFAULT_ROLE.get(family, "")
    at = player_attrs(player, family)
    best, best_fit = DEFAULT_ROLE.get(family), -1.0
    for name in roles:
        fit = _role_fit(name, at)
        # tie-break toward the family default so undemanded roles keep their conventional label
        if fit > best_fit + 1e-9 or (abs(fit - best_fit) < 1e-9 and name == DEFAULT_ROLE.get(family)):
            best, best_fit = name, fit
    return best

# Tactic sliders (all 0-100, 50 = neutral). Documented so the UI and engine agree.
TACTIC_KEYS = ["tempo", "width", "directness", "patience", "counter",
               "line_height", "press", "compactness"]
DEFAULT_TACTICS = {k: 50 for k in TACTIC_KEYS}

# Famous-side fingerprints over [possession, press, line, directness, width, counter].
STYLES = {
    "Guardiola City ’23": [82, 74, 72, 30, 66, 20],
    "Klopp Liverpool ’19": [58, 88, 78, 66, 60, 62],
    "Spain ’12 (tiki-taka)": [86, 70, 66, 22, 58, 15],
    "Ancelotti Madrid ’24": [55, 52, 55, 62, 55, 74],
    "Simeone Atlético": [42, 46, 34, 60, 42, 66],
    "Xabi Leverkusen ’24": [64, 70, 64, 52, 62, 48],
    "Arteta Arsenal ’24": [72, 72, 66, 42, 62, 30],
    "De Zerbi Brighton ’23": [76, 64, 60, 26, 54, 20],
    "Bielsa (man-to-man)": [50, 92, 84, 68, 66, 56],
    "Mourinho (low-block counter)": [38, 42, 36, 70, 46, 80],
    "Conte (3-5-2 wing-backs)": [50, 58, 56, 58, 74, 58],
    "Cruyff Total Football": [80, 74, 80, 32, 72, 20],
    "Nagelsmann (verticality)": [60, 80, 72, 58, 60, 46],
    "Deschamps France (pragmatic)": [48, 50, 48, 56, 50, 70],
    "Route one (long-ball)": [33, 52, 46, 92, 56, 66],
    "Barcelona ’15 (MSN)": [83, 74, 70, 42, 62, 34],
    "Enrique PSG ’25": [72, 78, 70, 44, 60, 38],
    "Zidane Madrid (three-peat)": [58, 55, 57, 56, 56, 68],
    "Ancelotti Milan ’07": [60, 44, 52, 44, 44, 40],
}


# --------------------------------------------------------------- attributes -- #
def _clamp(v, lo=1, hi=99):
    return max(lo, min(hi, v))


def _sc(v, ref, floor=12, span=74):
    """Scale a raw per-90 value onto ~0-99 (floor at low output, ~86 at the reference)."""
    return _clamp(floor + span * ((v or 0) / ref)) if ref else floor


_PACE_BASE = {"W": 74, "WM": 71, "FB": 70, "ST": 70, "AM": 63, "CM": 60, "DM": 55,
              "CB": 50, "GK": 50}


def player_attrs(p: dict, family: str) -> dict:
    """0-99 attributes for the engine. Prefers FIFA/EA FC card attributes (stable, real
    pace/shooting/passing/defending) as the primary source; falls back to per-90-stat
    derivation only for players not in the FIFA data."""
    f = p.get("fifa")
    if f:
        return {
            "shooting": _clamp(f["sho"]),
            "creativity": _clamp(0.7 * f["pas"] + 0.3 * f["dri"]),
            "dribbling": _clamp(f["dri"]),
            "passing": _clamp(f["pas"]),
            "progression": _clamp(0.5 * f["pas"] + 0.4 * f["dri"] + 0.1 * f["pac"]),
            "defending": _clamp(f["def"]),
            "aerial": _clamp(0.55 * f["hea"] + 0.45 * f["phy"]),
            "pressing": _clamp(0.5 * f["phy"] + 0.5 * f["def"]),
            "pace": _clamp(f["pac"]),
            "rating": p.get("rating") or f["o"],
        }
    n, pc = p.get("per90", {}), p.get("pct", {})
    tk_int = (n.get("tackles", 0) + n.get("interceptions", 0))
    passing = _clamp(0.70 * (pc.get("passing") or 60) + 0.30 * _sc(n.get("passes", 0), 72))
    creativity = _sc(n.get("xa", 0) + n.get("chances", 0) * 0.18, 0.55)
    dribbling = _sc(n.get("dribbles", 0), 2.4)
    aerial = pc.get("aerial") or (52 if family in ("CB", "ST") else 42)
    ts = p.get("top_speed")
    if ts:                                               # REAL UCL max sprint (km/h) → 0-99
        pace = _clamp(round((ts - 28) * 9 + 30), 20, 96)
    else:                                                # estimate: position base ± dribbles/clearances
        base = _PACE_BASE.get(family, 60)
        pace = _clamp(base + (n.get("dribbles", 0) - 1.0) * 6 - max(0, n.get("clearances", 0) - 3.0) * 3, 22, 96)
    return {
        "shooting": _sc(n.get("xg", 0), 0.45),
        "creativity": creativity,
        "dribbling": dribbling,
        "passing": passing,
        "progression": _clamp(0.45 * _sc(n.get("passes", 0), 72) + 0.30 * dribbling + 0.25 * creativity),
        "defending": _clamp(0.6 * _sc(tk_int, 5.2) + 0.4 * (pc.get("duels") or 50)),
        "aerial": _clamp(aerial),
        "pressing": _sc(tk_int, 5.6),
        "pace": pace,
        "rating": p.get("rating") or 55,
    }


# ------------------------------------------------------------- auto best XI --- #
def build_xi(squad: list[dict], formation: str) -> list[dict]:
    """Greedily fill each formation slot with the highest-rated eligible unused player."""
    slots = FORMATIONS.get(formation) or FORMATIONS["4-3-3"]
    pool = sorted(squad, key=lambda p: -(p.get("rating") or 0))
    used, xi = set(), []
    for s in slots:
        elig = FAMILY_POS[s["family"]]
        cands = [p for p in pool if p["player"] not in used
                 and (p.get("position") or "").upper() in elig]
        # Rating alone filled the left back slot first and therefore gave it to the best
        # fullback in the squad, whichever flank he actually plays — PSG lined up with
        # Hakimi at left back and Nuno Mendes at right back. A player's own side is worth a
        # few rating points here, so the natural pairing wins unless the gap is big.
        pick = max(cands, key=lambda p: (p.get("rating") or 0) + _side_bonus(p, s),
                   default=None)
        if pick is None:                                   # fall back to best remaining
            pick = next((p for p in pool if p["player"] not in used), None)
        if pick:
            used.add(pick["player"])
            xi.append({**s, "player": pick, "role": _best_role(s["family"], pick)})
        else:
            xi.append({**s, "player": None, "role": DEFAULT_ROLE[s["family"]]})
    return xi


# --------------------------------------------------------------- the model --- #
def _units(xi: list[dict]) -> dict:
    """Aggregate the XI into team unit strengths (0-99), applying each player's role."""
    A = {"attack": [], "midfield": [], "defense": [], "press_resist": [],
         "def_pace": [], "aerial": [], "att_pace": [], "gk": 55, "def_help": 0.0,
         "press_roles": 0.0}
    for s in xi:
        p = s.get("player")
        if not p:
            continue
        fam, line = s["family"], s["line"]
        at = player_attrs(p, fam)
        r = ROLES.get(fam, {}).get(s.get("role")) or _role()
        fit = _role_fit(s.get("role"), at)               # profiling: right role = more value
        # How hard the ROLES press, independent of the slider: a Ball-Winner and a Pressing
        # Forward hunt the ball, a Trequartista is excused it. Summed here and spent on PPDA
        # in _metrics, so choosing a free 10 really does cost the press a man.
        A["press_roles"] += r["press"]
        if fam == "GK":
            A["gk"] = at["rating"]
            A["press_resist"].append(at["passing"] * (1 + r["buildup"]))
            continue
        # attacking contribution (front + creative mids)
        if line == "ATT" or fam in ("AM",):
            val = 0.50 * at["shooting"] + 0.34 * at["creativity"] + 0.16 * at["dribbling"]
            A["attack"].append(_clamp(val * (1 + r["att"]) * fit))
            A["att_pace"].append(at["pace"])
        # A wide midfielder feeds BOTH units — he is the flank's attacking outlet and an
        # extra body in the middle third — which is why he needs his own branch: before
        # this, a 4-4-2's LM/RM sat in neither list and were invisible to the model.
        if fam == "WM":
            att = 0.42 * at["shooting"] + 0.34 * at["creativity"] + 0.24 * at["dribbling"]
            A["attack"].append(_clamp(att * (1 + r["att"]) * fit * 0.86))
            A["att_pace"].append(at["pace"])
            mid = 0.38 * at["passing"] + 0.32 * at["progression"] + 0.30 * at["creativity"]
            A["midfield"].append(_clamp(mid * (1 + r["mid"]) * fit * 0.92))
            # A wide midfielder who tracks back protects a flank, but he is not a defender:
            # dropping his (low) defending into the back-line average would DRAG the unit
            # down for doing more work. It's a small bonus on top of the line instead.
            A["def_help"] += max(0.0, r["def"]) * (12 + at["defending"] / 12.0)
        if fam in ("CM", "DM", "AM"):
            val = 0.40 * at["passing"] + 0.34 * at["progression"] + 0.26 * at["creativity"]
            A["midfield"].append(_clamp(val * (1 + r["mid"]) * fit))
        if fam in ("CB", "FB", "DM"):
            wdef = {"CB": 1.0, "FB": 0.85, "DM": 0.6}[fam]
            val = 0.58 * at["defending"] + 0.24 * at["aerial"] + 0.18 * at["pace"]
            A["defense"].append(_clamp(val * (1 + r["def"]) * wdef * fit + (1 - wdef) * 55))
            if fam in ("CB", "DM"):
                A["press_resist"].append(at["passing"] * (1 + r["buildup"]))
            if fam in ("CB", "FB"):
                A["def_pace"].append(at["pace"])
                A["aerial"].append(at["aerial"])
        # inverted FB feeds midfield too
        if fam == "FB" and r["mid"]:
            A["midfield"].append(_clamp(at["progression"] * (1 + r["mid"])))

    def mean(xs, d=60):
        return sum(xs) / len(xs) if xs else d
    rts = [s["player"]["rating"] for s in xi if s.get("player") and s["player"].get("rating")]
    return {
        "attack": mean(A["attack"]), "midfield": mean(A["midfield"]),
        "defense": _clamp(mean([*A["defense"], A["gk"] * 0.9]) + min(4.0, A["def_help"])),
        "press_resist": mean(A["press_resist"]),
        "def_pace": mean(A["def_pace"], 55), "aerial": mean(A["aerial"], 50),
        "att_pace": mean(A["att_pace"], 62), "gk": A["gk"],
        "press_roles": round(A["press_roles"], 3),          # summed role pressing (see _metrics)
        "avg_rating": sum(rts) / len(rts) if rts else 62,   # team-quality signal for projections
    }


# Neutral opponent for single-team mode (FIFA-attribute scale: a solid average side).
_BASE_OPP = {"attack": 78, "midfield": 77, "defense": 77, "press_resist": 75,
             "def_pace": 73, "aerial": 73, "att_pace": 74, "gk": 78}


def _metrics(u: dict, t: dict, ou: dict, ot: dict) -> dict:
    """Project match metrics for a side (units u, tactics t) vs opponent (ou, ot).
    Formulas are deliberately simple + monotonic so the 'why' is explainable.

    The sliders are the point of the Lab, so they carry real weight here, and every one of
    them does something: tempo and compactness used to appear nowhere in this function, and
    nothing a side did could LOWER what it conceded — only a high line with quick defenders
    helped. Now the plan can be built to defend as well as to attack, and the settings that
    matter most are the ones aimed at the opponent's:

      • width vs their compactness      — width is worth most against a narrow block
      • counter vs their line height    — the space behind is only there if they push up
      • press vs their press-resistance — pressing a side that plays through it backfires
      • compactness                     — denies the space between the lines
      • tempo                           — more sequences, both ways: end-to-end games are open

    Every term is written as a deviation from a neutral 50, so a default setup against a
    default opponent produces exactly the same numbers as before this weighting existed."""
    d = lambda k: (t.get(k, 50) - 50) / 50.0                 # our slider in [-1,1]  # noqa: E731
    o = lambda k: (ot.get(k, 50) - 50) / 50.0                # theirs, for the matchups  # noqa: E731
    # possession is relative: their directness and counter-attacking hand the ball back
    poss = _clamp(50 + 0.14 * (u["midfield"] - ou["midfield"])
                  - 13 * (d("directness") - o("directness"))
                  - 11 * (d("counter") - o("counter"))
                  + 6 * (d("press") - o("press"))
                  - 5 * (d("tempo") - o("tempo")), 26, 76)
    terr = round(_clamp(50 + (poss - 50) * 0.6 + 30 * d("line_height") * 0.5
                        - 10 * d("counter"), 12, 88))
    # attack (centred on 77 = an average side on the FIFA-attribute scale)
    att = 1.35 + (u["attack"] - 77) / 32.0 + (u["midfield"] - 77) / 70.0
    att *= (1
            + 0.10 * d("width") * (0.6 + 0.6 * o("compactness"))   # stretch a narrow block
            + 0.08 * d("patience")                                 # work it, wait for the ball
            + 0.16 * d("counter") * o("line_height")               # the space behind their line
            - 0.07 * max(0.0, d("counter"))                        # ...but you attack less often
            + 0.06 * d("directness") * o("line_height")            # hit the space early
            - 0.04 * max(0.0, d("directness"))                     # ...or hand it straight back
            + 0.09 * d("press")                                    # turnovers high up the pitch
            + 0.07 * d("tempo"))                                   # more sequences per game
    att *= 1 - (ou["defense"] - 77) / 150.0 - 0.12 * o("compactness")
    # where the game is played: a side pinned in its own half simply gets fewer goes at it
    att *= 1 + 0.16 * ((terr - 50) / 50.0)
    xg = round(_clamp_f(att, 0.3, 3.3), 2)
    # concede
    xga = 1.35 + (ou["attack"] - 77) / 32.0 + (ou["midfield"] - 77) / 70.0
    xga *= 1 - (u["defense"] - 77) / 150.0
    xga *= 1 - 0.16 * d("compactness")                       # a compact block denies the pockets
    xga *= 1 - 0.08 * max(0.0, d("counter"))                 # sitting in cedes territory, not chances
    xga *= 1 + 0.06 * d("tempo")                             # an end-to-end game runs both ways
    # pressing high wins it back closer to their goal — worth most with legs up front, and
    # a liability against a side comfortable playing through it
    xga *= 1 - 0.14 * d("press") * (0.6 + 0.5 * _clamp_f((u["att_pace"] - 72) / 14.0, -1.0, 1.0))
    risk_press = 1 + 0.14 * d("press") * (0.5 + 0.5 * _clamp_f((ou["press_resist"] - 74) / 12.0,
                                                               -1.0, 1.0))
    risk_line = 1 + 0.16 * d("line_height") * _clamp_f((73 - u["def_pace"]) / 30.0, -0.5, 1.0)
    xga = round(_clamp_f(xga * risk_line * risk_press, 0.30, 3.2), 2)
    # PPDA: the slider sets the intent, the ROLES supply the legs — a side full of
    # ball-winners and pressing forwards gets after it harder than the same slider with a
    # free 10 and a deep-lying playmaker standing off.
    ppda = round(_clamp_f(13.5 - (t.get("press", 50) - 50) / 7.0
                          - (t.get("line_height", 50) - 50) / 15.0
                          - 3.5 * u.get("press_roles", 0.0), 5, 20), 1)
    prog = round(_clamp(0.7 * u["midfield"] + 0.3 * u["press_resist"] + 8 * d("directness")))
    return {"possession": round(poss), "xg": xg, "xga": xga, "ppda": ppda,
            "progression": prog, "territory": terr}


def _clamp_f(v, lo, hi):
    return max(lo, min(hi, v))


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _goal_pmf(k, mean, shape=None):
    """P(exactly k goals) when the match's own xG is itself uncertain — a Poisson whose mean
    is drawn from a Gamma around `mean`, which integrates to the negative binomial. This is
    the marginal the simulators sample from (see _xg_draw), so odds and scorelines agree.
    `shape=None` falls back to a plain Poisson on a fixed mean."""
    if not shape:
        return _pois(k, mean)
    r = shape
    p = r / (r + max(0.05, mean))
    # C(k+r-1, k) * p^r * (1-p)^k, with the binomial coefficient built by ratio so a
    # non-integer shape stays valid
    c = 1.0
    for i in range(k):
        c *= (r + i) / (i + 1)
    return c * (p ** r) * ((1 - p) ** k)


def _win_probs(hx, ax, shape=None):
    ph = pd = pa = 0.0
    for i in range(9):
        for j in range(9):
            p = _goal_pmf(i, hx, shape) * _goal_pmf(j, ax, shape)
            if i > j:
                ph += p
            elif i == j:
                pd += p
            else:
                pa += p
    tot = ph + pd + pa or 1
    h = round(ph / tot * 100)
    dd = round(pd / tot * 100)
    return {"home": h, "draw": dd, "away": 100 - h - dd}


# ---------------------------------------------------------- weaknesses -------- #
def _find(xi, sid=None, fam=None):
    for s in xi:
        if (sid and s["id"] == sid) or (fam and s["family"] == fam):
            return s
    return None


def _weaknesses(xi, u, t, m) -> list[dict]:
    out = []
    # miscast players: a starter asked to play a role his attributes don't support. The
    # single worst offender is surfaced so the user sees that role selection actually matters.
    miscast = []
    for s in xi:
        p = s.get("player")
        if not p or s["family"] == "GK":
            continue
        fit = _role_fit(s.get("role"), player_attrs(p, s["family"]))
        if fit < 0.92:
            miscast.append((fit, s))
    for fit, s in sorted(miscast, key=lambda z: z[0])[:2]:
        req = ROLE_REQ.get(s.get("role"), [])
        need = ", ".join(a for a, _d, _w in req) or "the key traits"
        alt = _best_role(s["family"], s["player"])
        tip = f" — {alt} suits him better." if alt and alt != s.get("role") else ""
        out.append({"title": f"{s['player']['player']} is miscast as {s.get('role')}",
                    "severity": "high" if fit < 0.82 else "med",
                    "reason": f"{s['player']['player']} doesn't have the {need} this role demands "
                    f"(role fit {round(fit * 100)}%). His output in it is dialled down.{tip}"})
    # flank exposure: an attacking FB + a slow same-side CB + a high line
    fbs = [s for s in xi if s["family"] == "FB" and s.get("player")]
    cbs = [s for s in xi if s["family"] == "CB" and s.get("player")]
    for fb in fbs:
        role = ROLES.get("FB", {}).get(fb.get("role")) or _role()
        left = fb["x"] < 50
        side = "Left" if left else "Right"
        near_cb = min(cbs, key=lambda c: abs(c["x"] - fb["x"]), default=None) if cbs else None
        cb_pace = player_attrs(near_cb["player"], "CB")["pace"] if near_cb else 68
        exposure = role["flank"] + max(0, (t.get("line_height", 50) - 50) / 100.0)
        # a wide midfielder on that side who tucks back covers the space the fullback leaves
        cover = next((s for s in xi if s["family"] == "WM" and s.get("player")
                      and (s["x"] < 50) == left
                      and (ROLES.get("WM", {}).get(s.get("role")) or _role())["def"] >= 0.05), None)
        if cover:
            exposure -= 0.25
        if exposure > 0.45 and cb_pace < 66:
            out.append({"title": f"{side} flank exposed in transition", "severity": "high",
                        "reason": f"{fb['player']['player']} pushes high ({fb.get('role')}) while "
                        f"{near_cb['player']['player'] if near_cb else 'the cover CB'} lacks recovery "
                        f"pace (est. {round(cb_pace)}). Quick wingers can attack the space behind."})
    # an open block: nobody squeezing the space between the lines
    if t.get("compactness", 50) < 36:
        out.append({"title": "Open block between the lines",
                    "severity": "high" if u["defense"] < 82 else "med",
                    "reason": f"Compactness is set to {t.get('compactness', 50)}, so the gaps "
                    "between your units stay wide open. Anyone who receives on the half-turn "
                    "in there is running at your back four before it can set."})
    # end-to-end by design, with defenders who can't win the footrace it creates
    if t.get("tempo", 50) > 72 and u["def_pace"] < 73:
        out.append({"title": "End-to-end tempo with a slow back line", "severity": "med",
                    "reason": f"A tempo of {t.get('tempo', 50)} turns the game into transitions, "
                    f"and your defence's recovery pace (est. {round(u['def_pace'])}) is not built "
                    "for that. Every attack you lose becomes a race you're behind in."})
    # play out under pressure
    if u["press_resist"] < 72 and t.get("directness", 50) < 46:
        gk = _find(xi, fam="GK")
        gk_role = gk.get("role") if gk else ""
        if gk_role != "Sweeper Keeper":
            out.append({"title": "Vulnerable building out under pressure", "severity": "med",
                        "reason": f"You've set a patient, short build-up but the back line's passing "
                        f"under pressure is modest (press-resistance {round(u['press_resist'])}). A "
                        f"high press can force turnovers in dangerous areas."})
    # no holding midfielder
    if not any(s["family"] == "DM" and s.get("player") for s in xi):
        out.append({"title": "Midfield can be bypassed centrally", "severity": "med",
                    "reason": "No dedicated holding midfielder screens the defence — through-balls "
                    "and runners into the space in front of the CBs are harder to control."})
    # high line + slow defenders
    if t.get("line_height", 50) > 62 and u["def_pace"] < 68:
        out.append({"title": "Space in behind a high line", "severity": "high",
                    "reason": f"A high defensive line with slow-ish defenders (est. pace "
                    f"{round(u['def_pace'])}) leaves room in behind for pace to run onto."})
    # aerial / set-pieces
    if u["aerial"] < 68:
        out.append({"title": "Set-piece & crossing vulnerability", "severity": "med",
                    "reason": f"Modest aerial ability at the back ({round(u['aerial'])}) — crosses "
                    f"and set pieces are a route in for the opponent."})
    # blunt vs a low block
    if u["attack"] > 84 and u["midfield"] < 76 and t.get("width", 50) < 45 and t.get("patience", 50) < 45:
        out.append({"title": "May struggle to unlock a low block", "severity": "low",
                    "reason": "Strong finishers but limited central creation, narrow shape and a "
                    "rushed final third — a deep, compact defence could frustrate you."})
    return out[:5]


# ---------------------------------------------------------- style + battles -- #
def _style_match(t: dict, m: dict) -> list[dict]:
    # Distance-based similarity over the tactical fingerprint (cosine is too flat when
    # every dimension is a positive 0-100 value). Closer vector = higher %.
    vec = [m["possession"], t.get("press", 50), t.get("line_height", 50),
           t.get("directness", 50), t.get("width", 50), t.get("counter", 50)]
    out = []
    for name, ref in STYLES.items():
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(vec, ref)) / len(vec))
        out.append({"name": name, "pct": round(_clamp_f(1 - dist / 55.0, 0, 1) * 100)})
    out.sort(key=lambda x: -x["pct"])
    return out[:4]


def _battles(uA, tA, uB, tB) -> list[dict]:
    def logi(x):                                          # softened + clamped so it never reads 0/100
        return _clamp_f(1 / (1 + math.exp(-x / 15.0)), 0.08, 0.92)
    b = [
        {"label": "Midfield control", "a": round(logi(uA["midfield"] - uB["midfield"]) * 100)},
        {"label": "Your attack vs their defence",
         "a": round(logi((uA["attack"] + uA["att_pace"] * 0.25)
                         - (uB["defense"] + uB["def_pace"] * 0.25)) * 100)},
        {"label": "Their attack vs your defence",
         "a": round(100 - logi((uB["attack"] + uB["att_pace"] * 0.25)
                               - (uA["defense"] + uA["def_pace"] * 0.25)) * 100)},
        {"label": "Aerial & set pieces", "a": round(logi(uA["aerial"] - uB["aerial"]) * 100)},
    ]
    return b


# ------------------------------------------------- playstyle chemistry ------- #
# Roles ARE playstyles, and playstyles interact: some pairings reinforce each other,
# others fight for the same space or leave a job undone. This is a transparent synergy
# layer — every +/- is a NAMED tactical relationship the UI can show, never a hidden
# number. It yields a 0-99 cohesion score that gently nudges the attack (a disjointed
# front line finishes fewer chances; a well-linked one, more).
_CHEM_BASE = 76
_CHEM_W = {("synergy", "high"): 6, ("synergy", "med"): 4, ("synergy", "low"): 2,
           ("clash", "high"): -9, ("clash", "med"): -6, ("clash", "low"): -3}


def _chem_label(score):
    if score >= 86:
        return "Excellent cohesion"
    if score >= 77:
        return "Well balanced"
    if score >= 67:
        return "Workable"
    if score >= 56:
        return "Some friction"
    return "Disjointed"


def _side(x):
    return "L" if x < 45 else ("R" if x > 55 else "C")


def _chemistry(xi, t):
    """Named, explainable synergies and clashes between the roles (playstyles) in the XI.
    Returns {score, label, links[]} where each link is a concrete tactical relationship
    (an overlapping wing-back behind an inside forward; two poachers who never link up).
    Nothing is hidden — the score is exactly the sum of the listed relationships."""
    slots = [s for s in xi if s.get("player")]

    def fam(*f):
        return [s for s in slots if s["family"] in f]

    def role(s):
        return s.get("role") or ""

    def nm(s):
        return s["player"]["player"]

    sts, ws, ams, wms = fam("ST"), fam("W"), fam("AM"), fam("WM")
    cms, dms, fbs, cbs = fam("CM"), fam("DM"), fam("FB"), fam("CB")
    gk = next((s for s in slots if s["family"] == "GK"), None)

    CREATE = {"Advanced Playmaker", "Playmaker", "Deep-Lying Playmaker",
              "Inverted Winger", "Inverted Fullback", "Mezzala",
              "Trequartista", "Enganche", "Wide Playmaker", "Half-Space Midfielder"}
    WIDE = {"Winger (Wide)", "Attacking Wing-Back", "Touchline Winger"}   # width / crosses
    RUN = {"Advanced Forward", "Poacher", "Shadow Striker", "Inside Forward",
           "Winger (Wide)", "Complete Forward", "Box Crasher", "Raumdeuter",
           "Touchline Winger"}                              # attack the space in behind
    NARROW = {"Raumdeuter", "Defensive Winger", "Half-Space Midfielder"}  # give up the touchline
    creators = [s for s in (ams + ws + wms + cms + dms) if role(s) in CREATE]
    widemen = [s for s in (ws + wms + fbs) if role(s) in WIDE]

    links = []

    def add(kind, sev, title, detail, players):
        links.append({"kind": kind, "sev": sev, "title": title,
                      "detail": detail, "players": players})

    # ---------------- clashes ----------------
    poachers = [s for s in sts if role(s) == "Poacher"]
    if len(poachers) >= 2:
        add("clash", "high", "Two poachers, no link play",
            "Both strikers only play off the shoulder and sit in the box — with neither "
            "dropping to link, the front line gets cut off from midfield.",
            [nm(s) for s in poachers])

    f9 = [s for s in sts if role(s) == "False 9"]
    others = ws + ams + [s for s in sts if s not in f9]
    if f9 and not any(role(s) in RUN for s in others):
        add("clash", "high", "False 9 with no runners beyond",
            f"{nm(f9[0])} drops in to overload midfield, but no forward attacks the space "
            "he vacates — the movement cancels itself out and there's no threat in behind.",
            [nm(f9[0])])

    if poachers and not creators and not widemen:
        add("clash", "med", "Poacher starved of supply",
            f"{nm(poachers[0])} lives on the last line, but no creator or wide outlet feeds "
            "the box — a pure finisher with no service goes quiet.", [nm(poachers[0])])

    tms = [s for s in sts if role(s) == "Target Man"]
    if tms and not widemen and t.get("width", 50) < 46:
        add("clash", "med", "Target man with no width",
            f"{nm(tms[0])} wants crosses to attack, but no wide player holds the touchline "
            "and your width is set narrow — his aerial threat is wasted.", [nm(tms[0])])

    inv_fb = [s for s in fbs if role(s) == "Inverted Fullback"]
    if len(inv_fb) >= 2:
        add("clash", "med", "Both fullbacks invert — no natural width",
            "Two inverted fullbacks tuck into midfield, so unless the wingers stay wide the "
            "team has nobody stretching the pitch and attacks turn predictable and central.",
            [nm(s) for s in inv_fb])

    for w in ws:                                            # winger + FB both cut inside
        if role(w) not in ("Inverted Winger", "Inside Forward") or _side(w["x"]) == "C":
            continue
        same = [f for f in inv_fb if _side(f["x"]) == _side(w["x"])]
        if same:
            sd = "Left" if _side(w["x"]) == "L" else "Right"
            add("clash", "high", f"{sd} flank vacated",
                f"{nm(w)} cuts inside and {nm(same[0])} tucks in behind him, so nobody holds "
                f"the {sd.lower()} touchline — the whole side gets congested and easy to defend.",
                [nm(w), nm(same[0])])

    awb = [s for s in fbs if role(s) == "Attacking Wing-Back"]
    holding = [s for s in dms if role(s) in ("Anchor", "Ball-Winner")]
    if len(awb) >= 2 and not holding:
        add("clash", "med", "Both wing-backs bomb on, no screen",
            "Two attacking wing-backs push high while no anchoring midfielder protects the "
            "space they leave — you're open to a fast transition through the middle.",
            [nm(s) for s in awb])

    narrow_wm = [s for s in wms if role(s) in NARROW]
    if len(narrow_wm) >= 2 and not [f for f in fbs if role(f) == "Attacking Wing-Back"]:
        add("clash", "high", "Both wide midfielders come inside — nobody holds the width",
            "Neither flank midfielder stays on the touchline and no fullback overlaps past "
            "them, so the whole side plays in a narrow column that a compact block can "
            "simply squeeze.", [nm(s) for s in narrow_wm])

    ten = [s for s in ams if role(s) in ("Enganche", "Trequartista")]
    if ten and t.get("press", 50) > 66:
        add("clash", "med", "A free 10 in a high press",
            f"{nm(ten[0])} is given a free role with no pressing duty while the team presses "
            "aggressively — the press goes a man short and gets played through his side.",
            [nm(ten[0])])

    deep_pm = [s for s in (dms + cms + ams + wms) if role(s) in
               ("Deep-Lying Playmaker", "Playmaker", "Advanced Playmaker",
                "Trequartista", "Enganche", "Wide Playmaker")]
    if len(deep_pm) >= 3:
        add("clash", "low", "Too many playmakers, one ball",
            "Three ball-dominant playmakers occupy similar central zones and end up "
            "competing for the same touches instead of stretching the opposition.",
            [nm(s) for s in deep_pm])

    # ---------------- synergies ----------------
    for w in ws:                                            # overlap: IF cuts in, AWB overlaps
        if role(w) not in ("Inside Forward", "Inverted Winger") or _side(w["x"]) == "C":
            continue
        mate = [f for f in awb if _side(f["x"]) == _side(w["x"])]
        if mate:
            add("synergy", "high", "Overlap down the flank",
                f"{nm(w)} cuts inside while {nm(mate[0])} overlaps outside — the classic "
                "give-the-defender-two-problems combination.", [nm(w), nm(mate[0])])

    for w in ws:                                            # width + control on one side
        if role(w) != "Winger (Wide)" or _side(w["x"]) == "C":
            continue
        mate = [f for f in inv_fb if _side(f["x"]) == _side(w["x"])]
        if mate:
            add("synergy", "med", "Balanced flank: winger wide, fullback inside",
                f"{nm(mate[0])} steps into midfield to control the ball while {nm(w)} holds "
                "the touchline — width and control on the same side.", [nm(w), nm(mate[0])])

    for w in wms:                                           # WM tucks in, fullback goes past
        if role(w) not in ("Half-Space Midfielder", "Wide Playmaker", "Raumdeuter") \
                or _side(w["x"]) == "C":
            continue
        mate = [f for f in awb if _side(f["x"]) == _side(w["x"])]
        if mate:
            add("synergy", "high", "Inside-out flank: midfielder tucks, fullback overlaps",
                f"{nm(w)} comes into the half-space and {nm(mate[0])} takes the touchline "
                "outside him — the flank still gets stretched and you gain a body in midfield.",
                [nm(w), nm(mate[0])])

    tw = [s for s in wms if role(s) == "Touchline Winger"]
    dw = [s for s in wms if role(s) == "Defensive Winger"]
    if tw and dw:
        add("synergy", "med", "Lopsided flanks: width one side, cover the other",
            f"{nm(tw[0])} holds the touchline to attack while {nm(dw[0])} doubles up on the "
            "opposite flank — a deliberately unbalanced shape that stretches one side and "
            "locks the other.", [nm(tw[0]), nm(dw[0])])

    if tms and widemen:
        add("synergy", "high", "Target man with real service",
            f"{nm(tms[0])} attacks crosses and {nm(widemen[0])} delivers them — finisher and "
            "supply line fit together.", [nm(tms[0]), nm(widemen[0])])

    if poachers and creators:
        add("synergy", "med", "Poacher with a supplier",
            f"{nm(creators[0])} creates and {nm(poachers[0])} finishes — a natural "
            "creator-to-poacher link.", [nm(poachers[0]), nm(creators[0])])

    if f9:
        runners = [s for s in (ws + ams) if role(s) in RUN]
        if runners:
            add("synergy", "high", "False 9 with runners beyond",
                f"{nm(f9[0])} drops to overload midfield and {nm(runners[0])} attacks the "
                "space it opens — drop-and-run in tandem.", [nm(f9[0]), nm(runners[0])])

    dlp = [s for s in dms if role(s) == "Deep-Lying Playmaker"]
    bw = [s for s in dms if role(s) == "Ball-Winner"]
    if dlp and bw:
        add("synergy", "med", "Creator + destroyer in midfield",
            f"{nm(bw[0])} wins it back and {nm(dlp[0])} dictates once you have it — the "
            "complementary double pivot.", [nm(dlp[0]), nm(bw[0])])

    bpcb = [s for s in cbs if role(s) == "Ball-Playing"]
    if gk and role(gk) == "Sweeper Keeper" and len(bpcb) >= 2:
        add("synergy", "low", "Coherent build-out from the back",
            "A sweeper-keeper behind ball-playing centre-backs gives a settled numerical "
            "base to play out under pressure.", [nm(gk), nm(bpcb[0])])

    b2b = [s for s in cms if role(s) == "Box-to-Box"]
    orch = [s for s in (cms + dms) if role(s) in ("Playmaker", "Deep-Lying Playmaker")]
    if b2b and orch:
        add("synergy", "low", "Runner alongside an orchestrator",
            f"{nm(orch[0])} sets the tempo while {nm(b2b[0])} provides the box-to-box legs "
            "around him — a balanced central pairing.", [nm(b2b[0]), nm(orch[0])])

    score = round(_clamp_f(
        _CHEM_BASE + sum(_CHEM_W.get((l["kind"], l["sev"]), 0) for l in links), 35, 99))
    order = {"high": 0, "med": 1, "low": 2}
    links.sort(key=lambda l: (0 if l["kind"] == "clash" else 1, order.get(l["sev"], 3)))
    return {"score": score, "label": _chem_label(score), "links": links}


# -------------------------------------------------------- shape + network ---- #
def _positions(xi, t):
    """Average positions after tactics/roles reshape the base formation — the spec's
    'roles change the movement model'. Returns dots with an involvement (touch) proxy."""
    def d(k):
        return (t.get(k, 50) - 50) / 50.0
    out = []
    for s in xi:
        p = s.get("player")
        x, y, fam = s["x"], s["y"], s["family"]
        r = ROLES.get(fam, {}).get(s.get("role")) or _role()
        if s["line"] in ("DEF", "MID"):
            y += 8 * d("line_height")                    # line pushes the block up/down
        if s["line"] == "ATT":
            y += 5 * d("directness")
        if fam in ("FB", "W", "WM"):
            x += (1 if x > 50 else -1) * 8 * d("width")  # width spreads the flanks
        if fam == "FB":
            x += -(1 if x > 50 else -1) * 22 * max(0, r["mid"])   # inverted FB tucks in
            y += 10 * max(0, r["att"])                   # wing-back pushes up
        if fam == "WM":                                  # narrow roles come off the touchline
            x += -(1 if x > 50 else -1) * 26 * max(0.0, -r["flank"])
            x += (1 if x > 50 else -1) * 6 * max(0.0, r["flank"])
        if fam == "ST":
            y += -13 * max(0, r["mid"])                  # False 9 drops
        if fam in ("W", "AM", "CM", "WM"):
            y += 5 * r["att"]
        x, y = _clamp_f(x, 4, 96), _clamp_f(y, 3, 94)
        at = player_attrs(p, fam) if p else None
        inv = 28 if fam == "GK" else (round(0.4 * at["passing"] + 0.35 * at["progression"]
                                            + 0.25 * at["creativity"]) if at else 45)
        out.append({"id": s["id"], "x": round(x, 1), "y": round(y, 1), "fam": fam,
                    "involvement": inv, "name": (p["player"] if p else s["id"]),
                    "num": (p["player"].split()[-1][:3] if p else s["id"])})
    return out


def _network(xi, positions):
    """Weighted passing links: closer + better passers + role affinity = thicker line.
    Keeps the strongest ~16 so the graph reads cleanly."""
    slots = {s["id"]: s for s in xi}
    pos = {p["id"]: p for p in positions}
    edges = []
    ids = list(pos.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = pos[ids[i]], pos[ids[j]]
            sa, sb = slots[ids[i]], slots[ids[j]]
            pa, pb = sa.get("player"), sb.get("player")
            if not pa or not pb:
                continue
            dist = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if dist > 40:
                continue
            aa, ab = player_attrs(pa, sa["family"]), player_attrs(pb, sb["family"])
            fams = {sa["family"], sb["family"]}
            bonus = 1.0
            if fams == {"CB", "DM"} or fams == {"CB", "FB"} or fams == {"DM", "CM"}:
                bonus = 1.2
            if "DM" in fams or "AM" in fams:
                bonus *= 1.1
            w = max(0, 1 - dist / 40) * ((aa["passing"] + ab["passing"]) / 200) * bonus
            edges.append({"from": ids[i], "to": ids[j], "w": round(w, 3)})
    edges.sort(key=lambda e: -e["w"])
    return edges[:16]


def _viz(xi, t, m):
    pos = _positions(xi, t)
    return {"positions": pos, "network": _network(xi, pos), "territory": m["territory"],
            "possession": m["possession"]}


# --------------------------------------------------- season / cup projection - #
# Club -> (league, games, teams). A team not listed is treated as a national side.
LEAGUE_INFO = {
    "Real Madrid": ("La Liga", 38, 20), "Barcelona": ("La Liga", 38, 20),
    "Atlético Madrid": ("La Liga", 38, 20),
    "Manchester City": ("Premier League", 38, 20), "Arsenal": ("Premier League", 38, 20),
    "Liverpool": ("Premier League", 38, 20), "Manchester United": ("Premier League", 38, 20),
    "Chelsea": ("Premier League", 38, 20), "Tottenham Hotspur": ("Premier League", 38, 20),
    "Newcastle United": ("Premier League", 38, 20), "Aston Villa": ("Premier League", 38, 20),
    "Bournemouth": ("Premier League", 38, 20),
    "Bayern München": ("Bundesliga", 34, 18), "Bayer Leverkusen": ("Bundesliga", 34, 18),
    "Borussia Dortmund": ("Bundesliga", 34, 18),
    "Internazionale": ("Serie A", 38, 20), "Napoli": ("Serie A", 38, 20), "Milan": ("Serie A", 38, 20),
    "Juventus": ("Serie A", 38, 20), "PSG": ("Ligue 1", 34, 18),
}
# projected points → finishing position (top-5-league distribution)
_POS_CURVE = [(88, 1), (82, 2), (75, 3), (69, 4), (64, 5), (59, 6), (55, 7), (51, 8),
              (47, 9), (43, 11), (39, 13), (35, 15), (32, 17), (28, 18)]


def _xpts(xg, xga):
    """Expected points per game vs an average opponent, over the same over-dispersed goal
    model the matches are simulated from (see _xg_draw) rather than a Poisson on a fixed
    mean — which flattered the strong, because a fixed mean never has an off day."""
    pw = pd = 0.0
    for i in range(9):
        for j in range(9):
            p = _goal_pmf(i, xg, _XG_SHAPE) * _goal_pmf(j, xga, _XG_SHAPE)
            if i > j:
                pw += p
            elif i == j:
                pd += p
    return pw * 3 + pd


def _pos_for(pts, n):
    for thr, pos in _POS_CURVE:
        if pts >= thr:
            return min(pos, n)
    return n


def _run(S, stages, div=5.5):
    """Cumulative advancement through a knockout bracket. stages: [(label, opp_strength)] —
    winning at stage i (vs opp_strength) advances to stage i+1; the last label = trophy.
    Opponent strength rises each round to mimic facing better sides deeper in."""
    labels = [s[0] for s in stages]
    reach, pc = [100.0], 1.0
    for i in range(len(stages) - 1):
        pc *= 1 / (1 + math.exp(-(S - stages[i][1]) / div))
        reach.append(pc * 100)
    out = [{"stage": labels[i], "reach": round(reach[i])} for i in range(len(labels))]
    exits = [reach[i] - (reach[i + 1] if i + 1 < len(reach) else 0) for i in range(len(reach))]
    return out, labels[exits.index(max(exits))], round(reach[-1])


def _project(u, t, team, chem_mult=1.0, ctx=None):
    """League points/position + a Champions League run for clubs; a WC/Euro run for nations.
    Strength = squad quality (avg XI rating) nudged by how effective the chosen tactics are
    (the model's xG/xGA → expected points vs an average opponent) and by playstyle chemistry
    (chem_mult). div=7 gives knockout football realistic variance, so even the best side is
    only ~20% to win a trophy.

    `ctx` (optional, supplied by the server from the real league table) carries how hard that
    club's league actually is — see _league_ctx. Without it the old generic curve is used."""
    m = _metrics(u, t, _BASE_OPP, DEFAULT_TACTICS)         # season-representative form
    xpts = _xpts(round(m["xg"] * chem_mult, 2), m["xga"])
    # FIFA overall scale is narrow (~74-88 for these sides) but reflects big quality gaps,
    # so amplify it into the strength range, then nudge by tactic effectiveness.
    S = _clamp_f(round((u.get("avg_rating", 74) - 74) * 2.5 + 55 + (xpts - 1.4) * 4), 1, 90)
    # The same side takes more points in a weaker league, because most of the fixture list
    # is easier — Bayern really did take 2.62 a game last season and PSG 2.24, rates no side
    # sustains in the Premier League. `difficulty` is the league's UEFA country coefficient
    # normalised to England = 1.00, so this is 0 for the hardest league and ~+10% for Ligue 1.
    league_mult = 1 + 0.25 * (1 - (ctx or {}).get("difficulty", 1.0))
    # the base cap still holds a title season to ~91 points; the league multiplier lifts it
    # only as far as a dominant side in a weak league really goes (Bayern's 89 from 34)
    ppg = _clamp_f(_clamp_f(0.5 + (S - 50) * 0.052, 0.4, 2.4) * league_mult, 0.4, 2.62)
    if team in LEAGUE_INFO or ctx:
        c = ctx or {}
        lg, games, n = LEAGUE_INFO.get(team, (c.get("league") or "League",
                                              c.get("games", 38), c.get("n", 20)))
        pts = round(ppg * games)
        run, likely, win = _run(S, [("League phase", 58), ("Round of 16", 71),
                                     ("Quarter-final", 77), ("Semi-final", 82),
                                     ("Final", 85), ("Champions", 0)], div=7)
        # Finishing position against the REAL league: every rival's current pace carried to
        # a full season. A generic curve can't do this — it was built on 38 games, so 82
        # points read as second place even in a 34-game league where it wins the title by
        # nine, and it knew nothing about who you actually have to beat.
        rivals = (ctx or {}).get("rivals")
        pos = (1 + sum(1 for r in rivals if r > pts)) if rivals else _pos_for(pts, n)
        return {"kind": "club", "league": lg, "games": games, "n_teams": n,
                "points": pts, "position": min(pos, n), "ppg": round(ppg, 2),
                "title_bar": (round(rivals[0]) if rivals else None),
                "comp": "Champions League", "run": run, "likely": likely, "win_pct": win}
    run, likely, win = _run(S, [("Group stage", 54), ("Round of 16", 66), ("Quarter-final", 74),
                                ("Semi-final", 80), ("Final", 85), ("Winners", 0)], div=7)
    return {"kind": "national", "comp": "World Cup / Euro", "strength": round(S),
            "run": run, "likely": likely, "win_pct": win}


# ------------------------------------------------------------------ main ----- #
def simulate(xi, tactics, opponent=None, team=None, form_home=None, form_away=None,
             league_ctx=None) -> dict:
    """xi: list of slots each {family,line,role,player}. tactics: slider dict.
    opponent (optional): {'units': {...}, 'tactics': {...}, 'name': str}.
    form_home/form_away (optional): signed recent-form ratings (~-1..+1, see
    SoccerDB.team_form_rating) that nudge the matchup toward the in-form side.
    league_ctx (optional): the club's real league context (difficulty + rivals' full-season
    pace) so the projection knows how hard that particular title actually is."""
    t = {**DEFAULT_TACTICS, **(tactics or {})}
    u = _units(xi)
    if opponent and opponent.get("units"):
        ou, ot = opponent["units"], {**DEFAULT_TACTICS, **(opponent.get("tactics") or {})}
    else:
        ou, ot = _BASE_OPP, DEFAULT_TACTICS
    m = _metrics(u, t, ou, ot)
    # playstyle chemistry: a cohesive set of roles finishes chances a little better, a
    # disjointed one a little worse. Gentle (±10% on xG) so it colours the setup without
    # overriding squad quality — and every point of it is itemised in res["chemistry"].
    chem = _chemistry(xi, t)
    chem_mult = _clamp_f(1 + 0.006 * (chem["score"] - _CHEM_BASE), 0.90, 1.10)
    m["xg"] = round(m["xg"] * chem_mult, 2)
    res = {"units": {k: round(v) for k, v in u.items()}, "metrics": m, "chemistry": chem,
           "weaknesses": _weaknesses(xi, u, t, m), "style": _style_match(t, m),
           "viz": _viz(xi, t, m)}
    if team:
        res["projection"] = _project(u, t, team, chem_mult, league_ctx)
    if opponent and opponent.get("units"):
        om = _metrics(ou, ot, u, t)
        # recent-form nudge: tilt expected goals toward whichever side has the stronger
        # recent league + UCL results. A gentle multiplier so form colours the matchup
        # without overriding the squads' quality gap.
        fh, fa = form_home or 0.0, form_away or 0.0
        if fh or fa:
            diff = fh - fa
            k = 0.16
            m["xg"] = round(m["xg"] * _clamp_f(1 + k * diff, 0.85, 1.15), 2)
            om["xg"] = round(om["xg"] * _clamp_f(1 - k * diff, 0.85, 1.15), 2)
            res["form_adj"] = {"home": round(fh, 2), "away": round(fa, 2), "diff": round(diff, 2)}
        res["opponent_metrics"] = om
        res["win_probs"] = _win_probs(m["xg"], om["xg"], _XG_SHAPE)
        res["battles"] = _battles(u, t, ou, ot)
    return res


def team_units(xi, tactics) -> dict:
    """Just the unit vector — used to precompute an opponent for head-to-head."""
    return _units(xi)


# ------------------------------------------------- single-match simulation --- #
# The matchup card gives the ODDS — the whole distribution of results. This plays ONE
# match out of exactly that distribution: the engine decides WHO scored, who assisted and
# when, weighted by the same attributes and roles that produced the xG. Re-simulating
# redraws, so a scoreline is a single sample and the odds are what you'd see over many.
#
# The projection is the side's EXPECTED xG, not the xG of any particular afternoon. Real
# match xG is heavily over-dispersed around it: the fixture that projects 2.0 produces 0.9
# one week and 3.4 the next, because chances arrive in clusters and the game state (an
# early goal, a red card, a side chasing it from the 60th minute) reshapes everything after
# it. So each match draws its own xG from a Gamma around the projection, and the goals come
# from a Poisson given THAT xG. Compounded, that is the standard Gamma-Poisson (negative
# binomial) goal model — it produces the goalless draws and the 4-3s a bare Poisson on a
# fixed mean never gets to. `_win_probs` integrates over the same Gamma, so the odds on the
# card remain exactly the distribution the scorelines are drawn from.
_XG_SHAPE = 5.0          # variance = mean^2 / shape, i.e. sd ~= 0.45 x the projection


def _xg_draw(rng, mean):
    """One match's xG, drawn around the model's expectation for it."""
    m = max(0.05, mean)
    return round(_clamp_f(rng.gammavariate(_XG_SHAPE, m / _XG_SHAPE), 0.05, 6.0), 2)
_GOAL_BASE = {"ST": 0.86, "W": 0.62, "AM": 0.50, "WM": 0.36, "CM": 0.24, "DM": 0.10,
              "FB": 0.09, "CB": 0.12}
_ASSIST_BASE = {"ST": 0.45, "W": 0.95, "AM": 1.00, "WM": 0.88, "CM": 0.66, "DM": 0.32,
                "FB": 0.58, "CB": 0.10}
_CARD_BASE = {"CB": 1.00, "DM": 1.00, "FB": 0.85, "CM": 0.80, "WM": 0.70, "ST": 0.50,
              "W": 0.45, "AM": 0.45, "GK": 0.12}
# Real goals-by-15-minute-block shape: more goals late, as legs go and games open up.
_MIN_BLOCKS = [(1, 15, 0.84), (16, 30, 0.95), (31, 45, 1.02),
               (46, 60, 1.08), (61, 75, 1.15), (76, 90, 1.46)]


def _pois_draw(rng, lam):
    """Sample a goal count from Poisson(lam) — Knuth's method, capped so a freak run
    of luck can't produce a 15-0."""
    L, k, p = math.exp(-max(lam, 0.01)), 0, 1.0
    while p > L and k < 12:
        k += 1
        p *= rng.random()
    return k - 1


def _minute(rng, used, et=False):
    """A match minute for an event, shaped like real goal timing, avoiding collisions.
    Returns (sort_minute, display_label) — a 90th-minute event may land in stoppage.
    et=True draws from the extra-time half-hour instead (91-120)."""
    if et:
        for _ in range(10):
            m = rng.randint(91, 120)
            if m not in used:
                break
        used.add(m)
        return float(m), f"{m}'"
    tot = sum(b[2] for b in _MIN_BLOCKS)
    m = 45
    for _ in range(10):
        x = rng.random() * tot
        for lo, hi, w in _MIN_BLOCKS:
            x -= w
            if x <= 0:
                m = rng.randint(lo, hi)
                break
        if m not in used:
            break
    used.add(m)
    if m == 90 and rng.random() < 0.55:
        add = rng.randint(1, 5)
        return 90 + add / 10.0, f"90+{add}'"
    return float(m), f"{m}'"


def _pick_w(rng, pairs, exclude=None):
    """Weighted random choice over [(slot, weight)], optionally excluding one slot."""
    cand = [(s, w) for s, w in pairs if s is not exclude and w > 0]
    tot = sum(w for _, w in cand)
    if not cand or tot <= 0:
        return None
    x = rng.random() * tot
    for s, w in cand:
        x -= w
        if x <= 0:
            return s
    return cand[-1][0]


def _goal_weights(xi):
    """How likely each starter is to be the one who finishes — position, finishing
    ability and how far forward the role pushes him."""
    out = []
    for s in xi:
        p, fam = s.get("player"), s.get("family")
        if not p or fam == "GK":
            continue
        at = player_attrs(p, fam)
        r = ROLES.get(fam, {}).get(s.get("role")) or _role()
        # Finishing still decides who scores, but gently: a steep exponent funnelled a
        # campaign's goals into the single best finisher (an elite striker was taking 55% of
        # them and reaching 25 in a run). Real front lines share the load — the centre
        # forward leads it, the wide men and the 10 get a real cut.
        w = _GOAL_BASE.get(fam, 0.2) * (max(30, at["shooting"]) / 74.0) ** 1.2 * (1 + 1.5 * r["att"])
        if fam in ("CB", "FB"):                      # defenders score from set pieces
            w *= 0.55 + at["aerial"] / 120.0
        out.append((s, max(0.01, w)))
    return out


def _assist_weights(xi):
    """How likely each starter is to lay the goal on — creativity, plus roles that get
    on the ball (playmakers) or get to the byline (wide, overlapping)."""
    out = []
    for s in xi:
        p, fam = s.get("player"), s.get("family")
        if not p or fam == "GK":
            continue
        at = player_attrs(p, fam)
        r = ROLES.get(fam, {}).get(s.get("role")) or _role()
        w = (_ASSIST_BASE.get(fam, 0.3) * (max(30, at["creativity"]) / 74.0) ** 1.6
             * (1 + 1.2 * r["mid"] + 0.6 * r["flank"]))
        out.append((s, max(0.01, w)))
    return out


def _nm(s):
    return s["player"]["player"] if s and s.get("player") else ""


def _photo(s):
    return (s["player"].get("photo") if s and s.get("player") else None)


def _goal_events(rng, xi, n, side, used, et=False):
    """Play out n goals for one side: who, when, and how (open play / header / penalty)."""
    gw, aw = _goal_weights(xi), _assist_weights(xi)
    pen_taker = max(gw, key=lambda z: player_attrs(z[0]["player"], z[0]["family"])["shooting"],
                    default=(None, 0))[0] if gw else None
    evs = []
    for _ in range(n):
        pen = rng.random() < 0.07 and pen_taker is not None
        sc = pen_taker if pen else _pick_w(rng, gw)
        if sc is None:
            continue
        at = player_attrs(sc["player"], sc["family"])
        header = (not pen) and at["aerial"] > 72 and rng.random() < 0.24
        asst = None
        if not pen and rng.random() < (0.86 if header else 0.70):
            asst = _pick_w(rng, aw, exclude=sc)
        mn, lbl = _minute(rng, used, et=et)
        evs.append({"minute": mn, "label": lbl, "side": side, "type": "goal",
                    "player": _nm(sc), "photo": _photo(sc),
                    "assist": _nm(asst) if asst else None,
                    "how": "penalty" if pen else ("header" if header else "open play")})
    return evs


def _card_events(rng, xi, t, side, used):
    """Bookings — an aggressive press and a compact, physical block earn more of them."""
    roles_press = sum((ROLES.get(s.get("family"), {}).get(s.get("role")) or _role())["press"]
                      for s in xi if s.get("player"))
    lam = _clamp_f(1.1 + (t.get("press", 50) - 50) / 45.0
                   + (t.get("compactness", 50) - 50) / 80.0
                   + roles_press * 0.7, 0.3, 3.6)
    n = _pois_draw(rng, lam)
    pairs = [(s, _CARD_BASE.get(s.get("family"), 0.5)) for s in xi if s.get("player")]
    evs, seen, red_done = [], set(), False
    for _ in range(n):
        s = _pick_w(rng, [(sl, w) for sl, w in pairs if id(sl) not in seen])
        if s is None:
            continue
        seen.add(id(s))                                  # one booking per player
        red = (not red_done) and rng.random() < 0.035
        red_done = red_done or red
        mn, lbl = _minute(rng, used)
        evs.append({"minute": mn, "label": lbl, "side": side,
                    "type": "red" if red else "yellow", "player": _nm(s), "photo": _photo(s)})
    return evs


def _chance_events(rng, xi, opp_xi, xg, goals, side, used):
    """The near-misses the scoreline hides: chances burned when a side under-performs its
    xG, and the keeper's night when it over-performs against him."""
    evs = []
    if xg - goals >= 0.75:
        s = _pick_w(rng, _goal_weights(xi))
        if s:
            mn, lbl = _minute(rng, used)
            evs.append({"minute": mn, "label": lbl, "side": side, "type": "miss",
                        "player": _nm(s), "photo": _photo(s),
                        "how": rng.choice(["big chance spurned", "hit the post",
                                           "denied one-on-one"])})
    if xg >= 1.3 and goals <= 1:
        gk = next((s for s in opp_xi if s.get("family") == "GK" and s.get("player")), None)
        if gk and rng.random() < 0.7:
            mn, lbl = _minute(rng, used)
            evs.append({"minute": mn, "label": lbl,
                        "side": "away" if side == "home" else "home", "type": "save",
                        "player": _nm(gk), "photo": _photo(gk), "how": "big save"})
    return evs


def _motm(xi_h, xi_a, evs, gh, ga):
    """Man of the match: goals and assists first, then squad quality, with a nod to the
    winning side and to a keeper who kept a clean sheet."""
    best, best_sc = None, -1e9
    for xi, side, gf, gagt in ((xi_h, "home", gh, ga), (xi_a, "away", ga, gh)):
        for s in xi:
            if not s.get("player"):
                continue
            nm = _nm(s)
            g = sum(1 for e in evs if e["type"] == "goal" and e["player"] == nm)
            a = sum(1 for e in evs if e["type"] == "goal" and e.get("assist") == nm)
            sc = g * 3.2 + a * 1.7 + (s["player"].get("rating") or 70) / 45.0
            sc += 0.7 if gf > gagt else (0.0 if gf == gagt else -0.5)
            if s["family"] == "GK":
                sc += 1.5 if gagt == 0 else -0.4
            if any(e["type"] == "red" and e["player"] == nm for e in evs):
                sc -= 4.0
            if sc > best_sc:
                best, best_sc = {"player": nm, "side": side, "photo": _photo(s),
                                 "goals": g, "assists": a,
                                 "rating": s["player"].get("rating")}, sc
    return best


def _match_story(home, away, gh, ga, xgh, xga, evs):
    """One line of context: what the scoreline says, and whether it flattered anyone."""
    hn, an = home.split(" ")[0], away.split(" ")[0]
    if gh > ga:
        w, l, gw, gl, xw, xl = hn, an, gh, ga, xgh, xga
    elif ga > gh:
        w, l, gw, gl, xw, xl = an, hn, ga, gh, xga, xgh
    else:
        base = f"{hn} and {an} share the points, {gh}-{ga}."
        if gh == 0:
            return base + " Neither side could find a way through."
        return base + (" Both sides took their chances." if gh >= 2 else " A cagey, tight afternoon.")
    line = f"{w} win it {gw}-{gl}."
    if gw - xw >= 0.9:
        line += f" A clinical night — {w} scored {gw} from {xw:.2f} xG."
    elif xl - gl >= 0.9:
        line += f" {l} will feel robbed, creating {xl:.2f} xG and taking {gl}."
    elif gw - gl >= 3:
        line += " A comprehensive, one-sided performance."
    elif abs(xw - xl) < 0.35:
        line += " Fine margins — there was almost nothing between the two."
    if any(e["type"] == "red" for e in evs):
        line += " The red card was the turning point."
    return line


def simulate_match(xi, tactics, opp_xi, opponent, team=None, opp_name="Opponent",
                   form_home=None, form_away=None, seed=None) -> dict:
    """Play a single match between the user's XI and the opponent's, drawn from the same
    model the odds come from. Same arguments as simulate(), plus the opponent's rebuilt XI
    (needed to pick their scorers) and a seed — pass a new seed to re-simulate."""
    base = simulate(xi, tactics, opponent=opponent, team=None,
                    form_home=form_home, form_away=form_away)
    if not base.get("win_probs"):
        return {"available": False}
    rng = random.Random(seed)
    # this match's xG, not the season-long expectation behind the odds
    xgh = _xg_draw(rng, base["metrics"]["xg"])
    xga = _xg_draw(rng, base["opponent_metrics"]["xg"])
    gh, ga = _pois_draw(rng, xgh), _pois_draw(rng, xga)
    t = {**DEFAULT_TACTICS, **(tactics or {})}
    ot = {**DEFAULT_TACTICS, **((opponent or {}).get("tactics") or {})}
    used: set = set()
    evs = _goal_events(rng, xi, gh, "home", used) + _goal_events(rng, opp_xi, ga, "away", used)
    evs += _card_events(rng, xi, t, "home", used) + _card_events(rng, opp_xi, ot, "away", used)
    evs += _chance_events(rng, xi, opp_xi, xgh, gh, "home", used)
    evs += _chance_events(rng, opp_xi, xi, xga, ga, "away", used)
    evs.sort(key=lambda e: e["minute"])

    def shots(xg, g):
        sh = max(g, round(xg * 8.4 * (0.82 + 0.36 * rng.random())))
        return sh, max(g, min(sh, round(sh * (0.30 + 0.14 * rng.random()))))
    sh_h, sot_h = shots(xgh, gh)
    sh_a, sot_a = shots(xga, ga)
    poss = base["metrics"]["possession"]
    corner = lambda sh: max(0, int(round(sh * 0.45 + rng.gauss(0, 1.2))))  # noqa: E731
    stats = [
        {"label": "Possession %", "home": poss, "away": 100 - poss, "dp": 0},
        {"label": "Shots", "home": sh_h, "away": sh_a, "dp": 0},
        {"label": "On target", "home": sot_h, "away": sot_a, "dp": 0},
        {"label": "xG", "home": xgh, "away": xga, "dp": 2},
        {"label": "Corners", "home": corner(sh_h), "away": corner(sh_a), "dp": 0},
    ]
    home_name = team or "Your side"
    res = "W" if gh > ga else ("D" if gh == ga else "L")
    return {
        "available": True, "home": home_name, "away": opp_name,
        "score": {"home": gh, "away": ga}, "result": res,
        "xg": {"home": xgh, "away": xga},
        "odds": base["win_probs"], "xg_expected": {"home": base["metrics"]["xg"], "away": base["opponent_metrics"]["xg"]}, "events": evs, "stats": stats,
        "motm": _motm(xi, opp_xi, evs, gh, ga),
        "story": _match_story(home_name, opp_name, gh, ga, xgh, xga, evs),
        "seed": seed,
    }


# --------------------------------------------- Champions League campaign ----- #
# The projection card gives the ODDS of a European run; this plays the run OUT, match by
# match, from the same model. It is dropped into the REAL Champions League field: the
# actual 36-team league phase (the completed 2025/26 table, with the real clubs, their
# real points and the real qualification bands), so the campaign is anchored to reality
# rather than to invented opposition:
#   • league phase — 8 matches, two drawn from each seeding pot, 4 home and 4 away
#   • the points those 8 games earn REPLACE the side's row in the real table, which is
#     then re-sorted → the finishing rank, and with it the real path:
#     top 8 straight to the last 16, 9th-24th into the knockout playoff, 25th+ out
#   • every knockout tie is two legs (the final is one, on neutral ground), level
#     aggregates go to extra time and then penalties — no away goals, as in the real thing
# Opponents are the real clubs with their real squads, so the scorers are real too, and
# each leg's goals come from the same Poisson (xG) the matchup odds are built on.
_UCL_HOME_XG = 1.10          # home sides in Europe score ~10% more...
_UCL_AWAY_XG = 0.93          # ...and their visitors ~7% less. A neutral final gets neither.
_UCL_ET_RATE = 0.30          # extra time: 30 cagier minutes, so well under a third of the 90
_UCL_GAP_K = 0.028           # squad-quality amplifier: a 15-point rating gulf ~1.5x the xG
_UCL_XG_SCALE = 0.86         # campaign scoring damper — see the note above _ucl_xg
# A final is a tighter game than the rounds that lead to it — one match, no second leg to
# fix it in, and both sides start by making sure they don't lose it. In the last eighteen
# the real ones averaged 2.72 goals against 2.99 in the quarters and last 16, and 3.09 in
# the semis; this is that ~10% gap, which our finals didn't have at all.
_UCL_FINAL_XG = 0.90
_UCL_TOP8 = 8                # rank 1-8  -> straight to the Round of 16
_UCL_PLAYOFF_LAST = 24       # rank 9-24 -> knockout playoff; 25th and below are out
# Our team names vs the names the European field is published under.
_UCL_NAME_ALIAS = {"psg": "paris saint germain", "internazionale": "inter",
                   "tottenham": "tottenham hotspur", "atletico madrid": "atletico madrid",
                   "bayern munich": "bayern munchen", "inter milan": "inter",
                   "sporting lisbon": "sporting cp"}


def _ucl_fold(s: str) -> str:
    """Fold a club name for matching: accents stripped, punctuation dropped, aliased."""
    import unicodedata
    f = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    f = " ".join(f.replace(".", " ").replace("-", " ").replace("/", " ").split())
    return _UCL_NAME_ALIAS.get(f, f)


def _ucl_same(a: str, b: str) -> bool:
    return _ucl_fold(a) == _ucl_fold(b)


def _ucl_pots(rows, exclude):
    """The field split into four seeding pots by league-phase rank — pot 1 the strongest
    nine, pot 4 the weakest. The user's own club never appears in a pot."""
    field = [r for r in rows if not _ucl_same(r["name"], exclude)]
    size = max(1, len(field) // 4)
    return [field[i * size:(i + 1) * size] if i < 3 else field[3 * size:] for i in range(4)]


def _ucl_fixtures(rng, rows, team, build):
    """The league-phase draw: two opponents out of each pot, one at home and one away —
    the real format's shape, so the eight games span the whole quality range of the field.
    A club whose squad can't be resolved is passed over and the next name out of the same
    pot takes its place, so the side always plays a full eight."""
    fx = []
    for pot in _ucl_pots(rows, team):
        cands = list(pot)
        rng.shuffle(cands)
        drawn = []
        for row in cands:
            if build(row):
                drawn.append(row)
            if len(drawn) == 2:
                break
        if len(drawn) < 2:
            continue
        home_first = rng.random() < 0.5
        fx.append((drawn[0], "H" if home_first else "A"))
        fx.append((drawn[1], "A" if home_first else "H"))
    rng.shuffle(fx)                                        # matchday order
    return fx


def _ucl_xg(A, B, venue):
    """The expected-goals pair for one match — the same metrics the matchup odds use,
    with squad-quality gap, playstyle chemistry, recent form and home advantage on top."""
    m = _metrics(A["units"], A["tactics"], B["units"], B["tactics"])
    om = _metrics(B["units"], B["tactics"], A["units"], A["tactics"])
    xg, oxg = m["xg"] * A.get("chem_mult", 1.0), om["xg"]
    # The matchup model is centred on top-5-league opposition, where two sides are rarely
    # far apart. The European field is not: it runs from Arsenal to Kairat Almaty, and the
    # FIFA overall scale compresses that range into a dozen points. So the campaign
    # amplifies the squad-quality gap — the same move the season projection makes — which
    # is what turns a 15-point rating gulf into the 4-0 those ties actually tend to be.
    gap = _clamp_f(A["units"].get("avg_rating", 74) - B["units"].get("avg_rating", 74), -15, 15)
    edge = math.exp(_UCL_GAP_K * gap)
    xg, oxg = xg * edge, oxg / edge
    diff = (A.get("form") or 0.0) - (B.get("form") or 0.0)
    if diff:
        xg *= _clamp_f(1 + 0.16 * diff, 0.85, 1.15)
        oxg *= _clamp_f(1 - 0.16 * diff, 0.85, 1.15)
    if venue == "H":
        xg, oxg = xg * _UCL_HOME_XG, oxg * _UCL_AWAY_XG
    elif venue == "A":
        xg, oxg = xg * _UCL_AWAY_XG, oxg * _UCL_HOME_XG
    # European nights are tighter than the domestic model expects: sides are better matched,
    # more of the games matter, and a campaign of them ran hot — an elite side was averaging
    # 2.7 goals a game and its striker 14 a campaign, when only about two players in the
    # whole competition reach 12 in a real season.
    xg, oxg = xg * _UCL_XG_SCALE, oxg * _UCL_XG_SCALE
    return round(_clamp_f(xg, 0.2, 3.6), 2), round(_clamp_f(oxg, 0.2, 3.6), 2), m["possession"]


def _ucl_match(rng, A, B, venue, rnd, label=None, damp=1.0):
    """One match of the campaign: goals drawn from that xG pair, then who scored, who
    laid it on and when — the same weighting the single-match simulator uses. Each match
    draws its OWN xG around the projection (see _xg_draw), so a campaign has its flat
    afternoons and its 4-1s rather than the same game replayed eight times."""
    exg, eoxg, poss = _ucl_xg(A, B, venue)
    xg, oxg = _xg_draw(rng, exg * damp), _xg_draw(rng, eoxg * damp)
    gf, ga = _pois_draw(rng, xg), _pois_draw(rng, oxg)
    used: set = set()
    evs = (_goal_events(rng, A["xi"], gf, "us", used)
           + _goal_events(rng, B["xi"], ga, "them", used))
    evs.sort(key=lambda e: e["minute"])
    return {"round": rnd, "label": label, "opponent": B["name"], "logo": B.get("logo"),
            "venue": venue, "score": {"us": gf, "them": ga},
            "xg": {"us": xg, "them": oxg}, "possession": poss,
            "result": "W" if gf > ga else ("D" if gf == ga else "L"), "goals": evs,
            "contrib": _match_contrib(rng, A["xi"], xg)}


def _ucl_extra_time(rng, A, B, venue, used, damp=1.0):
    """Thirty more minutes when a tie is level — played at a fraction of the 90's rate,
    because sides that reach extra time are usually the ones afraid to lose it."""
    exg, eoxg, _ = _ucl_xg(A, B, venue)
    gf = _pois_draw(rng, _xg_draw(rng, exg * _UCL_ET_RATE * damp))
    ga = _pois_draw(rng, _xg_draw(rng, eoxg * _UCL_ET_RATE * damp))
    evs = (_goal_events(rng, A["xi"], gf, "us", used, et=True)
           + _goal_events(rng, B["xi"], ga, "them", used, et=True))
    evs.sort(key=lambda e: e["minute"])
    return gf, ga, evs


def _pen_odds(xi):
    """Shootout conversion rate for a side, from its five best finishers' shooting."""
    sh = sorted((player_attrs(s["player"], s["family"])["shooting"]
                 for s in xi if s.get("player") and s.get("family") != "GK"), reverse=True)[:5]
    avg = sum(sh) / len(sh) if sh else 70
    return _clamp_f(0.62 + (avg - 74) / 220.0, 0.55, 0.88)


def _shootout(rng, xi_a, xi_b):
    """Penalties: five kicks each at the sides' conversion rates, then sudden death.
    Scored kick-by-kick rather than as a coin flip, so the better takers really do
    have an edge — a small one, as they do in reality."""
    pa, pb = _pen_odds(xi_a), _pen_odds(xi_b)
    a = b = 0
    for _ in range(5):
        a += rng.random() < pa
        b += rng.random() < pb
    while a == b:                                          # sudden death
        ka, kb = rng.random() < pa, rng.random() < pb
        a, b = a + ka, b + kb
        if ka != kb:
            break
    return int(a), int(b)


def _ucl_tie(rng, A, B, rnd, second_leg_home, one_off=False):
    """A knockout tie. Two legs, aggregate scores, no away goals (as UEFA has scored it
    since 2021); level after 180 minutes → extra time in the second leg → penalties.
    The final (one_off) is a single match on neutral ground."""
    damp = _UCL_FINAL_XG if one_off else 1.0
    if one_off:
        leg = _ucl_match(rng, A, B, "N", rnd, "Final", damp=damp)
        legs, gf, ga = [leg], leg["score"]["us"], leg["score"]["them"]
    else:
        v1 = "H" if not second_leg_home else "A"
        l1 = _ucl_match(rng, A, B, v1, rnd, "1st leg")
        l2 = _ucl_match(rng, A, B, "H" if second_leg_home else "A", rnd, "2nd leg")
        legs = [l1, l2]
        gf = l1["score"]["us"] + l2["score"]["us"]
        ga = l1["score"]["them"] + l2["score"]["them"]
    et = pens = None
    if gf == ga:
        last = legs[-1]
        used = {int(e["minute"]) for e in last["goals"]}
        efa, efb, evs = _ucl_extra_time(rng, A, B, "N" if one_off else
                                        ("H" if second_leg_home else "A"), used, damp=damp)
        last["goals"] += evs
        last["goals"].sort(key=lambda e: e["minute"])
        last["score"]["us"] += efa
        last["score"]["them"] += efb
        su, st = last["score"]["us"], last["score"]["them"]
        last["result"] = "W" if su > st else ("D" if su == st else "L")
        last["extra_time"] = True
        gf, ga, et = gf + efa, ga + efb, True
        if gf == ga:
            pa, pb = _shootout(rng, A["xi"], B["xi"])
            pens = {"us": pa, "them": pb}
    won = (pens["us"] > pens["them"]) if pens else (gf > ga)
    if pens:
        line = (f"{'Won' if won else 'Lost'} {pens['us']}-{pens['them']} on penalties "
                f"after {gf}-{ga} on aggregate")
    elif one_off:
        line = "after extra time" if et else ""
    else:
        line = f"{'Won' if won else 'Lost'} {gf}-{ga} on aggregate" + (" after extra time" if et else "")
    return {"round": rnd, "opponent": B["name"], "logo": B.get("logo"), "legs": legs,
            "agg": {"us": gf, "them": ga}, "extra_time": bool(et), "pens": pens,
            "won": bool(won), "line": line, "one_off": one_off}


def _ucl_pick(rng, rows, taken, lo, hi, sharpness, build):
    """Draw the next knockout opponent from the band of the field that would still be in
    it — weighted toward the higher seeds, sharply so in the later rounds, because that is
    who actually survives that deep. A club whose squad can't be resolved is dropped and
    the draw is made again."""
    for _ in range(6):
        band = [r for r in rows if lo <= r["rank"] <= hi
                and not any(_ucl_same(r["name"], t) for t in taken)]
        if not band:
            band = [r for r in rows if not any(_ucl_same(r["name"], t) for t in taken)]
        if not band:
            return None
        pairs = [(r, math.exp(-r["rank"] / sharpness)) for r in band]
        tot = sum(w for _, w in pairs)
        x, pick = rng.random() * tot, band[-1]
        for r, w in pairs:
            x -= w
            if x <= 0:
                pick = r
                break
        if build(pick):
            return pick
        taken.append(pick["name"])                         # unresolvable → out of the draw
    return None


# Per-90 creation and carrying rates before quality is applied. Calibrated against the real
# competition, whose season leaders land around 34-39 chances created, 8-13 gilt-edged ones
# and 40-56 completed dribbles across a 13-16 game run.
_CC_BASE = {"AM": 2.05, "W": 1.75, "WM": 1.55, "CM": 1.35, "FB": 1.15, "ST": 1.00,
            "DM": 0.95, "CB": 0.35, "GK": 0.05}
_DRB_BASE = {"W": 2.30, "AM": 1.75, "WM": 1.60, "ST": 1.25, "FB": 0.90, "CM": 0.85,
             "DM": 0.50, "CB": 0.22, "GK": 0.02}
# Of the chances a player creates, how many are gilt-edged. Kept deliberately low: at a
# quarter of them an elite squad had three men clearing eight big chances in a single
# campaign, when the real competition produces about four such players across all 36 clubs.
_BIG_SHARE = 0.13


def _match_contrib(rng, xi, xg):
    """One match's chances created, big chances and completed dribbles per starter.

    Goals and assists fall out of the scoreline, but the rest of a campaign's leaderboards
    don't — so they're drawn here from the same place everything else comes from: the
    player's own attributes and the job his role gives him, lifted or damped by how much
    the side is creating overall. A big chance is one of his own chances that was gilt-
    edged, so it can never exceed the chances he made."""
    tilt = _clamp_f(xg / 1.45, 0.55, 1.6)
    out = {}
    for s in xi:
        p, fam = s.get("player"), s.get("family")
        if not p:
            continue
        at = player_attrs(p, fam)
        r = ROLES.get(fam, {}).get(s.get("role")) or _role()
        cc_rate = (_CC_BASE.get(fam, 0.8) * (max(30, at["creativity"]) / 74.0) ** 1.7
                   * (1 + 0.5 * r["mid"] + 0.5 * r["flank"] + 0.3 * r["att"]) * tilt)
        drb_rate = (_DRB_BASE.get(fam, 0.6) * (max(30, at["dribbling"]) / 74.0) ** 2.2
                    * (1 + 0.4 * r["att"] + 0.4 * r["flank"]))
        cc = _pois_draw(rng, cc_rate)
        big = sum(1 for _ in range(cc) if rng.random() < _BIG_SHARE)
        drb = _pois_draw(rng, drb_rate)
        if cc or drb:
            out[_nm(s)] = [cc, big, drb]
    return out


def _ord(n):
    """1st, 2nd, 3rd, 4th … — finishing positions read badly without it."""
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _ucl_scorers(matches):
    """Campaign goals and assists for the user's own players, best first."""
    tally: dict = {}
    for mt in matches:
        for e in mt.get("goals", []):
            if e["side"] != "us":
                continue
            g = tally.setdefault(e["player"], {"player": e["player"], "photo": e.get("photo"),
                                               "goals": 0, "assists": 0})
            g["goals"] += 1
            g["photo"] = g["photo"] or e.get("photo")      # an assist-first entry has none yet
            if e.get("assist"):
                a = tally.setdefault(e["assist"], {"player": e["assist"], "photo": None,
                                                   "goals": 0, "assists": 0})
                a["assists"] += 1
    out = sorted(tally.values(), key=lambda x: (-x["goals"], -x["assists"], x["player"]))
    return out[:8]


def _ucl_leaders(matches, photos):
    """The campaign's leaderboards for the user's side: goals and assists fall out of the
    scorelines, chances created, big chances and dribbles out of _match_contrib. Same shape
    as the season simulation's stat leaders, so the UI renders them the same way."""
    agg: dict = {}

    def row(name):
        return agg.setdefault(name, {"player": name, "photo": photos.get(name),
                                     "goals": 0, "assists": 0, "cc": 0, "bcc": 0, "drb": 0})
    for mt in matches:
        for e in mt.get("goals", []):
            if e["side"] != "us":
                continue
            r = row(e["player"])
            r["goals"] += 1
            r["photo"] = r["photo"] or e.get("photo")
            if e.get("assist"):
                row(e["assist"])["assists"] += 1
        for name, (cc, bcc, drb) in (mt.get("contrib") or {}).items():
            r = row(name)
            r["cc"] += cc
            r["bcc"] += bcc
            r["drb"] += drb
    cats = [("goals", "👟 Goals"), ("assists", "🅰 Assists"), ("cc", "🎯 Chances created"),
            ("bcc", "💥 Big chances created"), ("drb", "🌀 Dribbles completed")]
    out = []
    for key, label in cats:
        top = sorted((r for r in agg.values() if r[key]),
                     key=lambda x: (-x[key], x["player"]))[:5]
        if top:
            out.append({"key": key, "label": label,
                        "top": [{"player": r["player"], "photo": r["photo"], "value": r[key]}
                                for r in top]})
    return out


def _ucl_story(team, outcome, rank, ties):
    """One line on how the campaign went."""
    short = team.split(" ")[0]
    if outcome["stage"] == "Champions":
        beaten = ties[-1]["opponent"] if ties else "the field"
        return f"{short} are champions of Europe — {beaten} beaten in the final."
    if outcome["stage"] == "League phase":
        return f"{short} finished {_ord(rank)} of 36 and went out in the league phase."
    last = ties[-1] if ties else None
    who = last["opponent"] if last else "the opposition"
    if outcome["stage"] == "Final":
        return f"{short} got all the way to the final and lost it to {who}."
    return (f"{short} went out in the {outcome['stage'].lower()} to {who}"
            + (f" — {last['line'].lower()}." if last and last["line"] else "."))


def simulate_ucl(xi, tactics, team, table, build_opp, form_home=None, seed=None,
                 team_logo=None) -> dict:
    """Play a full Champions League campaign for this XI.

    table:     the real league-phase table — [{'name','id','rank','pts','gd','logo'}], 36 rows.
    build_opp: (row) -> {'name','xi','units','tactics','form','logo'} — materialises one
               club's real squad. Called lazily, so only the clubs actually drawn are built.
    seed:      re-seed to replay the campaign differently; everything else is unchanged.
    team_logo: crest for the side, used when it isn't in the real field and has to be
               written into it.
    """
    if not table:
        return {"available": False, "error": "No Champions League field available."}
    rng = random.Random(seed)
    t = {**DEFAULT_TACTICS, **(tactics or {})}
    u = _units(xi)
    chem = _chemistry(xi, t)
    A = {"name": team, "xi": xi, "tactics": t, "units": u, "form": form_home or 0.0,
         "chem_mult": _clamp_f(1 + 0.006 * (chem["score"] - _CHEM_BASE), 0.90, 1.10)}
    rows = sorted(({**r} for r in table), key=lambda r: r["rank"])

    # Where the side sits in the real field. A club that did not qualify for it takes the
    # last-placed side's slot, so the campaign is still played against the real 36.
    mine = next((r for r in rows if _ucl_same(r["name"], team)), None)
    substituted = None
    if mine is None:
        substituted = rows[-1]["name"]
        mine = rows[-1]
        mine.update({"name": team, "logo": team_logo, "id": None})

    # ---- league phase: 8 matches, two from each pot, 4 home and 4 away ----
    built: dict = {}

    def opp(row):
        key = _ucl_fold(row["name"])
        if key not in built:
            o = build_opp(row) or {}
            o.setdefault("name", row["name"])
            o.setdefault("logo", row.get("logo"))
            o.setdefault("tactics", DEFAULT_TACTICS)
            built[key] = o if o.get("xi") else None
        return built[key]

    lp = []
    for row, venue in _ucl_fixtures(rng, rows, team, opp):
        B = opp(row)
        if not B:
            continue
        lp.append(_ucl_match(rng, A, B, venue, "League phase", f"Matchday {len(lp) + 1}"))
    rec = {"w": sum(1 for m in lp if m["result"] == "W"), "d": sum(1 for m in lp if m["result"] == "D"),
           "l": sum(1 for m in lp if m["result"] == "L"),
           "gf": sum(m["score"]["us"] for m in lp), "ga": sum(m["score"]["them"] for m in lp)}
    rec["pts"] = rec["w"] * 3 + rec["d"]
    rec["played"] = len(lp)

    # The simulated points replace this club's row in the real table; every other club keeps
    # what it actually did, and the table is re-sorted on points then goal difference.
    for r in rows:
        r["is_user"] = bool(_ucl_same(r["name"], team))
        if r["is_user"]:
            r["pts"], r["gd"] = rec["pts"], rec["gf"] - rec["ga"]
    rows.sort(key=lambda r: (-r["pts"], -(r.get("gd") or 0), r["name"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    rank = next(r["rank"] for r in rows if r["is_user"])
    if rank <= _UCL_TOP8:
        path = "Straight to the Round of 16"
    elif rank <= _UCL_PLAYOFF_LAST:
        path = "Into the knockout playoff"
    else:
        path = "Eliminated in the league phase"

    # ---- knockout rounds, one tie at a time until they lose one ----
    ties, taken, stage = [], [team], "League phase"
    if rank <= _UCL_PLAYOFF_LAST:
        seq = []
        if rank > _UCL_TOP8:
            # The playoff pairs seeds 9-16 with 17-24; the better seed hosts the 2nd leg.
            band = (17, 24) if rank <= 16 else (9, 16)
            seq.append(("Knockout playoff", band, 9.0, rank <= 16))
        # In the last 16 the eight league-phase seeds meet the playoff winners.
        seq.append(("Round of 16", (9, 24) if rank <= _UCL_TOP8 else (1, 8), 8.0,
                    rank <= _UCL_TOP8))
        seq.append(("Quarter-final", (1, 24), 7.0, None))
        seq.append(("Semi-final", (1, 24), 5.5, None))
        seq.append(("Final", (1, 24), 4.5, None))
        for rnd, (lo, hi), sharp, home2 in seq:
            row = _ucl_pick(rng, rows, taken, lo, hi, sharp, opp)
            B = opp(row) if row else None
            if not B:
                break
            taken.append(B["name"])
            if home2 is None:                              # no seeding left this deep
                home2 = rng.random() < 0.5
            tie = _ucl_tie(rng, A, B, rnd, home2, one_off=(rnd == "Final"))
            ties.append(tie)
            if not tie["won"]:
                stage = rnd
                break
            stage = "Champions" if rnd == "Final" else rnd

    won_it = stage == "Champions"
    if won_it:
        outcome = {"stage": "Champions", "won_it": True, "title": "🏆 Champions of Europe"}
    elif stage == "League phase":
        outcome = {"stage": "League phase", "won_it": False,
                   "title": f"Out in the league phase — {_ord(rank)} of {len(rows)}"}
    elif stage == "Final":
        outcome = {"stage": "Final", "won_it": False, "title": "🥈 Runners-up"}
    else:
        outcome = {"stage": stage, "won_it": False, "title": f"Eliminated in the {stage}"}

    all_matches = lp + [lg for tie in ties for lg in tie["legs"]]
    summary = {"played": len(all_matches),
               "w": sum(1 for m in all_matches if m["result"] == "W"),
               "d": sum(1 for m in all_matches if m["result"] == "D"),
               "l": sum(1 for m in all_matches if m["result"] == "L"),
               "gf": sum(m["score"]["us"] for m in all_matches),
               "ga": sum(m["score"]["them"] for m in all_matches)}
    outcome["line"] = _ucl_story(team, outcome, rank, ties)
    return {
        "available": True, "team": team, "seed": seed, "chemistry": chem["score"],
        "league_phase": {"matches": lp, "record": rec, "rank": rank, "n": len(rows),
                         "path": path, "top8": rank <= _UCL_TOP8,
                         "substituted_for": substituted},
        "table": rows, "ties": ties, "outcome": outcome,
        "scorers": _ucl_scorers(all_matches), "summary": summary,
        "leaders": _ucl_leaders(all_matches, {_nm(s): _photo(s) for s in xi if s.get("player")}),
    }
