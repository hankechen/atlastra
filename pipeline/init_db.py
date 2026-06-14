"""
Create (or reset) the DuckDB warehouse from pipeline/schema.sql.

Usage:
    python -m pipeline.init_db            # create tables if missing
    python -m pipeline.init_db --reset    # drop the db file and recreate
"""
import argparse
import sys

import duckdb

# Allow running both as a module (-m pipeline.init_db) and as a script.
try:
    from config import DB_PATH, SCHEMA_PATH, WAREHOUSE_DIR
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, SCHEMA_PATH, WAREHOUSE_DIR


def init_db(reset: bool = False) -> None:
    WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing database at {DB_PATH}")

    con = duckdb.connect(str(DB_PATH))
    con.execute(SCHEMA_PATH.read_text())

    tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
    con.close()

    print(f"Initialized DuckDB at {DB_PATH}")
    print(f"Created/verified {len(tables)} tables:")
    for t in tables:
        print(f"  - {t}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop and recreate the db file")
    args = ap.parse_args()
    init_db(reset=args.reset)
