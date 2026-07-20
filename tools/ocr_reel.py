"""
OCR a skills reel's on-screen captions into a timeline of (time -> caption text).
Extracts frames at 1 fps, crops to the lower caption band, runs tesseract, de-noises
persistent watermark lines, and groups consecutive similar captions into segments.

Usage: python3 tools/ocr_reel.py <video.mp4> [--fps 1] [--band 0.28]
Prints segments as "mm:ss-mm:ss  caption" and writes <video>.captions.json
"""
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


def extract_frames(video, outdir, fps, band):
    # crop bottom `band` fraction, scale to width 960, one jpg per sampled frame
    vf = f"fps={fps},crop=iw:ih*{band}:0:ih*(1-{band}),scale=960:-2"
    subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vf", vf, "-q:v", "3",
                    str(Path(outdir) / "f_%05d.jpg")], capture_output=True)
    return sorted(Path(outdir).glob("f_*.jpg"))


def ocr(path):
    r = subprocess.run(["tesseract", str(path), "-", "--psm", "6"],
                       capture_output=True, text=True)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in r.stdout.splitlines()]
    return [ln for ln in lines if len(ln) >= 4]


def scan(video, fps=1.0, band=0.30):
    """Full OCR pass -> list of caption segments [{start,end,text}]. Parallel tesseract."""
    from concurrent.futures import ProcessPoolExecutor
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        frames = extract_frames(video, td, fps, band)
        with ProcessPoolExecutor() as pool:
            per_frame = list(pool.map(ocr, [str(f) for f in frames]))
    rows = []
    for k, lines in enumerate(per_frame):
        t = k / fps
        for ln in lines:
            c = clean(ln)
            if c:
                rows.append((t, c))
    line_counts = Counter(c.lower() for _, c in rows if c)
    n_frames = max(1, len(per_frame))
    persistent = {ln for ln, n in line_counts.items()
                  if n > 0.25 * n_frames and len(ln) <= 14}
    kept = [(t, c) for t, c in rows if c and c.lower() not in persistent and len(c) >= 6]

    def sig(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())[:24]
    segs = []
    for t, c in kept:
        if segs and abs(t - segs[-1]["end"]) <= 2.5 and sig(c)[:10] == sig(segs[-1]["text"])[:10]:
            segs[-1]["end"] = t
            if len(c) > len(segs[-1]["text"]):
                segs[-1]["text"] = c
        else:
            segs.append({"start": t, "end": t, "text": c})
    return [s for s in segs if len(s["text"]) >= 8]


def clean(text):
    t = re.sub(r"[^a-zA-Z0-9 '&!.-]", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def main():
    video = Path(sys.argv[1])
    fps = float(sys.argv[sys.argv.index("--fps") + 1]) if "--fps" in sys.argv else 1.0
    band = float(sys.argv[sys.argv.index("--band") + 1]) if "--band" in sys.argv else 0.28
    with tempfile.TemporaryDirectory() as td:
        frames = extract_frames(video, td, fps, band)
        rows = []                                         # (t_seconds, cleaned_text)
        for k, f in enumerate(frames):
            t = k / fps
            best = ""
            for ln in ocr(f):
                c = clean(ln)
                if len(c) > len(best):
                    best = c
                rows.append((t, c))
    # drop watermark/persistent lines: any short token-line appearing in >25% of frames
    line_counts = Counter(c.lower() for _, c in rows if c)
    n_frames = max(1, len(frames))
    persistent = {ln for ln, n in line_counts.items()
                  if n > 0.25 * n_frames and len(ln) <= 14}
    kept = [(t, c) for t, c in rows if c and c.lower() not in persistent and len(c) >= 6]

    # group consecutive kept captions with similar text into segments
    def sig(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())[:24]
    segs = []
    for t, c in kept:
        if segs and abs(t - segs[-1]["end"]) <= 2.5 and sig(c)[:10] == sig(segs[-1]["text"])[:10]:
            segs[-1]["end"] = t
            if len(c) > len(segs[-1]["text"]):
                segs[-1]["text"] = c
        else:
            segs.append({"start": t, "end": t, "text": c})
    segs = [s for s in segs if len(s["text"]) >= 8]

    def mmss(x):
        return f"{int(x)//60}:{int(x)%60:02d}"
    for s in segs:
        print(f"  {mmss(s['start'])}-{mmss(s['end'])}  {s['text']}")
    out = video.with_suffix(".captions.json")
    out.write_text(json.dumps(segs, ensure_ascii=False, indent=1))
    print(f"\n{len(segs)} caption segments -> {out.name}")


if __name__ == "__main__":
    main()
