"""
Clip harvester — turns the skills corpus into a labelled clip dataset.

Reads ml/skills_corpus.json, and for each skill's compilation videos:
  1. downloads the video (yt-dlp, progressive H.264 stream — no ffmpeg needed)
  2. splits it into shots via content-aware cut detection (frame-diff, in OpenCV)
  3. keeps shots of a sensible length (drops intros / title cards / long talking heads)
  4. dedups near-identical shots (replays / slow-mo) via a perceptual hash
  5. writes ml/dataset/<skill>/<videoid>_<n>.mp4 and appends a row to labels.csv

Every clip in a "Best Rabonas" video is a weak-labelled rabona example, so the
whole thing is auto-labelled — the label is the skill the source video is about.

Deps: yt-dlp, opencv (cv2), numpy.  No ffmpeg, no scenedetect required
(OpenCV can't decode VP9/webm without ffmpeg, so we fetch progressive H.264 MP4).

Examples:
  python ml/harvest_clips.py --smoke                 # 1 skill, 1 video (verify setup)
  python ml/harvest_clips.py --skills rabona,nutmeg  # just these skills
  python ml/harvest_clips.py --limit-videos 2        # 2 videos per skill
  python ml/harvest_clips.py                          # everything (downloads GBs)
"""
import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "skills_corpus.json"
DATASET = HERE / "dataset"
CACHE = HERE / "_cache"
LABELS = DATASET / "labels.csv"

# Progressive H.264 MP4 (fmt 18=360p, 22=720p) — no ffmpeg merge needed AND OpenCV
# can decode it (it can't do VP9/webm without ffmpeg). 360p first keeps the shot
# frame-buffers small and processing fast; re-harvest at higher res later if wanted.
YDL_FORMAT = "18/22/b[ext=mp4][vcodec^=avc][acodec!=none]"


def log(msg):
    print(msg, flush=True)


def download(video_id: str) -> Path | None:
    """Fetch a video to the cache (skip if already there). Returns path or None."""
    import yt_dlp
    dest = CACHE / f"{video_id}.mp4"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    CACHE.mkdir(parents=True, exist_ok=True)
    opts = {"format": YDL_FORMAT, "outtmpl": str(CACHE / f"{video_id}.%(ext)s"),
            "quiet": True, "no_warnings": True, "noprogress": True, "retries": 2,
            "concurrent_fragment_downloads": 1}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except Exception as e:                               # noqa: BLE001
        log(f"      ! download failed: {type(e).__name__} {str(e)[:70]}")
        return None
    # yt-dlp may have chosen a non-mp4 container; grab whatever it wrote
    hits = list(CACHE.glob(f"{video_id}.*"))
    return hits[0] if hits else None


def ahash(frame) -> int:
    """64-bit average hash of a frame (for replay/near-duplicate detection)."""
    g = cv2.cvtColor(cv2.resize(frame, (8, 8)), cv2.COLOR_BGR2GRAY)
    bits = (g > g.mean()).flatten()
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def harvest_video(video_path, out_dir, video_id, args, seen_hashes):
    """One OpenCV pass: detect hard cuts by frame-diff, buffer each shot, and write
    the ones that pass the length + dedup filters. Returns label rows."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        log("      ! OpenCV could not open the file")
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not w or not h:
        cap.release()
        return []
    min_f, max_f = int(args.min_dur * fps), int(args.max_dur * fps)
    # 'avc1' = H.264 via macOS AVFoundation (works without ffmpeg; 'mp4v' silently fails)
    fourcc = cv2.VideoWriter_fourcc(*"avc1")
    rows, kept = [], 0

    def flush(start_f, frames):
        nonlocal kept
        n = len(frames)
        if kept >= args.max_clips_per_video or n < min_f or n > max_f:
            return
        if start_f / fps < args.skip_intro:             # drop the opening title sequence
            return
        mid = frames[n // 2]
        gray = cv2.cvtColor(mid, cv2.COLOR_BGR2GRAY)
        if float(gray.std()) < args.min_std:            # near-uniform = solid title card
            return
        if float((gray < 30).mean()) > 0.8:             # mostly black = countdown / black card
            return
        hh = ahash(mid)
        if any(hamming(hh, p) <= args.dedup_dist for p in seen_hashes):   # replay/near-dup
            return
        seen_hashes.append(hh)
        clip = out_dir / f"{video_id}_{start_f:06d}.mp4"
        vw = cv2.VideoWriter(str(clip), fourcc, fps, (w, h))
        for fr in frames:
            vw.write(fr)
        vw.release()
        kept += 1
        rows.append([str(clip.relative_to(HERE)), out_dir.name, video_id,
                     round(start_f / fps, 2), round((start_f + n) / fps, 2),
                     round(n / fps, 2), f"{hh:016x}"])

    buf, prev, shot_start, fidx = [], None, 0, 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        small = cv2.cvtColor(cv2.resize(fr, (32, 18)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        is_cut = prev is not None and float(np.abs(small - prev).mean()) > args.cut_thresh
        prev = small
        if is_cut or len(buf) >= max_f:                 # shot boundary (hard cut or too long)
            flush(shot_start, buf)
            buf, shot_start = [], fidx
        buf.append(fr)
        fidx += 1
    flush(shot_start, buf)
    cap.release()
    return rows


def already_done(out_dir: Path, video_id: str) -> bool:
    return any(out_dir.glob(f"{video_id}_*.mp4"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills", help="comma-separated skill ids (default: all)")
    ap.add_argument("--limit-videos", type=int, default=0, help="max videos per skill (0=all)")
    ap.add_argument("--max-clips-per-video", type=int, default=40)
    ap.add_argument("--min-dur", type=float, default=0.8, help="min clip seconds")
    ap.add_argument("--max-dur", type=float, default=8.0, help="max clip seconds")
    ap.add_argument("--cut-thresh", type=float, default=22.0, help="frame-diff cut sensitivity")
    ap.add_argument("--dedup-dist", type=int, default=6, help="max hamming dist for a duplicate")
    ap.add_argument("--skip-intro", type=float, default=3.0, help="drop shots before this second (title cards)")
    ap.add_argument("--min-std", type=float, default=16.0, help="drop near-uniform frames (black/title cards)")
    ap.add_argument("--keep-cache", action="store_true", help="keep downloaded source videos")
    ap.add_argument("--smoke", action="store_true", help="1 skill, 1 video (verify pipeline)")
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text())
    skills = corpus["skills"]
    if args.smoke:
        skills, args.limit_videos, args.max_clips_per_video = skills[:1], 1, 12
    elif args.skills:
        want = {s.strip() for s in args.skills.split(",")}
        skills = [s for s in skills if s["id"] in want]

    DATASET.mkdir(parents=True, exist_ok=True)
    new_file = not LABELS.exists()
    lf = open(LABELS, "a", newline="")
    writer = csv.writer(lf)
    if new_file:
        writer.writerow(["clip", "skill", "source_video", "start_sec", "end_sec", "duration", "ahash"])

    totals = {}
    for sk in skills:
        sid = sk["id"]
        out_dir = DATASET / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        vids = sk["videos"][:args.limit_videos] if args.limit_videos else sk["videos"]
        seen_hashes = []
        log(f"\n=== {sid}  ({len(vids)} videos) ===")
        for v in vids:
            vid = v["id"]
            if already_done(out_dir, vid):
                log(f"  · {vid} already harvested — skip")
                continue
            log(f"  ↓ {vid}  {v['title'][:50]}")
            path = download(vid)
            if not path:
                continue
            rows = harvest_video(path, out_dir, vid, args, seen_hashes)
            for r in rows:
                writer.writerow(r)
            lf.flush()
            totals[sid] = totals.get(sid, 0) + len(rows)
            log(f"     -> {len(rows)} clips kept")
            if not args.keep_cache:
                path.unlink(missing_ok=True)
    lf.close()

    log("\n=== summary ===")
    for sid in sorted(totals):
        log(f"  {totals[sid]:>4}  {sid}")
    log(f"  total: {sum(totals.values())} clips -> {DATASET}")
    log(f"  labels: {LABELS}")


if __name__ == "__main__":
    main()
