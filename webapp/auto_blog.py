"""
Weekly auto-blog — picks the week's standout storyline (the single best-rated
performance across the covered competitions) and writes a full structured blog post
about it, in the same single-player-deep-dive voice as the site's hand-written posts.

Server-side only. Always lands as a DRAFT (blog.save_generated_post) -- never
published automatically. An admin reviews it on /admin.html and approves or discards
it; see blog.py's docstring for why that gate matters (posts render `p.html` as raw,
trusted HTML). Reads GEMINI_API_KEY / ANTHROPIC_API_KEY from the environment same as
weekly_recap.py and scout_ai.py; with neither configured, generation just no-ops
(there's no safe deterministic fallback for a whole article the way there is for a
short recap).

Cached per ISO week (one draft per week, like weekly_recap) so a restart or a manual
retrigger doesn't spam duplicate drafts for the same week.
"""
import os
import re
import sys
import datetime

try:
    from webapp import blog
    from webapp.scout_ai import _summary as _player_summary
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from webapp import blog
    from webapp.scout_ai import _summary as _player_summary

MODEL = "claude-opus-4-8"

SYSTEM = """You are a sharp football (soccer) writer for Atlastra, producing a single-subject \
analytical article about one player's week, grounded entirely in the data given.

Rules:
- Base EVERY claim on the data provided (this week's match performance, the season profile \
percentiles/ratings/stats). Cite specifics naturally (a rating, a percentile, a per-90 number).
- Do NOT invent anything not in the data: no quotes, no injuries, no transfer talk, no other \
matches, no facts about the opponent beyond the scoreline given.
- "Atlastra rating" is the 0-99 composite vs positional peers; percentiles are vs the same \
position in the top-5 leagues. The match "rating" is FotMob's 0-10 rating for that one game.
- British football terminology. Confident, vivid, but accurate -- this is analysis, not a puff \
piece; note weaknesses or context that complicates the headline number if the data supports it.

Output ONLY a single JSON object (no markdown fences, no prose outside it) with this exact shape:
{
  "title": "a specific, punchy headline about this player's week (not generic)",
  "subtitle": "one sentence expanding on the title",
  "emoji": "one emoji that fits the angle",
  "tags": ["2-4 short tags, e.g. team/competition/theme names"],
  "read_min": 3,
  "body": [ ...blocks... ]
}

Each block in "body" is one of:
  {"t":"h2","text":"..."}
  {"t":"p","html":"... may use <b> and <i> only, no other tags ..."}
  {"t":"stat","items":[{"k":"label","v":"value"}, ...]}   -- 3-5 items, headline numbers
  {"t":"list","items":["...", "..."]}
  {"t":"quote","text":"..."}                              -- one pull-quote sentence, your own synthesis, not attributed to anyone

Write 6-10 blocks: open with 1-2 "p" blocks setting up this week's performance, a "stat" block \
with the headline numbers, one or more "h2" sections putting it in season context (using the \
percentile/profile data), a "list" or second stat block, a "quote", and a closing "h2" verdict. \
Total ~450-650 words across the p/list/quote text. Do not add anything outside the JSON object."""


def _slugify(title: str, week: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    if len(s) > 70:
        s = s[:70].rsplit("-", 1)[0]                   # trim to the last whole word
    return f"{s}-{week.lower()}"


def _iso_week():
    y, w, _ = datetime.date.today().isocalendar()
    return f"{y}-W{w:02d}"


def _pick_topic(week_data: dict):
    """The week's storyline: the single best-rated performance with real output --
    filters out a stray high rating from a player who barely touched the ball."""
    for p in week_data.get("performers") or []:
        if p.get("goals") or p.get("assists") or (p.get("rating") or 0) >= 8.5:
            return p
    perf = week_data.get("performers") or []
    return perf[0] if perf else None


def generate_weekly_draft(db, week_data: dict, force: bool = False) -> dict:
    """db: an open SoccerDB (or compatible) connection, for web_player(). week_data:
    live_feed.week_summary_data()'s output. Returns {'available': bool, 'slug'?: str,
    'skipped'?: str}. Writes a draft row via blog.save_generated_post on success."""
    week = _iso_week()
    if not force and blog.week_has_post(week):
        return {"available": False, "skipped": "already generated this week"}
    topic = _pick_topic(week_data or {})
    if not topic:
        return {"available": False, "skipped": "no standout performance this week"}

    profile = db.web_player(topic["name"])
    if not profile:
        return {"available": False, "skipped": f"no Atlastra profile for {topic['name']}"}

    payload = {
        "this_week": {"opponent": topic.get("opponent"), "team": topic.get("team"),
                      "competition": topic.get("competition"), "rating": topic.get("rating"),
                      "goals": topic.get("goals"), "assists": topic.get("assists"),
                      "player_of_the_match": topic.get("potm")},
        "season_profile": _player_summary(profile),
    }
    import json as _json
    user = ("Write this week's article about this player, from this data (JSON):\n\n"
            + _json.dumps(payload, ensure_ascii=False, default=str))

    raw, model_used = None, None
    from webapp import gemini
    if gemini.available():
        raw = gemini.generate(SYSTEM + "\n\n---\n\n" + user, temperature=0.55)
        model_used = "gemini-flash"
    if not raw and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        try:
            import anthropic
            msg = anthropic.Anthropic().messages.create(
                model=MODEL, max_tokens=4000, thinking={"type": "adaptive"},
                system=SYSTEM, messages=[{"role": "user", "content": user}])
            raw = "".join(b.text for b in msg.content if b.type == "text").strip()
            model_used = MODEL
        except Exception:                                # noqa: BLE001
            raw = None
    if not raw:
        return {"available": False, "skipped": "no LLM configured (GEMINI_API_KEY/ANTHROPIC_API_KEY)"}

    parsed = gemini.extract_json(raw)
    if not _valid_post(parsed):
        return {"available": False, "skipped": "model output failed validation"}

    slug = _slugify(parsed["title"], week)
    post = {
        "slug": slug, "title": parsed["title"], "subtitle": parsed.get("subtitle"),
        "author": "Atlastra", "date": datetime.date.today().isoformat(),
        "read_min": int(parsed.get("read_min") or 4), "emoji": parsed.get("emoji", "⚽"),
        "image": topic.get("photo") or profile.get("photo"),
        "tags": parsed.get("tags") or [], "player": topic["name"], "body": parsed["body"],
    }
    blog.save_generated_post(post, model_used, week)
    return {"available": True, "slug": slug, "player": topic["name"], "model": model_used}


_ALLOWED_BLOCK_TYPES = {"h2", "p", "stat", "list", "quote", "table"}


def _valid_post(p) -> bool:
    """Reject anything that doesn't match the exact schema the frontend renderer
    expects -- a malformed draft should fail loudly here, not render broken on the
    admin preview or (post-approval) the public page."""
    if not isinstance(p, dict):
        return False
    if not (isinstance(p.get("title"), str) and p["title"].strip()):
        return False
    body = p.get("body")
    if not isinstance(body, list) or not (3 <= len(body) <= 16):
        return False
    for b in body:
        if not isinstance(b, dict) or b.get("t") not in _ALLOWED_BLOCK_TYPES:
            return False
        if b["t"] in ("h2", "quote") and not isinstance(b.get("text"), str):
            return False
        if b["t"] == "p" and not isinstance(b.get("html"), str):
            return False
        if b["t"] in ("stat", "list") and not isinstance(b.get("items"), list):
            return False
        if b["t"] == "p" and re.search(r"<(?!/?[bi]>)[a-zA-Z]", b.get("html") or ""):
            return False  # only <b>/<i> allowed in generated HTML
    return True
