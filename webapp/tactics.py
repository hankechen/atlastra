"""
Tactics Lab — a transparent, explainable tactical engine.

NOT a black-box ML model: every projected number is a documented function of real
player stats (per-90 output, progression, duels, passing — from v_stats_combined_player)
and the user's tactical settings. That traceability is the whole point — it lets the Lab
say *why* a setup succeeds or fails, and show what each change does ("What Changed?").

Flow:  squad -> pick XI in a formation -> assign roles -> set tactic sliders
       -> simulate() -> unit strengths, projected metrics, weaknesses, style match,
          and (vs an opponent) win probability + tactical battles.
"""
from __future__ import annotations

import math

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
        _slot("LM", "W", 17, 52, "MID"), _slot("LCM", "CM", 40, 46, "MID"),
        _slot("RCM", "CM", 60, 46, "MID"), _slot("RM", "W", 83, 52, "MID"),
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
    "W": {"LW", "RW", "LM", "RM", "W", "AM", "CAM", "F", "M"},
    "ST": {"ST", "CF", "FW", "F"},
}

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
    "AM": {
        "Advanced Playmaker": _role(mid=0.06, att=0.05, note="links play"),
        "Shadow Striker": _role(att=0.10, mid=-0.04, note="attacks the box"),
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
                "W": "Inside Forward", "ST": "Advanced Forward"}

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


_PACE_BASE = {"W": 74, "FB": 70, "ST": 70, "AM": 63, "CM": 60, "DM": 55, "CB": 50, "GK": 50}


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
        pick = next((p for p in pool if p["player"] not in used
                     and (p.get("position") or "").upper() in elig), None)
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
         "def_pace": [], "aerial": [], "att_pace": [], "gk": 55}
    for s in xi:
        p = s.get("player")
        if not p:
            continue
        fam, line = s["family"], s["line"]
        at = player_attrs(p, fam)
        r = ROLES.get(fam, {}).get(s.get("role")) or _role()
        fit = _role_fit(s.get("role"), at)               # profiling: right role = more value
        if fam == "GK":
            A["gk"] = at["rating"]
            A["press_resist"].append(at["passing"] * (1 + r["buildup"]))
            continue
        # attacking contribution (front + creative mids)
        if line == "ATT" or fam in ("AM",):
            val = 0.50 * at["shooting"] + 0.34 * at["creativity"] + 0.16 * at["dribbling"]
            A["attack"].append(_clamp(val * (1 + r["att"]) * fit))
            A["att_pace"].append(at["pace"])
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
        "defense": mean([*A["defense"], A["gk"] * 0.9]), "press_resist": mean(A["press_resist"]),
        "def_pace": mean(A["def_pace"], 55), "aerial": mean(A["aerial"], 50),
        "att_pace": mean(A["att_pace"], 62), "gk": A["gk"],
        "avg_rating": sum(rts) / len(rts) if rts else 62,   # team-quality signal for projections
    }


# Neutral opponent for single-team mode (FIFA-attribute scale: a solid average side).
_BASE_OPP = {"attack": 78, "midfield": 77, "defense": 77, "press_resist": 75,
             "def_pace": 73, "aerial": 73, "att_pace": 74, "gk": 78}


def _metrics(u: dict, t: dict, ou: dict, ot: dict) -> dict:
    """Project match metrics for a side (units u, tactics t) vs opponent (ou, ot).
    Formulas are deliberately simple + monotonic so the 'why' is explainable."""
    d = lambda k: (t.get(k, 50) - 50) / 50.0                 # tactic in [-1,1]
    poss = _clamp(50 + 0.14 * (u["midfield"] - ou["midfield"])
                  - 11 * d("directness") - 9 * d("counter") + 5 * d("press"), 26, 76)
    # attack (centred on 77 = an average side on the FIFA-attribute scale)
    att = 1.35 + (u["attack"] - 77) / 32.0 + (u["midfield"] - 77) / 70.0
    att *= 1 + 0.06 * d("width") + 0.05 * d("patience") \
        + 0.10 * d("counter") * ((ot.get("line_height", 50) - 50) / 50.0) + 0.06 * d("press")
    att *= 1 - (ou["defense"] - 77) / 150.0
    xg = round(_clamp_f(att, 0.3, 3.3), 2)
    # concede
    xga = 1.35 + (ou["attack"] - 77) / 32.0 + (ou["midfield"] - 77) / 70.0
    xga *= 1 - (u["defense"] - 77) / 150.0
    risk_line = 1 + 0.10 * d("line_height") * _clamp_f((73 - u["def_pace"]) / 30.0, -0.5, 1.0)
    risk_press = 1 + 0.05 * d("press") * _clamp_f((74 - u["press_resist"]) / 30.0, -0.4, 1.0)
    xga = round(_clamp_f(xga * risk_line * risk_press, 0.30, 3.2), 2)
    ppda = round(_clamp_f(13.5 - (t.get("press", 50) - 50) / 7.0
                          - (t.get("line_height", 50) - 50) / 15.0, 5, 20), 1)
    prog = round(_clamp(0.7 * u["midfield"] + 0.3 * u["press_resist"] + 8 * d("directness")))
    terr = round(_clamp(50 + (poss - 50) * 0.6 + 30 * d("line_height") * 0.5 - 10 * d("counter"), 12, 88))
    return {"possession": round(poss), "xg": xg, "xga": xga, "ppda": ppda,
            "progression": prog, "territory": terr}


def _clamp_f(v, lo, hi):
    return max(lo, min(hi, v))


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _win_probs(hx, ax):
    ph = pd = pa = 0.0
    for i in range(8):
        for j in range(8):
            p = _pois(i, hx) * _pois(j, ax)
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
        if exposure > 0.45 and cb_pace < 66:
            out.append({"title": f"{side} flank exposed in transition", "severity": "high",
                        "reason": f"{fb['player']['player']} pushes high ({fb.get('role')}) while "
                        f"{near_cb['player']['player'] if near_cb else 'the cover CB'} lacks recovery "
                        f"pace (est. {round(cb_pace)}). Quick wingers can attack the space behind."})
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
        if fam in ("FB", "W"):
            x += (1 if x > 50 else -1) * 8 * d("width")  # width spreads the flanks
        if fam == "FB":
            x += -(1 if x > 50 else -1) * 22 * max(0, r["mid"])   # inverted FB tucks in
            y += 10 * max(0, r["att"])                   # wing-back pushes up
        if fam == "ST":
            y += -13 * max(0, r["mid"])                  # False 9 drops
        if fam in ("W", "AM", "CM"):
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
    """Expected points per game from a Poisson (xg, xga) vs an average opponent."""
    pw = pd = 0.0
    for i in range(8):
        for j in range(8):
            p = _pois(i, xg) * _pois(j, xga)
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


def _project(u, t, team):
    """League points/position + a Champions League run for clubs; a WC/Euro run for nations.
    Strength = squad quality (avg XI rating) nudged by how effective the chosen tactics are
    (the model's xG/xGA → expected points vs an average opponent). div=7 gives knockout
    football realistic variance, so even the best side is only ~20% to win a trophy."""
    m = _metrics(u, t, _BASE_OPP, DEFAULT_TACTICS)         # season-representative form
    xpts = _xpts(m["xg"], m["xga"])
    # FIFA overall scale is narrow (~74-88 for these sides) but reflects big quality gaps,
    # so amplify it into the strength range, then nudge by tactic effectiveness.
    S = _clamp_f(round((u.get("avg_rating", 74) - 74) * 2.5 + 55 + (xpts - 1.4) * 4), 1, 90)
    ppg = _clamp_f(0.5 + (S - 50) * 0.052, 0.4, 2.4)       # caps a title season ~90 pts
    if team in LEAGUE_INFO:
        lg, games, n = LEAGUE_INFO[team]
        pts = round(ppg * games)
        run, likely, win = _run(S, [("League phase", 58), ("Round of 16", 71),
                                     ("Quarter-final", 77), ("Semi-final", 82),
                                     ("Final", 85), ("Champions", 0)], div=7)
        return {"kind": "club", "league": lg, "games": games, "n_teams": n,
                "points": pts, "position": _pos_for(pts, n), "ppg": round(ppg, 2),
                "comp": "Champions League", "run": run, "likely": likely, "win_pct": win}
    run, likely, win = _run(S, [("Group stage", 54), ("Round of 16", 66), ("Quarter-final", 74),
                                ("Semi-final", 80), ("Final", 85), ("Winners", 0)], div=7)
    return {"kind": "national", "comp": "World Cup / Euro", "strength": round(S),
            "run": run, "likely": likely, "win_pct": win}


# ------------------------------------------------------------------ main ----- #
def simulate(xi, tactics, opponent=None, team=None, form_home=None, form_away=None) -> dict:
    """xi: list of slots each {family,line,role,player}. tactics: slider dict.
    opponent (optional): {'units': {...}, 'tactics': {...}, 'name': str}.
    form_home/form_away (optional): signed recent-form ratings (~-1..+1, see
    SoccerDB.team_form_rating) that nudge the matchup toward the in-form side."""
    t = {**DEFAULT_TACTICS, **(tactics or {})}
    u = _units(xi)
    if opponent and opponent.get("units"):
        ou, ot = opponent["units"], {**DEFAULT_TACTICS, **(opponent.get("tactics") or {})}
    else:
        ou, ot = _BASE_OPP, DEFAULT_TACTICS
    m = _metrics(u, t, ou, ot)
    res = {"units": {k: round(v) for k, v in u.items()}, "metrics": m,
           "weaknesses": _weaknesses(xi, u, t, m), "style": _style_match(t, m),
           "viz": _viz(xi, t, m)}
    if team:
        res["projection"] = _project(u, t, team)
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
        res["win_probs"] = _win_probs(m["xg"], om["xg"])
        res["battles"] = _battles(u, t, ou, ot)
    return res


def team_units(xi, tactics) -> dict:
    """Just the unit vector — used to precompute an opponent for head-to-head."""
    return _units(xi)
