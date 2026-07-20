"""
Prewarm the free-form Signature Skills for the curated top-25 stars.

For each star we find their best recent skills reel (same YouTube search top_stars()
uses, with club disambiguation for the ambiguous names), run Gemini ONCE, and store
the result under every name alias the profile might key on (so the profile's p.name
always hits the cache). Run on the server, where GEMINI_API_KEY lives.
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp import live_feed_fotmob as lf          # noqa: E402
from webapp import signature_skills as ss           # noqa: E402

# star name -> extra alias keys the profile may use (canonical p.name first)
ALIAS = {
    "Kylian Mbappé": ["Kylian Mbappe-Lottin", "Kylian Mbappé"],
    "Vinícius Jr":   ["Vinícius Júnior", "Vinícius Jr"],
}


def do(item):
    name, club, _pos = item
    keys = ALIAS.get(name, [name])
    qclub = club if name in lf._STARS_QUALIFY else None
    v = lf._skills_video(name, qclub)
    if not v:
        return name, "NO VIDEO"
    url = f"https://youtu.be/{v['id']}"
    # Gemini once, stored under the primary (canonical) key
    res = ss.generate(keys[0], url, refresh=True)
    if not res.get("available"):
        return name, "GEMINI FAILED (cap?)"
    skills = res["skills"]
    # copy the same row under any additional aliases (no extra Gemini call)
    if len(keys) > 1:
        import datetime
        import json
        con = ss._conn()
        gen = datetime.datetime.now().isoformat(timespec="seconds")
        for k in keys[1:]:
            con.execute("INSERT OR REPLACE INTO sig VALUES (?,?,?,?)",
                        [k, json.dumps(skills, ensure_ascii=False), url, gen])
        con.commit()
        con.close()
    top = " | ".join(s["skill"] for s in skills)
    return name, f"OK [{', '.join(keys)}] -> {top}"


def main():
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, msg in pool.map(do, lf._STARS):
            print(f"{name:26s} {msg}", flush=True)


if __name__ == "__main__":
    main()
