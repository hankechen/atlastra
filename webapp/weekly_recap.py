"""
Week in Review — an AI-written recap of the week's football: top performers, best
goals and notable results, generated from Atlastra's FotMob-sourced data via Claude.

Server-side only. Reads ANTHROPIC_API_KEY from the environment (standard SDK auth);
with no key it falls back to a deterministic local write-up. Cached per ISO week in
a small sqlite file so repeat views don't re-spend tokens — pass refresh=True to
regenerate.
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
ENGINE_NAME = "Atlastra recap engine"
CACHE_PATH = DATA_DIR / "weekly_recap.sqlite"

SYSTEM = """You are a sharp football (soccer) writer producing a weekly round-up for a \
stats-savvy audience. Write with energy and authority, grounded entirely in the data given.

Rules:
- Use ONLY the data provided (results, player ratings, goals with xG). Cite specifics \
naturally — ratings ("a 9.4 match rating"), scorelines, xG ("an xG of just 0.04").
- Do NOT invent anything not in the data: no quotes, no injuries, no transfer talk, no \
matches or players not listed, no table positions.
- "Rating" is FotMob's 0–10 match rating; "xG" is expected goals (lower = more \
spectacular finish). A "worldie" flag means a low-xG wondergoal.
- British football terminology. Confident, vivid, but accurate.

Output GitHub-flavoured Markdown, ~250–400 words, with exactly these sections:
## ⚽ The Week in Football
2–3 sentences setting the scene — the headline storylines from the results.

## 🌟 Standout Performers
3–4 players, each a sentence or two tying their rating + goals/assists to the match. \
Lead with the best.

## 🔥 Goals of the Week
2–3 of the best goals, noting the finish/xG where it stands out.

Keep it tight and readable. No preamble, nothing outside these sections."""


def _iso_week():
    y, w, _ = datetime.date.today().isocalendar()
    return f"{y}-W{w:02d}"


def _conn():
    con = sqlite3.connect(str(CACHE_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS recaps "
                "(week TEXT PRIMARY KEY, recap TEXT, model TEXT, generated_at TEXT)")
    return con


def _local_recap(data: dict) -> str:
    """Deterministic write-up when no Claude key is configured."""
    perf = data.get("performers") or []
    goals = data.get("goals") or []
    results = data.get("results") or []
    lines = ["## ⚽ The Week in Football"]
    if results:
        r = results[0]
        big = ", ".join(f"{x['home']} {x['home_score']}–{x['away_score']} {x['away']}"
                        for x in results[:3])
        lines.append(f"{len(results)} matches across the covered competitions this week. "
                     f"Among the standout results: {big}.")
    lines.append("\n## 🌟 Standout Performers")
    for p in perf[:4]:
        ga = []
        if p.get("goals"):
            ga.append(f"{p['goals']} goal{'s' if p['goals'] != 1 else ''}")
        if p.get("assists"):
            ga.append(f"{p['assists']} assist{'s' if p['assists'] != 1 else ''}")
        gastr = (" with " + " and ".join(ga)) if ga else ""
        potm = " — Player of the Match" if p.get("potm") else ""
        lines.append(f"- **{p['name']}** ({p['team']}) earned a **{p['rating']}** rating "
                     f"against {p['opponent']}{gastr}{potm}.")
    lines.append("\n## 🔥 Goals of the Week")
    for g in goals[:3]:
        wow = " — a stunning low-xG strike" if g.get("worldie") else ""
        xg = f" (xG {g['xg']})" if g.get("xg") is not None else ""
        a = (g.get("assist") or "").strip()
        a = a[len("assist by "):].strip() if a.lower().startswith("assist by") else a
        assist = f", set up by {a}" if a else ""
        lines.append(f"- **{g['scorer']}** for {g['team']} vs {g['opponent']} "
                     f"({g['minute']}'){xg}{wow}{assist}.")
    return "\n".join(lines)


def generate(data: dict, refresh: bool = False) -> dict:
    if not data or not (data.get("performers") or data.get("results")):
        return {"available": False, "error": "No matches this week yet."}
    week = _iso_week()
    con = _conn()
    if not refresh:
        row = con.execute("SELECT recap, model, generated_at FROM recaps WHERE week = ?",
                          [week]).fetchone()
        if row:
            con.close()
            return {"available": True, "cached": True, "recap": row[0], "model": row[1],
                    "generated_at": row[2], "week": week}

    user = "Write the weekly round-up from this data (JSON):\n\n" + json.dumps(data, ensure_ascii=False)
    recap, model_used = None, None
    from webapp import gemini
    if gemini.available():                               # preferred: Gemini
        recap = gemini.generate(SYSTEM + "\n\n---\n\n" + user, temperature=0.5)
        model_used = "gemini-flash" if recap else None
    if not recap and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        try:
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=MODEL, max_tokens=3000, thinking={"type": "adaptive"},
                system=SYSTEM, messages=[{"role": "user", "content": user}])
            recap = "".join(b.text for b in msg.content if b.type == "text").strip()
            model_used = MODEL if recap else None
        except Exception:  # noqa: BLE001
            recap = None
    if not recap:                                        # offline fallback
        recap, model_used = _local_recap(data), ENGINE_NAME

    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO recaps VALUES (?,?,?,?)",
                [week, recap, model_used, generated_at])
    con.commit()
    con.close()
    return {"available": True, "cached": False, "recap": recap, "model": model_used,
            "generated_at": generated_at, "week": week}
