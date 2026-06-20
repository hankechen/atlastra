"""
One-command Phase-One pipeline: scrape -> (re)create db -> load -> derive.

    python -m pipeline.run_pipeline            # full run
    python -m pipeline.run_pipeline --quick    # focus season, players only
    python -m pipeline.run_pipeline --no-scrape  # reload from cached raw parquet

After it finishes, run the use-case demo:
    python tests/test_use_cases.py
"""
import argparse

from pipeline import scrape as scrape_mod
from pipeline import init_db as init_mod
from pipeline import load as load_mod
from pipeline import scrape_enrich as enrich_scrape_mod
from pipeline import scrape_duels as duels_scrape_mod
from pipeline import load_enrich as enrich_load_mod
from pipeline import scrape_datamb as datamb_scrape_mod
from pipeline import scrape_ucl as ucl_scrape_mod
from pipeline import load_ucl as ucl_load_mod
from pipeline import load_datamb as datamb_load_mod
from pipeline import scrape_sofa_domestic as sofadom_scrape_mod
from pipeline import load_sofa_domestic as sofadom_load_mod
from pipeline import rate as rate_mod
from pipeline import build_views as views_mod
from pipeline import scrape_transfermarkt as tm_scrape_mod
from pipeline import load_transfermarkt as tm_load_mod
from pipeline import profile as profile_mod
from pipeline import rate_combined as rate_combined_mod
from pipeline import scrape_fotmob_positions as fmpos_scrape_mod
from pipeline import load_fotmob_positions as fmpos_load_mod
from pipeline import positions_history as poshist_mod
from pipeline import load_live as live_mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="focus season, players only")
    ap.add_argument("--no-scrape", action="store_true", help="reuse cached raw parquet")
    ap.add_argument("--no-enrich", action="store_true", help="skip FotMob enrichment")
    ap.add_argument("--no-ucl", action="store_true", help="skip SofaScore UCL stats")
    args = ap.parse_args()

    if not args.no_scrape:
        print("### 1. SCRAPE (Understat) ###")
        scrape_mod.scrape(quick=args.quick)
        if not args.no_enrich:
            print("\n### 1b. SCRAPE (FotMob enrichment) ###")
            try:
                enrich_scrape_mod.scrape()
                print("\n### 1c. SCRAPE (FotMob duels) ###")
                duels_scrape_mod.scrape()
            except Exception as e:
                print(f"FotMob enrichment scrape failed ({repr(e)[:80]}); continuing without it.")
            print("\n### 1d. SCRAPE (datamb.football Wyscout stats) ###")
            try:
                datamb_scrape_mod.scrape()
            except Exception as e:
                print(f"datamb scrape failed ({repr(e)[:80]}); continuing without it.")
        if not args.no_ucl:
            print("\n### 1e. SCRAPE (SofaScore Champions League) ###")
            try:
                ucl_scrape_mod.scrape()
            except Exception as e:
                print(f"SofaScore UCL scrape failed ({repr(e)[:80]}); continuing without it.")
        print("\n### 1f. SCRAPE (SofaScore Top-5 defense: clearances/errors) ###")
        try:
            sofadom_scrape_mod.scrape()
        except Exception as e:
            print(f"SofaScore domestic-defense scrape failed ({repr(e)[:80]}); continuing without it.")
        print("\n### 1g. SCRAPE (Transfermarkt market values) ###")
        try:
            tm_scrape_mod.scrape()
        except Exception as e:
            print(f"Transfermarkt scrape failed ({repr(e)[:80]}); continuing without it.")
        print("\n### 1h. SCRAPE (FotMob detailed positions: LW/RW/LB/RB) ###")
        try:
            fmpos_scrape_mod.scrape()
        except Exception as e:
            print(f"FotMob positions scrape failed ({repr(e)[:80]}); continuing without it.")

    print("\n### 2. INIT DB ###")
    init_mod.init_db(reset=True)

    print("\n### 3. LOAD + DERIVE ###")
    load_mod.load_all()

    if not args.no_enrich:
        print("\n### 4. LOAD FotMob ENRICHMENT ###")
        enrich_load_mod.load_enrich()

    if not args.no_ucl:
        print("\n### 5. LOAD SofaScore UCL ###")
        ucl_load_mod.load_ucl()

    print("\n### 6. LOAD datamb (Wyscout) ###")
    datamb_load_mod.load_datamb()
    print("\n### 6b. LOAD SofaScore Top-5 defense (clearances/errors) ###")
    sofadom_load_mod.load_sofa_domestic()

    print("\n### 6c. RESOLVE POSITIONS (FotMob LW/RW/LB/RB/...) ###")
    try:
        fmpos_load_mod.load_fotmob_positions()
    except Exception as e:
        print(f"position resolve skipped ({repr(e)[:80]}); run `python -m pipeline.load_fotmob_positions`.")

    print("\n### 7. PLAYER RATINGS (position-weighted engine) ###")
    try:
        rate_mod.rate()
    except Exception as e:
        print(f"rating engine skipped ({repr(e)[:80]}); "
              f"run `python -m pipeline.rate` once datamb is loaded.")

    print("\n### 8. LOAD Transfermarkt market values ###")
    tm_load_mod.load_transfermarkt()

    print("\n### 9. BUILD STAT VIEWS (UCL / Top-5 / combined / career) ###")
    views_mod.build_views()

    print("\n### 9b. PER-SEASON POSITIONS (stat-derived fine groups for history) ###")
    try:
        poshist_mod.build_positions_history()
    except Exception as e:
        print(f"position history skipped ({repr(e)[:80]}); run `python -m pipeline.positions_history`.")

    print("\n### 10. PLAYER PROFILES (strengths / weaknesses / full profile) ###")
    try:
        profile_mod.build_profiles()
    except Exception as e:
        print(f"profile build skipped ({repr(e)[:80]}); run `python -m pipeline.profile`.")

    print("\n### 11. COMBINED RATINGS (League + UCL, common-metric) ###")
    try:
        rate_combined_mod.rate_combined()
    except Exception as e:
        print(f"combined ratings skipped ({repr(e)[:80]}); run `python -m pipeline.rate_combined`.")

    print("\n### 12. LIVE MATCHES (SofaScore fixtures/results/in-play) ###")
    try:
        live_mod.load_live()
    except Exception as e:
        print(f"live feed skipped ({repr(e)[:80]}); run `python -m pipeline.load_live`.")

    print("\nDone. Try:  python tests/test_use_cases.py")


if __name__ == "__main__":
    main()
