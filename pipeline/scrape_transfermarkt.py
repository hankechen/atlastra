"""
Transfermarkt market-value scraper (use case 3: Player Profile -> Market Value).

No source in the pipeline carries market value, so we scrape Transfermarkt's
per-league "most valuable players" tables (marktwerte/wettbewerb/<code>), which
list each player's id, name, club, age and current market value in EUR. These
pages cover ~100 most-valuable players per league -- enough for the rated /
profiled population. Values are "current", tagged with FOCUS_SEASON at load.

Parsed per row from the players' value-history links:
    .../profil/spieler/<id>   -> id + name
    .../marktwertverlauf/spieler/<id> -> id + "€NN.NNm"  (market value)

Output: data/raw/transfermarkt/market_values_<season>.parquet
Run:    python -m pipeline.scrape_transfermarkt
"""
import re
import sys
import time
import warnings

import pandas as pd
import tls_requests

try:
    from config import RAW_DIR, FOCUS_SEASON, TRANSFERMARKT_LEAGUE_CODES
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import RAW_DIR, FOCUS_SEASON, TRANSFERMARKT_LEAGUE_CODES

warnings.filterwarnings("ignore")

TM_RAW = RAW_DIR.parent / "transfermarkt"
BASE = "https://www.transfermarkt.com"
HEADERS = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")}
RATE_LIMIT_SEC = 2.0
MAX_PAGES = 6  # market-value lists run ~4 pages; cap as a safety net

NAME_RE = re.compile(r'<td class="hauptlink">\s*<a title="([^"]+)" '
                     r'href="/[^"]+/profil/spieler/(\d+)"')
VALUE_RE = re.compile(r'/marktwertverlauf/spieler/(\d+)">(€[\d.,]+(?:bn|m|k|Th\.)?)<')


def _to_eur(token: str) -> float | None:
    """'€200.00m' -> 200000000.0 ; '€900k' -> 900000.0"""
    m = re.match(r"€([\d.,]+)(bn|m|k|Th\.)?", token)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = {"bn": 1e9, "m": 1e6, "k": 1e3, "Th.": 1e3, None: 1.0}[m.group(2)]
    return round(num * mult, 2)


def scrape_league(league_key: str, code: str) -> pd.DataFrame:
    rows = {}
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}/x/marktwerte/wettbewerb/{code}/plus/?page={page}"
        try:
            r = tls_requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"    {league_key} page {page} failed ({repr(e)[:60]}); stopping league.")
            break
        names = {pid: nm for nm, pid in NAME_RE.findall(r.text)}
        values = {pid: tok for pid, tok in VALUE_RE.findall(r.text)}
        page_ids = names.keys() & values.keys()
        if not page_ids:
            break  # ran past the last populated page
        for pid in page_ids:
            rows.setdefault(pid, (names[pid], _to_eur(values[pid])))
        time.sleep(RATE_LIMIT_SEC)
    df = pd.DataFrame(
        [(int(pid), nm, val, league_key) for pid, (nm, val) in rows.items()],
        columns=["tm_player_id", "player_name", "market_value_eur", "league_key"],
    )
    print(f"  {league_key}: {len(df)} players")
    return df


def scrape(season: str = FOCUS_SEASON) -> None:
    TM_RAW.mkdir(parents=True, exist_ok=True)
    frames = [scrape_league(lk, code) for lk, code in TRANSFERMARKT_LEAGUE_CODES.items()]
    out = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    out["season"] = season
    path = TM_RAW / f"market_values_{season}.parquet"
    out.to_parquet(path, index=False)
    print(f"transfermarkt: {len(out)} players -> {path}")


if __name__ == "__main__":
    scrape()
