"""
Availability / absence-spell tests.

These are derived numbers with no source to check against, so the tests do two
things: prove the window logic is internally consistent, and check a handful of
seasons whose real history is a matter of public record. If Rodri's 2024/25 does
not come back as a season-long absence, the derivation is wrong no matter how
tidy the SQL is.
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
        if not conn._table_exists("player_availability"):
            pytest.skip("absences not built (run python -m pipeline.build_absences)")
        yield conn


def _one(d, name, season):
    r = d.con.execute("""
        SELECT sum(a.window_matches), sum(a.played), max(a.longest_spell)
        FROM player_availability a JOIN players p USING(player_id)
        WHERE p.player_name = ? AND a.season = ?""", [name, season]).fetchone()
    return None if not r or not r[0] else {"window": int(r[0]), "played": int(r[1]),
                                           "longest": int(r[2] or 0)}


# --------------------------------------------------------------------------- #
# Internal consistency
# --------------------------------------------------------------------------- #
def test_window_arithmetic_adds_up(d):
    """Played + missed must equal the window, every time — the spells are derived
    from the same grid as the window, so any mismatch means the join is wrong."""
    bad = d.con.execute("""
        SELECT count(*) FROM player_availability
        WHERE played + matches_missed <> window_matches""").fetchone()[0]
    assert bad == 0


def test_availability_is_a_percentage(d):
    row = d.con.execute("""
        SELECT min(availability_pct), max(availability_pct), min(played), min(window_matches)
        FROM player_availability""").fetchone()
    assert 0 <= row[0] and row[1] <= 100
    assert row[2] >= 1, "a window starts at a player's first appearance, so he played at least once"
    assert row[3] >= 1


def test_spells_sit_inside_the_window(d):
    """An absence can only be counted between a player's first appearance for a
    club and the end of his stint there. A spell outside that means we are
    charging him for matches played before he signed or after he left."""
    bad = d.con.execute("""
        SELECT count(*) FROM player_absence_spell s
        JOIN player_availability a
          ON a.player_id = s.player_id AND a.season = s.season AND a.team_id = s.team_id
        WHERE s.start_date < a.win_from OR s.end_date > a.win_to""").fetchone()[0]
    assert bad == 0


def test_midseason_signing_is_not_marked_absent_for_his_old_club(d):
    """The failure mode this design exists to avoid: a player who moves in January
    must not appear to have missed half a season through injury at either club."""
    movers = d.con.execute("""
        SELECT player_id, season FROM player_availability
        GROUP BY 1, 2 HAVING count(*) > 1 LIMIT 50""").fetchall()
    if not movers:
        pytest.skip("no mid-season movers in the panel")
    for pid, season in movers:
        # cast both ends, since one is a match timestamp and the other a date
        rows = d.con.execute("""
            SELECT CAST(win_from AS DATE), CAST(win_to AS DATE) FROM player_availability
            WHERE player_id = ? AND season = ? ORDER BY win_from""", [pid, season]).fetchall()
        for a, b in zip(rows, rows[1:]):
            assert a[1] < b[0], "a player's stints at two clubs must not overlap"


# --------------------------------------------------------------------------- #
# Against seasons whose history is known
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,season,max_played,min_spell", [
    ("Rodri", "2425", 8, 20),      # ruptured his ACL in September 2024, out for the season
    ("Gavi", "2324", 20, 15),      # ruptured his ACL in November 2023
])
def test_known_season_ending_injuries(d, name, season, max_played, min_spell):
    got = _one(d, name, season)
    if got is None:
        pytest.skip(f"{name} {season} not in the panel")
    assert got["played"] <= max_played, f"{name} {season}: played {got['played']}"
    assert got["longest"] >= min_spell, f"{name} {season}: longest spell {got['longest']}"


@pytest.mark.parametrize("name,season", [("Bruno Fernandes", "2425")])
def test_known_ever_presents(d, name, season):
    """The other direction — a famously ever-present player must not show gaps."""
    got = _one(d, name, season)
    if got is None:
        pytest.skip(f"{name} {season} not in the panel")
    assert got["played"] / got["window"] >= 0.9
    assert got["longest"] <= 2


# --------------------------------------------------------------------------- #
# The API and the published relationship
# --------------------------------------------------------------------------- #
def test_player_api(d):
    a = d.web_availability("Rodri", FOCUS_SEASON)
    if not a.get("available"):
        pytest.skip("Rodri has no window this season")
    assert 0 <= a["pct"] <= 100
    assert a["played"] + a["missed"] == a["window_matches"]
    assert a["verdict_class"] in {"great", "good", "neutral", "bad"}
    assert len(a["career"]) >= 1
    assert all(0 <= c["pct"] <= 100 for c in a["career"])


def test_absence_risk_is_monotonic(d):
    """The one claim the card makes about the future: more absence this season
    means more absence next season. If that ordering ever breaks, the sentence on
    the page is false and should not be shown."""
    risk = d._absence_risk()
    assert risk and len(risk) == 3
    assert [r["prior"] for r in risk] == ["none", "5-9", "10+"]
    rates = [r["rate"] for r in risk]
    assert rates == sorted(rates), f"risk should rise with prior absence, got {rates}"
    assert all(r["n"] >= 100 for r in risk)
