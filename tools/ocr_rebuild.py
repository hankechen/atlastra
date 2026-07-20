"""
Rebuild per-skill clips for all 25 stars at 1080p, no Gemini: download each reel at
1080p, OCR its captions, keyword-match each cached skill to a caption, and cut a clip
only where a caption confidently names the action. Writes clips to
webapp/frontend/clips/players/ (1080p), a rebuild_manifest.json, and one verification
frame per clip into scratchpad/ocr/verify/ for eyeball review.

Runs locally (Mac: yt-dlp + tesseract + ffmpeg).
"""
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ocr_reel                                            # noqa: E402
import ocr_clip                                            # noqa: E402

OUT = ROOT / "webapp" / "frontend" / "clips" / "players"
SCR = Path("/private/tmp/claude-502/-Users-hanke-soccer-analytics/"
           "3760f18c-73b1-41c0-8d97-ea2943094fb9/scratchpad/ocr")
REELS, VERIFY = SCR / "reels1080", SCR / "verify"
API = "https://atlastra.dedyn.io/api/signature_skills?name="

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


def fetch(name):
    with urllib.request.urlopen(API + urllib.parse.quote(name), timeout=30) as r:
        return json.load(r)


def download(url, dest):
    if dest.exists() and dest.stat().st_size > 2_000_000:
        return True
    subprocess.run([sys.executable, "-m", "yt_dlp", "-f",
                    "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
                    "-o", str(dest), url], capture_output=True)
    return dest.exists()


def frame(clip, out):
    subprocess.run(["ffmpeg", "-y", "-ss", "2.5", "-i", str(clip), "-frames:v", "1",
                    "-vf", "scale=420:-2", str(out)], capture_output=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    REELS.mkdir(parents=True, exist_ok=True)
    VERIFY.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for primary, aliases in STARS:
        sg = ocr_clip.slug(primary)
        try:
            d = fetch(primary)
        except Exception as e:                            # noqa: BLE001
            print(f"{primary:24s} API ERR {str(e)[:40]}", flush=True)
            continue
        skills, url = d.get("skills", []), d.get("video")
        if not url:
            print(f"{primary:24s} no reel", flush=True)
            continue
        reel = REELS / f"{sg}.mp4"
        if not download(url, reel):
            print(f"{primary:24s} DOWNLOAD FAILED", flush=True)
            continue
        capf = REELS / f"{sg}.captions.json"
        if capf.exists():
            segs = json.loads(capf.read_text())
        else:
            segs = ocr_reel.scan(reel)
            capf.write_text(json.dumps(segs, ensure_ascii=False))
        segs = [s for s in segs if len(s["text"]) >= 8]
        used, clips = set(), []
        for i, s in enumerate(skills, 1):
            acts = ocr_clip.skill_actions(s["skill"])
            cap, act = ocr_clip.find_caption(acts, segs, used) if acts else (None, None)
            if cap:
                used.add(cap["start"])
                fn = f"{sg}-{i}.mp4"
                ok = ocr_clip.cut(reel, cap["start"], OUT / fn)
                if ok:
                    frame(OUT / fn, VERIFY / f"{sg}-{i}.jpg")
                clips.append({"i": i, "skill": s["skill"], "matched": act, "t": cap["start"],
                              "caption": cap["text"], "file": fn if ok else None})
            else:
                clips.append({"i": i, "skill": s["skill"], "matched": None, "file": None})
        manifest[primary] = {"aliases": aliases, "slug": sg,
                             "captions": len(segs), "clips": clips}
        got = sum(1 for c in clips if c.get("file"))
        print(f"{primary:24s} {got}/{len(skills)} clips  ({len(segs)} captions)", flush=True)
    (ROOT / "tools" / "rebuild_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))
    tot = sum(sum(1 for c in v["clips"] if c.get("file")) for v in manifest.values())
    print(f"\nTOTAL {tot} clips cut -> tools/rebuild_manifest.json ; frames in {VERIFY}")


if __name__ == "__main__":
    main()
