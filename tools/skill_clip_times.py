"""
Ask Gemini for the timestamp of each cached signature skill in the player's reel, so we
can cut a per-skill clip from that same video. Reads the existing sig cache (keeps the
skill names/notes stable), adds a "t" (start seconds) to each skill row. Run on the server.

Usage: python3 tools/skill_clip_times.py ["Player Name"]   (no arg = all 25 stars)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp import gemini                                 # noqa: E402
from webapp import live_feed_fotmob as lf                 # noqa: E402
from webapp import signature_skills as ss                 # noqa: E402

ALIAS = {
    "Kylian Mbappé": ["Kylian Mbappe-Lottin", "Kylian Mbappé"],
    "Vinícius Jr":   ["Vinícius Júnior", "Vinícius Jr"],
}


def _secs(t):
    """Parse '1:23' / '83' / 83 -> int seconds, or None."""
    if isinstance(t, (int, float)):
        return int(t)
    s = str(t or "").strip()
    if ":" in s:
        try:
            m, sec = s.split(":")[-2:]
            return int(m) * 60 + int(float(sec))
        except Exception:                                 # noqa: BLE001
            return None
    try:
        return int(float(s))
    except Exception:                                     # noqa: BLE001
        return None


def process(keys):
    con = ss._conn()
    row = con.execute("SELECT data, video FROM sig WHERE player=?", [keys[0]]).fetchone()
    if not row:
        con.close()
        return keys[0], "NOT CACHED"
    skills, video = json.loads(row[0]), row[1]
    names = [s["skill"] for s in skills]
    prompt = (
        f"This is a skills/highlights compilation of the footballer {keys[0]}. For EACH skill "
        f"below, give the timestamp of the SINGLE clearest moment in THIS video where he "
        f"performs it — the start of that specific play. Skills: {json.dumps(names)}. "
        f"Reply as a JSON array of objects with keys \"skill\" (exactly as given) and \"time\" "
        f"(mm:ss of the start of that play in this video). JSON only, no prose.")
    raw = gemini.analyze_youtube(video, prompt)
    data = gemini.extract_json(raw)
    if not isinstance(data, list):
        con.close()
        return keys[0], "GEMINI FAILED"
    tmap = {str(d.get("skill", "")).strip(): _secs(d.get("time")) for d in data if isinstance(d, dict)}
    for s in skills:
        s["t"] = tmap.get(s["skill"])
    payload = json.dumps(skills, ensure_ascii=False)
    for k in keys:
        con.execute("UPDATE sig SET data=? WHERE player=?", [payload, k])
    con.commit()
    con.close()
    return keys[0], " | ".join(f"{s['skill']}@{s.get('t')}" for s in skills)


def main():
    if len(sys.argv) > 1:
        name = sys.argv[1]
        keys = ALIAS.get(name, [name])
        # if they passed a canonical/alias name directly, find its group
        for star, al in ALIAS.items():
            if name in al:
                keys = al
        print("%s -> %s" % process(keys))
        return
    for star, club, _pos in lf._STARS:
        keys = ALIAS.get(star, [star])
        who, msg = process(keys)
        print(f"{who:26s} {msg}", flush=True)


if __name__ == "__main__":
    main()
