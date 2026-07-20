"""
Match a player's cached skill labels to their reel's OCR caption timeline and cut a
1080p clip where a caption confidently names that action. No Gemini. Only cuts on a
match (else leaves the skill clip-less), so precision stays high.

Usage: python3 tools/ocr_clip.py "<Player>" <reel_1080.mp4> <captions.json> [--out DIR]
"""
import json
import re
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://atlastra.dedyn.io/api/signature_skills?name="

# distinctive action -> caption keywords (ordered: distinctive actions first)
LEX = [
    ("backheel", ["back heel", "backheel", "back-heel"]),
    ("rabona", ["rabona"]),
    ("nutmeg", ["nutmeg", "megs", "through the legs", "through his legs"]),
    ("panenka", ["panenka"]),
    ("header", ["header", "heads it", "with his head", "headed"]),
    ("volley", ["volley", "volleys"]),
    ("bicycle", ["bicycle", "overhead", "scorpion"]),
    ("free kick", ["free kick", "freekick", "free-kick"]),
    ("penalty", ["penalty", "spot kick", "spot-kick"]),
    ("chip", ["chip", "dink", "lob", "over the keeper", "over the goalkeeper",
              "over the gk", "lifted", "lifts it"]),
    ("trivela", ["trivela", "outside of the boot", "outside of his foot", "outside-of-the-boot"]),
    ("long range", ["from distance", "long range", "long-range", "screamer", "worldie",
                    "thunderbolt", "rocket", "outside the box", "from range", "with precision"]),
    ("stepover", ["stepover", "step over", "step-over"]),
    ("cut inside", ["cut inside", "cuts inside", "cuts in", "cutting inside"]),
    ("chop", ["chop", "chops"]),
    ("solo", ["solo", "slalom", "dribbles past", "runs past", "all alone", "on his own",
              "beats", "dances past", "does it all"]),
    ("tap-in", ["tap in", "tap-in"]),
    ("cutback", ["cutback", "cut back", "cut-back", "pull back", "pulls it back"]),
    ("assist", ["assist", "sets up", "lays it", "teed up"]),
]
GENERIC = {"finish", "strike", "goal", "shot"}


def slug(n):
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", n.lower())).strip("-")


def skill_actions(label):
    """Which LEX actions does this skill label mention?"""
    low = label.lower()
    hits = [act for act, _ in LEX if act.split()[0] in low or act.replace(" ", "") in low.replace(" ", "")]
    # also map common label words
    if "header" in low or "head" in low:
        hits.append("header")
    if "back heel" in low or "backheel" in low:
        hits.append("backheel")
    if "volley" in low:
        hits.append("volley")
    if "chip" in low or "dink" in low:
        hits.append("chip")
    if "free kick" in low or "free-kick" in low:
        hits.append("free kick")
    if "penalty" in low:
        hits.append("penalty")
    if "distance" in low or "long-range" in low or "long range" in low:
        hits.append("long range")
    return list(dict.fromkeys(hits))


def _kw_hit(kw, txt):
    # whole-word match so "lob" doesn't match inside garbled "Solelobtaebadet"
    return re.search(r"\b" + re.escape(kw) + r"\b", txt) is not None


def find_caption(actions, segs, used):
    kw_by_act = dict(LEX)
    for act in actions:                                   # distinctive order preserved by LEX
        kws = kw_by_act.get(act, [])
        for s in segs:
            if s["start"] in used or s["start"] < 1:
                continue
            txt = s["text"].lower()
            # skip garbled captions: need a few real (>=3-letter) word tokens
            if sum(1 for w in re.findall(r"[a-z]+", txt) if len(w) >= 3) < 2:
                continue
            if any(_kw_hit(k, txt) for k in kws):
                return s, act
    return None, None


def cut(reel, start, out, dur=7.0, lead=1.5):
    subprocess.run(["ffmpeg", "-y", "-ss", str(max(0, start - lead)), "-i", str(reel),
                    "-t", str(dur), "-vf", "scale=1920:-2", "-an", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "20", "-movflags", "+faststart", str(out)],
                   capture_output=True)
    return out.exists() and out.stat().st_size > 20_000


def main():
    player, reel, capf = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    outdir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else reel.parent
    outdir.mkdir(parents=True, exist_ok=True)
    d = json.load(urllib.request.urlopen(API + urllib.parse.quote(player), timeout=30))
    skills = d["skills"]
    segs = [s for s in json.load(capf.open()) if len(s["text"]) >= 8]
    sg = slug(player)
    used, results = set(), []
    for i, s in enumerate(skills, 1):
        acts = skill_actions(s["skill"])
        cap, act = find_caption(acts, segs, used) if acts else (None, None)
        if cap:
            used.add(cap["start"])
            fn = f"{sg}-{i}.mp4"
            ok = cut(reel, cap["start"], outdir / fn)
            results.append({"i": i, "skill": s["skill"], "matched": act,
                            "t": cap["start"], "caption": cap["text"], "file": fn if ok else None})
            print(f"  #{i} {s['skill'][:34]:34s} <- [{act}] @{int(cap['start'])//60}:{int(cap['start'])%60:02d}  \"{cap['text']}\"")
        else:
            results.append({"i": i, "skill": s["skill"], "matched": None, "t": None})
            print(f"  #{i} {s['skill'][:34]:34s} <- (no caption match)")
    (outdir / f"{sg}.match.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    got = sum(1 for r in results if r.get("file"))
    print(f"\n{player}: {got}/5 clips cut from captions -> {sg}.match.json")


if __name__ == "__main__":
    main()
