"""
Player archetype definitions (use case 10) — rule-based scouting roles.

Each fine position group (ST/W/AM/CM/DM/FB/CB/GK) gets 3 named roles. A role is a
"signature": metrics it indexes HIGH on (the skills that define it) and optionally
LOW on (what it sacrifices). A player is scored on the percentile vector from
player_radar_metrics (outfield) / player_profile_metrics (GK) — both already
percentiled WITHIN position — so the role captures relative emphasis, not raw level.

Metric labels MUST match those tables exactly (pipeline.profile PRETTY).
Shared by pipeline/archetypes.py (assignment) and analytics/queries.py (explorer).
"""

# outfield metric labels (player_radar_metrics)
ARCHETYPES = {
    "ST": [
        {"name": "Poacher", "high": ["non-penalty goals", "non-penalty xG", "finishing",
                                     "shots on target", "touches in box"],
         "low": ["progressive carries", "key passes", "take-ons"],
         "blurb": "Lives in the box — high-volume, high-efficiency finishing off limited touches."},
        {"name": "Target Man", "high": ["aerial duels %", "touches in box", "non-penalty goals",
                                        "finishing"], "low": ["take-ons", "progressive carries"],
         "blurb": "Aerial focal point who holds up play and finishes inside the box."},
        {"name": "Complete Forward", "high": ["non-penalty xG", "non-penalty goals", "key passes",
                                              "expected assists", "take-ons"], "low": [],
         "blurb": "Two-way threat — scores and creates, strong on the ball."},
        {"name": "Creative Forward", "high": ["key passes", "expected assists", "shot creation",
                                             "progressive passes", "take-ons"],
         "low": ["touches in box"],
         "blurb": "Drops and links — a false-9 profile prioritising creation over poaching."},
    ],
    "W": [
        {"name": "Inside Forward", "high": ["non-penalty goals", "non-penalty xG", "shots on target",
                                           "take-ons", "finishing"], "low": ["crosses to box"],
         "blurb": "Goal-hungry wide forward who cuts inside to shoot."},
        {"name": "Creator Winger", "high": ["expected assists", "key passes", "crosses to box",
                                           "shot creation"], "low": ["non-penalty goals"],
         "blurb": "Provider — crosses and key passes feed the strikers."},
        {"name": "Dribbling Carrier", "high": ["take-ons", "take-on %", "progressive carries",
                                              "progressive receptions"], "low": [],
         "blurb": "Beats players and drives the ball up the flank 1v1."},
    ],
    "AM": [
        {"name": "Advanced Playmaker", "high": ["key passes", "expected assists", "shot creation",
                                               "goal creation", "passes into final third"], "low": [],
         "blurb": "The chief creator — the team's chances run through him."},
        {"name": "Shadow Striker", "high": ["non-penalty goals", "non-penalty xG", "touches in box",
                                           "shots on target"], "low": ["pass completion %"],
         "blurb": "Goal-scoring 10 who arrives in the box late."},
        {"name": "Dribbling 10", "high": ["take-ons", "progressive carries", "take-on %",
                                         "progressive receptions"], "low": [],
         "blurb": "Press-resistant carrier who progresses through the lines on the dribble."},
    ],
    "CM": [
        {"name": "Deep-Lying Playmaker", "high": ["progressive passes", "pass completion %",
                                                 "passes into final third", "progressive pass accuracy",
                                                 "forward pass %"], "low": ["non-penalty goals", "touches in box"],
         "blurb": "Sets tempo from deep — high-volume, accurate progressive passing."},
        {"name": "Box-to-Box", "high": ["progressive carries", "tackles + interceptions",
                                        "recoveries", "non-penalty xG", "take-ons"], "low": [],
         "blurb": "All-action engine who covers ground both ways."},
        {"name": "Mezzala", "high": ["key passes", "expected assists", "take-ons",
                                     "progressive carries", "shot creation"], "low": ["recoveries"],
         "blurb": "Advanced, creative central mid who drifts wide to make things happen."},
    ],
    "DM": [
        {"name": "Anchor", "high": ["tackles + interceptions", "interceptions (adj)", "recoveries",
                                   "blocks + clearances", "tackle win %"],
         "low": ["non-penalty goals", "take-ons"],
         "blurb": "Pure screen — breaks up play and shields the back line."},
        {"name": "Regista", "high": ["progressive passes", "pass completion %", "passes into final third",
                                     "progressive pass accuracy", "forward pass %"], "low": [],
         "blurb": "Deep-lying conductor who dictates from in front of the defence."},
        {"name": "Ball-Winner", "high": ["tackles + interceptions", "recoveries",
                                        "progressive carries", "take-ons"], "low": [],
         "blurb": "Aggressive disruptor who wins it back and carries out of pressure."},
    ],
    "FB": [
        {"name": "Attacking Full-Back", "high": ["crosses to box", "key passes", "expected assists",
                                                "progressive carries", "take-ons"], "low": [],
         "blurb": "Flying wing-back — overlaps and delivers from wide."},
        {"name": "Defensive Full-Back", "high": ["tackles + interceptions", "interceptions (adj)",
                                                "aerial duels %", "blocks + clearances", "tackle win %"],
         "low": ["crosses to box", "take-ons"],
         "blurb": "Stay-at-home defender first — solid 1v1 and in the air."},
        {"name": "Inverted Full-Back", "high": ["progressive passes", "pass completion %",
                                               "passes into final third", "forward pass %"],
         "low": ["crosses to box"],
         "blurb": "Tucks inside to build — a full-back who plays like a midfielder."},
    ],
    "CB": [
        {"name": "Ball-Playing Defender", "high": ["progressive passes", "pass completion %",
                                                  "progressive pass accuracy", "passes into final third",
                                                  "forward pass %"], "low": [],
         "blurb": "Starts attacks — progressive, accurate distribution from the back."},
        {"name": "Stopper", "high": ["aerial duels %", "blocks + clearances", "tackle win %",
                                     "tackles + interceptions"], "low": ["progressive passes", "take-ons"],
         "blurb": "No-nonsense defender — wins headers, blocks, clears."},
        {"name": "Aggressor", "high": ["tackles + interceptions", "interceptions (adj)", "recoveries",
                                      "progressive carries"], "low": [],
         "blurb": "Steps out to defend on the front foot and carries into midfield."},
    ],
    "GK": [   # GK uses player_profile_metrics (GK vector) labels
        {"name": "Shot-Stopper", "high": ["shot-stopping (PSxG-GA)", "save %", "goals prevented"],
         "low": ["sweeping"],
         "blurb": "Elite reflexes — saves more than the chances faced suggest."},
        {"name": "Sweeper Keeper", "high": ["sweeping", "pass completion %", "long-pass accuracy"],
         "low": [],
         "blurb": "Commands space behind the line and plays out from the back."},
        {"name": "Ball-Playing Keeper", "high": ["pass completion %", "long-pass accuracy"],
         "low": ["sweeping"],
         "blurb": "Composed distributor — the first phase of build-up."},
    ],
}

GK_GROUP = "GK"
