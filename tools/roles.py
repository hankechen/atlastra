"""Learn positional roles from where players actually stand, instead of defining them.

    python -m tools.roles                  # report what the data supports
    python -m tools.roles --write          # also persist player_learned_role
    python -m tools.roles --k 8 --write

The engine's roles are hand-written: someone decided there is such a thing as an Inverted
Wing-Back and wrote down what it does. That is fine for the tactical MEANING of a role — how
hard it presses, whether it tucks inside — which no clustering can supply. It is not fine as
a claim about which roles are distinguishable at all, and that claim the warehouse can check:
player_heatmap holds ~10k player-seasons as 20x30 occupancy grids.

Two things had to be right before the output meant anything.

FOLD THE PITCH. Clustering the raw grids finds which touchline you stand near, not what you
do — left and right centre-backs come out as two different "roles" and every central player
collapses into one 4,600-strong blob. Mirroring each grid across the long axis before
clustering merges the left and right versions of a role, which is what we actually want:
the engine already tracks side separately (_slot_side), so side is not role.

CHECK IT SURVIVES A NEW SEASON. Clusters fitted on 2020-23 and applied unseen to 2023-26 move
their population shares by under 4%, and an independent refit lands its centroids within a
quarter of a cluster radius. That is what makes these roles rather than an artefact of one
season's data.

WHAT IT FOUND, and it does not flatter the current taxonomy:
  * DM and CM are one cluster. On the pitch they occupy the same space; whatever separates
    them, it is not position.
  * W and AM do not separate cleanly either — both wide-attacker clusters are ~50/50.
  * No WM cluster emerges at any k. The WM family and its six roles have no positional
    signature distinct from W and FB.
  * CB splits in TWO — a deep central one and a slightly higher, wider one. The data wants a
    distinction the engine does not make.
  * Silhouette is ~0.23 at k=8. Positions are a continuum, not islands, so treat a role
    assignment as a tendency and not a fact.

Keepers are left out entirely: they only have heatmaps in 2025/26, so there is nothing to
learn across seasons and nothing to hold out. The engine treats GK separately anyway.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                          # noqa: E402
from sklearn.cluster import KMeans                          # noqa: E402
from sklearn.metrics import silhouette_score                # noqa: E402

from analytics.queries import SoccerDB                      # noqa: E402

GRID = (20, 30)          # rows = width of the pitch, cols = goal-to-goal
COMPONENTS = 20          # 300 folded cells are mostly noise; 20 components hold ~74%
TRAIN_SEASONS = ("2021", "2122", "2223")


def _load(db):
    rows = db.con.execute("""
        SELECT h.player_id, h.season, h.grid, c.position_group, pl.player_name
        FROM player_heatmap h
        JOIN player_ratings_combined c
          ON c.player_id = h.player_id AND c.season = h.season AND c.scope = 'league'
        JOIN players pl ON pl.player_id = h.player_id
        WHERE c.position_group IS NOT NULL AND c.position_group <> 'GK'""").fetchall()
    F, meta = [], []
    for pid, season, grid, grp, name in rows:
        a = np.asarray(json.loads(grid) if isinstance(grid, str) else grid, float)
        if a.shape != GRID or a.sum() <= 0:
            continue
        f = ((a + a[::-1, :]) / 2.0)[:GRID[0] // 2, :].ravel()   # fold, keep one half
        F.append(f / f.sum())
        meta.append((int(pid), season, grp, name))
    return np.array(F), meta


def _project(F, train_mask):
    """PCA fitted on the training seasons only, so the held-out seasons stay held out."""
    mu = F[train_mask].mean(0)
    _u, _s, Vt = np.linalg.svd(F[train_mask] - mu, full_matrices=False)
    B = Vt[:COMPONENTS]
    return (F - mu) @ B.T, mu, B


def _describe(centre_cells):
    """Turn a cluster centroid back into something readable: how far up the pitch it sits,
    how far from the middle, and how spread out it is."""
    g = centre_cells.reshape(GRID[0] // 2, GRID[1])
    xs, ys = np.arange(GRID[1]), np.arange(GRID[0] // 2)
    cx = float((g.sum(0) * xs).sum() / g.sum())
    cy = float((g.sum(1) * ys).sum() / g.sum())
    sx = float(np.sqrt((g.sum(0) * (xs - cx) ** 2).sum() / g.sum()))
    return cx / (GRID[1] - 1) * 100, cy / (GRID[0] // 2 - 1) * 100, sx


def _name(depth, width, spread, groups):
    """A readable label. Named off the centroid's own geometry plus which position groups
    land in it — deliberately descriptive ("high and wide") rather than borrowing a role
    name the clustering did not actually establish."""
    top = groups[0][0] if groups else "?"
    band = ("deep" if depth < 25 else "defensive" if depth < 34 else
            "withdrawn" if depth < 40 else "middle" if depth < 52 else
            "advanced" if depth < 58 else "high" if depth < 62 else "forward")
    lane = ("central" if width > 52 else "inside" if width > 41 else
            "half-space" if width > 33 else "wide")
    return f"{band} {lane}" + (" (roaming)" if spread > 7.0 else "")


def learn(db, k, verbose=True):
    F, meta = _load(db)
    seasons = np.array([m[1] for m in meta])
    groups = np.array([m[2] for m in meta])
    train = np.isin(seasons, TRAIN_SEASONS)
    P, mu, B = _project(F, train)
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(P[train])
    lab = km.predict(P)
    if verbose:
        print(f"  {len(F)} player-seasons · {F.shape[1]} folded cells · {COMPONENTS} components")
        sil = silhouette_score(P, lab, sample_size=4000, random_state=0)
        print(f"  silhouette {sil:.3f} at k={k}"
              + ("  — a continuum, so read a role as a tendency" if sil < 0.4 else ""))
        # Held-out check, reported every run so a bad k cannot pass quietly.
        a = np.bincount(lab[train], minlength=k) / train.sum()
        b = np.bincount(lab[~train], minlength=k) / (~train).sum()
        print(f"  population drift on unseen seasons {50 * np.abs(a - b).sum():.1f}%\n")
        print(f"  {'role':<26}{'n':>6}{'depth':>8}{'width':>8}  composition")
    out = {}
    for c in range(k):
        idx = np.where(lab == c)[0]
        if not len(idx):
            continue
        d, w, s = _describe(F[idx].mean(0))
        comp = Counter(groups[idx]).most_common(3)
        label = _name(d, w, s, comp)
        if label in {v["label"] for v in out.values()}:      # two clusters, one description
            label = f"{label} [{comp[0][0]}]"                # disambiguate by who is in it
        while label in {v["label"] for v in out.values()}:
            label += "'"
        out[c] = {"label": label, "n": len(idx), "depth": d, "width": w, "spread": s,
                  "composition": [(g, round(100 * n / len(idx))) for g, n in comp]}
        if verbose:
            cs = ", ".join(f"{g} {100 * n / len(idx):.0f}%" for g, n in comp)
            print(f"  {label:<26}{len(idx):>6}{d:>7.1f}%{w:>7.1f}%  {cs}")
    return F, meta, P, lab, out, (mu, B, km)


def _confidence(P, km, lab):
    """How much the assignment means for one player: 1 - (distance to its own centre /
    distance to the next nearest). A player sitting between two roles scores near 0."""
    d = np.linalg.norm(P[:, None, :] - km.cluster_centers_[None, :, :], axis=2)
    part = np.partition(d, 1, axis=1)
    near, second = part[:, 0], part[:, 1]
    return np.clip(1 - near / np.maximum(second, 1e-9), 0, 1)


def write(db, k):
    F, meta, P, lab, out, (mu, B, km) = learn(db, k, verbose=True)
    conf = _confidence(P, km, lab)
    db.con.execute("""CREATE OR REPLACE TABLE player_learned_role (
        player_id INTEGER, season VARCHAR, role VARCHAR, confidence DOUBLE,
        depth DOUBLE, width DOUBLE, position_group VARCHAR)""")
    db.con.executemany(
        "INSERT INTO player_learned_role VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(meta[i][0], meta[i][1], out[lab[i]]["label"], float(conf[i]),
          round(out[lab[i]]["depth"], 1), round(out[lab[i]]["width"], 1), meta[i][2])
         for i in range(len(meta)) if lab[i] in out])
    n = db.con.execute("SELECT count(*) FROM player_learned_role").fetchone()[0]
    print(f"\n  wrote player_learned_role: {n} rows")
    # The point of the table is the DISAGREEMENTS: a player whose listed position is not the
    # one most of his cluster-mates hold is being played somewhere other than his label says.
    # Confidence has to be high for this to mean anything — a player sitting between two
    # clusters gets an arbitrary winner, and calling that a mismatch would be noise.
    dom = {c: v["composition"][0][0] for c, v in out.items()}
    print(f"  cluster -> dominant listed group: "
          + ", ".join(f"{v['label'].split(' (')[0]}={dom[c]}" for c, v in out.items()))
    print("\n  played away from their listed position (2025/26, confidence > 0.5):")
    rows = db.con.execute("""
        SELECT pl.player_name, r.position_group, r.role, r.confidence
        FROM player_learned_role r JOIN players pl ON pl.player_id = r.player_id
        WHERE r.season = '2526' AND r.confidence > 0.5
        ORDER BY r.confidence DESC""").fetchall()
    shown = 0
    for name, grp, role, cf in rows:
        want = next((dom[c] for c, v in out.items() if v["label"] == role), None)
        if want and want != grp:
            print(f"    {name:<26}listed {grp:<3} plays {role:<24}({want}-like)  {cf:.2f}")
            shown += 1
            if shown >= 10:
                break
    if not shown:
        print("    none — every high-confidence assignment agrees with the listed position")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--sweep", action="store_true", help="silhouette across k, then stop")
    a = ap.parse_args()
    with SoccerDB(read_only=not a.write) as db:
        print(f"\n=== learned positional roles, k={a.k} ===")
        if a.sweep:
            F, meta = _load(db)
            train = np.isin(np.array([m[1] for m in meta]), TRAIN_SEASONS)
            P, _mu, _B = _project(F, train)
            print(f"  {'k':>3}{'silhouette':>13}   sizes")
            for k in range(4, 13):
                km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(P[train])
                s = silhouette_score(P, km.predict(P), sample_size=4000, random_state=0)
                print(f"  {k:>3}{s:>13.4f}   {np.bincount(km.predict(P))}")
            return
        if a.write:
            write(db, a.k)
        else:
            learn(db, a.k)
            print("\n  nothing written — pass --write to persist player_learned_role")


if __name__ == "__main__":
    main()
