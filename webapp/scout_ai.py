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

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        con.close()
        return {"available": False, "error": "No Claude API key configured. Set "
                "ANTHROPIC_API_KEY in the server's environment, then restart the server."}

    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=MODEL, max_tokens=4000,
            thinking={"type": "adaptive"},
            system=SYSTEM,
            messages=[{"role": "user", "content":
                       "Write the scouting report from this player's data (JSON):\n\n"
                       + json.dumps(_summary(data), ensure_ascii=False)}],
        )
        report = "".join(b.text for b in msg.content if b.type == "text").strip()
        if not report:
            con.close()
            return {"available": False, "error": "The model returned an empty report; try again."}
    except Exception as e:  # noqa: BLE001 -- surface a friendly message to the UI
        con.close()
        name = type(e).__name__
        if "Authentication" in name:
            return {"available": False, "error": "Claude API authentication failed — check ANTHROPIC_API_KEY."}
        if "RateLimit" in name:
            return {"available": False, "error": "Claude API rate limited — wait a moment and retry."}
        return {"available": False, "error": f"Report generation failed ({name})."}

    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?)",
                [key, report, MODEL, generated_at])
    con.commit()
    con.close()
    return {"available": True, "cached": False, "report": report, "model": MODEL,
            "generated_at": generated_at, "player": data["name"], "season": data.get("season")}
