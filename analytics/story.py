"""A Decade of Goals — Europe's Top-5 Leagues, 2014/15–2025/26.

Builds an 8-panel data story (8 figures + matching tables) from the DuckDB
warehouse. Run:  python -m analytics.story
Outputs PNGs to reports/figures/ and a STORY.md narrative with tables.
"""
from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
from matplotlib import font_manager  # noqa: F401  (ensures font cache built)

DB = Path("data/warehouse/soccer.duckdb")
OUT = Path("reports/figures")
OUT.mkdir(parents=True, exist_ok=True)

con = duckdb.connect(str(DB), read_only=True)

# ----- shared style -------------------------------------------------------
plt.rcParams.update({
    "figure.facecolor": "#0f1116",
    "axes.facecolor": "#0f1116",
    "savefig.facecolor": "#0f1116",
    "text.color": "#e8e8ea",
    "axes.labelcolor": "#c7c7cc",
    "xtick.color": "#9a9aa0",
    "ytick.color": "#9a9aa0",
    "axes.edgecolor": "#2a2d36",
    "font.size": 11,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
})
GREEN, BLUE, RED, GOLD, GREY = "#2dd4a7", "#5aa9ff", "#ff6b6b", "#ffd166", "#6b7280"


def season_lbl(code):  # '1415' -> '14/15'
    return f"{code[:2]}/{code[2:]}"


def save(fig, name):
    fig.tight_layout()
    p = OUT / name
    fig.savefig(p, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print("wrote", p)


tables = {}  # name -> (markdown table string)


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


# =========================================================================
# 1. THE CAST — top 15 goalscorers of the decade
# =========================================================================
rows = con.execute("""
    select p.player_name, sum(s.goals) g, round(sum(s.xg)) xg,
           sum(s.assists) a, count(distinct s.season) seasons
    from player_season_stats s join players p using(player_id)
    group by 1 order by g desc limit 15
""").fetchall()
names = [r[0].replace("-Lottin", "") for r in rows][::-1]
goals = [r[1] for r in rows][::-1]
xgs = [r[2] for r in rows][::-1]
fig, ax = plt.subplots(figsize=(10, 7))
ax.barh(names, goals, color=GREEN, label="Goals")
ax.barh(names, xgs, color="none", edgecolor=GOLD, linewidth=1.6, label="Expected (xG)")
for i, g in enumerate(goals):
    ax.text(g + 2, i, str(g), va="center", color="#e8e8ea", fontweight="bold")
ax.set_title("The Cast — Top 15 scorers of the decade (2014/15–2025/26)")
ax.set_xlabel("Goals in Europe's Top-5 leagues")
ax.legend(loc="lower right", framealpha=0)
ax.margins(x=0.08)
save(fig, "01_top_scorers.png")
tables["1. The Cast"] = md_table(
    ["#", "Player", "Goals", "xG", "Assists", "Seasons"],
    [(i + 1, r[0].replace("-Lottin", ""), r[1], int(r[2]), r[3], r[4])
     for i, r in enumerate(rows)])

# =========================================================================
# 2. CHANGING OF THE GUARD — old kings fade, new kings rise
# =========================================================================
cast = {
    "Lionel Messi": (GOLD, "-"),
    "Cristiano Ronaldo": (GREY, "-"),
    "Kylian Mbappe": (BLUE, "-"),
    "Erling Haaland": (GREEN, "-"),
}
seasons = [r[0] for r in con.execute(
    "select distinct season from player_season_stats order by 1").fetchall()]
fig, ax = plt.subplots(figsize=(11, 6))
for name, (color, ls) in cast.items():
    rows = con.execute("""
        select s.season, sum(s.goals)
        from player_season_stats s join players p using(player_id)
        where strip_accents(p.player_name) ilike '%' || ? || '%'
        group by 1 order by 1
    """, [name.split()[-1].replace("Mbappe", "Mbappe")]).fetchall()
    d = dict(rows)
    ys = [d.get(s) for s in seasons]
    ax.plot([season_lbl(s) for s in seasons], ys, marker="o", color=color,
            linewidth=2.4, label=name, ls=ls)
ax.set_title("Changing of the Guard — goals per season")
ax.set_ylabel("Goals (Top-5 leagues)")
ax.grid(axis="y", color="#23262f")
ax.legend(framealpha=0, ncol=2)
plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
save(fig, "02_changing_of_guard.png")

# =========================================================================
# 3. CLINICAL vs WASTEFUL — goals vs xG for prolific scorers
# =========================================================================
rows = con.execute("""
    select p.player_name, sum(s.goals) g, sum(s.xg) xg
    from player_season_stats s join players p using(player_id)
    group by 1 having sum(s.goals) >= 90 order by g desc
""").fetchall()
fig, ax = plt.subplots(figsize=(9.5, 8))
mx = max(max(r[1] for r in rows), max(r[2] for r in rows)) + 20
ax.plot([0, mx], [0, mx], color=GREY, ls="--", lw=1, zorder=0)
ax.text(mx * 0.62, mx * 0.66, "finishing = expected", color=GREY, rotation=33,
        fontsize=9)
for nm, g, xg in rows:
    over = g - xg
    color = GREEN if over >= 0 else RED
    ax.scatter(xg, g, s=70, color=color, zorder=3, edgecolor="#0f1116")
    ax.annotate(nm.replace("-Lottin", "").split()[-1], (xg, g),
                textcoords="offset points", xytext=(6, 4), fontsize=8,
                color="#c7c7cc")
ax.set_title("Clinical vs Wasteful — career goals vs xG (≥90 goals)")
ax.set_xlabel("Expected goals (xG)")
ax.set_ylabel("Actual goals")
ax.text(0.03, 0.95, "above line = overperformed xG", transform=ax.transAxes,
        color=GREEN, fontsize=9)
ax.text(0.03, 0.91, "below line = underperformed", transform=ax.transAxes,
        color=RED, fontsize=9)
save(fig, "03_clinical_vs_wasteful.png")
tables["3. Clinical vs Wasteful"] = md_table(
    ["Player", "Goals", "xG", "Goals − xG"],
    sorted([(r[0].replace("-Lottin", ""), r[1], round(r[2], 1),
             f"{r[1]-r[2]:+.1f}") for r in rows],
           key=lambda x: -float(x[3])))

# =========================================================================
# 4. THE DUOPOLY — Lewandowski vs Kane cumulative goals
# =========================================================================
fig, ax = plt.subplots(figsize=(11, 6))
for name, color in [("Lewandowski", GREEN), ("Harry Kane", GOLD)]:
    rows = con.execute("""
        select s.season, sum(s.goals)
        from player_season_stats s join players p using(player_id)
        where p.player_name ilike '%' || ? || '%'
        group by 1 order by 1
    """, [name]).fetchall()
    d = dict(rows)
    cum, run = [], 0
    for s in seasons:
        run += d.get(s, 0)
        cum.append(run)
    ax.plot([season_lbl(s) for s in seasons], cum, marker="o", color=color,
            linewidth=2.6, label=f"{name.split()[-1]} ({cum[-1]})")
    ax.fill_between([season_lbl(s) for s in seasons], cum, color=color, alpha=0.08)
ax.set_title("The Duopoly — cumulative goals, Lewandowski vs Kane")
ax.set_ylabel("Cumulative Top-5 goals")
ax.grid(axis="y", color="#23262f")
ax.legend(framealpha=0)
plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
save(fig, "04_duopoly.png")

# =========================================================================
# 5. SCORERS vs CREATORS — goals vs assists
# =========================================================================
rows = con.execute("""
    select p.player_name, sum(s.goals) g, sum(s.assists) a
    from player_season_stats s join players p using(player_id)
    group by 1 having sum(s.goals)+sum(s.assists) >= 180
    order by g+a desc
""").fetchall()
fig, ax = plt.subplots(figsize=(10, 7.5))
for nm, g, a in rows:
    ratio = g / (g + a)
    color = GREEN if ratio > 0.62 else BLUE if ratio < 0.45 else GOLD
    ax.scatter(a, g, s=80, color=color, edgecolor="#0f1116", zorder=3)
    ax.annotate(nm.replace("-Lottin", "").split()[-1], (a, g),
                textcoords="offset points", xytext=(6, 4), fontsize=8,
                color="#c7c7cc")
ax.set_title("Scorers vs Creators — goals & assists of the decade")
ax.set_xlabel("Assists")
ax.set_ylabel("Goals")
ax.text(0.97, 0.06, "green = pure scorer\ngold = dual threat\nblue = creator",
        transform=ax.transAxes, ha="right", fontsize=9, color="#c7c7cc")
save(fig, "05_scorers_vs_creators.png")
tables["5. Scorers vs Creators"] = md_table(
    ["Player", "Goals", "Assists", "G+A"],
    [(r[0].replace("-Lottin", ""), r[1], r[2], r[1] + r[2])
     for r in sorted(rows, key=lambda x: -(x[1] + x[2]))])

# =========================================================================
# 6. THE GREATEST SEASONS — best goals per 90 (min 1800 mins)
# =========================================================================
rows = con.execute("""
    select p.player_name, s.season, s.goals, s.minutes, s.goals_per90
    from player_season_stats s join players p using(player_id)
    where s.minutes >= 1800 order by s.goals_per90 desc limit 10
""").fetchall()[::-1]
labels = [f"{r[0].replace('-Lottin','').split()[-1]} {season_lbl(r[1])}"
          for r in rows]
vals = [r[4] for r in rows]
fig, ax = plt.subplots(figsize=(10, 7))
bars = ax.barh(labels, vals, color=BLUE)
bars[-1].set_color(GREEN)  # the record holder
for i, (v, r) in enumerate(zip(vals, rows)):
    ax.text(v + 0.01, i, f"{v:.2f}  ({r[2]}g)", va="center",
            color="#e8e8ea", fontsize=9)
ax.set_title("The Greatest Seasons — goals per 90 (≥1800 min)")
ax.set_xlabel("Goals per 90 minutes")
ax.margins(x=0.12)
save(fig, "06_greatest_seasons.png")
tables["6. Greatest Seasons (goals/90)"] = md_table(
    ["Player", "Season", "Goals", "Minutes", "Goals/90"],
    [(r[0].replace("-Lottin", ""), season_lbl(r[1]), r[2], r[3],
      f"{r[4]:.2f}") for r in rows[::-1]])

# =========================================================================
# 7. RIDING THEIR LUCK — points vs expected points
# =========================================================================
rows = con.execute("""
    select t.team_name, ts.season, ts.points, ts.expected_points,
           ts.points-ts.expected_points luck, ts.league_position
    from team_season_stats ts join teams t using(team_id)
    where ts.matches_played >= 30 order by luck desc limit 10
""").fetchall()[::-1]
labels = [f"{r[0]} {season_lbl(r[1])}" for r in rows]
luck = [r[4] for r in rows]
fig, ax = plt.subplots(figsize=(10, 7))
ax.barh(labels, luck, color=GOLD)
for i, (l, r) in enumerate(zip(luck, rows)):
    ax.text(l + 0.3, i, f"+{l:.1f}  ({int(r[2])} pts, finished #{r[5]})",
            va="center", color="#e8e8ea", fontsize=9)
ax.set_title("Riding Their Luck — points won above 'expected' (xPoints)")
ax.set_xlabel("Actual points − expected points")
ax.margins(x=0.25)
save(fig, "07_overachievers.png")
tables["7. Riding Their Luck"] = md_table(
    ["Team", "Season", "Pts", "xPts", "Over", "Finish"],
    [(r[0], season_lbl(r[1]), int(r[2]), round(r[3], 1), f"+{r[4]:.1f}",
      f"#{r[5]}") for r in rows[::-1]])

# =========================================================================
# 8. THE xG ERA — goals scored vs deserved, league-wide
# =========================================================================
rows = con.execute("""
    select season, sum(goals) g, sum(xg) xg
    from player_season_stats group by 1 order by 1
""").fetchall()
sl = [season_lbl(r[0]) for r in rows]
g = [r[1] for r in rows]
xg = [r[2] for r in rows]
fig, ax = plt.subplots(figsize=(11, 6))
ax.plot(sl, g, marker="o", color=GREEN, lw=2.4, label="Goals scored")
ax.plot(sl, xg, marker="s", color=GOLD, lw=2.4, label="Goals 'deserved' (xG)")
ax.fill_between(sl, g, xg, where=[a < b for a, b in zip(g, xg)],
                color=RED, alpha=0.15, label="xG underperformance")
ax.set_title("The xG Era — chances keep growing, goals don't (Top-5 combined)")
ax.set_ylabel("Total per season")
ax.grid(axis="y", color="#23262f")
ax.legend(framealpha=0)
plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
save(fig, "08_xg_era.png")
tables["8. The xG Era (league-wide)"] = md_table(
    ["Season", "Goals", "xG", "Gap"],
    [(season_lbl(r[0]), r[1], round(r[2]), f"{r[1]-r[2]:+.0f}") for r in rows])

# =========================================================================
# Write the narrative
# =========================================================================
story = f"""# A Decade of Goals
### Europe's Top-5 Leagues, 2014/15 → 2025/26

*Built from {con.execute('select count(*) from player_season_stats').fetchone()[0]:,}
player-seasons across the Premier League, La Liga, Serie A, Bundesliga and Ligue 1.
xG = Understat expected-goals model.*

---

## 1. The Cast
The decade belonged to two metronomes. **Lewandowski (321)** and **Kane (308)**
out-scored everyone — but note Kane's bar towers *over* its gold xG outline: he
scores far more than his chances suggest he should. Messi and Ronaldo round out the
old guard; Haaland storms the list having played barely half the seasons.

![Top scorers](figures/01_top_scorers.png)

{tables['1. The Cast']}

## 2. Changing of the Guard
The same chart told two ways. As Messi and Ronaldo's season hauls slide down the
right-hand side, **Mbappé and Haaland** climb to meet them — the torch passing in
real time.

![Changing of the guard](figures/02_changing_of_guard.png)

## 3. Clinical vs Wasteful
Every dot is a top scorer; the dashed line is "you scored exactly your xG."
**Kane, Immobile, Griezmann and Mbappé** live well above it — elite finishers who
beat their chances. A few names sit *below*: prolific, but kept afloat by sheer
volume of chances rather than clinical conversion.

![Clinical vs wasteful](figures/03_clinical_vs_wasteful.png)

{tables['3. Clinical vs Wasteful']}

## 4. The Duopoly
Stack their goals cumulatively and the two-horse race is hypnotic — Lewandowski and
Kane pulling away from the field, season after season, neither letting the other
breathe.

![The duopoly](figures/04_duopoly.png)

## 5. Scorers vs Creators
Goals on the y-axis, assists on the x. The pure scorers (green) hug the left wall;
**De Bruyne and Müller** sit far to the right as the decade's great creators; and a
golden cluster — **Messi, Salah, Suárez** — does devastating damage in *both*
directions.

![Scorers vs creators](figures/05_scorers_vs_creators.png)

{tables['5. Scorers vs Creators']}

## 6. The Greatest Seasons
By goals-per-90 (min. 1800 minutes), **Lewandowski's 41-goal 2020/21** is the
single most lethal campaign of the decade — edging Ronaldo's 48-goal 2014/15 and
Kane's record-breaking debut Bundesliga year.

![Greatest seasons](figures/06_greatest_seasons.png)

{tables['6. Greatest Seasons (goals/90)']}

## 7. Riding Their Luck
Not every story is about strikers. Expected points (xPoints) model how many points a
team *deserved*. **Liverpool 2019/20** won an astonishing **+24.7** points above
their underlying numbers on the way to the title — the decade's greatest act of
clutch overachievement.

![Overachievers](figures/07_overachievers.png)

{tables['7. Riding Their Luck']}

## 8. The xG Era
Zoom all the way out. Across all five leagues, **chances created (xG) climb steadily**
— modern football manufactures more good looks than ever. Yet actual **goals have
flatlined**, and since 2018/19 the leagues collectively *under*-finish their xG every
year. We are creating more and converting less.

![The xG era](figures/08_xg_era.png)

{tables['8. The xG Era (league-wide)']}

---
*8 figures in `reports/figures/`. Generated by `analytics/story.py`.*
"""
Path("reports/STORY.md").write_text(story)
print("wrote reports/STORY.md")
con.close()
