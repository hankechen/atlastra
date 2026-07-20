"""
Verify the re-cut clips (from recut.json, new_t set) by having Gemini watch each and judge
if it shows that player doing that skill. Then link the passers into the cache and keep the
failers hidden. Free-tier friendly (2 workers). Run on the server.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import verify_skill_clips as V                            # noqa: E402
from webapp import signature_skills as ss                 # noqa: E402

ALIAS = {"Kylian Mbappe-Lottin": ["Kylian Mbappe-Lottin", "Kylian Mbappé"],
         "Vinícius Júnior": ["Vinícius Júnior", "Vinícius Jr"]}

recut = json.loads(Path("/opt/atlastra/tools/recut.json").read_text())
todo = [r for r in recut if r.get("new_t") is not None]


def check(r):
    path = V.CLIPS / r["file"]
    if not path.exists():
        return {**r, "match": None, "saw": "missing"}
    uri, name = V.upload(path)
    if not uri:
        return {**r, "match": None, "saw": "upload failed"}
    res = V.verify(uri, "video/mp4", r["player"], r["skill"])
    V.delete(name)
    m = res.get("match")
    print(f"{'OK  ' if m else ('WRONG' if m is False else '?')} {r['player']:22s} #{r['i']} {r['skill'][:30]:30s} | {res.get('saw')}", flush=True)
    return {**r, "match": m, "saw": res.get("saw")}


def main():
    with ThreadPoolExecutor(max_workers=1) as pool:
        out = list(pool.map(check, todo))
    # link passers, keep failers hidden
    con = ss._conn()
    linked = 0
    for r in out:
        if r.get("match") is not True:
            continue
        for key in ALIAS.get(r["player"], [r["player"]]):
            row = con.execute("SELECT data FROM sig WHERE player=?", [key]).fetchone()
            if not row:
                continue
            sk = json.loads(row[0])
            if r["i"] - 1 < len(sk):
                sk[r["i"] - 1]["clip"] = f"/clips/players/{r['file']}"
            con.execute("UPDATE sig SET data=? WHERE player=?", [json.dumps(sk, ensure_ascii=False), key])
        linked += 1
    con.commit()
    con.close()
    good = sum(1 for r in out if r.get("match") is True)
    print(f"\n{good}/{len(out)} passed and linked ({linked} rows). Failers stay hidden.")
    Path("/tmp/reverify.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
