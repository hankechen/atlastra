"""
Feature extraction — turn each harvested clip into a fixed-size feature sequence
using a FROZEN ImageNet-pretrained ResNet18 (we don't train the backbone, just use
it as a feature extractor). Samples N frames per clip, embeds each to 512-d, and
caches the (N, 512) sequences + labels + source video to features.pt.

Skills with too few clips to train on are dropped. Run on Apple-Silicon GPU (MPS).

Run:  ml/venv_ml/bin/python ml/extract_features.py   (needs the arm64 torch venv)
"""
import csv
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import torch
from torchvision.models import resnet18, ResNet18_Weights

HERE = Path(__file__).resolve().parent
LABELS = HERE / "dataset" / "labels.csv"
OUT = HERE / "features.pt"
N_FRAMES = 8
MIN_CLIPS = 40                                          # drop skills with fewer clips

device = "mps" if torch.backends.mps.is_available() else "cpu"
print(f"device: {device}")

weights = ResNet18_Weights.IMAGENET1K_V1
backbone = resnet18(weights=weights)
backbone.fc = torch.nn.Identity()                       # -> 512-d penultimate features
backbone.eval().to(device)

_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
_STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def to_tensor(frame):
    rgb = cv2.cvtColor(cv2.resize(frame, (224, 224)), cv2.COLOR_BGR2RGB)
    t = torch.from_numpy(rgb).permute(2, 0, 1).float() / 255.0
    return (t - _MEAN) / _STD


def sample_frames(path, n):
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    idxs = np.linspace(0, max(0, total - 1), n).astype(int)
    frames = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        frames.append(fr if ok and fr is not None else np.zeros((224, 224, 3), np.uint8))
    cap.release()
    return frames


def main():
    rows = list(csv.DictReader(open(LABELS)))
    cnt = Counter(r["skill"] for r in rows)
    skills = sorted(s for s, c in cnt.items() if c >= MIN_CLIPS)
    dropped = sorted(s for s, c in cnt.items() if c < MIN_CLIPS)
    skill2idx = {s: i for i, s in enumerate(skills)}
    rows = [r for r in rows if r["skill"] in skill2idx]
    print(f"{len(skills)} skills kept, dropped (too few): {dropped or 'none'}")
    print(f"{len(rows)} clips to embed")

    feats, labels, srcs = [], [], []
    with torch.no_grad():
        for k, r in enumerate(rows):
            frames = sample_frames(str(HERE / r["clip"]), N_FRAMES)
            batch = torch.stack([to_tensor(f) for f in frames]).to(device)
            emb = backbone(batch).cpu()                 # (N_FRAMES, 512)
            feats.append(emb)
            labels.append(skill2idx[r["skill"]])
            srcs.append(r["source_video"])
            if (k + 1) % 250 == 0:
                print(f"  {k + 1}/{len(rows)}")
    feats = torch.stack(feats)                           # (N, N_FRAMES, 512)
    torch.save({"feats": feats, "labels": torch.tensor(labels), "srcs": srcs,
                "skills": skills}, OUT)
    print(f"saved {OUT}  feats={tuple(feats.shape)}")


if __name__ == "__main__":
    main()
