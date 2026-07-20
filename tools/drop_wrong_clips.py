"""
Hide the clips the verifier flagged WRONG: set skill["clip"]=None for each (player,i)
in /tmp/clip_verify.json with match is False, across all alias keys. No Gemini needed.
The profile then falls back to the generic move example (or shows no ▶). Run on server.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp import signature_skills as ss                 # noqa: E402

ALIAS = {"Kylian Mbappe-Lottin": ["Kylian Mbappe-Lottin", "Kylian Mbappé"],
         "Vinícius Júnior": ["Vinícius Júnior", "Vinícius Jr"]}

v = json.loads(Path("/tmp/clip_verify.json").read_text())
wrong = {}
for r in v:
    if r["match"] is False:
        wrong.setdefault(r["player"], set()).add(r["i"])

con = ss._conn()
total = 0
for player, idxs in wrong.items():
    for key in ALIAS.get(player, [player]):
        row = con.execute("SELECT data FROM sig WHERE player=?", [key]).fetchone()
        if not row:
            continue
        skills = json.loads(row[0])
        n = 0
        for i, s in enumerate(skills, 1):
            if i in idxs and s.get("clip"):
                s["clip"] = None
                n += 1
        con.execute("UPDATE sig SET data=? WHERE player=?",
                    [json.dumps(skills, ensure_ascii=False), key])
        total += n
    kept = 5 - len(idxs)
    print(f"{player:24s} dropped {len(idxs)}, kept {kept} good clips")
con.commit()
con.close()
print(f"\nhid {total} wrong clip refs across all aliases")
