"""
Legend style-profiles for the "Find the Next X" tool.

Iconic, mostly pre-dataset players (Xavi, Pirlo, Bergkamp...) don't have rows in our
percentile tables, so each is hand-authored as a STYLE template: a vector over the six
Atlastra radar axes (0-100, read as "emphasis vs positional peers", not absolute quality).
The matcher centres and cosine-compares this vector against current players' real
percentile vectors (player_radar_metrics) within the legend's position family -- the same
shape-similarity the live "Similar Players" engine uses. So the legends are editorial
reference points; the *matches* are 100% data-driven.

Axes (must match analytics.queries.SoccerDB.RADAR_AXES):
  Chance Creation · Progression · Passing · Finishing · Defending · Dribbling
"""

AXES = ["Chance Creation", "Progression", "Passing", "Finishing", "Defending", "Dribbling"]


def _v(cc, prog, pas, fin, dfn, dri):
    return {"Chance Creation": cc, "Progression": prog, "Passing": pas,
            "Finishing": fin, "Defending": dfn, "Dribbling": dri}


# key -> profile.  match_groups = the fine position families we search for a successor in.
LEGENDS = {
    # ---- deep / central midfield ----
    "xavi": {"name": "Xavi", "club": "Barcelona", "era": "2000s–10s", "pos": "Central Midfield",
             "match_groups": ["CM", "DM", "AM"],
             "blurb": "Tempo-setting metronome — kept the ball moving with relentless short passing and positioning.",
             "axes": _v(80, 88, 99, 30, 45, 55)},
    "pirlo": {"name": "Andrea Pirlo", "club": "Milan / Juventus", "era": "2000s–10s", "pos": "Deep-Lying Playmaker",
              "match_groups": ["DM", "CM"],
              "blurb": "Regista supreme — dictated games from the base with vision and the cleanest long passing of his era.",
              "axes": _v(78, 90, 97, 40, 50, 35)},
    "iniesta": {"name": "Andrés Iniesta", "club": "Barcelona", "era": "2000s–10s", "pos": "Central Midfield",
                "match_groups": ["CM", "AM"],
                "blurb": "Press-resistant glider — escaped pressure with the ball glued to his feet, then threaded the killer pass.",
                "axes": _v(82, 88, 90, 45, 40, 92)},
    "modric": {"name": "Luka Modrić", "club": "Real Madrid", "era": "2010s–20s", "pos": "Central Midfield",
               "match_groups": ["CM", "DM"],
               "blurb": "All-action conductor — carries, switches play and covers ground while still controlling tempo.",
               "axes": _v(78, 90, 88, 45, 60, 80)},
    "gerrard": {"name": "Steven Gerrard", "club": "Liverpool", "era": "2000s–10s", "pos": "Box-to-Box Midfield",
                "match_groups": ["CM", "AM"],
                "blurb": "Driving box-to-box engine — carried teams with surging runs, thunderous shooting and chance creation.",
                "axes": _v(80, 78, 70, 75, 72, 60)},
    "busquets": {"name": "Sergio Busquets", "club": "Barcelona", "era": "2010s", "pos": "Defensive Midfield",
                 "match_groups": ["DM", "CM"],
                 "blurb": "Positional pivot — screened the defence and recycled possession with near-flawless decision-making.",
                 "axes": _v(45, 80, 95, 25, 78, 40)},
    # ---- attacking midfield / #10 ----
    "zidane": {"name": "Zinedine Zidane", "club": "Juventus / Real Madrid", "era": "1990s–2000s", "pos": "Attacking Midfield",
               "match_groups": ["AM", "CM"],
               "blurb": "Elegant orchestrator — turned in tight space, glided past pressure and unlocked defences with the final pass.",
               "axes": _v(88, 82, 85, 60, 35, 92)},
    "bergkamp": {"name": "Dennis Bergkamp", "club": "Arsenal", "era": "1990s–2000s", "pos": "Second Striker",
                 "match_groups": ["AM", "ST"],
                 "blurb": "Sublime link forward — exquisite first touch, vision and finishing as a creative second striker.",
                 "axes": _v(90, 70, 80, 82, 25, 78)},
    "riquelme": {"name": "Juan Román Riquelme", "club": "Boca / Villarreal", "era": "2000s", "pos": "Classic No. 10",
                 "match_groups": ["AM", "CM"],
                 "blurb": "Old-school enganche — slowed the game to his rhythm and created from pockets with peerless passing.",
                 "axes": _v(95, 75, 90, 55, 25, 70)},
    "kaka": {"name": "Kaká", "club": "Milan", "era": "2000s", "pos": "Attacking Midfield",
             "match_groups": ["AM", "W"],
             "blurb": "Explosive runner from deep — carried the ball at pace and arrived to finish.",
             "axes": _v(80, 90, 70, 75, 30, 88)},
    # ---- wingers ----
    "ronaldinho": {"name": "Ronaldinho", "club": "Barcelona", "era": "2000s", "pos": "Winger / Forward",
                   "match_groups": ["W", "AM"],
                   "blurb": "Joyful magician — outrageous dribbling and trickery feeding equally outrageous creativity.",
                   "axes": _v(90, 80, 78, 70, 25, 98)},
    "robben": {"name": "Arjen Robben", "club": "Bayern Munich", "era": "2000s–10s", "pos": "Inverted Winger",
               "match_groups": ["W"],
               "blurb": "Inverted-winger blueprint — cut inside off the right and bent it home, relentlessly.",
               "axes": _v(78, 80, 55, 82, 30, 92)},
    "ribery": {"name": "Franck Ribéry", "club": "Bayern Munich", "era": "2000s–10s", "pos": "Winger",
               "match_groups": ["W"],
               "blurb": "Direct, two-footed terror — beat his man and created from the left flank for a decade.",
               "axes": _v(88, 82, 60, 65, 35, 90)},
    # ---- strikers ----
    "henry": {"name": "Thierry Henry", "club": "Arsenal", "era": "2000s", "pos": "Centre Forward",
              "match_groups": ["ST", "W"],
              "blurb": "Complete modern forward — pace, finishing and creation, drifting left to attack the box.",
              "axes": _v(78, 80, 60, 92, 25, 85)},
    "ronaldo_r9": {"name": "Ronaldo Nazário", "club": "Inter / Real Madrid", "era": "1990s–2000s", "pos": "Centre Forward",
                   "match_groups": ["ST"],
                   "blurb": "Unstoppable No. 9 — explosive dribbling and ruthless finishing, a one-man counter-attack.",
                   "axes": _v(65, 78, 45, 95, 20, 90)},
    "ibrahimovic": {"name": "Zlatan Ibrahimović", "club": "Various", "era": "2000s–10s", "pos": "Centre Forward",
                    "match_groups": ["ST"],
                    "blurb": "Towering technician — held up play, linked and finished spectacularly as a focal-point striker.",
                    "axes": _v(70, 60, 60, 90, 30, 75)},
    "drogba": {"name": "Didier Drogba", "club": "Chelsea", "era": "2000s–10s", "pos": "Target Forward",
               "match_groups": ["ST"],
               "blurb": "Powerful target man — bullied centre-backs, held the line and delivered on the biggest nights.",
               "axes": _v(62, 55, 50, 90, 35, 62)},
    # ---- defenders ----
    "maldini": {"name": "Paolo Maldini", "club": "Milan", "era": "1990s–2000s", "pos": "Centre-Back / Full-Back",
                "match_groups": ["CB", "FB"],
                "blurb": "Defending as an art form — positioning and composure on the ball that defined the role.",
                "axes": _v(35, 70, 80, 25, 95, 50)},
    "puyol": {"name": "Carles Puyol", "club": "Barcelona", "era": "2000s–10s", "pos": "Centre-Back",
              "match_groups": ["CB"],
              "blurb": "Warrior centre-back — aggression, recovery pace and aerial dominance leading the line.",
              "axes": _v(25, 55, 65, 30, 96, 40)},
    "cafu": {"name": "Cafu", "club": "Roma / Milan", "era": "1990s–2000s", "pos": "Attacking Full-Back",
             "match_groups": ["FB", "W"],
             "blurb": "Tireless overlapping full-back — up and down the right all game, defending and creating.",
             "axes": _v(78, 82, 68, 40, 72, 75)},
    "roberto_carlos": {"name": "Roberto Carlos", "club": "Real Madrid", "era": "1990s–2000s", "pos": "Attacking Full-Back",
                       "match_groups": ["FB", "W"],
                       "blurb": "Explosive marauding full-back — rampaging runs, a cannon of a left foot and end product.",
                       "axes": _v(75, 85, 60, 60, 70, 78)},
}


def legend_list():
    """Light list for the picker, grouped-friendly (key + display fields)."""
    return [{"key": k, "name": v["name"], "club": v["club"], "era": v["era"], "pos": v["pos"]}
            for k, v in LEGENDS.items()]
