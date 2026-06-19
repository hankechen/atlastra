"""
Licensed player photos from Wikimedia Commons -> table `player_image`.

The app currently shows FotMob CDN headshots (player_enrichment.fotmob_player_id
-> playerimages/<id>.png). Those are licensed *to FotMob* (Imago/Getty), so
hotlinking them is a ToS/copyright grey area. This loader backfills a legal,
free alternative: each player's lead Wikipedia image, but ONLY when it lives on
Wikimedia Commons (CC BY-SA / public domain) -- never the locally-uploaded
non-free (`/wikipedia/en/`) images. Coverage is partial; queries.py falls back
to the FotMob URL for anyone Commons doesn't have.

We key the result by fotmob_player_id so queries.player_photo() can prefer a
licensed image with no call-site changes (the whole app already passes fpid).

Attribution (artist + licence) is stored per image for CC-BY-SA compliance.

Run (any time after player_enrichment exists):
    python -m pipeline.load_wikimedia_images
"""
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

import duckdb

try:
    from config import DB_PATH
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH

API = "https://en.wikipedia.org/w/api.php"
# Wikipedia blocks requests without a descriptive User-Agent.
UA = "AtlastraImageBot/1.0 (soccer-analytics; contact: local-dev)"
BATCH = 50
SLEEP = 1.0           # polite base delay between requests
MAX_RETRY = 5         # exponential backoff on 429/5xx
COMMONS = "/wikipedia/commons/"   # CC/PD; "/wikipedia/en/" would be local non-free


def _get(params: dict) -> dict:
    params = {**params, "format": "json", "action": "query", "maxlag": "5"}
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < MAX_RETRY - 1:
                wait = 2 ** attempt * 3      # 3, 6, 12, 24s
                retry_after = e.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = max(wait, int(retry_after))
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")


def _resolve_map(q: dict) -> dict:
    """Build sent-title -> final-page-title via the API's normalize+redirect chains."""
    norm = {x["from"]: x["to"] for x in q.get("normalized", [])}
    redir = {x["from"]: x["to"] for x in q.get("redirects", [])}

    def final(title):
        title = norm.get(title, title)
        return redir.get(title, title)
    return final


def _strip_html(s: str) -> str:
    return html.unescape(re.sub("<[^>]+>", "", s or "")).strip() or None


def _fetch_thumbs(names: list[str]) -> dict:
    """name -> {url, file} for names whose lead image is on Commons."""
    out = {}
    q = _get({"prop": "pageimages", "piprop": "thumbnail|name",
              "pithumbsize": "400", "redirects": "1", "titles": "|".join(names)})
    data = q.get("query", {})
    final = _resolve_map(data)
    by_title = {p["title"]: p for p in data.get("pages", {}).values()}
    for name in names:
        page = by_title.get(final(name))
        if not page:
            continue
        thumb = page.get("thumbnail", {})
        src = thumb.get("source")
        if src and COMMONS in src and page.get("pageimage"):
            # MediaWiki titles canonicalise underscores to spaces; match that so
            # the file title keys line up with the imageinfo response titles.
            out[name] = {"url": src, "file": "File:" + page["pageimage"].replace("_", " ")}
    return out


COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def _file_from_url(url: str) -> str | None:
    """Derive the Commons 'File:Name' title from a thumb or original image URL."""
    m = re.search(r"/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)", url)
    if not m:
        return None
    return "File:" + urllib.parse.unquote(m.group(1)).replace("_", " ")


def _fetch_credits(files: list[str], api: str = API) -> dict:
    """File title -> {license, credit, page} from Commons extmetadata."""
    out = {}
    params = {"prop": "imageinfo", "iiprop": "extmetadata|url",
              "format": "json", "action": "query", "maxlag": "5",
              "titles": "|".join(files)}
    url = api + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                q = json.loads(r.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < MAX_RETRY - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise
    for p in q.get("query", {}).get("pages", {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        ext = info.get("extmetadata", {})
        out[p["title"]] = {
            "license": (ext.get("LicenseShortName", {}) or {}).get("value"),
            "credit": _strip_html((ext.get("Artist", {}) or {}).get("value")),
            "page": info.get("descriptionurl"),
        }
    return out


def backfill_credits() -> None:
    """Populate license/credit/file_page for stored images via the Commons API,
    keyed off image_url (no name re-scan). Safe to re-run."""
    con = duckdb.connect(str(DB_PATH))
    rows = con.execute(
        "SELECT player_id, image_url FROM player_image WHERE image_url IS NOT NULL"
    ).fetchall()
    file_to_pids: dict[str, list] = {}
    for pid, url in rows:
        f = _file_from_url(url)
        if f:
            file_to_pids.setdefault(f, []).append(pid)
    files = list(file_to_pids)
    print(f"fetching attribution for {len(files)} Commons files…")
    creds: dict[str, dict] = {}
    for i in range(0, len(files), BATCH):
        try:
            creds.update(_fetch_credits(files[i:i + BATCH], api=COMMONS_API))
        except Exception as e:  # noqa: BLE001
            print(f"  batch {i//BATCH}: {e}")
        if (i // BATCH) % 4 == 0:
            print(f"  {min(i + BATCH, len(files))}/{len(files)} files")
        time.sleep(SLEEP)
    n = 0
    for f, c in creds.items():
        for pid in file_to_pids.get(f, []):
            con.execute(
                "UPDATE player_image SET license=?, credit=?, file_page=? WHERE player_id=?",
                [c.get("license"), c.get("credit"), c.get("page"), pid])
            n += 1
    con.close()
    print(f"backfilled attribution for {n} player photos.")


def load_wikimedia_images() -> None:
    con = duckdb.connect(str(DB_PATH))
    players = con.execute("""
        SELECT DISTINCT pl.player_id, pl.player_name,
               max(e.fotmob_player_id) AS fpid
        FROM players pl
        JOIN player_enrichment e USING(player_id)
        JOIN v_player_profile_full f USING(player_id)
        WHERE e.fotmob_player_id IS NOT NULL
        GROUP BY 1, 2
    """).fetchall()
    print(f"resolving Commons photos for {len(players)} players…")

    # name -> (player_id, fpid); full names are ~unique, last wins on collision
    by_name = {name: (pid, fpid) for pid, name, fpid in players}
    names = list(by_name)

    thumbs = {}
    for i in range(0, len(names), BATCH):
        chunk = names[i:i + BATCH]
        try:
            thumbs.update(_fetch_thumbs(chunk))
        except Exception as e:  # noqa: BLE001 -- skip a bad batch, keep going
            print(f"  batch {i//BATCH}: {e}")
        if (i // BATCH) % 5 == 0:
            print(f"  {min(i + BATCH, len(names))}/{len(names)} scanned, "
                  f"{len(thumbs)} on Commons")
        time.sleep(SLEEP)

    # second pass: attribution for the matched files
    files = sorted({t["file"] for t in thumbs.values()})
    credits = {}
    for i in range(0, len(files), BATCH):
        try:
            credits.update(_fetch_credits(files[i:i + BATCH], api=COMMONS_API))
        except Exception as e:  # noqa: BLE001
            print(f"  credits batch {i//BATCH}: {e}")
        time.sleep(SLEEP)

    rows = []
    for name, t in thumbs.items():
        pid, fpid = by_name[name]
        c = credits.get(t["file"], {})
        rows.append((pid, int(fpid) if fpid is not None else None, name,
                     t["url"], c.get("license"), c.get("credit"), c.get("page")))

    con.execute("DROP TABLE IF EXISTS player_image")
    con.execute("""CREATE TABLE player_image
        (player_id BIGINT, fotmob_player_id BIGINT, player_name VARCHAR,
         image_url VARCHAR, license VARCHAR, credit VARCHAR, file_page VARCHAR)""")
    if rows:
        con.executemany("INSERT INTO player_image VALUES (?,?,?,?,?,?,?)", rows)
    con.close()
    print(f"player_image: {len(rows)} licensed Commons photos "
          f"({100*len(rows)//max(len(players),1)}% of players covered).")


if __name__ == "__main__":
    # `--credits` re-fetches only attribution for already-stored images.
    if "--credits" in sys.argv:
        backfill_credits()
    else:
        load_wikimedia_images()
