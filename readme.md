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
9. Player techniques (most commonly used techniques from a player) — answered two ways: **Signature Actions**, the on-ball actions a player performs most relative to position peers (take-ons, through balls, crosses, carries, key passes, aerials…) straight from the warehouse; and **Signature Skills** (below), the named moves themselves, which needed video rather than event data
10. Player Archetypes (rule-based scouting roles per position — e.g. Poacher, Deep-Lying Playmaker, Ball-Playing Defender — with a fit %, signature traits, and most-similar players)

## Phase Two — Web App & Advanced Features
Phase One use cases are live in a web UI (**Atlastra**), alongside the following:

11. **Live Matches** — live scores, fixtures and results across the top-5 leagues, the Champions League, and international tournaments; match pages with a formation-pitch lineup (goal/assist icons, click a player for their match stats + club), two-tone stats, and a "Today" tab.
12. **Live Win Probability** — on any match under way, 1X2 updated from the score and the clock, with the swing through the match drawn as a curve. Goals do not arrive evenly — the last quarter of an hour carries nearly twice the goals of the first — so a lead is worth more at 80' than at 20'. Validated at half-time against 10,955 historical matches: 0.844 log loss and 59% top-pick against 1.060 and 47% for the same model before kick-off, calibrated to within two points at every confidence level.
13. **Measured Roles** — where a player actually stands, clustered from ~10k player-seasons of positional heatmaps rather than read off his listed position (`tools/roles.py`). Surfaces the players being used somewhere other than their label says — wingers playing as wing-backs, centre-backs playing full-back. The clusters are fitted on 2020-23 and hold on 2023-26 with under 4% drift, and what they do *not* support is written down too: DM and CM are one cluster, and no wide-midfield role separates at any k.
14. **Match Predictions** — 1X2 win probabilities from Atlastra's own fitted goals model: each squad is scored into attack/midfield/defence/goalkeeping strength and run through the same Poisson core the Tactics Lab uses (estimated on 3,358 real team-matches, validated on three full seasons). Falls back to a form-and-ranking model where squad data is too thin — national teams, and clubs outside our player coverage. Updates live as a match progresses.
15. **Custom Rating System** — a position-weighted composite player rating + classification, plus separate common-metric **League** and **Champions League** ratings (back-filled across 12 seasons) shown as dual gauges.
16. **Rankings & Awards** — per-position top-20 (League / UCL scope toggle) and the best individual seasons of all time (Combined / League / UCL).
17. **National Teams** — every national team with roster, recent results/fixtures, and a latest starting XI; match heroes link through to team pages.
18. **Per-season analysis** — a season selector on the player profile, percentile radars, SofaScore heatmaps, and a "Former Players" directory of notable ex-top-5 stars.
19. **Scout Report** — a scout-style written report per player from their ratings, percentiles, archetype and trend. Powered by the **Claude API** when an `ANTHROPIC_API_KEY` is configured, with a built-in offline rule-based engine as the default/fallback. Cached per player.
20. **Best XI on a Budget** — set a transfer budget and a formation; a knapsack optimiser returns the highest-rated legal XI it can afford, on a pitch.
21. **Find the Next X** — pick a legend (Xavi, Pirlo, Bergkamp…) and find the current players whose statistical style is the closest match (cosine similarity in the radar space).
22. **Player Cards** — a shareable, downloadable FUT-style collectible card (rating, archetype, top-5 percentile stats) rendered to an image.
23. **Football DNA Map** — every outfielder placed on a 2D style map (PCA on z-scored per-90 features) where distance = dissimilarity; pan/zoom and spotlight a player's nearest matches.
24. **User profile, follows & optional accounts** — follow players and teams, a watchlist and saved comparisons, an editable identity profile (picture, bio, favourite clubs/players, location, member-since), and in-app/desktop **notifications** when followed teams/players kick off, go live, score, or finish. Everything works as a **guest** (stored locally in the browser); signing in is **optional** and just **syncs** that data across devices — a lightweight account system (salted/PBKDF2 passwords, HttpOnly session cookie, standalone SQLite store; `webapp/auth.py`).
25. **Match Previews** — auto-generated previews for every upcoming fixture in the live feed (national teams included): recent form, key players matched to our ratings, head-to-head, and a model projection. Appears as a **Preview tab on each match page** (the default tab for not-yet-started matches) and as a standalone fixtures-list page. (A two-club xG/Poisson engine — `web_match_preview` — also exists for in-season league fixtures.)
26. **Big Game Index** — each player's goal involvements per 90 split by opponent quality (vs top-half "big games" vs bottom-half sides), flagging **Big-Game Players** who step up against the best vs **Flat-Track Bullies** who feast on weak sides. Built from a per-player per-match log (`player_match_log`, scraped from Understat); shown as a leaderboard and a per-profile badge.
27. **Fair Value** — a trained market-value model (`ml/train_market_value.py`) that prices every player from his output, age and minutes, and flags who the market over- and under-rates against his Transfermarkt valuation.
28. **World Cup hub** — group tables, bracket, fixtures/results and stat leaders for the current edition, refreshed server-side.
29. **Team Styles** — each club placed on the same style axes as the Tactics Lab's fingerprints (possession, press, line height, directness, width, counter).
30. **Blog, highlights, comments** — written pieces, a top-highlights feed, and threaded comments on players and matches.
31. **Games** — Daily Challenge, Draft Battle, Guess the Rating, Higher or Lower and Guess the Player, all built off the same ratings.
32. **Signature Skills** — the *named moves* a player is known for, on his profile. Gemini watches his
    YouTube highlight reel (`webapp/gemini.py::analyze_youtube`) and returns his five signature
    techniques with a one-line description each — concrete named moves like "La Croqueta to Escape
    Pressure", "Outside of Foot Trivela Cross" or "Byline Cutback After Body Feint", not vague labels
    like "great dribbling". Every skill carries a **▶ example clip cut from that player's own reel**
    (Gemini timestamps the moment, yt-dlp + ffmpeg cut it), falling back to a generic demonstration of
    the move where no own-clip exists. Cached per player in SQLite (`webapp/signature_skills.py`), with
    the biggest stars prewarmed. This is what closes use case 9 properly: the stats say a player takes
    a lot of people on, the video says *how*.
33. **Career Trajectory** — the one model here that looks *forward*. Everything above scores the season
    that happened; this projects where a player's rating goes **next** season, from twelve seasons of
    the top-5 panel (11,713 season-to-season transitions). Two heads, because "how good will he be" and
    "will he still be here" are different questions: a projection with an error bar, and the odds he is
    still a top-5 regular at all. Held out on 3,639 projections it had never seen, it lands within
    **8.42 rating points against 9.51** for assuming no change at all — 11.4% skill over persistence,
    beating it in every held-out season — and calls the direction right **69%** of the time on players
    who actually moved. The availability head separates 0.795 AUC. The error bar is fitted rather than flat — two quantile models,
    conformalized on a calibration season — so it widens where the model knows less and leans the way
    the risk does. A flat ±sd covered 83% of weak players and only 73% of strong ones; the fitted
    interval runs 76–82% across the range and contained the eventual rating 78% of the time overall
    against a nominal 80%. Shown on the profile with the drivers behind it, and as a
    **Risers & Fallers** board with breakout candidates and the **measured aging curve** by position.
    Trained on transitions through 2021/22 and scored blind on 2022/23–2024/25 against the only
    baseline that matters — persistence, "he'll be exactly as good as he was" (`ml/train_trajectory.py`).
    One deliberate handicap is worth naming: only players whose **age we actually know** are modelled,
    which costs a fifth of the training rows. Birth dates come from FotMob, whose coverage begins in
    2020/21, so a missing one nearly means "had already left the top-5 by 2020" — the availability
    target itself. Left in, that artefact of our own scrape becomes the most predictive feature in the
    model and inflates availability AUC from 0.79 to 0.84. The lower, real number is the one reported.
    Goalkeepers were excluded at first for a similar reason — the engine only rated them in 2025/26, so
    no keeper had a season-to-season transition anywhere in the warehouse and the model read their empty
    attacking line as an outfielder in freefall, "correcting" a 19-rated keeper up to 47. That belonged
    upstream rather than here: `pipeline/scrape_sofa_gk.py` backfilled keeper metrics to 2015/16, and
    including them then *improved* the model. Their ratings swing ~13 points a season against ~9 for a
    midfielder, and the fitted interval finds that on its own — GK bands come out about 5 points wider.

34. **Squad Planner** — the first feature that asks what a squad will need rather than describing what
    it is. For any top-5 club, each position is scored against **what a strong club has there** (the
    80th percentile of every club's best player in that position — measured, not chosen), both today
    and once the current group has aged the chosen number of seasons: next season from the trajectory
    model, the years after from the measured aging curve. Ranks the club's priorities with the reason
    in words ("minutes here average 34 years old", "widens to 9 short within 3 seasons"), then names
    players who would raise each one. Positions **cover for one another** at a discount — a defensive
    midfielder counts toward central midfield, labelled as covering rather than counted as a
    specialist, because a planner that reads position labels literally tells a club with Casemiro,
    Ugarte, Mainoo, Fernandes and Mount that it urgently needs a central midfielder.

35. **Availability** — how much of his club's football a player was actually there for, on every
    profile: share of league matches played, the absence spells behind it, and a season-by-season
    career strip where a lost year reads as a notch. Derived from the match log rather than scraped
    from an injury feed (`pipeline/build_absences.py`) — a match the club played and the player did
    not is an absence, a run of them is a spell. A player's window starts at his **first appearance
    for that club**, so a January signing is not recorded as having missed the first half of a season
    he spent elsewhere, and ends when he next turns out for someone else. Validated against seasons
    whose history is public: Rodri's 2024/25 comes back as 3 of 35 matches with a single 31-match
    spell, which is his ACL. Nothing here is called an injury — a suspension looks identical from a
    match log, and only the length is observed.
    - **What it is worth is written down too.** It does *not* scar the next season's rating: control
      for a player's level and those who missed 10+ consecutive matches move almost identically to
      those who missed none (−9.3 against −9.3 in the 65–80 band). It adds ~0.001 AUC to the
      availability head, because minutes-share already proxies it. What it does predict is more
      absence — 10.2% → 13.3% → **17.7%** by how long a player was out this season. A model on that
      target beat reading one column by 0.015 AUC, so the **rates are published and no individual is
      given an injury-risk score**, which is more than the data can carry.

36. **Most likely to score or assist** — on every match preview, the chance each player records a goal
    or an assist *in that fixture*. Previews used to name a club's best players by season rating,
    which gives the same answer every week whoever they play; this reads the opponent's leakiness,
    the venue, the player's recent minutes and his form (`ml/train_match_contribution.py`, 711,663
    player-fixtures). Modelled over the whole window grid — every club match in a player's spell,
    appeared or not — so rotation and injury are inside the number rather than a caveat beside it.
    Trained through 2022/23 and scored on the seasons after: **AUC 0.79 against 0.65** for ranking by
    season rating, and of the three players it flags per fixture **34.3% deliver, against 23.3%** for
    naming the top-rated three. The probabilities are calibrated, not indicative — each band lands
    within a point or two of its own number across the held-out seasons.

37. **If nobody moves** — next season's league table on the Squad Planner, projected on the one
    assumption that makes the question answerable: that every squad stays exactly as it is. That is
    not a limitation dressed up as a feature — it is the counterfactual a planner wants before
    deciding whether to sign anyone. Points are fitted to real final tables from this season's points
    and measured squad strength (the minutes-weighted rating of a club's top 14); held out it lands
    within **9.5 points against 9.82** for assuming this season repeats, which is better but not by
    much, so the page says to read a position as a range. Each squad's **projected drift** sits beside
    the table rather than inside the points model — a percentile rating mean-reverts, so almost every
    strong squad drifts down in absolute terms and only the comparison between clubs carries meaning.

## Questions we asked the data, and what it said
Not everything worth knowing becomes a feature. These are measurements run against the panel where the
answer was the point — including where the answer was "no", which is written down rather than quietly
dropped. Each is a script in `tools/`, so a change gets re-measured instead of argued about.

### Is form real? (`python -m tools.form_test`)
Every broadcast treats form as a fact. It is a testable claim, and 21,586 matches are enough to test
it — carefully, because the obvious version answers itself. "Players who scored recently score next
match" is true of good players in every window; form only means something if it beats **the player's
own baseline**. And the usual test is confounded even then: a baseline built from a dozen earlier
matches is a *noisy* estimate of ability, and recent form is simply five more matches of the same
thing, so a model improves when form is added even if recency means nothing. So the test controls
against a full-season ability estimate (excluding the window and the match itself), which is what
that confound requires.

| | naive baseline | well-estimated ability |
|---|---|---|
| weight on form | 0.270 | **0.202** |
| hot-vs-cold gap, xG+xA per 90 | +0.074 | **+0.051** (19σ) |
| held-out MAE improvement | 1.26% | **0.70%** |

**Form is real, and it is nearly useless for predicting one match.** About 45% of the apparent effect
is the estimation artefact above. What survives is genuine and large in significance terms — a hot
player beats a cold one of the same ability by roughly 25% relative output, holding across every
ability band and on goals as well as xG — but it moves single-match error by under one percent,
because one match is mostly noise. It earns a feature in the match model below, not a headline. One
caveat the data cannot settle: a player whose role improves mid-season looks "hot" and then keeps
performing, which is a role change rather than a hot hand, and this cannot separate the two.

### What does a transfer cost? (`python -m tools.transfer_effect`)
The trajectory model projects a player forward knowing nothing about where he will play, which is a
stated blind spot. This measures how big it is. Comparing movers to stayers directly would answer the
wrong question — players who move are disproportionately the ones already declining — so the
counterfactual is the model's own projection, made without knowledge of the move, and the residual is
what the move is worth. Scored on transitions the model never trained on (3,639, a quarter of them
moves):

| | residual vs projection | n |
|---|---|---|
| stayed | **+0.26** | 2,718 |
| moved | **−1.94** | 921 |
| → to a stronger club | −0.15 | 337 |
| → sideways | −2.21 | 276 |
| → to a weaker club | −2.95 | 159 |

A move costs about **2.2 rating points against expectation** (5.4σ) — but essentially all of it is
sideways and downward moves. **Going to a better club costs nothing.** The interesting reading is that
the cost is not adaptation to a new dressing room, which would apply in every direction; it tracks
where a player is going, and a move down is partly the market pricing a decline the model has not
seen yet. Knowing only "did he move" would cut held-out error 0.66% — and it is not knowable at
projection time anyway, since the transfer has not happened. This measures the blind spot; it does
not close it.

## Tactics Lab
The largest single feature (`/tactics.html`, `webapp/tactics.py`) — a tactical sandbox that is
deliberately **not** a black box: every projected number is either **fitted to real results and scored
against them** (`tools/backtest.py`) or a documented judgement, and the interface says which is which.
Expected goals come from a Poisson model fitted to thousands of real matches; player ability, twelve
seasons of per-90 output, real league tables and the real Champions League field all feed it.

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
  tactical weaknesses with reasons; the real club-seasons whose measured profile is closest to your
  setup; a passing-network and average-
  position map; playstyle **chemistry** listing every synergy and clash by name; and an AI analyst read.
- **Simulations** — a full-season projection with the real league table, a single match played out of
  the matchup's own odds (scorers, assists, bookings, timeline, man of the match), and a complete
  **Champions League campaign**: the real 36-club league phase, with every opponent fielding the eleven and
  shape they last used *in that competition* (a club's genuinely last teamsheet is usually a rotated league or
  cup side, which is a different team), your points slotted into the real
  table, then the bracket — two legs, extra time, penalties — either resolved instantly or played out
  match by match with the goals landing as the clock runs. Ends with campaign leaderboards.
- **World Cup** — the same idea in the 2026 format, which is a different tournament rather than a
  reskin: 48 nations in twelve real groups, the top two of each plus the eight best third-placed
  sides (decided across all twelve, so the other eleven are played too), then five *single* matches
  to the trophy. A one-off is far kinder to the weaker side than a two-legged tie, and the numbers
  agree — checked against 356 real World Cup matches, the better-ranked side wins only 44% of the
  time when the two are within ten places and 72% across a fifty-place gulf, so the campaign
  engine's quality amplifier is recalibrated rather than reused (`python -m tools.backtest --wc`).
  Goals per match land on the real 2.67.
- **Any season since 2014/15** — field a player as he was in a given year (2014/15 Messi is a different
  footballer from the 2021/22 one), with his rating converted onto today's scale and a card
  synthesised from that season's output.
- **Share a setup** — one link reopens the exact build: formation, sliders, every slot, both sides,
  added players and past seasons. It encodes only what you changed, so links stay short.

### How good it is, and how we know
`python -m tools.backtest` scores the engine against real results — every top-5 match of a season for
the odds, every final table for the projection, and the weakness rules for whether they hold up. It is
in the repo precisely so a change gets measured instead of argued about. Current standing:

| | Tactics Lab | baseline (season base rates) |
|---|---|---|
| 1X2 log loss, 2025/26 (1,679 matches) | **1.0003** | 1.0696 |
| 1X2 log loss, 2024/25 · 2023/24 | **1.0135 · 1.0057** | 1.0783 · 1.0729 |
| top-pick accuracy | **51.5%** | 44.4% |
| season points | **MAE 7.8, unbiased** | — |
| in-play 1X2 at half-time (10,955 matches) | **0.8436** | 1.0596 (same model pre-kick-off) |

Predicted probabilities track reality closely across the range (of the matches called 60-70%, about
two-thirds finish that way), and the season card shows its own error bar — "79 ± 8 pts" — because the
backtest knows what that number is worth.

`python -m tools.fit` is the other half: it re-derives those constants from the warehouse and prints
each one beside the value currently in the source. The backtest can tell you a constant is wrong; the
fitter tells you what it should be. It writes nothing — a fit worth adopting is worth reading first,
and a few constants are deliberately held away from their fitted value for reasons the data cannot
see. It also reports what it *cannot* re-derive (the unit weights, the role and chemistry constants),
so the boundary between measured and chosen stays visible rather than implied.

**What is fitted, and what is judgement.** The interface says so on the page, and so does this:

- **fitted to real results** — expected goals (a Poisson GLM on 3,358 team-matches, the Maher /
  Dixon-Coles form), the strength→points line (fitted to real final tables), the pressing effect
  (fitted to real PPDA — and it reversed the sign the engine had assumed), who takes a team's goals
  and assists (fitted to 1,108 players' real shares), and the attacking unit's attribute weights
- **measured, not invented** — the style comparison runs against real club-seasons' xG and PPDA
  profiles; one weakness rule is confirmed against a season of results and carries a badge
- **editorial** — roles, role fit and chemistry. Nothing in the warehouse records which role a player
  was actually given, so they can be neither fitted nor falsified; they are labelled as judgement in
  the UI and only ever nudge the numbers

Roughly 60% of what the Lab tells you is now fitted or measured, and ~75% of the part that makes
predictions. Getting past that needs event or tracking data with player positions, not more of the
same data.

## Data sources
The suggested APIs (Football API, Sportmonks, RapidAPI) were not used; the data is assembled by scraping/ingesting public sources into the DuckDB warehouse:
- **Understat** — 12 seasons of player season stats, 2014/15 onward (the base table)
- **FotMob** — enrichment (2020/21+), detailed positions, club/league crests, player photos, live
  squads, **and the entire live feed**: scores, fixtures, results, lineups, match detail and the
  Champions League field. It answers from a datacentre IP, which is why the live surface runs
  server-side with no scraper on a home machine (`ATLASTRA_FOTMOB=1`).
- **SofaScore** — Champions League player stats (back to 08/09), season heatmaps, and **goalkeeper
  metrics for the top-5 domestic leagues back to 2015/16** (saves, goals conceded, clean sheets —
  `pipeline/scrape_sofa_gk.py`). That last one exists because the rating engine's other keeper source,
  datamb/Wyscout, publishes the current season only, which left keepers rated in 2025/26 and no other
  season. It blocks datacentre IPs, so it is no longer used for anything live.
- **Player ability ratings** — a stable, ability-based rating and attribute set per player
  (`data/fifa_ratings.json`, derived from the EA FC 26 dataset), used as ONE input to the Tactics Lab
  alongside twelve seasons of per-90 output, real match results and the real league tables. Preferred
  over our own percentile rating for the squad-strength inputs because it is stable rather than
  season-dependent; every number the Lab actually predicts with is fitted to real results on top of it
- **datamb (Wyscout)** — current-season advanced per-90 metrics (progressive actions, signature actions)
- **Transfermarkt** — market values · **Wikimedia Commons** — licensed player photos
- **Claude API (Opus 4.8)** — optional, powers the Scout Report when a key is set
- **Gemini (flash)** — reads YouTube highlight reels for **Signature Skills** (the one source of
  video-derived data in the app), and optionally powers the Tactics Lab's analyst read and the
  scout-report fallback
- **YouTube** — player highlight reels, the source behind Signature Skills and its example clips

## Running it
```
python -m webapp.server                    # → http://localhost:8000
ATLASTRA_FOTMOB=1 python -m webapp.server  # with the live feed + Tactics Lab club squads
python -m tools.backtest                   # score the engine against real results
python -m tools.backtest --season 2425 --weaknesses
python -m tools.fit                        # re-derive the fitted constants, print a diff
python -m tools.fit --only xg --season 2425
python -m tools.roles                      # learn positional roles from heatmaps
python -m tools.roles --write              # persist player_learned_role
python -m ml.train_trajectory              # fit + project next season, write the tables
python -m ml.train_trajectory --report     # score the held-out seasons only, write nothing
python -m ml.train_match_contribution      # fit the per-fixture goal-involvement model
python -m pipeline.build_absences          # absence spells + availability from the match log
python -m tools.form_test                  # is form real? (--metric ga for goals rather than xG)
python -m tools.transfer_effect            # what a transfer costs against the model's expectation
python -m pytest tests/ -q -m "not slow"   # the fast suite (drop the -m to refit the models)
```
The trajectory model needs a birth date per player, which the current-squad pull only covers for
2025/26. `python -m pipeline.scrape_dob && python -m pipeline.load_dob` back-fills them from FotMob
(resumable) — without it the model still runs, it just loses age on the older seasons. Keeper ratings
need `python -m pipeline.scrape_sofa_gk && python -m pipeline.load_sofa_gk` before
`python -m pipeline.rate_combined`, or GKs are rated in the current season only.

The per-match log is backfilled season by season:
```
python -m pipeline.backfill_match_log --warm    # slow, cache only, holds no DB lock
python -m pipeline.backfill_match_log --load    # fast; loads COMPLETE seasons only
```
Understat throttles a sustained pull, so `--warm` retries with a widening pause and is safe to run
repeatedly — cached pages are skipped, so each run gets further. `--load` verifies a season brought
all five leagues and **rolls it back if not**: a season that silently lands missing one league would
look finished to everything downstream while missing a fifth of its matches.

Order matters for a rebuild from scratch, because each step feeds the next:
```
python -m pipeline.run_pipeline                                   # warehouse
python -m pipeline.backfill_match_log                             # 12 seasons of match logs
python -m pipeline.build_absences                                 # -> availability
python -m pipeline.scrape_dob    && python -m pipeline.load_dob   # -> ages
python -m pipeline.scrape_sofa_gk && python -m pipeline.load_sofa_gk
python -m pipeline.rate_combined                                  # -> ratings incl. keepers
python -m ml.train_trajectory                                     # needs ages + availability
python -m ml.train_match_contribution                             # needs the match log
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


