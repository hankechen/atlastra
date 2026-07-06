"""
Search-engine plumbing for the Atlastra webapp (stdlib-only, no deps).

The frontend is JavaScript-rendered: every .html file ships an empty shell that
JS fills after fetching /api/*. Crawlers that don't run JS (and Google's first
indexing pass) therefore see a blank page with a generic <title> and no
description -- so nothing ranks and the long tail of player/team pages is never
even discovered (they live behind a JS search box, with no crawlable links).

This module fixes that without an SSR rewrite:
  * robots_txt()  -- crawl guidance + sitemap pointer
  * sitemap_xml() -- every player and team URL, generated from the warehouse so
                     Google can discover the pages it otherwise can't find
  * inject_head() -- rewrites each served HTML page's <head> with a real,
                     page-specific <title>, meta description, canonical URL and
                     Open Graph/Twitter cards. For a player/team page the copy is
                     built from that entity's actual stats, so the search snippet
                     is meaningful even before any JavaScript runs.

Everything derives from ONE canonical origin (SITE_URL) regardless of which host
served the request, so hits to the raw IP/sslip.io host don't fragment ranking
signals across duplicate URLs.
"""
import html
import os
import threading
import time
from urllib.parse import quote

from config import DB_PATH

# Canonical public origin. Every canonical link, sitemap entry and OG url points
# here no matter which host answered, consolidating SEO onto one domain. Override
# with ATLASTRA_SITE_URL when the domain changes (e.g. a real atlastra.com).
SITE_URL = (os.environ.get("ATLASTRA_SITE_URL") or "https://atlastra.duckdns.org").rstrip("/")
SITE_NAME = "Atlastra"

# Per-page copy for the fixed (non-entity) pages. Title is the full <title>; the
# suffix " | Atlastra" is added automatically except on the home page.
PAGE_META = {
    "index.html": ("Atlastra — Football Intelligence",
        "Player ratings, scouting reports, live scores and analytics for Europe's "
        "top-5 leagues, the Champions League and the World Cup.", True),
    "players.html": ("Player Ratings & Profiles",
        "Browse Atlastra's 0–99 player ratings and profiles across Europe's top "
        "leagues — filter by position, club and form.", False),
    "teams.html": ("Team Ratings & Standings",
        "Team ratings, league standings and squad analysis for the top-5 European "
        "leagues and the Champions League.", False),
    "search.html": ("Search Players, Teams & Matches",
        "Search Atlastra for any player or team, or look up a head-to-head match "
        "between two sides.", False),
    "leaders.html": ("Stat Leaders",
        "This season's leaders for goals, assists and advanced metrics across "
        "Europe's top leagues.", False),
    "rankings.html": ("Player Rankings",
        "The top-rated players by position and league on Atlastra's rating engine.", False),
    "compare.html": ("Compare Players",
        "Compare any two players head-to-head across ratings, stats and play style.", False),
    "archetypes.html": ("Player Archetypes & Scouting Roles",
        "Explore scouting archetypes — find players who fit a role and see their "
        "closest stylistic matches.", False),
    "scout.html": ("Scouting Explorer",
        "Filter and shortlist players by role fit, stats and market value.", False),
    "worldcup.html": ("World Cup Hub",
        "World Cup fixtures, results, bracket, standings and player ratings.", False),
    "ucl.html": ("Champions League Hub",
        "UEFA Champions League fixtures, results, bracket and player ratings back "
        "to 2008/09.", False),
    "live.html": ("Live Scores & Fixtures",
        "Live football scores and fixtures for the top-5 leagues, the Champions "
        "League and international tournaments.", False),
    "bestxi.html": ("Team of the Week & Best XI",
        "The best XI by Atlastra ratings, plus tournament teams of the week.", False),
}

# Pages with no standalone search value -> keep them out of the index (private
# dashboards, ephemeral live pages, and the casual guessing games).
NOINDEX_PAGES = {"admin.html", "profile.html", "draft.html", "guess.html", "mystery.html",
                 "higherlower.html", "predict.html", "daily.html", "card.html", "styles.html"}

# ---- warehouse-backed entity index (players + teams), cached ----------------
# Loaded once and refreshed on a long TTL: the underlying stats only change when
# the pipeline reruns, so a per-request DB hit would be pure waste. A player-name
# lookup on the hot path is then just a dict get.
_TTL = 6 * 3600
_lock = threading.Lock()
_cache = {"ts": 0.0, "players": {}, "teams": set(), "lastmod": None}

# Must match the server's own DB mode: DuckDB won't open the same file read-only in
# one handle while another holds it read-write, so a mismatch would fail the load
# and silently shrink the sitemap to just the static pages. server.configure() sets
# this from DB_READ_ONLY at startup; the read-only default suits standalone use.
_READ_ONLY = True


def configure(read_only: bool) -> None:
    """Align seo's warehouse connection with the server's DB mode (call at startup)."""
    global _READ_ONLY
    _READ_ONLY = read_only


def _refresh(force: bool = False) -> dict:
    now = time.time()
    with _lock:
        if not force and _cache["players"] and now - _cache["ts"] < _TTL:
            return _cache
    # short-lived connection, opened at most once per TTL, in the server's DB mode.
    players, teams = {}, set()
    try:
        from analytics.queries import SoccerDB
        with SoccerDB(read_only=_READ_ONLY) as db:
            rows = db.con.execute(
                "SELECT player, team, main_position, rating, classification "
                "FROM v_player_profile_full WHERE player IS NOT NULL").fetchall()
        for name, team, pos, rating, cls in rows:
            players[name] = {"team": team, "pos": pos, "rating": rating, "cls": cls}
            if team:
                teams.add(team)
    except Exception:                                     # noqa: BLE001 -- SEO is best-effort
        pass
    try:
        lastmod = time.strftime("%Y-%m-%d", time.gmtime(os.path.getmtime(DB_PATH)))
    except OSError:
        lastmod = None
    with _lock:
        _cache.update(ts=now, players=players, teams=teams, lastmod=lastmod)
        return _cache


# ---- robots.txt -------------------------------------------------------------
def robots_txt() -> bytes:
    """Allow crawling of pages, keep bots out of the JSON API and private areas,
    and point them at the sitemap."""
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /admin.html\n"
        f"\nSitemap: {SITE_URL}/sitemap.xml\n"
    )
    return body.encode()


# ---- sitemap.xml ------------------------------------------------------------
def _url(path: str, lastmod: str | None, priority: str) -> str:
    loc = html.escape(f"{SITE_URL}{path}", quote=False)
    lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    return f"  <url><loc>{loc}</loc>{lm}<priority>{priority}</priority></url>"


def sitemap_xml() -> bytes:
    """Full URL set: the fixed pages plus every player and team page, so Google can
    discover the entity pages that have no crawlable inbound links."""
    c = _refresh()
    lm = c["lastmod"]
    urls = [_url("/", lm, "1.0")]
    for page in ("players.html", "teams.html", "search.html", "leaders.html",
                 "rankings.html", "archetypes.html", "compare.html", "scout.html",
                 "worldcup.html", "ucl.html", "live.html", "bestxi.html"):
        urls.append(_url("/" + page, lm, "0.8"))
    for name in sorted(c["players"]):
        urls.append(_url(f"/player.html?name={quote(name, safe='')}", lm, "0.6"))
    for team in sorted(c["teams"]):
        urls.append(_url(f"/team.html?name={quote(team, safe='')}", lm, "0.6"))
    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(urls) + "\n</urlset>\n")
    return doc.encode()


# ---- per-page <head> injection ----------------------------------------------
def _meta_block(title: str, desc: str, canonical: str, noindex: bool) -> str:
    """The tags injected before </head>: description, canonical, robots, Open Graph
    and Twitter card. Title is swapped separately."""
    t, d = html.escape(title), html.escape(desc)
    can = html.escape(canonical, quote=True)
    img = html.escape(f"{SITE_URL}/favicon.svg", quote=True)
    tags = [f'<meta name="description" content="{d}">',
            f'<link rel="canonical" href="{can}">']
    if noindex:
        tags.append('<meta name="robots" content="noindex, follow">')
    tags += [
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:title" content="{t}">',
        f'<meta property="og:description" content="{d}">',
        f'<meta property="og:url" content="{can}">',
        f'<meta property="og:image" content="{img}">',
        '<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{t}">',
        f'<meta name="twitter:description" content="{d}">',
    ]
    return "".join(tags)


def _entity_meta(page: str, query: dict) -> tuple[str, str] | None:
    """(title, description) for a player/team page built from real stats, or None
    if this isn't an entity page (falls back to the page default)."""
    name = (query.get("name") or [""])[0]
    if page == "player.html" and name:
        p = _refresh()["players"].get(name)
        if p:
            role = f"{name}, {p['pos']}" if p["pos"] else name
            club = f" for {p['team']}" if p["team"] else ""
            rat = f" Atlastra rating {p['rating']}" if p["rating"] is not None else ""
            cls = f" ({p['cls']})" if p["cls"] else ""
            title = f"{name}{(' — ' + p['team']) if p['team'] else ''}"
            desc = (f"{role}{club}.{rat}{cls}. Season stats, scouting report, "
                    f"market value, play style and similar players on Atlastra.")
            return title, desc
        return name, (f"{name} — player profile, ratings, stats and scouting report "
                      f"on Atlastra.")
    if page == "team.html" and name:
        return (f"{name} — Squad & Ratings",
                f"{name} squad ratings, league standing, form and performance "
                f"analysis on Atlastra.")
    return None


def inject_head(body: bytes, page: str, query: dict, req_path: str) -> bytes:
    """Rewrite a served HTML page's <title> and inject SEO meta tags. Best-effort:
    any failure returns the original bytes unchanged."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    ent = _entity_meta(page, query)
    if ent:
        raw_title, desc = ent
        noindex = False
    else:
        meta = PAGE_META.get(page)
        if meta:
            raw_title, desc, _home = meta
        else:                                             # unknown page -> generic
            raw_title, desc = SITE_NAME, PAGE_META["index.html"][1]
        noindex = page in NOINDEX_PAGES
    is_home = bool(PAGE_META.get(page, (None, None, False))[2])
    title = raw_title if is_home else f"{raw_title} | {SITE_NAME}"

    # canonical: home -> origin; entity pages keep their ?name= so each is distinct;
    # everything else is the bare page path (drops tracking/query noise).
    if page == "index.html":
        canonical = SITE_URL + "/"
    elif ent:
        canonical = f"{SITE_URL}/{page}?name={quote((query.get('name') or [''])[0], safe='')}"
    else:
        canonical = f"{SITE_URL}/{page}"

    block = _meta_block(title, desc, canonical, noindex)
    esc_title = html.escape(title)
    # swap the existing <title>…</title> (present on every page), else prepend one.
    lo = text.lower()
    i, j = lo.find("<title"), lo.find("</title>")
    if 0 <= i < j:
        j += len("</title>")
        text = text[:i] + f"<title>{esc_title}</title>" + text[j:]
    k = text.lower().find("</head>")
    if k >= 0:
        text = text[:k] + block + text[k:]
    return text.encode("utf-8")
