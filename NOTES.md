# Soccer Analytics — Phase One

Data collection & organization for the **Top-5 European leagues**, every season
from **2014/15 to 2025/26**, loaded into a DuckDB warehouse, with test/demo
scripts illustrating the README's Phase-One use cases.

**Season coverage:**
- **Understat** (xG/xA core, matches, standings): all 12 seasons, 2014/15→2025/26.
- **FotMob** advanced stats (big chances, dribbles, tackles, interceptions,
  passes, etc.): 2020/21→2025/26 (earlier seasons predate full Opta coverage).
- **Duels**: 2025/26 only (FotMob only exposes them for a player's current season).

## TL;DR — how to run

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

python -m pipeline.run_pipeline      # scrape Understat -> DuckDB (~2-3 min, then cached)
python tests/test_use_cases.py       # human-readable demo of all 8 use cases
python -m pytest tests/ -q           # the same checks as assertions
```

Warehouse lands at `data/warehouse/soccer.duckdb`. Raw pulls are cached as
Parquet under `data/raw/understat/`, so re-runs with `--no-scrape` are instant.

## Data source decision

The README asks to try API-Football / Sportmonks / RapidAPI first, then fall
back to web scraping. In this environment:

| Source | Status | Notes |
|---|---|---|
| API-Football / Sportmonks / RapidAPI | ✗ | require API keys; none present |
| FBref (via `soccerdata`) | ✗ | **HTTP 403** — IP-blocked by Cloudflare |
| **Understat** (via `soccerdata`) | ✓ | **primary** — free, reachable, xG/xA model |
| **FotMob** (custom signed client) | ✓ | **enrichment** — fills Understat's gaps |

**Understat** is the primary source; **FotMob** backfills the Opta-style stats
Understat lacks (dribbles, tackles, interceptions, big chances, pass
completion). FotMob's API rejects unsigned requests, so we generate its `x-mas`
auth header ourselves — see [FotMob auth](#fotmob-auth) below.

## What we collect (README Phase-One stats)

From **Understat** (`player_season_stats`): games, minutes, goals, assists,
**xG, xA, npxG**, shots, **chances created** (= key passes), xGChain, xGBuildup,
position; plus match results + xG and team xPoints/PPDA for standings.

From **FotMob** (`player_enrichment`, joined by fuzzy name match): **big chances
created / missed**, chances created, **successful dribbles** (+ success %),
**tackles**, **interceptions**, recoveries, **duels won** (+ win %), **passes
completed** (+ accuracy %), and FotMob's own 0–10 season rating. Per-90 and total
variants where available.

Duels aren't in FotMob's season-stats CDN, so they're scraped per player from
`/api/data/playerData` (`firstSeasonStats`) — see [duels](#duels) below.

**Still not available** (not exposed by either free source's season API):
`progressive passes` (Opta/FBref only — FBref is IP-blocked), plus `market
value`, `manager`, `venue`. These are **absent rather than faked**; query methods
that would surface them return `None` with an inline comment.

## Schema (`pipeline/schema.sql`)

```
leagues ── teams ── players                      (dimensions)
matches ── team_match_stats                      (match facts; 2 team rows/match)
player_season_stats                              (per player/team/league/season)
team_season_stats   (derived standings)
player_ratings      (derived classification)
```

- `player_season_stats` carries season totals **and** derived per-90 rates.
- `team_season_stats` (standings) is computed from `team_match_stats`
  (W/D/L, GF/GA, GD, points, xG for/against, xPoints, league position).
- `player_ratings` powers the classification use case (see below).

## Ratings & classification (use case #2)

Per position group **within a league + season**:
1. percentile-rank each relevant per-90 metric (weights differ by position —
   attackers weight goals/xG, midfielders weight creation, etc.),
2. combine into a weighted composite,
3. **scale by availability** = `min(1, minutes / 1800)` so a hot 700-minute
   cameo can't outrank an ever-present 27-goal striker,
4. percentile-rank the composite → `rating` (0–100) and a tier:
   `Best In Position` (rank 1) › `World-Class` (≥95th) › `Elite` (≥85th) ›
   `Above Average` (≥65th) › `Average` (≥35th) › `Below Average`.

Caveat: Understat is attack/possession oriented, so **DEF/GK ratings are weak**
(they lean on xGBuildup/involvement, not defensive actions). This improves once
a defensive-stats source is added — see below.

## Cross-year progression (use case #4)

Player-season stats, matches and standings are collected for **all 12 seasons
(2014/15→2025/26)**, so progression and historical league tables work across the
full range. Season codes are soccerdata-style (`"1415"` = 2014/15); configured in
`config.py` (`ALL_SEASONS`, `ENRICH_SEASONS`, `DUELS_SEASONS`).

## Cross-cutting fix: accent-insensitive search

Player names mix accented and ASCII spellings (e.g. Kylian is stored
`Kylian Mbappe-Lottin`, his brother as `Ethan Mbappé`). All name lookups use
DuckDB `strip_accents()` on both sides so `"Mbappe"`, `"Mbappé"`, `"Vinic"` all
resolve to the right player.

<a name="fotmob-auth"></a>
## FotMob auth (`x-mas` header) — implemented

FotMob's `/api/data/*` endpoints reject unsigned requests. The browser signs
each call with an `x-mas` header; `pipeline/fotmob_auth.py` reproduces it:

```
body      = {"url": <relative path>, "code": <epoch ms>, "foo": <build hash>}
signature = MD5( json(body) + <secret> ).upper()
x-mas     = base64( json({"body": body, "signature": signature}) )
```

The `<secret>` (currently the "Three Lions" lyrics) and `<build hash>`
(`production:<sha>`) are per-deploy constants embedded in FotMob's `_app-*.js`
bundle. To stay robust when they rotate, `FotmobAuth` **extracts them live**
from the bundle on first use rather than hard-coding them.

How enrichment is collected (verified June 2026):
1. One signed `GET /api/data/leagues?id=<id>&season=2025%2F2026` per league
   returns `stats.players` — 37 ranked stat categories, each with a `fetchAllUrl`
   on the **public** `data.fotmob.com` CDN (no auth).
2. For each gap stat, fetch that CDN list (all ranked players) and merge by
   FotMob player id. `pipeline/load_enrich.py` fuzzy-matches each FotMob player
   to an Understat `player_id` (accent-folded, per league; ~95% matched at
   ~99.5 avg confidence).

Field semantics were reverse-engineered and verified before trusting them:
"total" categories expose the season total in `StatValue`; "per 90" categories
expose the rate in `StatValue` and the season total in `SubStatValue` — except
dribbles/passes, whose `SubStatValue` is a percentage, so their totals are
derived as `per90 × minutes / 90`. (FotMob's `StatValueCount` is **not** a clean
total and is ignored.)

<a name="duels"></a>
### Duels (per-player)
FotMob's season CDN top-lists don't include duels (the endpoint 403s for any
duel key), but each player's `/api/data/playerData` payload carries
`duel_won` and `duel_won_percent` under `firstSeasonStats` (verified to be the
2025/26 domestic season — per90 × minutes reconciles to the season minutes).
`pipeline/scrape_duels.py` fetches these per FotMob player id (~2.3k signed
calls, **resumable** via a checkpointed cache) and `load_enrich.py` merges them
into `player_enrichment` (`duels_won`, `duels_won_pct`).

### Possible next step
`player_ratings` (use case #2) is still Understat-only, so DEF/GK ratings remain
weak. Now that FotMob tackles/interceptions/recoveries/duels are loaded, the
rating model could fold them in to make defender classification meaningful.

## File map

```
config.py                     leagues, seasons, paths, rating + FotMob constants
pipeline/schema.sql           DuckDB DDL
pipeline/scrape.py            Understat -> data/raw/understat/*.parquet
pipeline/fotmob_auth.py       FotMob x-mas request signing (live secret extraction)
pipeline/scrape_enrich.py     FotMob enrichment -> data/raw/fotmob/*.parquet
pipeline/scrape_duels.py      FotMob per-player duels -> data/raw/fotmob/*.parquet
pipeline/init_db.py           create/reset warehouse from schema.sql
pipeline/load.py              raw -> tables + derived standings & ratings
pipeline/load_enrich.py       FotMob enrichment -> player_enrichment (fuzzy match)
pipeline/run_pipeline.py      one-command: scrape (+enrich) -> init -> load
analytics/queries.py          SoccerDB: one method per use case
tests/test_use_cases.py       pytest suite + readable demo of use cases 1-8
```
