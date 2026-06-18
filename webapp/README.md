# Atlastra web UI

Frontend for the warehouse — a **Home dashboard** and a **Player Profile** page,
matching the product design. Zero extra dependencies: the backend is Python's
stdlib `http.server`, the charts are Chart.js from a CDN.

## Run
```
python -m webapp.server      # -> http://localhost:8000
```
Pages: `/` (Home dashboard) · `/players.html` (Players directory — searchable,
position-filtered grid of top-rated players) · `/player.html?name=…` (Profile).
"Players" in the nav and the cards in the directory link through to profiles;
the profile search box also accepts any player name (Enter to load).

## What's real vs placeholder
All real data comes from the DuckDB warehouse via `analytics.queries.SoccerDB`:

- **Real:** Atlastra rating + classification + percentile (rating engine),
  stat tiles, percentile radar (from `player_profile_metrics`), career-progression
  chart (selectable stat), strengths/weaknesses, Top-10 rankings, trending,
  stats spotlight (top scorer/assists/xG/chances/dribbles), league standings +
  form, market value, age, overview counts.
- **Placeholder** (no source in the warehouse, labelled in the UI): live matches,
  Ballon d'Or predictor, Team of the Season, nationality, contract, season
  heatmap, technique analysis, play-style tags, similar players.

## Files
```
webapp/
  server.py            stdlib HTTP server: static + /api/* (SoccerDB.web_* methods)
  frontend/
    index.html  players.html  player.html
    css/styles.css
    js/api.js  js/home.js  js/players.js  js/player.js
```
API endpoints: `/api/overview`, `/api/rankings`, `/api/spotlight`,
`/api/standings?league=…`, `/api/players?group=…&search=…`,
`/api/player?name=…&career_stat=…`.
