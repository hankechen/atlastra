"""
Player archetypes (use case 10): assign every player a scouting role + a fit %,
and find their most-similar players.

Archetype assignment stays rule-based — see analytics/archetype_defs. For each fine
position group we score players on the percentile vectors already computed within
that position (player_radar_metrics for outfield, the GK vector in
player_profile_metrics for keepers):
  - archetype = the role prototype the player's centred profile aligns with best
    (cosine over the role's signature metrics; captures relative EMPHASIS).
  - fit %    = the player's mean percentile on that role's "high" metrics
    (how strongly they show its signature skills).

`similar` (the "similar players" rail on the profile page) is real, unsupervised
ML: scikit-learn k-NN (StandardScaler + cosine NearestNeighbors) over each
player's full ~25-metric percentile vector, run entirely locally on data already
in the warehouse -- no API, no paid tier. This replaced a hand-rolled cosine
calc over a 6-axis average of the same metrics (2026-08); the 25-dim vector is
strictly more signal, and StandardScaler equalises per-metric variance before
distance is computed, which a plain row-normalise doesn't.

Writes `player_archetypes` and `player_similar`. Run after pipeline.profile:
    python -m pipeline.archetypes
"""
import sys

import duckdb
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

try:
    from config import DB_PATH
    from analytics.archetype_defs import ARCHETYPES, GK_GROUP
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH
    from analytics.archetype_defs import ARCHETYPES, GK_GROUP

SIMILAR_K = 8
# similar players are pooled by attacking/middle/defensive PHASE (not fine position
# or archetype) so a winger can be similar to an AM/striker, etc.
BROAD = {"ST": "ATT", "W": "ATT", "AM": "ATT",
         "CM": "MID", "DM": "MID", "FB": "DEF", "CB": "DEF"}


def _assign(grp, mat):
    """mat: DataFrame index=player_id, cols=metric_label, vals=percentile (0-100)."""
    archs = ARCHETYPES[grp]
    used = [m for m in {x for a in archs for x in a["high"] + a["low"]} if m in mat.columns]
    if not used:
        return []
    M = mat[used]
    metrics = list(M.columns)
    A = np.zeros((len(archs), len(metrics)))
    for ai, a in enumerate(archs):
        for m in a["high"]:
            if m in metrics:
                A[ai, metrics.index(m)] = 1.0
        for m in a["low"]:
            if m in metrics:
                A[ai, metrics.index(m)] = -0.6
    P = M.to_numpy(dtype=float)
    C = (np.nan_to_num(P, nan=50.0) - 50.0) / 50.0          # centre on average
    Cn = C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)
    An = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-9)
    S = Cn @ An.T                                            # players x archetypes
    order = np.argsort(-S, axis=1)
    rows = []
    for i, pid in enumerate(M.index):
        def fit(ai):
            hs = [metrics.index(m) for m in archs[ai]["high"] if m in metrics]
            vals = [P[i, j] for j in hs if not np.isnan(P[i, j])]
            return int(round(np.mean(vals))) if vals else None
        a1 = int(order[i, 0])
        a2 = int(order[i, 1]) if S.shape[1] > 1 else a1
        rows.append((int(pid), grp, archs[a1]["name"], fit(a1), archs[a2]["name"], fit(a2)))
    return rows


def _similar(mat, k=SIMILAR_K):
    """Nearest players by cosine similarity on their STANDARDIZED percentile profile
    across all ~25 tracked metrics -- scikit-learn NearestNeighbors, run locally.
    StandardScaler equalises each metric's variance before distance is computed
    (a plain L2 row-normalise doesn't), then cosine k-NN finds players with the
    same SHAPE of strengths regardless of overall level. `mat`: index=player_id,
    columns=metric_label, values=percentile (0-100)."""
    mat = mat.fillna(50.0)
    ids = [int(x) for x in mat.index]
    if len(ids) < 2:
        return []
    X = StandardScaler().fit_transform(mat.to_numpy(dtype=float))
    nn = NearestNeighbors(n_neighbors=min(k + 1, len(ids)), metric="cosine").fit(X)
    dist, idx = nn.kneighbors(X)
    rows = []
    for i, pid in enumerate(ids):
        rank = 0
        for d, j in zip(dist[i], idx[i]):
            if j == i:                                       # a point is its own nearest neighbour
                continue
            rank += 1
            rows.append((pid, rank, ids[j], float(round(max(0.0, 1.0 - d) * 100, 1))))
            if rank >= k:
                break
    return rows


def build_archetypes() -> None:
    con = duckdb.connect(str(DB_PATH))
    rad = con.execute(
        "SELECT player_id, position_group, metric_label, percentile FROM player_radar_metrics"
    ).df()
    gk = con.execute(
        "SELECT player_id, metric_label, percentile FROM player_profile_metrics "
        "WHERE position_group = 'GK'"
    ).df()

    # archetype assignment: per FINE position group
    arch_rows = []
    for grp in ARCHETYPES:
        df = gk if grp == GK_GROUP else rad[rad["position_group"] == grp]
        if df.empty:
            continue
        mat = df.pivot_table(index="player_id", columns="metric_label",
                             values="percentile", aggfunc="first")
        arch_rows += _assign(grp, mat)

    # similar players: outfield by attacking/middle/defensive PHASE on the full
    # ~25-metric profile (cross-position, cross-archetype); GK on its own keeper vector.
    sim_rows = []
    rad = rad.assign(broad=rad["position_group"].map(BROAD))
    for phase in ("ATT", "MID", "DEF"):
        sub = rad[rad["broad"] == phase]
        if not sub.empty:
            mat = sub.pivot_table(index="player_id", columns="metric_label",
                                  values="percentile", aggfunc="first")
            sim_rows += _similar(mat)
    if not gk.empty:
        sim_rows += _similar(gk.pivot_table(index="player_id", columns="metric_label",
                                            values="percentile", aggfunc="first"))

    con.execute("DROP TABLE IF EXISTS player_archetypes")
    con.execute("""CREATE TABLE player_archetypes
        (player_id BIGINT, position_group VARCHAR, archetype VARCHAR, fit INTEGER,
         archetype2 VARCHAR, fit2 INTEGER)""")
    if arch_rows:
        con.executemany("INSERT INTO player_archetypes VALUES (?,?,?,?,?,?)", arch_rows)
    con.execute("DROP TABLE IF EXISTS player_similar")
    con.execute("""CREATE TABLE player_similar
        (player_id BIGINT, rank INTEGER, similar_player_id BIGINT, similarity DOUBLE)""")
    if sim_rows:
        con.executemany("INSERT INTO player_similar VALUES (?,?,?,?)", sim_rows)
    con.execute("CREATE INDEX IF NOT EXISTS idx_arch ON player_archetypes(player_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_sim ON player_similar(player_id)")
    n = len(arch_rows)
    con.close()
    print(f"player_archetypes: {n} players assigned a role; "
          f"player_similar: {len(sim_rows)} neighbour links.")


if __name__ == "__main__":
    build_archetypes()
