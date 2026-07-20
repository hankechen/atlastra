"""
Record each extracted per-skill clip path back into the signature-skills cache.
Reads tools/clips_manifest.json (from extract_skill_clips.py) and sets skill["clip"]
= "/clips/players/<file>" for every alias key. Run on the server after the clips are
scp'd into webapp/frontend/clips/players/.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp import signature_skills as ss                 # noqa: E402

manifest = json.loads((Path(__file__).resolve().parent / "clips_manifest.json").read_text())
con = ss._conn()
for primary, info in manifest.items():
    by_i = {c["i"]: c for c in info["clips"] if c}
    for key in info["aliases"]:
        row = con.execute("SELECT data FROM sig WHERE player=?", [key]).fetchone()
        if not row:
            continue
        skills = json.loads(row[0])
        for i, s in enumerate(skills, 1):
            c = by_i.get(i)
            s["clip"] = f"/clips/players/{c['file']}" if c else None
        con.execute("UPDATE sig SET data=? WHERE player=?",
                    [json.dumps(skills, ensure_ascii=False), key])
        n = sum(1 for s in skills if s.get("clip"))
        print(f"{key:26s} {n} clips linked", flush=True)
con.commit()
con.close()
