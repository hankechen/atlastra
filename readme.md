## Intro - Atlastra

This web app will focus on tracking, analyzing, and comparing soccer statistics across the top 5 leagues. Stats tracked will include basic counting stats (eg: g/a, appearances) as well as advanced stats(xG,xA,cc,BCC,BCM, successful dribbles, duel percentage, progressive passes, etc). Results of analysis will lead to graphs/visuals, and player scores based on a formula. Features on this app will include live scores, player profiles and statistics, a custom player ranking system, award predictions, and comparison features across players. 

## Use cases 
These are Phase One use cases, more will be added on with later phases. 
1. Player Statistics (Games, G/A, duels percentage, dribbles completed/percentage, chances created, big chances created, xG, xA, big chances missed, tackles, interceptions, passes completed)
2. Player Classfication (Best In Their Position, World-Class, Elite, Above Average, Average, Below Average)
3. Player Profile (Career, Market Value, Main Position, Strengths, Weaknesses, Areas of Improvement)
4. Player Cross Year Progression (Same Player compared across seasons), compared based on custom user-chosen stats or these default stats: 
(attackers - g/a, dribbles, chances created
midfielders - g/a, big chances created, passes completed, duels won,
defenders - tackles, interceptions, aerial/ground duels, recoveries) 
5. Player Comparisons (Compare Players with user-chosen stats or default stats seen above)
6. Team Performance/League Standing
7. Team Information (Squad, Manager, Venue)
8. Search by Player, Team, or Match (Two Teams, Resulted sorted by recency)
9. Player techniques (most commonly used techniques from a player) — implemented as **Signature Actions**: the on-ball actions a player performs most relative to position peers (take-ons, through balls, crosses, carries, key passes, aerials…), since literal move-recognition needs event/video data the warehouse doesn't have
10. Player Archetypes (rule-based scouting roles per position — e.g. Poacher, Deep-Lying Playmaker, Ball-Playing Defender — with a fit %, signature traits, and most-similar players)

## Phase Two — Web App & Advanced Features
Phase One use cases are live in a web UI (**Atlastra**), alongside the following:

11. **Live Matches** — live scores, fixtures and results across the top-5 leagues, the Champions League, and international tournaments; match pages with a formation-pitch lineup (goal/assist icons, click a player for their match stats + club), two-tone stats, and a "Today" tab.
12. **Match Predictions** — 1X2 win probabilities derived from bookmaker odds (vig removed), updating live as a match progresses.
13. **Custom Rating System** — a position-weighted composite player rating + classification, plus separate common-metric **League** and **Champions League** ratings (back-filled across 12 seasons) shown as dual gauges.
14. **Rankings & Awards** — per-position top-20 (League / UCL scope toggle) and the best individual seasons of all time (Combined / League / UCL).
15. **National Teams** — every national team with roster, recent results/fixtures, and a latest starting XI; match heroes link through to team pages.
16. **Per-season analysis** — a season selector on the player profile, percentile radars, SofaScore heatmaps, and a "Former Players" directory of notable ex-top-5 stars.
17. **Scout Report** — a scout-style written report per player from their ratings, percentiles, archetype and trend. Powered by the **Claude API** when an `ANTHROPIC_API_KEY` is configured, with a built-in offline rule-based engine as the default/fallback. Cached per player.
18. **Best XI on a Budget** — set a transfer budget and a formation; a knapsack optimiser returns the highest-rated legal XI it can afford, on a pitch.
19. **Find the Next X** — pick a legend (Xavi, Pirlo, Bergkamp…) and find the current players whose statistical style is the closest match (cosine similarity in the radar space).
20. **Player Cards** — a shareable, downloadable FUT-style collectible card (rating, archetype, top-5 percentile stats) rendered to an image.
21. **Football DNA Map** — every outfielder placed on a 2D style map (PCA on z-scored per-90 features) where distance = dissimilarity; pan/zoom and spotlight a player's nearest matches.
22. **User profile, follows & optional accounts** — follow players and teams, a watchlist and saved comparisons, an editable identity profile (picture, bio, favourite clubs/players, location, member-since), and in-app/desktop **notifications** when followed teams/players kick off, go live, score, or finish. Everything works as a **guest** (stored locally in the browser); signing in is **optional** and just **syncs** that data across devices — a lightweight account system (salted/PBKDF2 passwords, HttpOnly session cookie, standalone SQLite store; `webapp/auth.py`).
23. **Match Previews** — auto-generated previews for every upcoming fixture in the live feed (national teams included): recent form, key players matched to our ratings, head-to-head, and a bookmaker-consensus projection. Appears as a **Preview tab on each match page** (the default tab for not-yet-started matches) and as a standalone fixtures-list page. (A two-club xG/Poisson engine — `web_match_preview` — also exists for in-season league fixtures.)
24. **Big Game Index** — each player's goal involvements per 90 split by opponent quality (vs top-half "big games" vs bottom-half sides), flagging **Big-Game Players** who step up against the best vs **Flat-Track Bullies** who feast on weak sides. Built from a per-player per-match log (`player_match_log`, scraped from Understat); shown as a leaderboard and a per-profile badge.
25. **Fair Value** — a trained market-value model (`ml/train_market_value.py`) that prices every player from his output, age and minutes, and flags who the market over- and under-rates against his Transfermarkt valuation.
26. **World Cup hub** — group tables, bracket, fixtures/results and stat leaders for the current edition, refreshed server-side.
27. **Team Styles** — each club placed on the same style axes as the Tactics Lab's fingerprints (possession, press, line height, directness, width, counter).
28. **Blog, highlights, comments** — written pieces, a top-highlights feed, and threaded comments on players and matches.
29. **Games** — Daily Challenge, Draft Battle, Guess the Rating, Higher or Lower and Guess the Player, all built off the same ratings.

## Tactics Lab
The largest single feature (`/tactics.html`, `webapp/tactics.py`) — a tactical sandbox that is
deliberately **not** a black box: every projected number is a documented function of real player
attributes and the settings you chose, so it can explain *why* a setup works.

- **Build an XI** — pick a club or a national side, or start from an empty pitch and build your own
  team from the whole player universe. Both your side and the opposition are fully editable: drag
  players to reshape the formation, swap anyone in, and assign roles.
- **Roles** — 40+ per-position roles (Deep-Lying Playmaker, Raumdeuter, Enganche, Inverted Fullback…)
  each nudging how a player's quality feeds the team, with a **role fit** that penalises miscasting
  and explains it in words.
- **Tactics** — eight sliders (tempo, width, directness, patience, counter, line height, press,
  compactness) that all genuinely move the model, and matter most against the opponent's settings:
  width against a narrow block, counter-attacking against a high line, pressing against a side that
  plays through it.
- **Output** — projected xG/xGA, possession, PPDA, progression and territory; unit strengths; ranked
  tactical weaknesses with reasons; closest famous-side style match; a passing-network and average-
  position map; playstyle **chemistry** listing every synergy and clash by name; and an AI analyst read.
- **Simulations** — a full-season projection with the real league table, a single match played out of
  the matchup's own odds (scorers, assists, bookings, timeline, man of the match), and a complete
  **Champions League campaign**: the real 36-club league phase, your points slotted into the real
  table, then the bracket — two legs, extra time, penalties — either resolved instantly or played out
  match by match with the goals landing as the clock runs. Ends with campaign leaderboards.
- **Any season since 2014/15** — field a player as he was in a given year (2014/15 Messi is a different
  footballer from the 2021/22 one), with his rating converted onto today's scale and a card
  synthesised from that season's output.
- **Share a setup** — one link reopens the exact build: formation, sliders, every slot, both sides,
  added players and past seasons. It encodes only what you changed, so links stay short.

## Data sources
The suggested APIs (Football API, Sportmonks, RapidAPI) were not used; the data is assembled by scraping/ingesting public sources into the DuckDB warehouse:
- **Understat** — 12 seasons of player season stats, 2014/15 onward (the base table)
- **FotMob** — enrichment (2020/21+), detailed positions, club/league crests, player photos, live
  squads, **and the entire live feed**: scores, fixtures, results, lineups, match detail and the
  Champions League field. It answers from a datacentre IP, which is why the live surface runs
  server-side with no scraper on a home machine (`ATLASTRA_FOTMOB=1`).
- **SofaScore** — Champions League player stats (back to 08/09) and season heatmaps. It blocks
  datacentre IPs, so it is no longer used for anything live.
- **EA FC / FIFA 26 player cards** — ability-based ratings and attributes that drive the Tactics Lab
  (`data/fifa_ratings.json`), chosen over our own percentile rating because it is stable rather than
  season-dependent
- **datamb (Wyscout)** — current-season advanced per-90 metrics (progressive actions, signature actions)
- **Transfermarkt** — market values · **Wikimedia Commons** — licensed player photos
- **Claude API (Opus 4.8)** — optional, powers the Scout Report when a key is set
- **Gemini (flash)** — optional, powers the Tactics Lab's analyst read and the scout-report fallback

## Running it
```
python -m webapp.server                    # → http://localhost:8000
ATLASTRA_FOTMOB=1 python -m webapp.server  # with the live feed + Tactics Lab club squads
```
The Scout Report's AI mode is optional: `ANTHROPIC_API_KEY=sk-ant-... python -m webapp.server` (without it, the offline report engine is used).

## Live
Deployed at **https://atlastra.dedyn.io** — an AWS EC2 instance running the server as a systemd
service behind Caddy (automatic HTTPS). Deploy notes and the instance setup script live in `deploy/`.

## Please try the following APIs if available. Please try web scraping if none of the below works.  
1. Football API
2. Sportsmonks
3. Soccer data (Rapid API)

## Phase One
Data Collection/Organization, Have a script to download the Top 5 European Leagues data from year 2025/26, 
and put the data into a duckdb database. Please organize the data in reasonably well-defined database tables. 
Have testing scripts to illustrate the above use cases. 


