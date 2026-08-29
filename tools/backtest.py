"""Score the Tactics Lab engine against real results.

    python -m tools.backtest              # 1X2 match odds + season points, 2025/26
    python -m tools.backtest --season 2425

This exists so a change to the engine can be MEASURED rather than argued about. Every
number the Lab shows is downstream of _metrics and _project, and until this harness existed
neither had ever been compared with an outcome — which is how a set of reference constants
that no player could reach survived in the card synthesiser, and how the points curve
under-projected every club in Europe by six points a season without anyone noticing.

What it measures
  in-play odds     the same, for the win probability shown on a live match page, scored at
                   half-time against every historical match whose half-time score we hold.
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


def _ensemble_probs(db):
    """The pieces every ensemble check shares: the results-only model's held-out
    predictions, keyed by (season, home_team_id, away_team_id). Neither
    webapp/tactics.py nor webapp/live_feed_fotmob.py is touched anywhere in this
    file -- fit_held_out() trains through 2223, same cutoff that module's own
    reported metrics use, so scoring it here on 2324+ is a fair comparison, not
    the optimistic in-sample number the final refit-on-everything artifact would
    give."""
    from ml.train_match_outcome import fit_held_out
    model, df, X, _y, _tr, te, classes, *_ = fit_held_out()
    Pte = model.predict_proba(X[te].values)
    dft = df[te].reset_index(drop=True)
    key_to_p = {(r.season, int(r.home_team_id), int(r.away_team_id)): Pte[i]
                for i, r in enumerate(dft.itertuples())}
    return key_to_p, classes


def _card_ml_pairs(db, season, key_to_p, classes):
    """(card_probs, ml_probs, actual) for every match in `season` that both models can
    score -- a buildable auto-XI for the card engine AND inside the ML model's held-out
    window. Reused by both backtest_ensemble (one season) and backtest_ensemble_weight
    (validation + test seasons), so the matching logic can't drift between them."""
    rows = _matches(db, season)
    units = _squads(db, rows)
    D = T.DEFAULT_TACTICS
    card, ml_p, actual = [], [], []
    for r in rows:
        u, ou = units.get(r["home"]), units.get(r["away"])
        if not u or not ou:
            continue
        hid, aid = db.find_team_id(r["home"]), db.find_team_id(r["away"])
        p_res = key_to_p.get((season, hid, aid))
        if p_res is None:
            continue                                       # not in the held-out window
        act = "H" if r["gf"] > r["ga"] else ("D" if r["gf"] == r["ga"] else "A")
        xh = T._metrics(u, D, ou, D)["xg"] * T._UCL_HOME_XG
        xa = T._metrics(ou, D, u, D)["xg"] * T._UCL_AWAY_XG
        w = T._win_probs(xh, xa, T._XG_SHAPE)
        card.append({"H": w["home"] / 100, "D": w["draw"] / 100, "A": w["away"] / 100})
        ml_p.append(dict(zip(classes, p_res)))
        actual.append(act)
    return card, ml_p, actual


def _blend_score(card, ml_p, actual, w):
    """log loss/Brier/top-pick for a fixed card-side weight w (1-w on the ML model)."""
    preds = [(w * c["H"] + (1 - w) * m["H"], w * c["D"] + (1 - w) * m["D"],
              w * c["A"] + (1 - w) * m["A"], a)
             for c, m, a in zip(card, ml_p, actual)]
    return _score(preds)


def backtest_ensemble(db, season):
    """Does blending the card-based Tactics Lab engine with ml.train_match_outcome's
    independent results-only model actually beat either alone, at a naive 50/50 weight?
    (backtest_ensemble_weight below checks whether 50/50 is even the RIGHT weight.)"""
    key_to_p, classes = _ensemble_probs(db)
    card, ml_p, actual = _card_ml_pairs(db, season, key_to_p, classes)
    if not card:
        print(f"\n=== ensemble, {season} ===\n  no matches with both a buildable XI and "
              "results-model coverage (is this season inside the ML model's held-out window?)")
        return
    print(f"\n=== ensemble: Tactics Lab (card) + results-only ML, {season} ===")
    print(f"  {len(card)} matches scorable by both\n")
    print(f"  {'model':<32}{'log loss':>10}{'Brier':>9}{'top-pick':>10}")
    for label, w in (("Tactics Lab (card)", 1.0), ("results-only ML", 0.0),
                     ("ensemble (50/50 avg)", 0.5)):
        ll, br, acc = _blend_score(card, ml_p, actual, w)
        print(f"  {label:<32}{ll:>10.4f}{br:>9.4f}{acc:>9.1f}%")


VALIDATION_SEASONS = ("2324", "2425")
FINAL_TEST_SEASON = "2526"


def backtest_ensemble_weight(db):
    """Don't just assume 50/50 -- fit the blend weight, and report the honest result on
    a season the search never saw. 2324+2425 is the VALIDATION slice (search the
    card-model weight w over [0, 1]); 2526 is the TEST slice, touched only once, after
    the weight is already fixed -- so the headline number isn't inflated by picking the
    weight and grading it on the same matches it was picked from."""
    key_to_p, classes = _ensemble_probs(db)
    val_card, val_ml, val_act = [], [], []
    for s in VALIDATION_SEASONS:
        c, m, a = _card_ml_pairs(db, s, key_to_p, classes)
        val_card += c
        val_ml += m
        val_act += a
    if not val_card:
        print("\n=== ensemble weight search ===\n  no validation matches scorable")
        return
    test_card, test_ml, test_act = _card_ml_pairs(db, FINAL_TEST_SEASON, key_to_p, classes)
    if not test_card:
        print(f"\n=== ensemble weight search ===\n  no {FINAL_TEST_SEASON} test matches scorable")
        return

    print(f"\n=== ensemble weight search — validation: {'+'.join(VALIDATION_SEASONS)} "
          f"({len(val_card)} matches) ===")
    print(f"  {'card weight':>12}{'log loss':>10}{'Brier':>9}{'top-pick':>10}")
    grid = [round(x * 0.05, 2) for x in range(21)]
    scored = {w: _blend_score(val_card, val_ml, val_act, w) for w in grid}
    best_w = min(scored, key=lambda w: scored[w][0])
    shown = sorted({0.0, 0.25, 0.5, 0.75, 1.0, best_w})
    for w in shown:
        ll, br, acc = scored[w]
        print(f"  {w:>12.2f}{ll:>10.4f}{br:>9.4f}{acc:>9.1f}%"
              + ("  <- best" if w == best_w else ""))
    print(f"\n  best card-side weight on validation: {best_w:.2f} "
          f"(results-only weight {1 - best_w:.2f})")

    print(f"\n=== final test — {FINAL_TEST_SEASON} ({len(test_card)} matches, NEVER used "
          "to pick the weight) ===")
    print(f"  {'model':<32}{'log loss':>10}{'Brier':>9}{'top-pick':>10}")
    for label, w in (("card-only", 1.0), ("results-only ML", 0.0), ("naive 50/50", 0.5),
                     (f"tuned blend (card={best_w:.2f})", best_w)):
        ll, br, acc = _blend_score(test_card, test_ml, test_act, w)
        print(f"  {label:<32}{ll:>10.4f}{br:>9.4f}{acc:>9.1f}%")


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


def backtest_live(db):
    """The in-play model, tested where we can actually test it: half-time.

    matches_history holds the half-time score for 10,955 matches, which is the one historical
    snapshot of a match IN PROGRESS the warehouse has. Feeding the model league-average rates
    isolates what it adds over knowing nothing — the score-and-clock arithmetic — since no
    squad information from 2008-14 reaches it.
    """
    rows = db.con.execute(
        """SELECT home_goals, away_goals, home_goals_ht, away_goals_ht
           FROM matches_history
           WHERE home_goals IS NOT NULL AND home_goals_ht IS NOT NULL""").fetchall()
    if not rows:
        print("\n=== in-play win probability ===\n  no half-time scores in the warehouse")
        return
    hg = sum(r[0] for r in rows) / len(rows)
    ag = sum(r[1] for r in rows) / len(rows)
    live, pre, acts = [], [], []
    for h, a, hh, ah in rows:
        act = "H" if h > a else ("D" if h == a else "A")
        acts.append(act)
        w = T.live_win_probs(hg, ag, 45, int(hh), int(ah))
        live.append((w["home"] / 100, w["draw"] / 100, w["away"] / 100, act))
        w0 = T.live_win_probs(hg, ag, 0, 0, 0)
        pre.append((w0["home"] / 100, w0["draw"] / 100, w0["away"] / 100, act))
    c, n = Counter(acts), len(acts)
    base = [(c["H"] / n, c["D"] / n, c["A"] / n, a) for a in acts]
    print(f"\n=== in-play win probability, at half-time ===")
    print(f"  {n} matches · league-average rates {hg:.2f}-{ag:.2f}\n")
    print(f"  {'model':<36}{'log loss':>10}{'Brier':>9}{'top-pick':>10}")
    for label, preds in (("at half-time (score + clock)", live),
                         ("same model before kick-off", pre),
                         ("baseline: base rates", base)):
        ll, br, acc = _score(preds)
        print(f"  {label:<36}{ll:>10.4f}{br:>9.4f}{acc:>9.1f}%")
    buckets = defaultdict(list)
    for ph, _pd, _pa, act in live:
        buckets[min(9, int(ph * 10))].append(1 if act == "H" else 0)
    print("\n  calibration — predicted home win vs actual:")
    for b in sorted(buckets):
        v = buckets[b]
        if len(v) < 40:
            continue
        print(f"     {b*10:>2}-{b*10+10:>3}%   n={len(v):>5}   actual {100*sum(v)/len(v):>5.1f}%")


def backtest_wc(db, n=9000):
    """The World Cup sim against the last five World Cups.

    356 real matches, bucketed by the FIFA-ranking gap between the two sides, because that is
    the thing the tournament model has to get right: international football is a far greater
    leveller than the club game, and a campaign engine tuned on Champions League ties is much
    too sure of the favourite. The check is deliberately on UPSET RATES rather than on who
    won any particular match — a 48-team tournament is mostly variance, and a model that
    matched the 2022 bracket exactly would be overfitted to one draw.
    """
    real = db.con.execute("""
        SELECT m.home_goals, m.away_goals, fh.ranking, fa.ranking
        FROM wc_matches m
        LEFT JOIN fifa_rankings fh ON fh.team_name = m.home_name
        LEFT JOIN fifa_rankings fa ON fa.team_name = m.away_name
        WHERE m.home_goals IS NOT NULL AND fh.ranking IS NOT NULL
          AND fa.ranking IS NOT NULL""").fetchall()
    if not real:
        print("\n=== World Cup ===\n  no historical World Cup matches")
        return

    def bucket(gap):
        return "0-10" if gap <= 10 else "11-25" if gap <= 25 else "26-50" if gap <= 50 else "51+"
    obs = defaultdict(lambda: [0, 0])
    tot = 0
    for hg, ag, rh, ra in real:
        tot += hg + ag
        fav_home = rh < ra
        fg, dg = (hg, ag) if fav_home else (ag, hg)
        b = obs[bucket(abs(rh - ra))]
        b[0] += fg > dg
        b[1] += 1
    from webapp import server as _srv
    field = _srv._wc_field(db)
    sides = {}
    for r in field:
        o = _srv._wc_opponent(db, r)
        if o:
            o["rank"] = r["rank"]
            sides[r["name"]] = o
    if len(sides) < 8:
        print("\n=== World Cup ===\n  could not build the field")
        return
    names = list(sides)
    rng = __import__("random").Random(4)
    sim = defaultdict(lambda: [0, 0])
    sgoals = 0
    for _ in range(n):
        a, c = rng.sample(names, 2)
        A, B = sides[a], sides[c]
        gf, ga = T._wc_neutral(rng, A, B)
        sgoals += gf + ga
        fav_a = A["rank"] < B["rank"]
        fg, dg = (gf, ga) if fav_a else (ga, gf)
        b = sim[bucket(abs(A["rank"] - B["rank"]))]
        b[0] += fg > dg
        b[1] += 1
    print(f"\n=== World Cup — {len(real)} real matches vs {n} simulated ===")
    print(f"  goals per match: sim {sgoals / n:.2f} · real {tot / len(real):.2f}\n")
    print(f"  {'rank gap':<10}{'sim fav%':>10}{'real fav%':>11}{'diff':>8}{'real n':>9}")
    err = 0.0
    for k in ("0-10", "11-25", "26-50", "51+"):
        if not obs[k][1] or not sim[k][1]:
            continue
        rp = 100.0 * obs[k][0] / obs[k][1]
        sp = 100.0 * sim[k][0] / sim[k][1]
        err += abs(sp - rp)
        print(f"  {k:<10}{sp:>9.1f}%{rp:>10.1f}%{sp - rp:>+8.1f}{obs[k][1]:>9}")
    print(f"  mean absolute error {err / 4:.2f} points")
    print("  (the 51+ bucket holds ~30 real matches, so treat its gap as noise)")


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
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument("--wc", action="store_true", help="also check the World Cup sim")
    ap.add_argument("--ensemble", action="store_true",
                    help="also check blending in ml.train_match_outcome's results-only model")
    ap.add_argument("--ensemble-weight", action="store_true",
                    help="fit the blend weight on 2324+2425, report the honest result on 2526")
    args = ap.parse_args()
    with SoccerDB(read_only=True) as db:
        if not args.skip_odds:
            backtest_odds(db, args.season)
        if not args.skip_points:
            backtest_points(db, args.season)
        if not args.skip_live:
            backtest_live(db)
        if args.ensemble:
            backtest_ensemble(db, args.season)
        if args.ensemble_weight:
            backtest_ensemble_weight(db)
        if args.wc:
            backtest_wc(db)
        if args.weaknesses:
            backtest_weaknesses(db, args.season)


if __name__ == "__main__":
    main()
