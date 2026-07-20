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
}


# --------------------------------------------------------------- attributes -- #
def _clamp(v, lo=1, hi=99):
    return max(lo, min(hi, v))


def _sc(v, ref, floor=12, span=74):
    """Scale a raw per-90 value onto ~0-99 (floor at low output, ~86 at the reference)."""
    return _clamp(floor + span * ((v or 0) / ref)) if ref else floor


_PACE_BASE = {"W": 74, "FB": 70, "ST": 70, "AM": 63, "CM": 60, "DM": 55, "CB": 50, "GK": 50}


def player_attrs(p: dict, family: str) -> dict:
    """Derive 0-99 attributes from a player's per-90 stats + rate stats. pace is an
    explicit ESTIMATE (no tracking data): a position baseline nudged by dribble volume
    (quick) and heavy clearances (slower stopper)."""
    n, pc = p.get("per90", {}), p.get("pct", {})
    tk_int = (n.get("tackles", 0) + n.get("interceptions", 0))
    passing = _clamp(0.70 * (pc.get("passing") or 60) + 0.30 * _sc(n.get("passes", 0), 72))
    creativity = _sc(n.get("xa", 0) + n.get("chances", 0) * 0.18, 0.55)
    dribbling = _sc(n.get("dribbles", 0), 2.4)
    aerial = pc.get("aerial") or (52 if family in ("CB", "ST") else 42)
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
            xi.append({**s, "player": pick, "role": DEFAULT_ROLE[s["family"]]})
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
        if fam == "GK":
            A["gk"] = at["rating"]
            A["press_resist"].append(at["passing"] * (1 + r["buildup"]))
            continue
        # attacking contribution (front + creative mids)
        if line == "ATT" or fam in ("AM",):
            val = 0.50 * at["shooting"] + 0.34 * at["creativity"] + 0.16 * at["dribbling"]
            A["attack"].append(_clamp(val * (1 + r["att"])))
            A["att_pace"].append(at["pace"])
        if fam in ("CM", "DM", "AM"):
            val = 0.40 * at["passing"] + 0.34 * at["progression"] + 0.26 * at["creativity"]
            A["midfield"].append(_clamp(val * (1 + r["mid"])))
        if fam in ("CB", "FB", "DM"):
            wdef = {"CB": 1.0, "FB": 0.85, "DM": 0.6}[fam]
            val = 0.58 * at["defending"] + 0.24 * at["aerial"] + 0.18 * at["pace"]
            A["defense"].append(_clamp(val * (1 + r["def"]) * wdef + (1 - wdef) * 55))
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
    return {
        "attack": mean(A["attack"]), "midfield": mean(A["midfield"]),
        "defense": mean([*A["defense"], A["gk"] * 0.9]), "press_resist": mean(A["press_resist"]),
        "def_pace": mean(A["def_pace"], 55), "aerial": mean(A["aerial"], 50),
        "att_pace": mean(A["att_pace"], 62), "gk": A["gk"],
    }


# Neutral opponent for single-team mode.
_BASE_OPP = {"attack": 63, "midfield": 63, "defense": 63, "press_resist": 60,
             "def_pace": 60, "aerial": 52, "att_pace": 62, "gk": 60}


def _metrics(u: dict, t: dict, ou: dict, ot: dict) -> dict:
    """Project match metrics for a side (units u, tactics t) vs opponent (ou, ot).
    Formulas are deliberately simple + monotonic so the 'why' is explainable."""
    d = lambda k: (t.get(k, 50) - 50) / 50.0                 # tactic in [-1,1]
    poss = _clamp(50 + 0.16 * (u["midfield"] - ou["midfield"])
                  - 11 * d("directness") - 9 * d("counter") + 5 * d("press"), 26, 76)
    # attack
    att = 1.25 + (u["attack"] - 63) / 38.0 + (u["midfield"] - 63) / 85.0
    att *= 1 + 0.06 * d("width") + 0.05 * d("patience") \
        + 0.10 * d("counter") * ((ot.get("line_height", 50) - 50) / 50.0) + 0.06 * d("press")
    att *= 1 - (ou["defense"] - 63) / 120.0
    xg = round(_clamp_f(att, 0.35, 3.3), 2)
    # concede
    xga = 1.25 + (ou["attack"] - 63) / 38.0 + (ou["midfield"] - 63) / 85.0
    xga *= 1 - (u["defense"] - 63) / 115.0
    risk_line = 1 + 0.10 * d("line_height") * _clamp_f((60 - u["def_pace"]) / 40.0, -0.5, 1.0)
    risk_press = 1 + 0.05 * d("press") * _clamp_f((58 - u["press_resist"]) / 40.0, -0.4, 1.0)
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
    # flank exposure: an attacking FB + a slow same-side CB + a high line
    fbs = [s for s in xi if s["family"] == "FB" and s.get("player")]
    cbs = [s for s in xi if s["family"] == "CB" and s.get("player")]
    for fb in fbs:
        role = ROLES.get("FB", {}).get(fb.get("role")) or _role()
        left = fb["x"] < 50
        side = "Left" if left else "Right"
        near_cb = min(cbs, key=lambda c: abs(c["x"] - fb["x"]), default=None) if cbs else None
        cb_pace = player_attrs(near_cb["player"], "CB")["pace"] if near_cb else 55
        exposure = role["flank"] + max(0, (t.get("line_height", 50) - 50) / 100.0)
        if exposure > 0.45 and cb_pace < 56:
            out.append({"title": f"{side} flank exposed in transition", "severity": "high",
                        "reason": f"{fb['player']['player']} pushes high ({fb.get('role')}) while "
                        f"{near_cb['player']['player'] if near_cb else 'the cover CB'} lacks recovery "
                        f"pace (est. {round(cb_pace)}). Quick wingers can attack the space behind."})
    # play out under pressure
    if u["press_resist"] < 60 and t.get("directness", 50) < 46:
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
    if t.get("line_height", 50) > 62 and u["def_pace"] < 54:
        out.append({"title": "Space in behind a high line", "severity": "high",
                    "reason": f"A high defensive line with slow-ish defenders (est. pace "
                    f"{round(u['def_pace'])}) leaves room in behind for pace to run onto."})
    # aerial / set-pieces
    if u["aerial"] < 50:
        out.append({"title": "Set-piece & crossing vulnerability", "severity": "med",
                    "reason": f"Low aerial-duel win rate at the back ({round(u['aerial'])}) — crosses "
                    f"and set pieces are a route in for the opponent."})
    # blunt vs a low block
    if u["attack"] > 68 and u["midfield"] < 60 and t.get("width", 50) < 45 and t.get("patience", 50) < 45:
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


# ------------------------------------------------------------------ main ----- #
def simulate(xi, tactics, opponent=None) -> dict:
    """xi: list of slots each {family,line,role,player}. tactics: slider dict.
    opponent (optional): {'units': {...}, 'tactics': {...}, 'name': str}."""
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
    if opponent and opponent.get("units"):
        om = _metrics(ou, ot, u, t)
        res["opponent_metrics"] = om
        res["win_probs"] = _win_probs(m["xg"], om["xg"])
        res["battles"] = _battles(u, t, ou, ot)
    return res


def team_units(xi, tactics) -> dict:
    """Just the unit vector — used to precompute an opponent for head-to-head."""
    return _units(xi)
