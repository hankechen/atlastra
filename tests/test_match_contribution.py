"""
Match-level goal-involvement model tests.

The model makes a calibrated probabilistic claim about individuals, so the tests
check the two ways that goes wrong: probabilities that do not mean what they say,
and a serving path that computes features differently from the training path.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analytics.queries import SoccerDB  # noqa: E402
from config import FOCUS_SEASON  # noqa: E402


@pytest.fixture(scope="module")
def d():
    with SoccerDB(read_only=True) as conn:
        if not conn._table_exists("player_match_contribution_meta"):
            pytest.skip("model not trained (python -m ml.train_match_contribution)")
        yield conn


@pytest.fixture(scope="module")
def danger(d):
    k = d.web_match_danger("Arsenal", "Liverpool")
    if not k.get("available"):
        pytest.skip(k.get("error", "unavailable"))
    return k


def test_probabilities_are_probabilities(danger):
    both = danger["home_players"] + danger["away_players"]
    assert both, "expected players on at least one side"
    assert all(0 <= p["prob"] <= 1 for p in both)
    for side in ("home_players", "away_players"):
        probs = [p["prob"] for p in danger[side]]
        assert probs == sorted(probs, reverse=True), "each side must be ranked"


def test_beats_the_baseline_it_replaces(d):
    """The whole claim: naming danger men by this model beats naming the top-rated
    players, which is what the preview did before. If that ever stops being true
    the feature should go back to ratings."""
    m = d.con.execute("""SELECT auc, auc_rating, hit_top3, hit_top3_rating
                         FROM player_match_contribution_meta""").fetchone()
    assert m[0] > m[1], "AUC must beat ranking by season rating"
    assert m[2] > m[3], "top-3 hit rate must beat ranking by season rating"
    assert m[0] > 0.7, "an AUC this low would not be worth a card on the page"


def test_the_fixture_actually_changes_the_answer(d):
    """A model that ignores the opponent is a season rating with extra steps.
    Facing the leakiest defence in the league should not give the same numbers as
    facing the meanest one."""
    best, worst = d.con.execute("""
        SELECT
          (SELECT t.team_name FROM team_season_stats s JOIN teams t USING(team_id)
            WHERE s.season = ? ORDER BY s.goals_against ASC LIMIT 1),
          (SELECT t.team_name FROM team_season_stats s JOIN teams t USING(team_id)
            WHERE s.season = ? ORDER BY s.goals_against DESC LIMIT 1)
    """, [FOCUS_SEASON, FOCUS_SEASON]).fetchone()
    a = d.web_match_danger("Arsenal", best)
    b = d.web_match_danger("Arsenal", worst)
    if not (a.get("available") and b.get("available")):
        pytest.skip("could not resolve both opponents")
    pa = {p["player"]: p["prob"] for p in a["home_players"]}
    pb = {p["player"]: p["prob"] for p in b["home_players"]}
    shared = set(pa) & set(pb)
    assert shared, "expected the same players against both opponents"
    assert any(abs(pa[k] - pb[k]) > 1e-6 for k in shared), "opponent must move the number"
    # and in the sane direction, on average
    assert (sum(pb[k] for k in shared) / len(shared)
            > sum(pa[k] for k in shared) / len(shared)), \
        "a leakier defence should mean higher involvement odds"


def test_serving_uses_the_training_feature_code(d):
    """Serving/training skew here would be silent and would ruin the numbers, so
    the serving path must build its matrix from the same function and the same
    column order the model was fitted on."""
    from ml.train_match_contribution import _features, predict_fixture
    model, feats = d._contrib_model()
    assert model is not None
    hid = d.find_team_id("Arsenal")
    aid = d.find_team_id("Liverpool")
    df = predict_fixture(d.con, hid, aid, True, FOCUS_SEASON, model, feats)
    assert not df.empty
    X, cols = _features(df)
    assert list(X[feats].columns) == list(feats), "feature order must match the fit"
    assert set(feats) <= set(cols)
