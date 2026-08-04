"""
Squad Planner tests.

The planner is opinionated — it tells a club what to buy — so the tests are
mostly about it not being confidently wrong: that positions cover for one
another, that a covering player is never misrepresented, and that a suggested
target is actually an improvement and actually available.

Skips cleanly when the trajectory model has not been trained yet.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analytics.queries import SoccerDB  # noqa: E402
from config import FOCUS_SEASON  # noqa: E402

CLUB = "Real Madrid"


@pytest.fixture(scope="module")
def d():
    with SoccerDB(read_only=True) as conn:
        if not conn._table_exists("player_trajectory"):
            pytest.skip("trajectory model not trained (run python -m ml.train_trajectory)")
        yield conn


@pytest.fixture(scope="module")
def plan(d):
    p = d.web_squad_plan(CLUB)
    if not p.get("available"):
        pytest.skip(p.get("error", "no plan"))
    return p


def test_plan_shape(plan):
    """Every position is reported, ordered by how badly it needs attention."""
    assert plan["team"]
    groups = [u["group"] for u in plan["units"]]
    assert set(groups) == set(SoccerDB.UNIT_LABELS)
    prios = [u["priority"] for u in plan["units"]]
    assert prios == sorted(prios, reverse=True)
    assert 1 <= len(plan["needs"]) <= 3
    assert set(plan["targets"]) == set(plan["needs"])


def test_every_unit_says_why(plan):
    """A priority with no stated reason is an oracle, not an analysis."""
    for u in plan["units"]:
        assert u["reasons"], f"{u['group']} has no reasons"
        assert all(isinstance(r, str) and r for r in u["reasons"])


def test_positions_cover_for_one_another(d):
    """The regression this feature was rebuilt around. Manchester United's
    midfielders are classified DM and AM with nobody labelled CM, and the first
    version told a club with Casemiro, Ugarte, Mainoo, Fernandes and Mount that
    its most urgent need was a central midfielder. Adjacent positions must count."""
    p = d.web_squad_plan("Manchester United")
    if not p.get("available"):
        pytest.skip("no plan for Manchester United")
    cm = next(u for u in p["units"] if u["group"] == "CM")
    assert cm["cover_depth"] > 0, "DMs and AMs should cover central midfield"
    assert cm["best_now"] is not None, "a covered unit is not an empty unit"


def test_cover_players_keep_their_own_rating(plan):
    """A covering player is shown at his real rating and counted at a discount.
    Displaying an 83-rated midfielder as '71' would misstate the player."""
    seen = False
    for u in plan["units"]:
        for p in u["players"]:
            if p.get("cover_from"):
                seen = True
                assert p["eff_rating"] < p["rating"], "cover must be discounted"
                assert p["cover_weight"] < 1
            else:
                assert p["eff_rating"] == float(p["rating"])
    assert seen, "expected at least one covering player somewhere in the squad"


def test_unit_strength_uses_the_discounted_value(plan):
    """best_now is the unit's effective strength, so it can never exceed the best
    effective rating in it — a discounted cover player must not set the bar."""
    for u in plan["units"]:
        if not u["players"]:
            continue
        assert u["best_now"] == pytest.approx(
            max(p["eff_rating"] for p in u["players"]), abs=0.06)


def test_targets_are_improvements_from_elsewhere(d, plan):
    """A suggestion has to beat what the club already has, and not already be there."""
    squad = {p["player"] for u in plan["units"] for p in u["players"]}
    for grp, targets in plan["targets"].items():
        unit = next(u for u in plan["units"] if u["group"] == grp)
        bar = unit["best_next"] if unit["best_next"] is not None else (unit["best_now"] or 0)
        for t in targets:
            assert t["player"] not in squad, f"{t['player']} already at the club"
            assert t["team"] != plan["team"]
            assert t["projected"] > bar


def test_benchmark_is_measured_not_invented(d, plan):
    """"What a strong club has" is the 80th percentile of every top-5 club's best
    player in that position — so it must sit between the median and the maximum."""
    for u in plan["units"]:
        row = d.con.execute("""
            WITH s AS (
                SELECT ps.team_id, c.rating
                FROM player_season_stats ps
                JOIN player_ratings_combined c
                     ON c.player_id = ps.player_id AND c.season = ps.season AND c.scope='league'
                WHERE ps.season = ? AND c.position_group = ?),
            best AS (SELECT team_id, max(rating) AS r FROM s GROUP BY 1)
            SELECT median(r), max(r) FROM best""", [FOCUS_SEASON, u["group"]]).fetchone()
        assert row[0] <= u["benchmark"] <= row[1]


def test_horizon_only_ages_players_forward(d):
    """Looking further ahead can only move the projection along the aging curve,
    so a longer horizon must not make an ageing unit look healthier."""
    short = d.web_squad_plan(CLUB, horizon=2)
    long = d.web_squad_plan(CLUB, horizon=4)
    if not (short.get("available") and long.get("available")):
        pytest.skip("no plan")
    old = {u["group"]: u for u in short["units"] if (u["mean_age"] or 0) >= 29}
    for grp, u in old.items():
        v = next(x for x in long["units"] if x["group"] == grp)
        if u["best_horizon"] is not None and v["best_horizon"] is not None:
            assert v["best_horizon"] <= u["best_horizon"] + 0.05


# --------------------------------------------------------------------------- #
# Projected table ("if nobody moves")
# --------------------------------------------------------------------------- #
def test_projected_table(d):
    """The table has to be a table: contiguous positions, sorted by points, and
    every club that plays in the league."""
    t = d.web_projected_table("ENG-Premier League")
    if not t.get("available"):
        pytest.skip(t.get("error", "unavailable"))
    rows = t["tables"]["ENG-Premier League"]
    assert len(rows) >= 15
    assert [r["pos"] for r in rows] == list(range(1, len(rows) + 1))
    pts = [r["projected_points"] for r in rows]
    assert pts == sorted(pts, reverse=True)
    assert all(0 <= r["projected_points"] <= 114 for r in rows)
    # the move column must agree with the two positions it is derived from
    assert all(r["move"] == r["position_now"] - r["pos"] for r in rows)


def test_projected_table_beats_persistence(d):
    """The only reason to show it. If assuming this season simply repeats were as
    good, the honest thing would be to show this season's table."""
    fit = d._table_fit()
    if not fit:
        pytest.skip("not enough history")
    assert fit["mae"] < fit["mae_persistence"]
    assert fit["n"] >= 300
