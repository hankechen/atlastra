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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="focus season, players only")
    ap.add_argument("--no-scrape", action="store_true", help="reuse cached raw parquet")
    ap.add_argument("--no-enrich", action="store_true", help="skip FotMob enrichment")
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

    print("\n### 2. INIT DB ###")
    init_mod.init_db(reset=True)

    print("\n### 3. LOAD + DERIVE ###")
    load_mod.load_all()

    if not args.no_enrich:
        print("\n### 4. LOAD FotMob ENRICHMENT ###")
        enrich_load_mod.load_enrich()

    print("\nDone. Try:  python tests/test_use_cases.py")


if __name__ == "__main__":
    main()
