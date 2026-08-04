"""
Train the skill classifier on cached clip features (features.pt).

A small trainable head — a bidirectional GRU over the 8 frame-features + an MLP —
learns to map a clip's feature sequence to one of the skills. The ResNet18 backbone
stays frozen (already applied in extract_features.py).

CRITICAL: the train/val split is BY SOURCE VIDEO — clips from the same compilation
never appear in both splits, so the reported accuracy isn't inflated by near-dupes.

Run:  ml/venv_ml/bin/python ml/train_classifier.py
"""
import argparse
import random
from collections import Counter, defaultdict
from pathlib import Path

import torch
import torch.nn as nn

HERE = Path(__file__).resolve().parent
device = "mps" if torch.backends.mps.is_available() else "cpu"
SEED = 0


def load_split(path):
    d = torch.load(path)
    feats, labels, srcs, skills = d["feats"], d["labels"], d["srcs"], d["skills"]
    n = len(labels)
    by = defaultdict(list)                               # (class, source) -> clip indices
    for i in range(n):
        by[(int(labels[i]), srcs[i])].append(i)
    cls_vids = defaultdict(list)
    for (c, src), idxs in by.items():
        cls_vids[c].append(idxs)
    rng = random.Random(SEED)
    tr, va = [], []
    for c, vids in cls_vids.items():                    # hold out ~20% of each class, by video
        rng.shuffle(vids)
        total, target, got = sum(len(v) for v in vids), 0, 0
        target = total * 0.2
        for j, idxs in enumerate(vids):
            to_val = got < target and j < len(vids) - 1  # keep >=1 video in train
            (va if to_val else tr).append(idxs)
            if to_val:
                got += len(idxs)
    tr = [i for g in tr for i in g]
    va = [i for g in va for i in g]
    return feats, labels, skills, tr, va


class GRUHead(nn.Module):
    """For per-frame feature sequences (N, T, D) — a GRU adds the temporal model."""

    def __init__(self, d=512, h=256, n_cls=18):
        super().__init__()
        self.gru = nn.GRU(d, h, batch_first=True, bidirectional=True)
        self.head = nn.Sequential(nn.LayerNorm(2 * h), nn.Linear(2 * h, 128),
                                  nn.ReLU(), nn.Dropout(0.3), nn.Linear(128, n_cls))

    def forward(self, x):
        o, _ = self.gru(x)                              # (B, T, 2h)
        return self.head(o.mean(1))


class MLPHead(nn.Module):
    """For single per-clip vectors (N, D) — e.g. a video backbone already pooled time."""

    def __init__(self, d=512, n_cls=18):
        super().__init__()
        self.head = nn.Sequential(nn.LayerNorm(d), nn.Linear(d, 256), nn.ReLU(),
                                  nn.Dropout(0.4), nn.Linear(256, n_cls))

    def forward(self, x):
        return self.head(x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", default=str(HERE / "features.pt"))
    args = ap.parse_args()
    feats, labels, skills, tr, va = load_split(args.features)
    n_cls = len(skills)
    seq = feats.dim() == 3                               # (N,T,D) sequence vs (N,D) pooled
    print(f"features: {tuple(feats.shape)}  -> {'GRU' if seq else 'MLP'} head")
    Xtr, ytr = feats[tr].to(device), labels[tr].to(device)
    Xva, yva = feats[va].to(device), labels[va].to(device)
    print(f"{n_cls} classes | train {len(tr)} clips | val {len(va)} clips (split by source video)")

    counts = Counter(int(l) for l in labels[tr])
    w = torch.tensor([1.0 / max(1, counts[c]) for c in range(n_cls)])
    w = (w / w.sum() * n_cls).to(device)                # inverse-frequency class weights

    torch.manual_seed(SEED)
    d = feats.shape[-1]
    model = (GRUHead(d=d, n_cls=n_cls) if seq else MLPHead(d=d, n_cls=n_cls)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.CrossEntropyLoss(weight=w)

    best, best_state = 0.0, None
    for epoch in range(80):
        model.train()
        perm = torch.randperm(len(tr))
        for i in range(0, len(tr), 64):
            b = perm[i:i + 64]
            opt.zero_grad()
            loss = lossf(model(Xtr[b]), ytr[b])
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            pred = model(Xva).argmax(1)
            acc = (pred == yva).float().mean().item()
        if acc > best:
            best, best_state = acc, {k: v.cpu().clone() for k, v in model.state_dict().items()}
        if (epoch + 1) % 10 == 0:
            print(f"  epoch {epoch + 1:>2}  val acc {acc:.3f}  (best {best:.3f})")

    # final report from the best checkpoint
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        pred = model(Xva).argmax(1).cpu()
    yv = yva.cpu()
    chance = 1.0 / n_cls
    print(f"\n=== best val accuracy: {best:.1%}  (chance {chance:.1%}, {best/chance:.1f}x) ===")
    print("\nper-class accuracy (val):")
    per = []
    for c in range(n_cls):
        m = yv == c
        a = (pred[m] == c).float().mean().item() if m.sum() else 0.0
        per.append((a, skills[c], int(m.sum())))
    for a, s, k in sorted(per, reverse=True):
        bar = "█" * int(a * 20)
        print(f"  {a:5.1%} {bar:<20} {s:14s} (n={k})")
    # top confusions
    conf = defaultdict(int)
    for t, p in zip(yv.tolist(), pred.tolist()):
        if t != p:
            conf[(skills[t], skills[p])] += 1
    print("\ntop confusions (true -> predicted):")
    for (t, p), n in sorted(conf.items(), key=lambda x: -x[1])[:8]:
        print(f"  {n:>3}  {t} -> {p}")
    torch.save({"state": best_state, "skills": skills, "val_acc": best},
               HERE / "skill_classifier.pt")
    print(f"\nsaved model -> {HERE / 'skill_classifier.pt'}")


if __name__ == "__main__":
    main()
