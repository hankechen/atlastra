"""
Video feature extraction — the motion-aware upgrade.

Instead of embedding frames independently (image backbone), this uses a FROZEN
r2plus1d_18 pretrained on Kinetics-400 (an *action-recognition* dataset). It encodes
spatiotemporal motion, so skills defined by movement (roulette, stepover, cruyff turn)
get a real signal. Each clip -> one 512-d motion feature; output is (N, 512).

Run:  ml/venv_ml/bin/python ml/extract_features_video.py
(set PYTORCH_ENABLE_MPS_FALLBACK=1 so any conv3d op unsupported on MPS falls to CPU)
"""
import csv
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import torch
from torchvision.models.video import r2plus1d_18, R2Plus1D_18_Weights

HERE = Path(__file__).resolve().parent
LABELS = HERE / "dataset" / "labels.csv"
OUT = HERE / "features_video.pt"
N_FRAMES = 16
SIZE = 112
MIN_CLIPS = 40
BATCH = 8

device = "mps" if torch.backends.mps.is_available() else "cpu"
print(f"device: {device}")

weights = R2Plus1D_18_Weights.KINETICS400_V1
backbone = r2plus1d_18(weights=weights)
backbone.fc = torch.nn.Identity()                       # -> 512-d spatiotemporal feature
backbone.eval().to(device)

# Kinetics normalisation
_MEAN = torch.tensor([0.43216, 0.394666, 0.37645]).view(3, 1, 1, 1)
_STD = torch.tensor([0.22803, 0.22145, 0.216989]).view(3, 1, 1, 1)


def clip_tensor(path):
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    idxs = np.linspace(0, max(0, total - 1), N_FRAMES).astype(int)
    frames = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if not ok or fr is None:
            fr = np.zeros((SIZE, SIZE, 3), np.uint8)
        else:
            fr = cv2.cvtColor(cv2.resize(fr, (SIZE, SIZE)), cv2.COLOR_BGR2RGB)
        frames.append(fr)
    cap.release()
    arr = np.stack(frames)                              # (T, H, W, C)
    t = torch.from_numpy(arr).permute(3, 0, 1, 2).float() / 255.0   # (C, T, H, W)
    return (t - _MEAN) / _STD


def main():
    rows = list(csv.DictReader(open(LABELS)))
    cnt = Counter(r["skill"] for r in rows)
    skills = sorted(s for s, c in cnt.items() if c >= MIN_CLIPS)
    skill2idx = {s: i for i, s in enumerate(skills)}
    rows = [r for r in rows if r["skill"] in skill2idx]
    print(f"{len(skills)} skills, {len(rows)} clips (16 frames each, r2plus1d_18/Kinetics)")

    feats, labels, srcs = [], [], []
    with torch.no_grad():
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            batch = torch.stack([clip_tensor(str(HERE / r["clip"])) for r in chunk]).to(device)
            emb = backbone(batch).cpu()                 # (B, 512)
            feats.append(emb)
            labels += [skill2idx[r["skill"]] for r in chunk]
            srcs += [r["source_video"] for r in chunk]
            if (start // BATCH) % 30 == 0:
                print(f"  {start + len(chunk)}/{len(rows)}")
    feats = torch.cat(feats)                             # (N, 512)
    torch.save({"feats": feats, "labels": torch.tensor(labels), "srcs": srcs,
                "skills": skills}, OUT)
    print(f"saved {OUT}  feats={tuple(feats.shape)}")


if __name__ == "__main__":
    main()
