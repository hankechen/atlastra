"""
Career Trajectory model tests.

Two kinds of check, because they fail for different reasons:

  * **shape** — the tables exist, the projections are sane, the API surfaces
    join, the aging curve is populated. These break when the pipeline changes.
  * **substance** — the model still beats the persistence baseline on the
    held-out seasons, and the measured aging curve still has the shape a
    football panel must have (young players gain, old players lose). These
    break when the *model* gets worse, which is the failure worth catching.

The substance tests re-fit on the warehouse and are slow (~30s), so they are
marked `slow`:  python -m pytest tests/test_trajectory.py -q -m "not slow"

Skips cleanly (rather than failing) when the model has not been trained yet:
    PYTHONPATH=. python -m ml.train_trajectory
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analytics.queries import SoccerDB  # noqa: E402
from config import FOCUS_SEASON  # noqa: E402


def db() -> SoccerDB:
    return SoccerDB(read_only=True)


def _trained(d) -> bool:
    return d._table_exists("player_trajectory")


@pytest.fixture(scope="module")
def d():
    with db() as conn:
        if not _trained(conn):
            pytest.skip("trajectory model not trained (run python -m ml.train_trajectory)")
        yield conn


# --------------------------------------------------------------------------- #
# Shape
# --------------------------------------------------------------------------- #
def test_projections_are_sane(d):
    """Every projection lands on the rating scale, with an error bar and odds."""
    df = d.con.execute("""
        SELECT projected, delta, band, lo, hi, p_present, rating_now, verdict
        FROM player_trajectory WHERE season = ?""", [FOCUS_SEASON]).df()
    assert len(df) > 500, "expected the whole rated cohort to be projected"
    assert df["projected"].between(0, 100).all()
    assert df["p_present"].between(0, 1).all()
    assert (df["band"] > 0).all(), "a projection without an error bar is a guess"
    # the interval must be ordered, on-scale, and contain its own point estimate —
    # two independently fitted quantile models guarantee none of the three
    assert (df["lo"] <= df["hi"]).all()
    assert df["lo"].between(0, 100).all() and df["hi"].between(0, 100).all()
    assert ((df["lo"] <= df["projected"]) & (df["projected"] <= df["hi"])).all()
    # the three numbers the card shows side by side must actually add up
    assert ((df["projected"] - df["rating_now"] - df["delta"]).abs() < 1e-6).all()
    assert set(df["verdict"]) <= {"Sharp rise", "Rising", "Steady", "Declining", "Sharp fall"}


def test_interval_varies_and_leans(d):
    """The band's reason to exist: it is fitted per player, not one width for all,
    and it leans toward the risk. High-rated players have further to fall."""
    df = d.con.execute("""
        SELECT rating_now, projected, lo, hi FROM player_trajectory WHERE season = ?""",
        [FOCUS_SEASON]).df()
    width = df["hi"] - df["lo"]
    assert width.nunique() > 50, "a per-player band that never varies is a constant"
    hi_rated, lo_rated = df[df["rating_now"] > 70], df[df["rating_now"] <= 50]
    if len(hi_rated) > 30 and len(lo_rated) > 30:
        # downside room, as a share of the interval, is larger for the highly rated
        share = lambda s: ((s["projected"] - s["lo"]) / (s["hi"] - s["lo"])).mean()
        assert share(hi_rated) > share(lo_rated)


def test_no_projection_for_the_unrated(d):
    """Only players who cleared this season's minutes bar get a projection —
    the model has nothing to go on otherwise, and must not invent one."""
    n = d.con.execute("""
        SELECT count(*) FROM player_trajectory t
        LEFT JOIN player_ratings_combined c
               ON c.player_id = t.player_id AND c.season = t.season AND c.scope = 'league'
        WHERE t.season = ? AND c.player_id IS NULL""", [FOCUS_SEASON]).fetchone()[0]
    assert n == 0


def test_player_api(d):
    """The profile card's payload: projection, drivers, history, curve, metrics."""
    t = d.web_trajectory("Bellingham", FOCUS_SEASON)
    if not t.get("available"):
        pytest.skip("player not in this season's rated cohort")
    assert t["target_season"] != FOCUS_SEASON
    assert 0 <= t["projected"] <= 100
    assert t["verdict_class"] in {"great", "good", "neutral", "warn", "bad"}
    assert t["drivers"], "the card promises to explain the projection"
    assert all(abs(x["impact"]) >= 0.15 for x in t["drivers"])
    assert len(t["history"]) >= 1
    assert t["model"]["mae"] < t["model"]["base_mae"], "shipped metrics must show real skill"


def test_board_api(d):
    """Risers, fallers, breakouts and at-risk are ordered as the page claims."""
    b = d.web_trajectory_board(FOCUS_SEASON, limit=10)
    assert b["available"]
    assert len(b["risers"]) == 10 and len(b["fallers"]) == 10
    assert b["risers"][0]["delta"] >= b["risers"][-1]["delta"]
    assert b["fallers"][0]["delta"] <= b["fallers"][-1]["delta"]
    assert b["risers"][0]["delta"] > b["fallers"][0]["delta"]
    # a "breakout candidate" must actually be projected to climb — sorting a young
    # cohort by projected rating alone once put "Falling" players under the heading
    assert all(r["age"] is not None and r["age"] <= 23 for r in b["breakouts"])
    assert all(r["delta"] > 0 for r in b["breakouts"])
    assert b["at_risk"][0]["p_present"] <= b["at_risk"][-1]["p_present"]


def test_goalkeepers_are_projected_and_get_a_wider_band(d):
    """Keepers were once excluded (rated in 2025/26 only, so no transition existed
    to fit). Now that pipeline/scrape_sofa_gk.py backfills them to 2015/16 they are
    modelled — and because a keeper's rating swings harder season to season than an
    outfielder's, the fitted interval should be noticeably wider for them. That the
    band finds this on its own is the check worth having."""
    df = d.con.execute("""
        SELECT c.position_group AS grp, t.hi - t.lo AS width
        FROM player_trajectory t
        JOIN player_ratings_combined c
          ON c.player_id = t.player_id AND c.season = t.season AND c.scope = 'league'
        WHERE t.season = ?""", [FOCUS_SEASON]).df()
    gk = df[df["grp"] == "GK"]
    assert len(gk) > 30, "keepers should be projected like everyone else"
    assert gk["width"].mean() > df[df["grp"] != "GK"]["width"].mean()


def test_gk_ratings_span_seasons(d):
    """The upstream fix this depends on: keeper ratings must exist across the
    panel, not just in the current season, or GK projections have nothing behind
    them. SofaScore does not populate 2014/15, so that season is expected empty."""
    df = d.con.execute("""
        SELECT season, count(*) AS n FROM player_ratings_combined
        WHERE scope = 'league' AND position_group = 'GK' GROUP BY 1 ORDER BY 1""").df()
    assert len(df) >= 10, "GK ratings should cover most of the collected seasons"
    assert (df[df["season"] != "1415"]["n"] > 80).all()


def test_aging_curve_shape(d):
    """The measured curve, not the model: young players gain rating relative to
    their peers and old players lose it. If this ever inverts, something upstream
    is broken — it is the one result no football dataset is allowed to contradict."""
    c = d.web_aging_curves()
    assert c["available"]
    allc = {p["age"]: p["delta"] for p in c["curves"]["ALL"]}
    young = [v for a, v in allc.items() if a <= 21]
    old = [v for a, v in allc.items() if a >= 33]
    assert young and old
    assert sum(young) / len(young) > 0, "under-21s should still be improving"
    assert sum(old) / len(old) < 0, "33+ should be declining relative to peers"
    assert c["peaks"]["ALL"] is None or 23 <= c["peaks"]["ALL"] <= 31


# --------------------------------------------------------------------------- #
# Substance — refits the model, so slow
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_beats_persistence_on_every_held_out_season():
    """The claim the UI makes. Persistence ("he'll be exactly as good as he was")
    is a strong baseline for a percentile rating, so beating it on all three
    blind seasons — not just on average — is the bar."""
    from ml.train_trajectory import train
    m = train(write=False)
    assert m["mae"] < m["base_mae"]
    assert m["skill_pct"] > 5
    for s in m["per_season"]:
        assert s["mae"] < s["base_mae"], f"no skill in {s['season']}"
    assert m["direction_acc"] > 0.55, "coin-flipping on direction is not a projection"
    assert m["avail_auc"] > 0.7
    # an 80% interval that covers 60% of the time is a lie, not an error bar
    assert 0.74 <= m["coverage"] <= 0.86
    # and it has to be better *conditioned* than the flat band it replaced: the
    # flat one over-covers weak players and under-covers strong ones
    spread = lambda k: max(b[k] for b in m["by_level"]) - min(b[k] for b in m["by_level"])
    assert spread("cover") < spread("cover_const")


if __name__ == "__main__":
    with db() as conn:
        if not _trained(conn):
            print("Model not trained. Run: PYTHONPATH=. python -m ml.train_trajectory")
            raise SystemExit(1)
        b = conn.web_trajectory_board(FOCUS_SEASON, limit=8)
        m = b["model"]
        print(f"\nProjections for {m['target_label']} "
              f"— MAE {m['mae']} vs {m['base_mae']} persistence "
              f"({m['skill_pct']}% skill), n={m['n_projected']}\n")
        for title, key in [("Risers", "risers"), ("Fallers", "fallers"),
                           ("Breakouts", "breakouts")]:
            print(f"--- {title} ---")
            for r in b[key]:
                print(f"  {r['player']:<24} {r['rating_now']:>3} -> {r['projected']:>5.1f} "
                      f"({r['delta']:+.1f})  {r['verdict']}")
            print()
