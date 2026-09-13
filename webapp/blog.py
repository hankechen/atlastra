"""
Atlastra blog — curated, data-driven articles.

Posts are stored as structured "blocks" (p / h2 / quote / stat / table / list) so the
frontend controls styling and everything stays crawlable/consistent. `blogpost.js`
renders `p.html` as raw HTML because posts are "trusted" -- true for the hand-written
SEED_POSTS below, and true for auto-generated ones ONLY because every one of those is
held as a 'draft' until a signed-in admin approves it (see auto_blog.py); nothing
auto-published, unreviewed, ever reaches this trust boundary.

Persistence: a small sqlite file (posts survive restarts/redeploys, unlike the old
hardcoded-list-only version). SEED_POSTS ships in code (git history is its backup);
generated posts live only in the DB.
"""
import json
import sqlite3
import sys
import datetime

try:
    from config import DATA_DIR
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DATA_DIR

DB_PATH = DATA_DIR / "blog.sqlite"

SEED_POSTS = [
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


def _conn():
    con = sqlite3.connect(str(DB_PATH))
    con.execute("""CREATE TABLE IF NOT EXISTS posts (
        slug TEXT PRIMARY KEY, title TEXT, subtitle TEXT, author TEXT, date TEXT,
        read_min INTEGER, emoji TEXT, image TEXT, tags TEXT, player TEXT, body TEXT,
        status TEXT DEFAULT 'draft', model TEXT, week TEXT, created_at TEXT)""")
    return con


def _row_to_post(row) -> dict:
    (slug, title, subtitle, author, date, read_min, emoji, image, tags, player, body,
     status, model, week, created_at) = row
    return {"slug": slug, "title": title, "subtitle": subtitle, "author": author,
            "date": date, "read_min": read_min, "emoji": emoji, "image": image,
            "tags": json.loads(tags) if tags else [], "player": player,
            "body": json.loads(body) if body else [], "status": status,
            "model": model, "week": week, "created_at": created_at}


def save_generated_post(post: dict, model: str, week: str) -> None:
    """Insert a newly auto-generated post as a 'draft' -- never published directly."""
    con = _conn()
    con.execute(
        "INSERT OR REPLACE INTO posts (slug, title, subtitle, author, date, read_min, "
        "emoji, image, tags, player, body, status, model, week, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'draft', ?, ?, ?)",
        [post["slug"], post["title"], post.get("subtitle"), post.get("author", "Atlastra"),
         post.get("date"), post.get("read_min", 4), post.get("emoji", "⚽"),
         post.get("image"), json.dumps(post.get("tags") or []), post.get("player"),
         json.dumps(post.get("body") or []), model, week,
         datetime.datetime.now().isoformat(timespec="seconds")])
    con.commit()
    con.close()


def _summary(p: dict) -> dict:
    return {k: p.get(k) for k in ("slug", "title", "subtitle", "author", "date",
                                  "read_min", "emoji", "image", "tags", "player")}


def list_posts() -> dict:
    """Public blog index: SEED_POSTS + published DB posts, newest first."""
    con = _conn()
    rows = con.execute(
        "SELECT slug, title, subtitle, author, date, read_min, emoji, image, tags, "
        "player, body, status, model, week, created_at FROM posts WHERE status = 'published'"
    ).fetchall()
    con.close()
    posts = list(SEED_POSTS) + [_row_to_post(r) for r in rows]
    posts.sort(key=lambda p: p.get("date") or "", reverse=True)
    return {"available": True, "posts": [_summary(p) for p in posts]}


def list_drafts() -> dict:
    """Admin-only: posts awaiting review, newest first."""
    con = _conn()
    rows = con.execute(
        "SELECT slug, title, subtitle, author, date, read_min, emoji, image, tags, "
        "player, body, status, model, week, created_at FROM posts WHERE status = 'draft' "
        "ORDER BY created_at DESC").fetchall()
    con.close()
    return {"available": True, "drafts": [_row_to_post(r) for r in rows]}


def get_post(slug: str, include_drafts: bool = False) -> dict:
    slug = (slug or "").strip()
    for p in SEED_POSTS:
        if p["slug"] == slug:
            return {"available": True, "post": p}
    con = _conn()
    row = con.execute(
        "SELECT slug, title, subtitle, author, date, read_min, emoji, image, tags, "
        "player, body, status, model, week, created_at FROM posts WHERE slug = ?",
        [slug]).fetchone()
    con.close()
    if not row:
        return {"available": False}
    post = _row_to_post(row)
    if post["status"] != "published" and not include_drafts:
        return {"available": False}
    return {"available": True, "post": post}


def approve_post(slug: str) -> bool:
    """Publish a draft: stamp today's date (when it actually goes live) and flip status."""
    con = _conn()
    cur = con.execute(
        "UPDATE posts SET status = 'published', date = ? WHERE slug = ? AND status = 'draft'",
        [datetime.date.today().isoformat(), slug])
    con.commit()
    ok = cur.rowcount > 0
    con.close()
    return ok


def week_has_post(week: str) -> bool:
    """Any post (draft or published) already generated for this ISO week -- the
    idempotency check so the weekly refresher can't spam duplicate drafts."""
    con = _conn()
    row = con.execute("SELECT 1 FROM posts WHERE week = ? LIMIT 1", [week]).fetchone()
    con.close()
    return row is not None


def discard_post(slug: str) -> bool:
    """Soft-delete: flips status rather than deleting the row, so week_has_post()
    still sees this week as attempted -- a discard shouldn't make the weekly
    refresher immediately regenerate another draft for the same week."""
    con = _conn()
    cur = con.execute(
        "UPDATE posts SET status = 'discarded' WHERE slug = ? AND status = 'draft'", [slug])
    con.commit()
    ok = cur.rowcount > 0
    con.close()
    return ok
