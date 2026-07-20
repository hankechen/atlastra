"""
Re-cut the clips that got a corrected timestamp from recut_wrong.py, at 1080p, from the
player's reel. Reads tools/recut.json, ensures a 1080p reel is downloaded, extracts a 7s
1080p clip at the new timestamp, overwrites webapp/frontend/clips/players/<file>, and
writes tools/reextract_manifest.json (player, i, file, new_t) for verify+merge.

Runs locally (Mac). Uses the reel URL from the live API (cache).
"""
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "webapp" / "frontend" / "clips" / "players"
REELS = Path("/private/tmp/claude-502/-Users-hanke-soccer-analytics/"
             "3760f18c-73b1-41c0-8d97-ea2943094fb9/scratchpad/ocr/reels1080")
API = "https://atlastra.dedyn.io/api/signature_skills?name="

# canonical primary -> slug already encoded in file names (e.g. kylian-mbappe-lottin-2.mp4)


def reel_url(player):
    with urllib.request.urlopen(API + urllib.parse.quote(player), timeout=30) as r:
        return json.load(r).get("video")


def download(url, dest):
    if dest.exists() and dest.stat().st_size > 2_000_000:
        return True
    subprocess.run([sys.executable, "-m", "yt_dlp", "-f",
                    "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
                    "-o", str(dest), url], capture_output=True)
    return dest.exists()


def cut(reel, start, out, dur=7.0, lead=1.5):
    subprocess.run(["ffmpeg", "-y", "-ss", str(max(0, start - lead)), "-i", str(reel),
                    "-t", str(dur), "-vf", "scale=1920:-2", "-an", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "20", "-movflags", "+faststart", str(out)],
                   capture_output=True)
    return out.exists() and out.stat().st_size > 20_000


def main():
    REELS.mkdir(parents=True, exist_ok=True)
    recut = json.loads((ROOT / "tools" / "recut.json").read_text())
    todo = [r for r in recut if r.get("new_t") is not None]
    # group by player so we download each reel once
    reels = {}
    out = []
    for r in todo:
        player, i, fn, t = r["player"], r["i"], r["file"], r["new_t"]
        sg = fn.rsplit("-", 1)[0]
        if player not in reels:
            url = reel_url(player)
            dest = REELS / f"{sg}.mp4"
            reels[player] = dest if (url and download(url, dest)) else None
        reel = reels[player]
        if not reel:
            print(f"{player:22s} #{i}  reel unavailable", flush=True)
            continue
        ok = cut(reel, float(t), OUT / fn)
        out.append({"player": player, "i": i, "file": fn, "new_t": t, "ok": ok})
        print(f"{player:22s} #{i}  t={t}  {'cut ✓' if ok else 'FAILED'}", flush=True)
    (ROOT / "tools" / "reextract_manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n{sum(1 for r in out if r['ok'])}/{len(todo)} re-cut at 1080p -> reextract_manifest.json")


if __name__ == "__main__":
    main()
