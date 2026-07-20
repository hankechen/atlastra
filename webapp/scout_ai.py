"""
Scout Report generator — turns a player's Atlastra data (rating, percentile ranks,
archetype, season trend, stats) into a written, scout-style report via the Claude API.

Server-side only. Reads ANTHROPIC_API_KEY from the environment (standard SDK auth);
if no key is configured it returns a friendly error instead of raising. Generated
reports are cached in a small sqlite file so repeat views don't re-spend tokens —
pass refresh=True to regenerate.
"""
import os
import json
import sqlite3
import datetime
import sys

try:
    from config import DATA_DIR
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DATA_DIR

MODEL = "claude-opus-4-8"
ENGINE_NAME = "Atlastra report engine"
CACHE_PATH = DATA_DIR / "scout_reports.sqlite"

SYSTEM = """You are an elite professional football (soccer) scout writing a recruitment \
report for a club's sporting director. Write in the voice of an experienced scout: precise, \
evaluative, decisive, and grounded in evidence.

Rules:
- Base EVERY claim on the data provided (percentile ranks, Atlastra ratings, per-90 stats, \
season trend, archetype). Cite the numbers naturally (e.g. "94th percentile for chance creation").
- Do NOT invent facts the data doesn't contain: no specific matches, no injury history, no \
transfer fees beyond the given market value, no club interest, no quotes.
- The "Atlastra rating" is a 1-99 composite vs positional peers; percentiles are vs the same \
position in the top-5 leagues. League/UCL are separate common-metric ratings. Treat them as the \
analytical basis, not gospel — note where the eye test might differ.
- British football terminology. Confident but fair; flag genuine weaknesses and risks.

Output GitHub-flavoured Markdown, ~450-750 words, with exactly these sections:
# {Name} — Scouting Report
*one line: age · nationality · position · club · market value*

## Overview
2-3 sentences: who he is and the headline verdict.

## Statistical Profile
What the percentiles and ratings say, grouped by area (attacking output, creation, \
progression/dribbling, defending, passing as relevant to the position).

## Strengths
3-5 bullet points, each tied to a number.

## Weaknesses & Risks
2-4 bullet points (include any data gaps honestly).

## Style of Play & Role
The archetype, how he plays, comparable players, and the role/system that fits.

## Trajectory
Age and the multi-season trend — improving, peaking, or declining, with the numbers.

## Recruitment Verdict
A clear recommendation, the level/role he suits, and a **Scout grade: X/10**.

Do not add a preamble or anything outside the report."""


def _conn():
    con = sqlite3.connect(str(CACHE_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS reports "
                "(key TEXT PRIMARY KEY, report TEXT, model TEXT, generated_at TEXT)")
    return con


def _summary(d: dict) -> dict:
    """Compact the web_player payload down to the analytical signal for the prompt."""
    seasons = {s["value"]: s["label"] for s in (d.get("seasons") or [])}
    keep = ["name", "team", "position_group", "detailed_position", "age", "nationality",
            "market_value_eur", "rating", "classification", "rank_in_group", "percentile",
            "ratings", "avg_rating", "tiles", "stats_scopes", "radar", "archetype",
            "signature_actions", "career", "career_stat", "strengths", "weaknesses",
            "areas_of_improvement", "hist_level"]
    out = {k: d[k] for k in keep if d.get(k) is not None}
    out["season"] = seasons.get(d.get("season"), d.get("season"))
    return out


def _ord(n):
    if n is None:
        return "—"
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _mv(eur):
    if not eur:
        return None
    return f"€{eur / 1e6:.0f}M" if eur >= 1e6 else f"€{eur / 1e3:.0f}K"


def _plur(n, word):
    n = int(n or 0)
    return f"{n} {word}{'' if n == 1 else 's'}"


_ROLE = {"CM": "a controlling central midfielder in a possession-based system",
         "DM": "a deep-lying pivot screening the defence", "AM": "an advanced playmaker behind the striker",
         "W": "a wide forward who threatens cutting inside", "ST": "a central striker leading the line",
         "CB": "a ball-playing centre-back", "FB": "an attacking full-back / wing-back",
         "GK": "a modern sweeper-keeper"}
_CAREER_LABEL = {"xa": "expected assists (xA)", "ga_per90": "goals + assists per 90", "goals": "goals",
                 "assists": "assists", "chances_created": "chances created",
                 "dribbles_completed": "dribbles completed"}


def _local_report(d: dict) -> str:
    """Rule-based scouting report assembled from the player's data — no LLM needed."""
    name = d.get("name", "Unknown")
    grp = (d.get("archetype") or {}).get("group") or d.get("position_group") or ""
    grp_label = (d.get("archetype") or {}).get("group_label") or grp
    pos = d.get("detailed_position") or d.get("position_group") or "player"
    rating = d.get("rating")
    cls = d.get("classification") or ""
    radar = sorted(d.get("radar") or [], key=lambda a: -a["value"])
    high = [a for a in radar if a["value"] >= 78]
    mid = [a for a in radar if 58 <= a["value"] < 78]
    low = [a for a in radar if a["value"] <= 46]
    ratings = d.get("ratings") or {}
    lg, ucl = ratings.get("league"), ratings.get("ucl")

    # ---- header ----
    bits = [str(d["age"]) for k in ["age"] if d.get(k)]
    for k in ["nationality"]:
        if d.get(k):
            bits.append(d[k])
    bits += [pos, d.get("team") or ""]
    if _mv(d.get("market_value_eur")):
        bits.append(_mv(d["market_value_eur"]))
    head = f"# {name} — Scouting Report\n*{' · '.join(b for b in bits if b)}*"

    # ---- overview ----
    ov = []
    if rating is not None:
        rank = f", ranking {_ord(d.get('percentile'))} percentile" if d.get("percentile") else ""
        ov.append(f"{name} is rated **{rating} ({cls})** among {grp_label}{rank} on Atlastra's composite.")
    arch = d.get("archetype") or {}
    if arch.get("archetype"):
        ov.append(f"He profiles as a **{arch['archetype']}** ({arch.get('fit', '?')}% fit).")
    if lg and ucl:
        ov.append(f"On common-metric scales he rates **{lg['rating']}** in league play "
                  f"({lg['classification']}) and **{ucl['rating']}** in the Champions League ({ucl['classification']}).")
    elif lg:
        ov.append(f"On the common-metric league scale he rates **{lg['rating']}** ({lg['classification']}).")

    # ---- statistical profile ----
    sp = []
    if high:
        sp.append("His standout areas are " + ", ".join(
            f"**{a['axis'].lower()}** ({_ord(a['value'])} percentile)" for a in high[:3]) + ".")
    if mid:
        sp.append("He is solid in " + ", ".join(a["axis"].lower() for a in mid[:3]) + ".")
    if low:
        sp.append("The data flags " + ", ".join(
            f"**{a['axis'].lower()}** ({_ord(a['value'])})" for a in low[:2])
            + (" as a relative limitation." if len(low) == 1 else " as relative limitations."))
    sc = (d.get("stats_scopes") or {}).get("league") or {}
    if sc.get("goals") is not None and sc.get("minutes"):
        sp.append(f"Over {int(sc['minutes'])} league minutes: {_plur(sc.get('goals'), 'goal')}, "
                  f"{_plur(sc.get('assists'), 'assist')}, {_plur(sc.get('chances_created'), 'chance')} created.")

    # ---- strengths ----
    st = [f"- Elite **{a['axis'].lower()}** — {_ord(a['value'])} percentile vs position peers."
          for a in high[:3]]
    for s in (d.get("signature_actions") or [])[:2]:
        if s.get("percentile", 0) >= 85:
            st.append(f"- High-volume **{s['name'].lower()}** — {s['value']} per 90 ({_ord(s['percentile'])} percentile).")
    if d.get("strengths"):
        st.append("- Underlying metrics: " + ", ".join(d["strengths"][:4]) + ".")
    if not st:
        st = ["- A balanced profile without an extreme statistical spike."]

    # ---- weaknesses ----
    wk = [f"- **{a['axis']}** is below par ({_ord(a['value'])} percentile) for the position." for a in low[:2]]
    if d.get("weaknesses"):
        wk.append("- Weaker underlying metrics: " + ", ".join(d["weaknesses"][:3]) + ".")
    if lg and ucl and (lg["rating"] - ucl["rating"]) >= 12:
        wk.append(f"- Champions League output ({ucl['rating']}, {ucl['classification']}) trails his league "
                  f"level ({lg['rating']}) on a smaller sample — a step-up question against elite opposition.")
    if not wk:
        wk = ["- No glaring statistical weakness; the main caveats are context and sample size."]

    # ---- style & role ----
    style = []
    if arch.get("blurb"):
        line = f"Plays as a **{arch.get('archetype')}**"
        if arch.get("archetype2") and (arch.get("fit2") or 0) >= 60:
            line += f" with **{arch['archetype2']}** traits ({arch['fit2']}%)"
        style.append(line + f" — {arch['blurb']}")
    sims = [s["player"] for s in (arch.get("similar") or [])[:3]]
    if sims:
        style.append("Statistically closest to " + ", ".join(sims) + ".")
    style.append(f"Best deployed as {_ROLE.get(grp, 'a regular starter')}.")

    # ---- trajectory ----
    tr = []
    age = d.get("age")
    if age:
        phase = ("still in his development phase with clear upside" if age <= 23
                 else "in his prime years" if age <= 29
                 else "an experienced operator, with age a longer-term consideration")
        tr.append(f"At **{age}**, he is {phase}.")
    car = [c for c in (d.get("career") or []) if c.get("value") is not None]
    if len(car) >= 3:
        lbl = _CAREER_LABEL.get(d.get("career_stat"), d.get("career_stat", "output"))
        early = sum(c["value"] for c in car[:2]) / 2
        recent = sum(c["value"] for c in car[-2:]) / 2
        dirn = ("on an upward trajectory" if recent > early * 1.12
                else "trending downward" if recent < early * 0.88 else "broadly steady")
        tr.append(f"His {lbl} has moved {car[0]['value']} → {car[-1]['value']} across the tracked "
                  f"seasons ({car[0]['season']}–{car[-1]['season']}), {dirn}.")
    if not tr:
        tr = ["Limited multi-season history available for a firm trajectory read."]

    # ---- verdict ---- blend the composite with the position-fair league rating so
    # players the datamb composite undersells (low-volume CBs, etc.) aren't penalised.
    lg_r = lg["rating"] if lg else None
    r = round((rating + lg_r) / 2) if (rating and lg_r) else (rating or lg_r or 0)
    if r >= 88:
        tier = "an elite, first-choice signing for any side at the highest level"
    elif r >= 80:
        tier = "a high-end starter for a top, Champions League-level team"
    elif r >= 72:
        tier = "a dependable starter or strong squad option for an upper-mid side"
    elif r >= 64:
        tier = "a useful rotation option or developmental signing"
    else:
        tier = "a depth or project signing that needs development"
    grade = max(4, min(10, round(r / 10))) if r else 5
    verdict = f"A clear recommendation: **{name} is {tier}**, suited to {_ROLE.get(grp, 'a regular role')}. " \
              f"**Scout grade: {grade}/10.**"

    return "\n\n".join([
        head,
        "## Overview\n" + " ".join(ov),
        "## Statistical Profile\n" + " ".join(sp),
        "## Strengths\n" + "\n".join(st),
        "## Weaknesses & Risks\n" + "\n".join(wk),
        "## Style of Play & Role\n" + " ".join(style),
        "## Trajectory\n" + " ".join(tr),
        "## Recruitment Verdict\n" + verdict,
    ])


def scout_report(data: dict, refresh: bool = False) -> dict:
    if not data or not data.get("name"):
        return {"available": False, "error": "Player not found."}
    key = f"{data['name']}|{data.get('season', '')}"
    con = _conn()
    if not refresh:
        row = con.execute("SELECT report, model, generated_at FROM reports WHERE key = ?",
                          [key]).fetchone()
        if row:
            con.close()
            return {"available": True, "cached": True, "report": row[0], "model": row[1],
                    "generated_at": row[2], "player": data["name"], "season": data.get("season")}

    user = ("Write the scouting report from this player's data (JSON):\n\n"
            + json.dumps(_summary(data), ensure_ascii=False))
    report, model_used = None, None
    from webapp import gemini
    if gemini.available():                              # preferred: Gemini
        report = gemini.generate(SYSTEM + "\n\n---\n\n" + user, temperature=0.5)
        model_used = "gemini-flash" if report else None
    if not report and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        try:
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=MODEL, max_tokens=4000, thinking={"type": "adaptive"},
                system=SYSTEM, messages=[{"role": "user", "content": user}])
            report = "".join(b.text for b in msg.content if b.type == "text").strip()
            model_used = MODEL if report else None
        except Exception:  # noqa: BLE001
            report = None
    if not report:                                      # offline fallback
        report, model_used = _local_report(data), ENGINE_NAME

    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?)",
                [key, report, model_used, generated_at])
    con.commit()
    con.close()
    return {"available": True, "cached": False, "report": report, "model": model_used,
            "generated_at": generated_at, "player": data["name"], "season": data.get("season")}
