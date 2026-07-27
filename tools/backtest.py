"""Score the Tactics Lab engine against real results.

    python -m tools.backtest              # 1X2 match odds + season points, 2025/26
    python -m tools.backtest --season 2425

This exists so a change to the engine can be MEASURED rather than argued about. Every
number the Lab shows is downstream of _metrics and _project, and until this harness existed
neither had ever been compared with an outcome — which is how a set of reference constants
that no player could reach survived in the card synthesiser, and how the points curve
under-projected every club in Europe by six points a season without anyone noticing.

What it measures
  1X2 match odds   log loss / Brier / top-pick accuracy against every top-5 result of the
                   season, with two baselines: the season's own outcome base rates (what you
                   score knowing nothing but the home-advantage split) and always-pick-home.
                   Also prints a calibration table — of the matches we call 60-70%, how many
                   actually finished that way.
  season points    the projection against the real final table: mean absolute error, signed
                   bias, and correlation.

Caveats, stated because they matter when reading the output
  * squads are TODAY'S rosters, not the XI that played that day. Recent seasons are a close
    match; older ones drift.
  * both sides use their auto-XI and default tactics — what the app shows before you touch
    anything.
  * the recent-form nudge is left OFF: it is computed over the whole season, so including it
    would be lookahead.
"""
import argparse
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.queries import SoccerDB                      # noqa: E402
from webapp import server, tactics as T                     # noqa: E402


def _score(preds):
    """preds: [(pHome, pDraw, pAway, actual)] -> log loss, Brier, top-pick accuracy."""
    n = len(preds)
    ll = brier = 0.0
    hit = 0
    for ph, pd, pa, act in preds:
        p = {"H": ph, "D": pd, "A": pa}
        ll += -math.log(max(p[act], 1e-12))
        for k in "HDA":
            brier += (p[k] - (1.0 if k == act else 0.0)) ** 2
        if max(p, key=p.get) == act:
            hit += 1
    return ll / n, brier / n, 100.0 * hit / n


def _squads(db, rows):
    """One auto-XI unit vector per club, reused for every match it plays."""
    units, seen = {}, {}
    for r in rows:
        seen[r["home"]] = r["hid"]
        seen[r["away"]] = r["aid"]
    for name, fid in seen.items():
        squad = server._tac_squad(db, name, tid=int(fid))
        xi = T.build_xi(squad, "4-3-3") if squad else []
        if xi and any(s.get("player") for s in xi):
            units[name] = T._units(xi)
    return units


def _matches(db, season):
    rows = db.con.execute("""
        SELECT th.team_name, ta.team_name, m.goals_for, m.goals_against,
               lh.fotmob_team_id, la.fotmob_team_id
        FROM team_match_stats m
        JOIN teams th ON th.team_id = m.team_id
        JOIN teams ta ON ta.team_id = m.opponent_team_id
        JOIN team_logos lh ON lh.team_id = m.team_id
        JOIN team_logos la ON la.team_id = m.opponent_team_id
        WHERE m.season = ? AND m.is_home AND m.goals_for IS NOT NULL
          AND lh.fotmob_team_id IS NOT NULL AND la.fotmob_team_id IS NOT NULL""",
        [season]).fetchall()
    return [{"home": h, "away": a, "gf": gf, "ga": ga, "hid": hid, "aid": aid}
            for h, a, gf, ga, hid, aid in rows]


def backtest_odds(db, season):
    rows = _matches(db, season)
    units = _squads(db, rows)
    D = T.DEFAULT_TACTICS
    eng, actual = [], []
    for r in rows:
        u, ou = units.get(r["home"]), units.get(r["away"])
        if not u or not ou:
            continue
        act = "H" if r["gf"] > r["ga"] else ("D" if r["gf"] == r["ga"] else "A")
        actual.append(act)
        xh = T._metrics(u, D, ou, D)["xg"] * T._UCL_HOME_XG
        xa = T._metrics(ou, D, u, D)["xg"] * T._UCL_AWAY_XG
        w = T._win_probs(xh, xa, T._XG_SHAPE)
        eng.append((w["home"] / 100, w["draw"] / 100, w["away"] / 100, act))
    if not eng:
        print("  no scorable matches"); return
    c, n = Counter(actual), len(actual)
    prior = [(c["H"] / n, c["D"] / n, c["A"] / n, a) for a in actual]
    home = [(0.999, 0.0005, 0.0005, a) for a in actual]
    print(f"\n=== 1X2 match odds, {season} ===")
    print(f"  {n} matches · actual split H {100*c['H']/n:.1f}% D {100*c['D']/n:.1f}% A {100*c['A']/n:.1f}%\n")
    print(f"  {'model':<32}{'log loss':>10}{'Brier':>9}{'top-pick':>10}")
    for label, preds in (("Tactics Lab", eng), ("baseline: base rates", prior),
                         ("baseline: always home", home)):
        ll, br, acc = _score(preds)
        print(f"  {label:<32}{ll:>10.4f}{br:>9.4f}{acc:>9.1f}%")
    buckets = defaultdict(list)
    for ph, _pd, _pa, act in eng:
        buckets[min(9, int(ph * 10))].append(1 if act == "H" else 0)
    print("\n  calibration — predicted home win vs actual:")
    for b in sorted(buckets):
        v = buckets[b]
        if len(v) < 15:
            continue
        print(f"     {b*10:>2}-{b*10+10:>3}%   n={len(v):>4}   actual {100*sum(v)/len(v):>5.1f}%")


def backtest_points(db, season):
    print(f"\n=== season points projection, {season} ===")
    preds, errs = [], []
    for lk in ("ENG-Premier League", "ESP-La Liga", "ITA-Serie A", "GER-Bundesliga", "FRA-Ligue 1"):
        df = db.league_standings(lk, season)
        for r in df.itertuples():
            row = db.con.execute("SELECT fotmob_team_id FROM team_logos WHERE team_name = ?",
                                 [r.team]).fetchone()
            if not row or not row[0]:
                continue
            squad = server._tac_squad(db, r.team, tid=int(row[0]))
            xi = T.build_xi(squad, "4-3-3") if squad else []
            if not xi or not any(s.get("player") for s in xi):
                continue
            proj = T._project(T._units(xi), T.DEFAULT_TACTICS, r.team, 1.0,
                              server._league_ctx(db, r.team))
            if proj.get("kind") != "club":
                continue
            actual = int(r.pts or 0)
            errs.append(proj["points"] - actual)
            preds.append((proj["points"], actual, r.team))
    if not preds:
        print("  no clubs scorable"); return
    n = len(preds)
    mp, ma = st.mean(p for p, _, _ in preds), st.mean(a for _, a, _ in preds)
    cov = sum((p - mp) * (a - ma) for p, a, _ in preds) / n
    corr = cov / (st.pstdev([p for p, _, _ in preds]) * st.pstdev([a for _, a, _ in preds]))
    print(f"  {n} clubs · MAE {st.mean(abs(e) for e in errs):.2f} pts · "
          f"bias {st.mean(errs):+.2f} · correlation {corr:.3f}")
    worst = sorted(preds, key=lambda x: -abs(x[0] - x[1]))[:5]
    print("  biggest misses: " + ", ".join(f"{t} {p} vs {a}" for p, a, t in worst))


def backtest_weaknesses(db, season):
    """Each squad-keyed weakness rule is a claim that a flagged side concedes more than the
    model expects. Test it: mean residual (actual goals against minus expected) for flagged
    clubs versus the rest."""
    import math as _math
    rows = db.con.execute("""
        SELECT th.team_name, ta.team_name, m.goals_against, lh.fotmob_team_id, la.fotmob_team_id
        FROM team_match_stats m
        JOIN teams th ON th.team_id = m.team_id
        JOIN teams ta ON ta.team_id = m.opponent_team_id
        JOIN team_logos lh ON lh.team_id = m.team_id
        JOIN team_logos la ON la.team_id = m.opponent_team_id
        WHERE m.season = ? AND m.goals_against IS NOT NULL
          AND lh.fotmob_team_id IS NOT NULL AND la.fotmob_team_id IS NOT NULL""", [season]).fetchall()
    units = {}
    for h, a, _ga, hid, aid in rows:
        for name, fid in ((h, hid), (a, aid)):
            if name not in units:
                squad = server._tac_squad(db, name, tid=int(fid))
                xi = T.build_xi(squad, "4-3-3") if squad else []
                units[name] = T._units(xi) if xi and any(s.get("player") for s in xi) else None
    resid = {}
    for h, a, ga, _hid, _aid in rows:
        u, ou = units.get(h), units.get(a)
        if not u or not ou:
            continue
        resid.setdefault(h, []).append(ga - T._base_xg(ou, u) * T._UCL_AWAY_XG)
    rules = {"aerial < 68": lambda u: u["aerial"] < 68,
             "def_pace < 68": lambda u: u["def_pace"] < 68,
             "press_resist < 72": lambda u: u["press_resist"] < 72,
             "midfield < 75": lambda u: u["midfield"] < 75,
             "gk < 75": lambda u: u["gk"] < 75}
    print(f"\n=== weakness rules, {season} ===")
    print("  a rule earns its place if flagged clubs concede MORE than the model expects\n")
    print(f"  {'rule':<24}{'flagged':>9}{'flagged':>10}{'others':>9}{'z':>7}")
    for name, fn in rules.items():
        f = [st.mean(resid[t]) for t in resid if units.get(t) and fn(units[t])]
        o = [st.mean(resid[t]) for t in resid if units.get(t) and not fn(units[t])]
        if len(f) < 5 or len(o) < 5:
            print(f"  {name:<24}{len(f):>9}{'too few to test':>26}")
            continue
        se = _math.sqrt(st.pvariance(f) / len(f) + st.pvariance(o) / len(o)) or 1e-9
        z = (st.mean(f) - st.mean(o)) / se
        print(f"  {name:<24}{len(f):>9}{st.mean(f):>+10.3f}{st.mean(o):>+9.3f}{z:>+7.1f}"
              + ("   supported" if z > 1.6 else ("   BACKWARDS" if z < -1.6 else "   no signal")))


def main():
    ap = argparse.ArgumentParser(description="Score the Tactics Lab engine against real results.")
    ap.add_argument("--season", default="2526")
    ap.add_argument("--skip-odds", action="store_true")
    ap.add_argument("--skip-points", action="store_true")
    ap.add_argument("--weaknesses", action="store_true", help="also test the weakness rules")
    args = ap.parse_args()
    with SoccerDB(read_only=True) as db:
        if not args.skip_odds:
            backtest_odds(db, args.season)
        if not args.skip_points:
            backtest_points(db, args.season)
        if args.weaknesses:
            backtest_weaknesses(db, args.season)


if __name__ == "__main__":
    main()
