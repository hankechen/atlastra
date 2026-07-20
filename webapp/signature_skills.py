"""
Signature Skills — Gemini watches a player's YouTube skills reel and returns their
top signature moves, ranked. Holistic (whole reel in context), so it sidesteps the
per-clip label-noise problem. Cached per player in sqlite so we pay Gemini once.
"""
import datetime
import json
import sqlite3
import sys
from pathlib import Path

try:
    from config import DATA_DIR
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DATA_DIR

from webapp import gemini

CACHE_PATH = DATA_DIR / "signature_skills.sqlite"
SKILLS = ("stepover, elastico, la croqueta, roulette, cruyff turn, nutmeg, body feint, "
          "chop/cut inside, ball roll, rabona, rainbow flick, sombrero, backheel, "
          "fake shot, chip/dink, trivela, curler, volley, knuckleball")


def _conn():
    con = sqlite3.connect(str(CACHE_PATH))
    con.execute("CREATE TABLE IF NOT EXISTS sig "
                "(player TEXT PRIMARY KEY, data TEXT, video TEXT, generated_at TEXT)")
    return con


def cached_players() -> list:
    """Names of players whose highlight reel has been analysed (i.e. have highlights)."""
    try:
        con = _conn()
        names = [r[0] for r in con.execute("SELECT player FROM sig").fetchall()]
        con.close()
        return names
    except Exception:                                    # noqa: BLE001
        return []


def generate(name: str, youtube_url: str | None, refresh: bool = False) -> dict:
    name = (name or "").strip()
    if not name:
        return {"available": False}
    con = _conn()
    if not refresh:
        row = con.execute("SELECT data, video, generated_at FROM sig WHERE player = ?",
                          [name]).fetchone()
        if row:
            con.close()
            return {"available": True, "cached": True, "player": name,
                    "skills": json.loads(row[0]), "video": row[1], "generated_at": row[2]}
    if not youtube_url or not gemini.available():
        con.close()
        return {"available": False, "player": name}

    prompt = (
        f"This is a football (soccer) skills/highlights compilation of {name}. Watch it and "
        f"identify their TOP 5 signature skills AS ACTUALLY SHOWN, ranked most characteristic "
        f"first. Each \"skill\" MUST be SPECIFIC and CONCRETE — name the actual technique, the "
        f"body mechanic, or the exact game situation you see. Do NOT use vague adjective+noun "
        f"labels. "
        f"BAD (too generic, never output these): \"Composed 1v1 Finishing\", \"Explosive 1v1 "
        f"Dribbling\", \"Powerful Ball Carrying\", \"Tight Space Escapes\", \"Clinical "
        f"Finishing\". "
        f"GOOD (specific): \"La Croqueta to Beat the Press\", \"Knock-Past the Fullback Down "
        f"the Line\", \"Trivela Through Ball\", \"Chop Inside Onto Right Foot\", \"Drag-Back "
        f"Turn Out of Pressure\", \"Near-Post Toe-Poke Finish\", \"First-Time Cross-Field "
        f"Switch\", \"Stepover Into Shot\". "
        f"Be specific to {name} and their position — include on-ball moves AND off-ball "
        f"qualities (runs, positioning, reading the game) where shown, but always name them "
        f"concretely. Reply as a compact JSON array of exactly 5 objects with keys \"skill\" "
        f"(a specific 3-6 word Title Case name) and \"note\" (a concrete 6-12 word observation "
        f"of how they use it in this video). No prose, JSON only.")
    raw = gemini.analyze_youtube(youtube_url, prompt)
    data = gemini.extract_json(raw)
    if not isinstance(data, list) or not data:
        con.close()
        return {"available": False, "player": name}
    skills = [{"skill": str(x.get("skill", "")).strip(), "note": str(x.get("note", "")).strip()}
              for x in data if isinstance(x, dict) and x.get("skill")][:5]
    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT OR REPLACE INTO sig VALUES (?,?,?,?)",
                [name, json.dumps(skills, ensure_ascii=False), youtube_url, generated_at])
    con.commit()
    con.close()
    return {"available": True, "cached": False, "player": name,
            "skills": skills, "video": youtube_url, "generated_at": generated_at}
