"""
Fusion classifier — concatenate the two frozen feature sets:
  - image  (ResNet18, appearance)   mean-pooled over frames  -> 512-d
  - video  (r2plus1d, motion)                                -> 512-d
into a 1024-d per-clip vector, then train an MLP head. The two backbones fail on
different skills, so the concatenation can beat either alone.

Same source-video split (no leakage). Run after both feature files exist.
"""
import random
from collections import Counter, defaultdict
from pathlib import Path

import torch
import torch.nn as nn

HERE = Path(__file__).resolve().parent
device = "mps" if torch.backends.mps.is_available() else "cpu"
SEED = 0


def main():
    im = torch.load(HERE / "features.pt")               # (N, 8, 512)
    vi = torch.load(HERE / "features_video.pt")          # (N, 512)
    assert im["labels"].tolist() == vi["labels"].tolist(), "feature files misaligned"
    labels, srcs, skills = im["labels"], im["srcs"], im["skills"]
    X = torch.cat([im["feats"].mean(1), vi["feats"]], dim=1)   # (N, 1024)
    n_cls = len(skills)

    by = defaultdict(list)
    for i in range(len(labels)):
        by[(int(labels[i]), srcs[i])].append(i)
    cls_vids = defaultdict(list)
    for (c, src), idxs in by.items():
        cls_vids[c].append(idxs)
    rng = random.Random(SEED)
    tr, va = [], []
    for c, vids in cls_vids.items():
        rng.shuffle(vids)
        target, got = sum(len(v) for v in vids) * 0.2, 0
        for j, idxs in enumerate(vids):
            to_val = got < target and j < len(vids) - 1
            (va if to_val else tr).append(idxs)
            if to_val:
                got += len(idxs)
    tr = [i for g in tr for i in g]
    va = [i for g in va for i in g]

    Xtr, ytr = X[tr].to(device), labels[tr].to(device)
    Xva, yva = X[va].to(device), labels[va].to(device)
    print(f"fused features: {tuple(X.shape)} | train {len(tr)} | val {len(va)}")

    counts = Counter(int(l) for l in labels[tr])
    w = torch.tensor([1.0 / max(1, counts[c]) for c in range(n_cls)])
    w = (w / w.sum() * n_cls).to(device)

    torch.manual_seed(SEED)
    model = nn.Sequential(nn.LayerNorm(1024), nn.Linear(1024, 384), nn.ReLU(),
                          nn.Dropout(0.4), nn.Linear(384, n_cls)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss(weight=w)

    best, best_pred = 0.0, None
    for epoch in range(120):
        model.train()
        perm = torch.randperm(len(tr))
        for i in range(0, len(tr), 64):
            b = perm[i:i + 64]
            opt.zero_grad()
            lossf(model(Xtr[b]), ytr[b]).backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            pred = model(Xva).argmax(1)
            acc = (pred == yva).float().mean().item()
        if acc > best:
            best, best_pred = acc, pred.cpu().clone()

    yv = yva.cpu()
    print(f"\n=== FUSED best val accuracy: {best:.1%}  (chance {1/n_cls:.1%}, {best*n_cls:.1f}x) ===")
    per = []
    for c in range(n_cls):
        m = yv == c
        a = (best_pred[m] == c).float().mean().item() if m.sum() else 0.0
        per.append((a, skills[c], int(m.sum())))
    print("per-class accuracy (val):")
    for a, s, k in sorted(per, reverse=True):
        print(f"  {a:5.1%} {'#' * int(a * 20):<20} {s:14s} (n={k})")


if __name__ == "__main__":
    main()
