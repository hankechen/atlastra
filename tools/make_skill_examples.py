"""
Build the per-skill example reels from the BEST-KNOWN practitioners of each move.

For each skill: search YouTube for that player's skill video, download it, detect
clean shots (frame-diff), pick a few good ones, and ffmpeg-stitch them into a short
reel at webapp/frontend/clips/<skill>.mp4. Uses ffmpeg for cut+concat (reliable,
no dropped frames) and progressive H.264 for OpenCV-decodable shot detection.

Run:  ml/../tools:  ./venv/bin/python tools/make_skill_examples.py [skill1,skill2]
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp.live_feed_fotmob import _yt_search   # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "webapp" / "frontend" / "clips"
YDL_FMT = "18/22/b[ext=mp4][vcodec^=avc][acodec!=none]"   # progressive H.264 (cv2-decodable)
W, H, N_SHOTS = 640, 360, 4

# skill -> a search targeting the iconic/best modern practitioner of that move
QUERIES = {
    "stepover": "Cristiano Ronaldo stepover skills compilation",
    "elastico": "elastico flip flap skills compilation football",
    "la_croqueta": "Iniesta la croqueta compilation",
    "roulette": "Zidane roulette marseille turn compilation",
    "cruyff_turn": "cruyff turn goals skills compilation football",
    "nutmeg": "Neymar nutmegs skills compilation",
    "body_feint": "best body feints skills compilation football",
    "chop": "Robben cut inside compilation",
    "ball_roll": "best sole roll skills compilation football",
    "rabona": "Angel Di Maria rabona skills",
    "rainbow_flick": "Neymar rainbow flick compilation",
    "sombrero": "Ronaldinho sombrero flick skills",
    "backheel": "best backheel goals compilation football",
    "fake_shot": "Neymar fake shot skill compilation",
    "chip": "Messi chip lob goals compilation",
    "trivela": "Ricardo Quaresma trivela compilation",
    "curler": "best finesse curl top corner goals compilation",
    "volley": "Cristiano Ronaldo volley goals compilation",
    "knuckleball": "Cristiano Ronaldo knuckleball free kicks",
}


def download(vid, dest):
    import yt_dlp
    opts = {"format": YDL_FMT, "outtmpl": str(dest), "quiet": True, "no_warnings": True,
            "noprogress": True, "retries": 2}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={vid}"])


def shots(path, cut_thresh=22.0):
    """(start_sec, end_sec) of hard-cut shots, via downscaled frame differencing."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    out, prev, start, i = [], None, 0, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        small = cv2.cvtColor(cv2.resize(fr, (32, 18)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None and float(np.abs(small - prev).mean()) > cut_thresh:
            out.append((start / fps, i / fps))
            start = i
        prev, i = small, i + 1
    out.append((start / fps, i / fps))
    cap.release()
    return out


def good_shot(path, s0, s1):
    """Keep 1.2-3s shots whose mid-frame isn't a title card / near-black."""
    dur = s1 - s0
    if dur < 0.6 or dur > 4.0:
        return False
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int((s0 + dur / 2) * fps))
    ok, fr = cap.read()
    cap.release()
    if not ok:
        return False
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    return g.std() >= 28 and (g < 30).mean() <= 0.4


def extract(path, a, b, out_path):
    """ffmpeg: one continuous segment [a,b], scaled/padded to 640x360 (no concat)."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}",
           "-i", str(path), "-vf",
           f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
           f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
           "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "24",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True)


def build(skill, query):
    import time
    vids = _yt_search(query, want=4)
    if not vids:
        return f"{skill}: no video"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.mp4"
        ok = False
        for vid in vids:                                 # try results until one downloads
            for _ in range(2):
                try:
                    download(vid, src)
                    ok = True
                    break
                except Exception:                        # noqa: BLE001
                    time.sleep(3)
            if ok:
                break
        if not ok or not src.exists():
            return f"{skill}: download failed"
        sl = [(a, b) for a, b in shots(str(src)) if a > 3 and good_shot(str(src), a, b)]  # skip intro
        if not sl:
            return f"{skill}: no good shots"
        # ONE full play: the longest clean single shot (a continuous take, so the move
        # isn't cut off mid-action). If it's short, let it run on a little past the cut
        # (the comps' slow-mo replay) so it's a satisfying length — still one clip.
        a, b = max(sl, key=lambda s: s[1] - s[0])
        b = min(max(b, a + 3.5), a + 6.0)                # 3.5-6s, single continuous window
        extract(src, max(0, a - 0.2), b, OUT / f"{skill}.mp4")
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(OUT / f"{skill}.mp4")],
                             capture_output=True, text=True).stdout.strip()
        return f"{skill}: {vid} -> 1 clip, {float(dur):.1f}s"


def main():
    only = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
    for skill, query in QUERIES.items():
        if only and skill not in only:
            continue
        print("  " + build(skill, query), flush=True)


if __name__ == "__main__":
    main()
