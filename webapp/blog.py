"""
Atlastra blog — curated, data-driven articles.

Posts are stored as structured "blocks" (p / h2 / quote / stat / table / list) so the
frontend controls styling and everything stays crawlable/consistent. Add a post by
appending to POSTS; newest first. list_posts() returns summaries for the index,
get_post(slug) returns the full article.
"""

POSTS = [
    {
        "slug": "why-pedri-isnt-effective-in-the-world-cup",
        "title": "Why Pedri isn’t effective in the World Cup",
        "subtitle": "The best midfielder in club football has been strangely muted for "
                    "Spain. The numbers show why.",
        "author": "Atlastra",
        "date": "2026-07-17",
        "read_min": 4,
        "emoji": "🎯",
        "image": "https://images.fotmob.com/image_resources/playerimages/1083323.png",
        "tags": ["World Cup", "Spain", "Analysis"],
        "player": "Pedri",
        "body": [
            {"t": "p", "html": "At Barcelona, Pedri is arguably the best central midfielder "
             "in world football. Our engine grades his league season a <b>90 — Best in "
             "Position, 100th percentile</b>. He completes <b>91%</b> of his passes, creates "
             "<b>2.4 chances per 90</b>, and ranks in the elite tier for progressive carries "
             "and shot creation. And yet, in the games that actually decide a World Cup, he "
             "has been close to invisible."},
            {"t": "stat", "items": [
                {"k": "League rating", "v": "90"},
                {"k": "World Cup rating", "v": "68"},
                {"k": "vs France (SF)", "v": "12′"},
                {"k": "vs Belgium (QF)", "v": "35′"},
            ]},
            {"t": "h2", "text": "The club Pedri and the country Pedri"},
            {"t": "p", "html": "Pedri’s greatness is a product of sustained territorial "
             "control. At Barça he receives the ball 60–70 times a game in a side that owns "
             "possession, and he turns that volume into progression: short combinations, "
             "line-breaking carries, and a metronomic passing rhythm that dictates tempo. "
             "His value lives in the <b>half-spaces of the final third</b> — the exact zones "
             "a dominant possession team manufactures over and over."},
            {"t": "p", "html": "International tournament football rarely offers that. Against "
             "Spain, opponents sit in a deep, compact block, the game becomes transitional "
             "and physical, and the tidy 12-yard windows Pedri thrives in simply don’t open. "
             "A metronome needs a song to keep time to; knockout football is mostly noise."},
            {"t": "h2", "text": "The knockout-game problem"},
            {"t": "p", "html": "Look at his World Cup match ratings and a pattern jumps out: "
             "his best games came against the <b>weakest opposition</b>, and in the biggest "
             "knockout ties he barely played."},
            {"t": "table",
             "head": ["Match", "Stage", "Minutes", "Rating"],
             "rows": [
                 ["vs Cape Verde", "Group", "90′", "8.6"],
                 ["vs Saudi Arabia", "Group", "70′", "7.4"],
                 ["@ Uruguay", "Group", "60′", "7.5"],
                 ["vs Austria", "R16", "89′", "7.5"],
                 ["@ Portugal", "QF", "85′", "7.0"],
                 ["vs Belgium", "QF", "35′", "6.5"],
                 ["@ France", "SF", "12′", "6.2"],
             ]},
            {"t": "p", "html": "An <b>8.6 against Cape Verde</b>. A <b>12-minute cameo against "
             "France</b> in the semi-final. As the opposition improved and the stakes rose, "
             "Pedri’s influence — and his minutes — shrank. Manager choice is doing a lot of "
             "talking here: in the matches Spain most needed control, they reached for more "
             "physical, more direct midfielders and left their best passer on the bench."},
            {"t": "h2", "text": "It’s also about end product"},
            {"t": "p", "html": "Tournaments are decided by scarce moments, and Pedri’s profile "
             "is creation-heavy but goal-light. Across the season he averages just <b>0.06 "
             "goals</b> and <b>0.19 xA</b> per 90. That’s fine when you’re the engine of a "
             "team scoring three a game — the assists and control accumulate. But when a "
             "knockout tie hinges on one or two flashes of decisive quality, a low-xG "
             "orchestrator can go 90 minutes looking neat without ever bending the game."},
            {"t": "list", "items": [
                "Less possession and territory against deep international blocks",
                "More transitional, physical midfield battles that blunt his short game",
                "Low direct output (0.06 goals / 0.19 xA per 90) in a low-chance format",
                "Competition for minutes from more physical profiles in knockout games",
            ]},
            {"t": "quote", "text": "Pedri doesn’t need more talent for the World Cup. He needs "
             "the ball, the territory, and the minutes — and Spain’s knockout football gives "
             "him less of all three."},
            {"t": "h2", "text": "The verdict"},
            {"t": "p", "html": "None of this makes Pedri a bad tournament player — our "
             "stats-based World Cup grade still lands him at <b>68 (Above Average)</b>, a "
             "level most players at the tournament never reach. But that is a chasm below the "
             "<b>90</b> he posts in club football, and the gap tells the story precisely: "
             "effectiveness in a World Cup is decided in the games that matter, and there "
             "Pedri has been muted against elite opposition, minimized in the knockouts, and "
             "short on the decisive end product that wins tournaments. The best midfielder in "
             "club football is still waiting for the international stage to let him be "
             "himself."},
            {"t": "p", "html": "<i>Analysis based on Atlastra’s rating engine and per-match "
             "form data. Ratings reflect performances through the current tournament.</i>"},
        ],
    },
]

_BY_SLUG = {p["slug"]: p for p in POSTS}


def _summary(p: dict) -> dict:
    return {k: p.get(k) for k in ("slug", "title", "subtitle", "author", "date",
                                  "read_min", "emoji", "image", "tags", "player")}


def list_posts() -> dict:
    return {"available": True, "posts": [_summary(p) for p in POSTS]}


def get_post(slug: str) -> dict:
    p = _BY_SLUG.get((slug or "").strip())
    if not p:
        return {"available": False}
    return {"available": True, "post": p}
