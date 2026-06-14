"""
Phase-One use-case tests / demonstrations.

Doubles as:
  * a pytest suite      ->  python -m pytest tests/test_use_cases.py -q
  * a readable demo     ->  python tests/test_use_cases.py

Each test maps to a README Phase-One use case (1-8) and asserts the pipeline
produced sane, queryable data. The demo runner prints the same queries in a
human-readable form so you can eyeball the results.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analytics.queries import SoccerDB  # noqa: E402
from config import FOCUS_SEASON, LEAGUES  # noqa: E402

PL = "ENG-Premier League"


def db() -> SoccerDB:
    return SoccerDB(read_only=True)


# --------------------------------------------------------------------------- #
# Use case 1: Player Statistics
# --------------------------------------------------------------------------- #
def test_player_statistics():
    with db() as d:
        df = d.player_statistics("Haaland", FOCUS_SEASON)
        assert not df.empty
        row = df.iloc[0]
        assert row["goals"] > 0 and row["minutes"] > 0
        # core Understat stats present
        for col in ["goals", "assists", "chances_created", "xg", "xa", "shots"]:
            assert col in df.columns
        # FotMob enrichment stats present (the README's defensive/duel gaps)
        for col in ["dribbles_completed", "tackles", "interceptions",
                    "big_chances_missed", "passes_completed", "fotmob_rating",
                    "duels_won", "duels_won_pct"]:
            assert col in df.columns


def test_enrichment_present():
    """FotMob enrichment loaded and joinable for a well-known player."""
    with db() as d:
        df = d.player_statistics("Bruno Fernandes", FOCUS_SEASON)
        assert df.iloc[0]["tackles"] is not None
        # league leaders for a gap stat work
        lead = d.stat_leaders("interceptions", PL, FOCUS_SEASON, limit=5)
        assert len(lead) == 5
        assert lead["interceptions"].is_monotonic_decreasing


# --------------------------------------------------------------------------- #
# Use case 2: Player Classification
# --------------------------------------------------------------------------- #
def test_player_classification():
    valid = {"Best In Position", "World-Class", "Elite",
             "Above Average", "Average", "Below Average"}
    with db() as d:
        cls = d.player_classification("Haaland", FOCUS_SEASON)
        assert not cls.empty
        assert cls.iloc[0]["classification"] in valid

        top = d.best_in_position(PL, "FWD", FOCUS_SEASON, limit=5)
        assert len(top) == 5
        assert top.iloc[0]["rank"] == 1
        assert top.iloc[0]["classification"] == "Best In Position"
        # ratings are sorted best-first
        assert top["rating"].is_monotonic_decreasing


# --------------------------------------------------------------------------- #
# Use case 3: Player Profile
# --------------------------------------------------------------------------- #
def test_player_profile():
    with db() as d:
        prof = d.player_profile("Haaland")
        assert prof["player_name"]
        assert prof["position_group"] == "FWD"
        assert isinstance(prof["career"], pd.DataFrame) and not prof["career"].empty


# --------------------------------------------------------------------------- #
# Use case 4: Cross-Year Progression
# --------------------------------------------------------------------------- #
def test_player_progression():
    with db() as d:
        prog = d.player_progression("Haaland")
        # we pulled two seasons, so an ever-present should have multiple rows
        assert prog["season"].nunique() >= 2
        assert "ga_per90" in prog.columns


# --------------------------------------------------------------------------- #
# Use case 5: Player Comparison
# --------------------------------------------------------------------------- #
def test_compare_players():
    with db() as d:
        cmp = d.compare_players(["Haaland", "Mbappé"], FOCUS_SEASON)
        # transposed: stats as rows, players as columns
        assert cmp.shape[1] >= 1
        assert "goals" in cmp.index


# --------------------------------------------------------------------------- #
# Use case 6: Team Performance / League Standing
# --------------------------------------------------------------------------- #
def test_league_standings():
    with db() as d:
        table = d.league_standings(PL, FOCUS_SEASON)
        assert len(table) == 20
        assert list(table["pos"]) == sorted(table["pos"])
        # points are non-increasing down the table
        assert table["pts"].is_monotonic_decreasing
        # internal consistency: w + d + l == matches played
        assert ((table["w"] + table["d"] + table["l"]) == table["mp"]).all()


def test_team_form():
    with db() as d:
        form = d.team_form("Arsenal", FOCUS_SEASON, last=5)
        assert len(form) == 5
        assert set(form["result"]).issubset({"W", "D", "L"})


# --------------------------------------------------------------------------- #
# Use case 7: Team Information
# --------------------------------------------------------------------------- #
def test_team_info():
    with db() as d:
        info = d.team_info("Liverpool", FOCUS_SEASON)
        assert info["team_name"]
        assert not info["squad"].empty
        # manager/venue are documented gaps (Understat does not provide them)
        assert "manager" in info and "venue" in info


# --------------------------------------------------------------------------- #
# Use case 8: Search by Player, Team, or Match
# --------------------------------------------------------------------------- #
def test_search():
    with db() as d:
        assert not d.search_players("Saka", FOCUS_SEASON).empty
        assert not d.search_teams("Manchester").empty
        h2h = d.search_matches("Arsenal", "Tottenham", FOCUS_SEASON)
        assert not h2h.empty  # north London derby happens twice a season


# --------------------------------------------------------------------------- #
# Demo runner
# --------------------------------------------------------------------------- #
def _h(title: str):
    print("\n" + "=" * 78 + f"\n {title}\n" + "=" * 78)


def demo():
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 40)
    with db() as d:
        _h("USE CASE 1 — Player Statistics: Mohamed Salah (2025/26)")
        print(d.player_statistics("Salah").T.to_string())
        print("\n(Understat xG/xA + FotMob dribbles/tackles/interceptions/big-chances/passes)")

        _h("USE CASE 1b — Enrichment leaders: most tackles & big chances created (PL)")
        print("Tackles:")
        print(d.stat_leaders("tackles", PL, limit=5).to_string(index=False))
        print("\nBig chances created:")
        print(d.stat_leaders("big_chances_created", PL, limit=5).to_string(index=False))
        print("\nMost duels won:")
        print(d.stat_leaders("duels_won", PL, limit=5).to_string(index=False))

        _h("USE CASE 2 — Classification: Best forwards in the Premier League")
        print(d.best_in_position(PL, "FWD", limit=10).to_string(index=False))
        print("\nSingle player classification — Bruno Fernandes:")
        print(d.player_classification("Bruno Fernandes").to_string(index=False))

        _h("USE CASE 3 — Player Profile: Erling Haaland")
        prof = d.player_profile("Haaland")
        for k in ("player_name", "main_position", "position_group", "market_value_eur"):
            print(f"  {k:18}: {prof[k]}")
        print("  career:")
        print(prof["career"].to_string(index=False))

        _h("USE CASE 4 — Cross-Year Progression: Erling Haaland (default FWD stats)")
        print(d.player_progression("Haaland").to_string(index=False))

        _h("USE CASE 5 — Comparison: Haaland vs Mbappé vs Kane (2025/26)")
        print(d.compare_players(["Haaland", "Mbappé", "Kane"]).to_string())

        _h("USE CASE 6 — League Standing: Premier League 2025/26 (top 8)")
        print(d.league_standings(PL).head(8).to_string(index=False))
        print("\nArsenal — last 5 matches:")
        print(d.team_form("Arsenal").to_string(index=False))

        _h("USE CASE 7 — Team Information: Liverpool")
        info = d.team_info("Liverpool")
        print(f"  {info['team_name']} ({info['team_code']}) — {info['league']}, {info['country']}")
        print(f"  manager: {info['manager']}   venue: {info['venue']}   (not in Understat)")
        print("  squad (top 10 by minutes):")
        print(info["squad"].head(10).to_string(index=False))

        _h("USE CASE 8 — Search")
        print("Players matching 'Vinic':")
        print(d.search_players("Vinic").to_string(index=False))
        print("\nTeams matching 'Real':")
        print(d.search_teams("Real").to_string(index=False))
        print("\nHead-to-head — Manchester City vs Liverpool:")
        print(d.search_matches("Manchester City", "Liverpool").to_string(index=False))


if __name__ == "__main__":
    demo()
