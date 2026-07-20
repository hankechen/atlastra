"""
Verify each per-skill clip by having Gemini WATCH the actual cut clip (uploaded via the
Files API) and judge whether it shows that player performing that skill. Cheap because
clips are ~7s. Prints/saves a JSON verdict per clip. Run on the server (needs GEMINI key).

Usage: verify_skill_clips.py [player_or_"all"] [--limit N]
"""
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp import gemini                                 # noqa: E402

KEY = os.environ.get("GEMINI_API_KEY", "")
BASE = "https://generativelanguage.googleapis.com"
CLIPS = Path(__file__).resolve().parent.parent / "webapp" / "frontend" / "clips" / "players"


def upload(path: Path):
    """Resumable upload; returns (file_uri, name) or (None, None)."""
    data = path.read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    start = urllib.request.Request(
        f"{BASE}/upload/v1beta/files?key={KEY}",
        data=json.dumps({"file": {"display_name": path.name}}).encode(),
        headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                 "X-Goog-Upload-Header-Content-Length": str(len(data)),
                 "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(start, timeout=60)
        up = r.headers.get("X-Goog-Upload-URL")
        if not up:
            return None, None
        put = urllib.request.Request(up, data=data, method="POST",
                                     headers={"X-Goog-Upload-Offset": "0",
                                              "X-Goog-Upload-Command": "upload, finalize",
                                              "Content-Length": str(len(data))})
        info = json.load(urllib.request.urlopen(put, timeout=120))["file"]
        name, uri, state = info["name"], info["uri"], info.get("state")
        for _ in range(20):                               # wait until ACTIVE
            if state == "ACTIVE":
                return uri, name
            if state == "FAILED":
                return None, None
            time.sleep(1.5)
            g = json.load(urllib.request.urlopen(f"{BASE}/v1beta/{name}?key={KEY}", timeout=30))
            state = g.get("state")
        return (uri, name) if state == "ACTIVE" else (None, None)
    except Exception as e:                                # noqa: BLE001
        print("  upload err", str(e)[:80], flush=True)
        return None, None


def delete(name):
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{BASE}/v1beta/{name}?key={KEY}", method="DELETE"), timeout=30)
    except Exception:                                     # noqa: BLE001
        pass


def verify(uri, mime, player, skill):
    prompt = (
        f"This is a short football clip. Question: does it clearly show {player} performing "
        f"the skill/action \"{skill}\"? Judge strictly — the specific action must actually "
        f"happen in this clip, not just the player being present. Reply JSON only: "
        f'{{"match": true/false, "saw": "<=12 words on what actually happens"}}.')
    parts = [{"text": prompt}, {"fileData": {"fileUri": uri, "mimeType": mime}}]
    raw = gemini._post(parts, temperature=0.1)
    j = gemini.extract_json(raw)
    return j if isinstance(j, dict) else {"match": None, "saw": "verify failed"}


def check_one(primary, c):
    path = CLIPS / c["file"]
    if not path.exists():
        return {"player": primary, "i": c["i"], "skill": c["skill"],
                "file": c["file"], "match": None, "saw": "missing file"}
    uri, name = upload(path)
    if not uri:
        res = {"match": None, "saw": "upload failed"}
    else:
        res = verify(uri, "video/mp4", primary, c["skill"])
        delete(name)
    rec = {"player": primary, "i": c["i"], "skill": c["skill"],
           "file": c["file"], "match": res.get("match"), "saw": res.get("saw")}
    flag = {True: "OK  ", False: "WRONG", None: "?   "}.get(rec["match"], "?")
    print(f"{flag} {primary:22s} {c['skill'][:34]:34s} | {rec['saw']}", flush=True)
    return rec


def main():
    from concurrent.futures import ThreadPoolExecutor
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 9999
    manifest = json.loads((Path(__file__).resolve().parent / "clips_manifest.json").read_text())
    tasks = []
    for primary, info in manifest.items():
        if which != "all" and which.lower() not in primary.lower():
            continue
        for c in info["clips"]:
            if c:
                tasks.append((primary, c))
    tasks = tasks[:limit]
    with ThreadPoolExecutor(max_workers=6) as pool:
        out = list(pool.map(lambda t: check_one(*t), tasks))
    Path("/tmp/clip_verify.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    bad = [r for r in out if r["match"] is False]
    print(f"\n{len(out)} checked, {len(bad)} WRONG -> /tmp/clip_verify.json")


if __name__ == "__main__":
    main()
