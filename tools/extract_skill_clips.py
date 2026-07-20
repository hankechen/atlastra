"""
Cut a per-skill clip for each of the 25 stars from their own highlight reel.

Reads skills+timestamps from the live API (populated by skill_clip_times.py), downloads
each reel once (yt-dlp, progressive mp4), and extracts a ~7s clip around each skill's
timestamp into webapp/frontend/clips/players/<slug>-<i>.mp4. Writes clips_manifest.json
so the server can record each clip path back into the signature-skills cache.

Runs locally (Mac has yt-dlp + ffmpeg; the EC2 IP is bot-blocked by YouTube).
"""
import json
import re
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "webapp" / "frontend" / "clips" / "players"
TMP = Path("/private/tmp/claude-502/-Users-hanke-soccer-analytics/"
           "3760f18c-73b1-41c0-8d97-ea2943094fb9/scratchpad/reels")
API = "https://atlastra.dedyn.io/api/signature_skills?name="

# (primary cache key, [all alias keys]) — primary is what the API/profile uses
STARS = [
    ("Harry Kane", ["Harry Kane"]), ("Michael Olise", ["Michael Olise"]),
    ("Lamine Yamal", ["Lamine Yamal"]),
    ("Kylian Mbappe-Lottin", ["Kylian Mbappe-Lottin", "Kylian Mbappé"]),
    ("Declan Rice", ["Declan Rice"]), ("Ousmane Dembélé", ["Ousmane Dembélé"]),
    ("Luis Díaz", ["Luis Díaz"]), ("Khvicha Kvaratskhelia", ["Khvicha Kvaratskhelia"]),
    ("Bruno Fernandes", ["Bruno Fernandes"]), ("Vitinha", ["Vitinha"]),
    ("Erling Haaland", ["Erling Haaland"]), ("Arda Güler", ["Arda Güler"]),
    ("Pedri", ["Pedri"]), ("Rayan Cherki", ["Rayan Cherki"]), ("Nico Paz", ["Nico Paz"]),
    ("Raphinha", ["Raphinha"]), ("Nuno Mendes", ["Nuno Mendes"]),
    ("Vinícius Júnior", ["Vinícius Júnior", "Vinícius Jr"]),
    ("Julián Álvarez", ["Julián Álvarez"]), ("Joshua Kimmich", ["Joshua Kimmich"]),
    ("Achraf Hakimi", ["Achraf Hakimi"]), ("Yan Diomande", ["Yan Diomande"]),
    ("Antoine Semenyo", ["Antoine Semenyo"]), ("Jude Bellingham", ["Jude Bellingham"]),
    ("João Neves", ["João Neves"]),
]


def slug(name):
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", n.lower())).strip("-")


def fetch(name):
    with urllib.request.urlopen(API + urllib.parse.quote(name), timeout=30) as r:
        return json.load(r)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def download(video_url, dest):
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return True
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "22/18/best[ext=mp4]",
                        "-o", str(dest), video_url], capture_output=True, text=True)
    return dest.exists() and r.returncode == 0


def extract(src, start, out_path):
    subprocess.run(["ffmpeg", "-y", "-ss", str(max(0, start - 1.5)), "-i", str(src),
                    "-t", "7", "-vf", "scale=640:-2", "-an", "-c:v", "libx264",
                    "-preset", "veryfast", "-movflags", "+faststart", str(out_path)],
                   capture_output=True)
    return out_path.exists() and out_path.stat().st_size > 10_000


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for primary, aliases in STARS:
        sg = slug(primary)
        try:
            d = fetch(primary)
        except Exception as e:                            # noqa: BLE001
            print(f"{primary:26s} API ERR {str(e)[:40]}", flush=True)
            continue
        if not d.get("available") or not d.get("video"):
            print(f"{primary:26s} no skills/video", flush=True)
            continue
        reel = TMP / f"{sg}.mp4"
        if not download(d["video"], reel):
            print(f"{primary:26s} DOWNLOAD FAILED", flush=True)
            continue
        dur = duration(reel)
        clips = []
        for i, s in enumerate(d["skills"], 1):
            t = s.get("t")
            if not isinstance(t, (int, float)) or t < 0 or t >= dur - 1:
                clips.append(None)
                continue
            fn = f"{sg}-{i}.mp4"
            ok = extract(reel, float(t), OUT / fn)
            clips.append({"i": i, "skill": s["skill"], "t": t, "file": fn} if ok else None)
        manifest[primary] = {"aliases": aliases, "slug": sg, "clips": clips}
        got = sum(1 for c in clips if c)
        print(f"{primary:26s} {got}/{len(d['skills'])} clips", flush=True)
    (ROOT / "tools" / "clips_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print("\nmanifest -> tools/clips_manifest.json")


if __name__ == "__main__":
    main()
