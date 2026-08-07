"""
Read-only query layer over the DuckDB warehouse.

Each public method maps to one of the README's Phase-One use cases and returns a
pandas DataFrame (or dict), so it can be reused by tests, notebooks, or a future
web/API layer.

Stats that Understat does not provide (duels, dribbles, tackles, interceptions,
big chances, passes completed, market value, manager, venue) are simply not
returned; see NOTES.md.
"""
import os
import random
import sys
import threading
import time

from functools import lru_cache

import duckdb
import numpy as np
import pandas as pd

try:
    from config import DB_PATH, FOCUS_SEASON, season_label
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DB_PATH, FOCUS_SEASON, season_label

try:
    from analytics.archetype_defs import ARCHETYPES
except ModuleNotFoundError:  # pragma: no cover
    from archetype_defs import ARCHETYPES

from legend_defs import LEGENDS, legend_list  # repo-root module (path set above)


# small coercion helpers for the web bundles (JSON-friendly, NaN-safe)
def _i(v):
    return None if v is None or pd.isna(v) else int(round(float(v)))


def _r(v, nd=1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _split(s):
    return [x.strip() for x in s.split(",")] if s else []


def _season_label(code):
    """'1415' -> '2014/15' — season codes are stored as the two two-digit years."""
    c = str(code or "")
    return f"20{c[:2]}/{c[2:]}" if len(c) == 4 and c.isdigit() else c


# A card of a given overall looks very different by position — these are the offsets from
# the overall that an EA FC card of that position typically carries (a 90-rated striker is
# not a 90 defender). Used to synthesise a card for a player we only have season stats for.
_CARD_SHAPE = {
    "ST": {"sho": 1, "pac": 0, "dri": -3, "pas": -12, "def": -38, "phy": -3, "hea": -3, "cre": -8},
    "W":  {"sho": -4, "pac": 4, "dri": 2, "pas": -8, "def": -30, "phy": -11, "hea": -13, "cre": -1},
    "AM": {"sho": -5, "pac": -3, "dri": 2, "pas": 1, "def": -25, "phy": -10, "hea": -12, "cre": 3},
    "CM": {"sho": -9, "pac": -6, "dri": -2, "pas": 2, "def": -8, "phy": -3, "hea": -8, "cre": 0},
    "WM": {"sho": -7, "pac": 2, "dri": 0, "pas": -3, "def": -18, "phy": -7, "hea": -10, "cre": -1},
    "DM": {"sho": -15, "pac": -8, "dri": -6, "pas": 0, "def": 2, "phy": 1, "hea": -3, "cre": -7},
    "FB": {"sho": -19, "pac": 3, "dri": -4, "pas": -3, "def": 1, "phy": -4, "hea": -8, "cre": -8},
    "CB": {"sho": -30, "pac": -6, "dri": -14, "pas": -8, "def": 3, "phy": 3, "hea": 3, "cre": -22},
    "GK": {"sho": -40, "pac": -20, "dri": -25, "pas": -12, "def": -20, "phy": -2, "hea": -10, "cre": -30},
}
# Which per-90 signal says a player was unusually good at each card attribute, and the value
# a typical rated player posts — MEASURED from the warehouse, not guessed. The first cut of
# this used invented references (passes 60, dribbles 2.4, tackles 5.0) that no player comes
# close to, so every synthesised card took the full penalty on passing, dribbling and
# defending. Half of these fields are also simply absent before the later seasons, and a
# missing stat must read as "unknown", never as "bad" — see the skip in _synth_card.
_CARD_SIGNAL = {"sho": ("xg", 0.19), "pas": ("passes", 39.0), "dri": ("dribbles", 1.08),
                "def": ("tackles_int", 3.03), "cre": ("creation", 1.30)}


def _synth_card(overall, family, p) -> dict:
    """Build an EA FC-style card for a player the Lab only has season statistics for — a
    historical season, or anyone without a current card.

    The rating alone isn't enough (a 90 striker and a 90 centre-back are different cards)
    and the raw per-90 percentiles alone are worse: they saturate, so 2014/15 Ronaldo comes
    out with 99 shooting and 29 dribbling, which the engine would read as a one-trick
    player. So the card is a positional template at his level, then nudged (at most ±7) by
    what that season's output was actually exceptional at. Shape from the stats, level from
    the rating."""
    shape = _CARD_SHAPE.get(family) or _CARD_SHAPE["CM"]
    n = p.get("per90") or {}
    sig = dict(n)
    sig["tackles_int"] = (n.get("tackles") or 0) + (n.get("interceptions") or 0)
    # What a player CREATED, which nothing read before: a season of 23 assists and 2.7
    # chances a game left no trace on the card, so the engine — which derives creativity
    # from passing and dribbling — decided 2014/15 Messi lacked the creativity to be an
    # Advanced Playmaker. Chances created and xA are populated for 95% of player-seasons,
    # assists for 67%, which makes this the most reliable signal available.
    sig["creation"] = ((n.get("chances") or 0) + 1.5 * (n.get("assists") or 0)
                       + 2.0 * (n.get("xa") or 0))
    card = {"o": int(overall)}
    for k, off in shape.items():
        base = overall + off
        name_ref = _CARD_SIGNAL.get(k)
        if name_ref:
            key, ref = name_ref
            got = sig.get(key) or 0
            # a stat we don't hold for that season says nothing about him — half these
            # fields are empty in the older data, and reading that as a weakness took 7
            # points off every historical player's passing, dribbling and defending
            if got > 0:
                span = 12.0 if k == "cre" else 7.0      # creation is the discriminating one
                base += max(-span, min(span, (got / ref - 1.0) * span))
        card[k] = int(max(20, min(99, round(base))))
    return card


def _fmt_season(code):
    return season_label(code) if code else code


# FotMob image CDNs -- crests keyed by team id, photos by player id.
# Set True to prefer licensed Wikimedia Commons photos (table player_image, built
# by pipeline.load_wikimedia_images) over the FotMob CDN, with per-photo credits.
# Off for now -- the app shows FotMob headshots; flip to re-enable.
USE_LICENSED_PHOTOS = False
FOTMOB_PLAYER_IMG = "https://images.fotmob.com/image_resources/playerimages/{}.png"
FOTMOB_TEAM_IMG = "https://images.fotmob.com/image_resources/logo/teamlogo/{}.png"


# Letters NFKD/strip_accents leaves alone (they're distinct letters, not accented
# ASCII) but that our warehouse spells inconsistently -- e.g. 'Ørjan Nyland' (ø) vs
# 'Martin Odegaard' (o). Fold them so SofaScore 'Ødegaard' matches our 'Odegaard'.
_SPECIAL_LETTERS = (("ø", "o"), ("æ", "ae"), ("å", "a"), ("ð", "d"), ("þ", "th"),
                    ("ß", "ss"), ("ł", "l"), ("đ", "d"), ("ı", "i"))


@lru_cache(maxsize=100_000)
def _fold_str(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    for a, b in _SPECIAL_LETTERS:
        s = s.replace(a, b)
    return s


def _fold(s):
    """Accent + special-letter folded lowercase string ('Dembélé'->'dembele',
    'Ødegaard'->'odegaard').

    Memoized because the World Cup lookups fold the same few thousand names on every
    profile request -- three methods each scan wc_player_stats and fold every row to
    match one player, which was the largest slice of Python time in a profile load.
    The mapping is a pure function of the string, so a cache of it cannot go stale.
    """
    return _fold_str(s if isinstance(s, str) else str(s or ""))


def _fold_sql(expr):
    """SQL counterpart of _fold(): strip_accents + lower + special-letter folding,
    so name matching is robust to ø/å/æ/… (applied to both column and parameter)."""
    s = f"strip_accents(lower({expr}))"
    for a, b in _SPECIAL_LETTERS:
        s = f"replace({s}, '{a}', '{b}')"
    return s


def _norm_team(s):
    """Accent/punctuation-folded team name for logo lookup (mirrors load_team_logos)."""
    import unicodedata
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s)
    for junk in (" fc", " cf", " afc", " calcio", " 1899"):
        s = s.replace(junk, " ")
    return " ".join(s.split())


def connect_retry(db_path, read_only=True, attempts=15, backoff=0.06, patience=45.0):
    """duckdb.connect that rides out file-lock contention. The live refresher
    (pipeline.load_live) takes a read-write lock every refresh; a connect that lands
    in that window otherwise raises. Retry only on lock/IO errors so real problems
    still surface immediately.

    `patience` is for the OTHER process: the web server now keeps one connection open
    across requests (see _pool_acquire), so a pipeline script started while the site is
    being used finds the warehouse held rather than momentarily busy. The server drops
    it after a short idle, so waiting works where failing fast would just mean running
    the command again. It says what it is waiting for rather than appearing to hang.
    """
    import time as _t
    deadline, told = _t.time() + patience, False
    i = 0
    while True:
        try:
            return duckdb.connect(str(db_path), read_only=read_only)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            held = any(k in msg for k in ("lock", "conflict", "being used"))
            if not held or (i >= attempts - 1 and _t.time() >= deadline):
                raise
            if not told and _t.time() > deadline - patience + 1.0:
                told = True
                print(f"warehouse is held by another process (likely the web server); "
                      f"waiting up to {patience:.0f}s for it to be released…",
                      file=sys.stderr, flush=True)
            _t.sleep(backoff if i < attempts else 0.5)
            i += 1


# ---- one open database, many cursors ---------------------------------------- #
# Opening the warehouse costs ~200ms: DuckDB reads the catalogue of a 155MB file.
# The web server opened and closed one per request, so EVERY endpoint paid it --
# a player profile fires nine calls and spent ~2.4s of its life just opening the
# same database nine times. DuckDB keeps the database instance alive as long as
# one connection to it is, and hands out further connections through cursor() in
# ~0.2ms, so the fix is simply not to let the last one close between requests.
#
# It is released again after ATLASTRA_DB_IDLE seconds of no use, and that matters:
# an open connection holds the file lock, which stops another process from opening
# the warehouse. Holding one forever would mean `pipeline.run_pipeline` could no
# longer run while the server is up. Thirty idle seconds is far longer than the gap
# between a page's own requests and far shorter than the pause before someone
# switches to a terminal to rebuild.
#
# ATLASTRA_DB_IDLE=0 means never release, which is what the deployed host sets:
# nothing else there opens the warehouse, and a site quiet most of the time would
# otherwise hand the catalogue read to whoever arrives next -- seconds on the
# t3.micro, not the ~200ms it costs on a laptop. ATLASTRA_NO_DB_POOL=1 opts out of
# pooling altogether.
_POOL: dict = {"key": None, "con": None, "live": 0, "last": 0.0}
_POOL_LOCK = threading.Lock()
_POOL_IDLE_S = float(os.environ.get("ATLASTRA_DB_IDLE", "30"))
_POOL_OFF = os.environ.get("ATLASTRA_NO_DB_POOL") == "1"
_POOL_KEEP = _POOL_IDLE_S <= 0                    # 0 -> hold it for the process's life

# Percentile fields for the profile's stat tiles, keyed by (season, position group).
# See _tile_percentiles for why this is safe to hold.
_TILE_FIELD: dict = {}


def warm_pool(db_path=None, read_only=True):
    """Open the warehouse now so the first visitor doesn't. Called at server start:
    the catalogue read is several seconds on a cold t3.micro, and a burst of requests
    all queue behind whoever triggers it."""
    if _POOL_OFF:
        return
    try:
        SoccerDB(db_path=db_path, read_only=read_only).close()
    except Exception as e:  # noqa: BLE001 -- warming is best-effort; requests still work
        print(f"db warm-up skipped: {type(e).__name__}: {str(e)[:100]}", flush=True)


def _pool_reaper():
    while True:
        time.sleep(5)
        with _POOL_LOCK:
            con, live, last = _POOL["con"], _POOL["live"], _POOL["last"]
            if _POOL_KEEP:
                continue
            if con is not None and live == 0 and time.time() - last > _POOL_IDLE_S:
                _POOL.update(con=None, key=None)
                try:
                    con.close()
                except Exception:  # noqa: BLE001 -- already gone; nothing to release
                    pass


def _pool_acquire(db_path, read_only):
    """A cursor on the shared connection, opening the database if nobody holds it."""
    key = (str(db_path), bool(read_only))
    with _POOL_LOCK:
        if _POOL["con"] is not None and _POOL["key"] != key:
            # A different path or mode: DuckDB refuses two configurations of one file
            # in a process, and mixing them is a caller bug rather than a pooling one.
            # Fall back to an unpooled connection instead of failing the request.
            return connect_retry(db_path, read_only=read_only), False
        if _POOL["con"] is None:
            _POOL["con"] = connect_retry(db_path, read_only=read_only)
            _POOL["key"] = key
            if not _POOL.get("reaper"):
                _POOL["reaper"] = True
                threading.Thread(target=_pool_reaper, daemon=True).start()
        _POOL["live"] += 1
        return _POOL["con"].cursor(), True


def _pool_release():
    with _POOL_LOCK:
        _POOL["live"] = max(0, _POOL["live"] - 1)
        _POOL["last"] = time.time()


class SoccerDB:
    def __init__(self, db_path=None, read_only=True):
        path = db_path or DB_PATH
        if _POOL_OFF:
            self.con, self._pooled = connect_retry(path, read_only=read_only), False
        else:
            self.con, self._pooled = _pool_acquire(path, read_only)
        self._logo_map = None
        self._wiki_photos = None
        self._fifa_ranks = None

    def fifa_rank(self, team_name):
        """FIFA World Ranking position for a national team (by SofaScore name), or
        None. Backed by the fifa_rankings snapshot (load_fifa_rankings)."""
        if self._fifa_ranks is None:
            try:
                rows = self.con.execute(
                    "SELECT team_name, ranking FROM fifa_rankings "
                    "WHERE ranking IS NOT NULL").fetchall()
            except Exception:  # noqa: BLE001 -- table not built yet
                rows = []
            self._fifa_ranks = {n: int(r) for n, r in rows}
        return self._fifa_ranks.get(team_name)

    def team_logo(self, name):
        """Crest URL for a team display name, or None. Resolves by normalized name
        against BOTH the FotMob name and our team_name, so it works whether the
        UI shows the Understat or FotMob spelling."""
        if self._logo_map is None:
            self._logo_map = {}
            try:
                rows = self.con.execute(
                    "SELECT fotmob_team_id, fotmob_name, team_name FROM team_logos "
                    "WHERE fotmob_team_id IS NOT NULL").fetchall()
            except Exception:  # noqa: BLE001 -- table may not exist yet
                rows = []
            for fid, fname, tname in rows:
                for n in (fname, tname):
                    k = _norm_team(n)
                    if k:
                        self._logo_map.setdefault(k, fid)
        fid = self._logo_map.get(_norm_team(name))
        return FOTMOB_TEAM_IMG.format(fid) if fid else None

    def player_photo(self, fotmob_player_id):
        """Photo URL for a player, keyed by FotMob id. Prefers a licensed
        Wikimedia Commons image (table player_image, backfilled by
        load_wikimedia_images) and falls back to the FotMob CDN for anyone
        Commons doesn't cover."""
        if fotmob_player_id is None or pd.isna(fotmob_player_id):
            return None
        fid = int(fotmob_player_id)
        if not USE_LICENSED_PHOTOS:
            return FOTMOB_PLAYER_IMG.format(fid)
        if self._wiki_photos is None:
            self._wiki_photos = {}
            try:
                rows = self.con.execute(
                    "SELECT fotmob_player_id, image_url FROM player_image "
                    "WHERE fotmob_player_id IS NOT NULL AND image_url IS NOT NULL"
                ).fetchall()
            except Exception:  # noqa: BLE001 -- table not built yet
                rows = []
            self._wiki_photos = {int(f): u for f, u in rows}
        return self._wiki_photos.get(fid) or FOTMOB_PLAYER_IMG.format(fid)

    def _photo_credit(self, pid: int) -> dict | None:
        """Attribution for a player's licensed Commons photo (None if the photo
        is the FotMob fallback or the table isn't built)."""
        if not USE_LICENSED_PHOTOS:
            return None
        try:
            r = self.con.execute(
                "SELECT credit, license, file_page FROM player_image "
                "WHERE player_id = ? AND image_url IS NOT NULL", [pid]).fetchone()
        except Exception:  # noqa: BLE001 -- table not built yet
            return None
        if not r:
            return None
        return {"credit": r[0], "license": r[1], "page": r[2]}

    def close(self):
        # Closes THIS cursor; the shared connection stays open so the next request
        # doesn't pay the catalogue read again (see _pool_acquire).
        try:
            self.con.close()
        finally:
            if self._pooled:
                self._pooled = False
                _pool_release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ----- small lookup helpers ------------------------------------------- #
    def find_player_id(self, name: str, season: str = FOCUS_SEASON) -> int | None:
        """Case-insensitive partial match. Ranks exact, then whole-word (so
        'Saka' -> Bukayo Saka, not Wan-Bis*saka*), then substring; ties by
        minutes."""
        fp, fq = _fold_sql("p.player_name"), _fold_sql("?")
        df = self.con.execute(
            f"""
            SELECT p.player_id, p.player_name, sum(ps.minutes) AS mins
            FROM players p JOIN player_season_stats ps USING(player_id)
            WHERE {fp} LIKE '%' || {fq} || '%'
            GROUP BY 1, 2
            ORDER BY
              ({fp} = {fq}) DESC,
              -- whole-word match: pad with spaces and treat '-' as a boundary, so
              -- 'Mbappe' matches both 'Ethan Mbappe' and 'Kylian Mbappe-Lottin'
              -- (then minutes break the tie -> Kylian), not Wan-Bis*saka*.
              ((' ' || replace({fp}, '-', ' ') || ' ') LIKE '% ' || {fq} || ' %') DESC,
              mins DESC
            LIMIT 1
            """,
            [name, name, name],
        ).fetchone()
        if df is not None:
            return int(df[0])
        return self._find_by_surname(name)

    def _find_by_surname(self, name: str) -> int | None:
        """Fallback when the full name isn't a substring of ours (e.g. SofaScore
        'Dayot Upamecano' vs our 'Dayotchanculle Upamecano'). Match on the surname
        as a whole word; if several share it, disambiguate by the first name (one
        is a prefix of the other). Only return on a unique result -- never guess."""
        parts = name.strip().split()
        if len(parts) < 2:
            return None
        first, surname = _fold(parts[0]), parts[-1]
        cands = self.con.execute(
            f"""
            SELECT p.player_id, p.player_name, sum(ps.minutes) AS mins
            FROM players p JOIN player_season_stats ps USING(player_id)
            WHERE (' ' || replace({_fold_sql("p.player_name")}, '-', ' ') || ' ')
                  LIKE '% ' || {_fold_sql("?")} || ' %'
            GROUP BY 1, 2 ORDER BY mins DESC
            """,
            [surname],
        ).fetchall()
        if not cands:
            return None
        # A lone surname match used to be returned outright -- but that wrongly maps a
        # DIFFERENT person who merely shares a surname (a non-DB 'Antoine Saliba' ->
        # our 'William Saliba'). Require a consistent first name (one a prefix of the
        # other), UNLESS our stored name is just the surname (no first name to compare,
        # e.g. 'Kudus'), where the surname match is all we have.
        hits = [c for c in cands
                if len(_fold(c[1]).split()) == 1
                or any(w.startswith(first) or first.startswith(w)
                       for w in _fold(c[1]).split())]
        return int(hits[0][0]) if len(hits) == 1 else None

    # common shorthands users type that aren't substrings of the canonical name
    _TEAM_ALIASES = {
        "man city": "Manchester City", "man utd": "Manchester United",
        "man united": "Manchester United", "spurs": "Tottenham",
        "tottenham hotspur": "Tottenham",                # the Tactics Lab's full club name
        "wolves": "Wolverhampton", "psg": "Saint Germain", "barca": "Barcelona",
        "atletico": "Atlético", "inter": "Internazionale", "gladbach": "Gladbach",
        "leverkusen": "Leverkusen", "dortmund": "Dortmund", "bayern": "Bayern",
    }

    # punctuation/accent-folded team_name in SQL (mirrors _norm_team) so a query
    # like 'Paris Saint-Germain' (hyphen) matches our 'Paris Saint Germain' (space).
    _NORM_TEAM_SQL = "regexp_replace(strip_accents(lower(team_name)), '[^a-z0-9]+', ' ', 'g')"
    # club-name filler tokens dropped, and spelling synonyms applied, when matching
    # SofaScore's long forms (e.g. 'FC Bayern München', 'Internazionale') to ours.
    _TEAM_FILLER = {"fc", "cf", "afc", "ac", "sc", "ssc", "as", "rc", "sl", "sd",
                    "ud", "cd", "ogc", "rcd", "aj", "us", "ss", "bsc", "vfb", "vfl",
                    "tsg", "rb", "sk", "fk", "if", "bk", "club", "calcio"}
    _TEAM_SYN = {"munchen": "munich", "internazionale": "inter",
                 "moenchengladbach": "gladbach", "monchengladbach": "gladbach"}

    def _team_id_like(self, term: str) -> int | None:
        term = _norm_team(term)
        if not term:
            return None
        row = self.con.execute(
            f"SELECT team_id FROM teams WHERE {self._NORM_TEAM_SQL} LIKE '%'||?||'%' "
            "ORDER BY length(team_name) LIMIT 1", [term]).fetchone()
        return None if row is None else int(row[0])

    def find_team_id(self, name: str) -> int | None:
        q = (name or "").strip()
        if not q:
            return None
        # 1) plain substring match (the canonical case; punctuation-folded both sides)
        tid = self._team_id_like(q)
        if tid is not None:
            return tid
        # 2) known shorthand -> canonical fragment
        alias = self._TEAM_ALIASES.get(q.lower())
        if alias and (tid := self._team_id_like(alias)) is not None:
            return tid
        # 3) drop club-filler tokens + apply spelling synonyms (SofaScore long forms
        #    like 'FC Bayern München' -> our 'Bayern Munich'), then retry as a phrase
        toks = [self._TEAM_SYN.get(t, t) for t in _norm_team(q).split()
                if t not in self._TEAM_FILLER]
        if toks:
            cleaned = " ".join(toks)
            if cleaned != _norm_team(q) and (tid := self._team_id_like(cleaned)) is not None:
                return tid
            # 4) token-AND fallback: every remaining word must appear
            if len(toks) > 1:
                where = " AND ".join([f"{self._NORM_TEAM_SQL} LIKE '%'||?||'%'"] * len(toks))
                row = self.con.execute(
                    f"SELECT team_id FROM teams WHERE {where} ORDER BY length(team_name) LIMIT 1",
                    toks).fetchone()
                if row is not None:
                    return int(row[0])
        return None

    def have_profiles(self, names: list[str]) -> set[str]:
        """Subset of `names` that resolve to a player profile in our warehouse."""
        return {n for n in set(names) if n and self.find_player_id(n) is not None}

    def ratings_by_name(self, names: list[str]) -> dict:
        """Map each resolvable name -> our Atlastra rating: the HIGHER of their
        League and UCL common-metric ratings for the current season (or the latest
        season available); falls back to the headline composite rating.
        """
        out = {}
        for n in set(names):
            if not n:
                continue
            pid = self.find_player_id(n)
            if pid is None:
                continue
            rows = self.con.execute(
                "SELECT season, rating FROM player_ratings_combined "
                "WHERE player_id=? AND rating IS NOT NULL", [pid]).fetchall()
            if rows:
                seasons = {s for s, _ in rows}
                sea = FOCUS_SEASON if FOCUS_SEASON in seasons else max(seasons)
                out[n] = int(round(max(r for s, r in rows if s == sea)))  # best of League/UCL
                continue
            # fall back to the datamb composite, but ONLY for the CURRENT season --
            # a stale rating (e.g. Nyland's 96.6 from 2024/25) is worse than none.
            r = self.con.execute(
                "SELECT rating FROM player_ratings WHERE player_id=? AND season=? "
                "AND rating IS NOT NULL ORDER BY minutes DESC LIMIT 1",
                [pid, FOCUS_SEASON]).fetchone()
            if r is not None:
                out[n] = int(round(r[0]))
        return out

    # ----- World Cup rating (0-100) --------------------------------------- #
    # A WC edition sits in / just after this domestic league-season code, so the
    # profile's season selector surfaces the right tournament (see wc_rating_for).
    _WC_EDITIONS = {"0910": "2010", "1314": "2014", "1718": "2018",
                    "2223": "2022", "2526": "2026"}

    def match_team_ids(self, event_id: int):
        """(home_team_id, away_team_id) for a match from our live_matches snapshot, or
        (None, None). Lets the preview prewarm BOTH teams' form/squad paths immediately
        -- without waiting a relay cycle for the SofaScore event header to reveal the
        team ids -- so a cold preview fills in one cycle instead of two."""
        try:
            row = self.con.execute(
                "SELECT home_team_id, away_team_id FROM live_matches WHERE event_id = ?",
                [int(event_id)]).fetchone()
        except Exception:
            return (None, None)
        return (row[0], row[1]) if row else (None, None)

    def wc_edition_for_event(self, event_id: int) -> str | None:
        """The World Cup edition ('2010'..'2026') a SofaScore match belongs to, from
        our snapshotted wc_matches, or None if it isn't a World Cup match. This is the
        robust WC-lineup signal on the cloud host, where the live /event header (which
        carries the uniqueTournament id) may not be cached."""
        try:
            row = self.con.execute(
                "SELECT season FROM wc_matches WHERE event_id = ?", [int(event_id)]).fetchone()
        except Exception:
            return None
        return str(row[0]) if row and row[0] is not None else None

    def wc_ratings_by_ids(self, edition: str, sofa_ids) -> dict:
        """SofaScore player_id -> stats-based WC 0-99 rating for an edition (match
        lineups). Precomputed in pipeline.rate_wc and stored on the row."""
        ids = [int(i) for i in sofa_ids if i is not None]
        if not ids or not edition:
            return {}
        rows = self.con.execute(
            f"SELECT player_id, atlas_rating FROM wc_player_stats WHERE season = ? "
            f"AND player_id IN ({','.join(['?'] * len(ids))})", [str(edition), *ids]).fetchall()
        return {int(pid): int(r) for pid, r in rows if r is not None}

    def wc_tournament_stats_by_ids(self, edition: str, sofa_ids) -> dict:
        """SofaScore player_id -> {goals, assists, apps} for a WC edition -- the
        player's whole-tournament totals, shown in the match player modal."""
        ids = [int(i) for i in sofa_ids if i is not None]
        if not ids or not edition:
            return {}
        rows = self.con.execute(
            f"SELECT player_id, goals, assists, appearances FROM wc_player_stats "
            f"WHERE season = ? AND player_id IN ({','.join(['?'] * len(ids))})",
            [str(edition), *ids]).fetchall()
        return {int(pid): {"goals": _i(g) or 0, "assists": _i(a) or 0, "apps": _i(ap) or 0}
                for pid, g, a, ap in rows}

    def wc_rating_for(self, player_id: int, season: str) -> dict | None:
        """Stats-based WC rating for one of OUR players in the World Cup that falls in
        `season`, or None if that season had no WC / the player has no WC minutes.
        Matched on folded name tokens (exact, or >=2 shared tokens) so name-format
        differences resolve -- e.g. our 'Kylian Mbappe-Lottin' vs WC 'Kylian Mbappé'."""
        edition = self._WC_EDITIONS.get(season)
        if edition is None:
            return None
        nm = self.con.execute("SELECT player_name FROM players WHERE player_id=?",
                              [player_id]).fetchone()
        if not nm:
            return None
        ours = set(_fold(nm[0]).replace("-", " ").split())
        if not ours:
            return None
        rows = self.con.execute(
            "SELECT player, atlas_rating, atlas_class, appearances, minutes "
            "FROM wc_player_stats WHERE season = ? AND atlas_rating IS NOT NULL", [edition]).fetchall()
        best = None
        for player, rating, cls, apps, mins in rows:
            wt = set(_fold(player).replace("-", " ").split())
            if wt == ours or len(wt & ours) >= 2:       # robust to name-format diffs
                if best is None or (mins or 0) > (best[4] or 0):
                    best = (player, rating, cls, apps, mins)
        if not best:
            return None
        rating, cls = int(best[1]), best[2]
        # apply the same manual WC rating override the directory uses, so the profile
        # gauge matches — and recompute the classification band from the new rating.
        ov = self._WC_RATING_OVERRIDE.get(nm[0].lower()) or self._WC_RATING_OVERRIDE.get(best[0].lower())
        if ov is not None:
            rating = int(ov)
            cls = ("World-Class" if rating >= 90 else "Elite" if rating >= 80
                   else "Above Average" if rating >= 65 else "Average" if rating >= 50
                   else "Below Average")
        return {"rating": rating, "classification": cls,
                "apps": _i(best[3]), "minutes": _i(best[4]), "edition": edition}

    _WC_POS = {"G": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Forward"}

    def _player_worldcups(self, player_id: int) -> list[dict]:
        """Every World Cup edition this player appeared in, newest first, for the
        profile's World Cup card. wc_player_stats uses SofaScore ids (a different
        namespace from ours), so match on folded name tokens like wc_rating_for
        (exact or >=2 shared tokens); keep the most-minutes row per edition."""
        nm = self.con.execute("SELECT player_name FROM players WHERE player_id=?",
                              [player_id]).fetchone()
        if not nm:
            return []
        ours = set(_fold(nm[0]).replace("-", " ").split())
        if not ours:
            return []
        rows = self.con.execute(
            "SELECT season, player, team, position, appearances, minutes, goals, "
            "assists, rating, atlas_rating, atlas_class FROM wc_player_stats").fetchall()
        best: dict[str, dict] = {}
        for (edn, player, team, pos, apps, mins, goals, ast, rating, ar, ac) in rows:
            wt = set(_fold(player).replace("-", " ").split())
            if not (wt == ours or len(wt & ours) >= 2):
                continue
            cur = best.get(edn)
            if cur is None or (mins or 0) > (cur["minutes"] or 0):
                best[edn] = {
                    "edition": edn, "team": team,
                    "position": self._WC_POS.get(pos, pos),
                    "apps": _i(apps), "minutes": _i(mins),
                    "goals": _i(goals), "assists": _i(ast),
                    "sofa_rating": round(float(rating), 2) if rating is not None else None,
                    "atlas_rating": _i(ar), "atlas_class": ac,
                }
        return [best[e] for e in sorted(best, reverse=True)]

    # ----- use case 1: player statistics ---------------------------------- #
    def player_statistics(self, player: str, season: str = FOCUS_SEASON) -> pd.DataFrame:
        """Understat core stats + FotMob enrichment (dribbles, tackles,
        interceptions, big chances, pass completion) where available."""
        pid = self.find_player_id(player, season)
        if pid is None:
            return pd.DataFrame()
        return self.con.execute(
            """
            SELECT pl.player_name, l.league_name AS league, t.team_name AS team, ps.season,
                   ps.position, ps.position_group,
                   ps.matches, ps.minutes, ps.goals, ps.assists,
                   ps.goals + ps.assists AS goal_contributions,
                   ps.shots, ps.key_passes AS chances_created,
                   ps.xg, ps.np_xg, ps.xa, ps.xg_chain, ps.xg_buildup,
                   ps.goals_per90, ps.assists_per90, ps.xg_per90, ps.xa_per90,
                   ps.key_passes_per90,
                   -- FotMob enrichment (NULL if no match):
                   e.big_chances_created, e.big_chances_missed,
                   e.dribbles_completed, e.dribble_success_pct,
                   e.tackles, e.interceptions, e.recoveries,
                   e.duels_won, e.duels_won_pct,
                   e.passes_completed, e.pass_accuracy_pct, e.fotmob_rating
            FROM player_season_stats ps
            JOIN players pl USING(player_id)
            JOIN teams   t  USING(team_id)
            JOIN leagues l  ON l.league_key = ps.league_key
            LEFT JOIN player_enrichment e
                   ON e.player_id = ps.player_id AND e.season = ps.season
                  AND e.league_key = ps.league_key
            WHERE ps.player_id = ? AND ps.season = ?
            ORDER BY ps.minutes DESC
            """,
            [pid, season],
        ).df()

    # ----- use case 2: player classification ------------------------------ #
    def player_classification(self, player: str, season: str = FOCUS_SEASON) -> pd.DataFrame:
        pid = self.find_player_id(player, season)
        if pid is None:
            return pd.DataFrame()
        return self.con.execute(
            """
            SELECT pl.player_name, r.league_key, r.season, r.position_group,
                   r.rating, r.classification, r.rank_in_group,
                   round(r.percentile_in_group * 100, 1) AS percentile
            FROM player_ratings r JOIN players pl USING(player_id)
            WHERE r.player_id = ? AND r.season = ?
            ORDER BY r.minutes DESC
            """,
            [pid, season],
        ).df()

    def best_in_position(self, league_key: str, position_group: str,
                         season: str = FOCUS_SEASON, limit: int = 10) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT r.rank_in_group AS rank, pl.player_name, t.team_name AS team,
                   r.rating, r.classification, ps.goals, ps.assists, round(ps.xg,1) AS xg
            FROM player_ratings r
            JOIN players pl USING(player_id)
            JOIN player_season_stats ps
              ON ps.player_id=r.player_id AND ps.season=r.season AND ps.league_key=r.league_key
            JOIN teams t ON t.team_id = ps.team_id
            WHERE r.league_key = ? AND r.position_group = ? AND r.season = ?
            ORDER BY r.rank_in_group LIMIT ?
            """,
            [league_key, position_group, season, limit],
        ).df()

    # ----- use case 3: player profile ------------------------------------- #
    def player_profile(self, player: str) -> dict:
        pid = self.find_player_id(player)
        if pid is None:
            return {}
        head = self.con.execute(
            "SELECT player_name, primary_position, position_group FROM players WHERE player_id=?",
            [pid],
        ).fetchone()
        # Career timeline (domestic + UCL merged per season) from v_player_career.
        career = self.con.execute(
            "SELECT season, team, competitions, games, minutes, goals, assists "
            "FROM v_player_career WHERE player_id = ? ORDER BY season",
            [pid],
        ).df()
        # Market value (Transfermarkt) + strengths/weaknesses/areas (rating engine).
        mv = self.con.execute(
            "SELECT market_value_eur FROM player_market_value WHERE player_id=? AND season=?",
            [pid, FOCUS_SEASON],
        ).fetchone()
        swot = self.con.execute(
            "SELECT rating, classification, strengths, weaknesses, areas_of_improvement, "
            "detailed_position FROM v_player_profile_full WHERE player_id=?",
            [pid],
        ).fetchone()
        return {
            "player_name": head[0],
            "main_position": head[1],
            "position_group": head[2],
            "detailed_position": swot[5] if swot else None,  # FotMob LW/RW/LB/RB/CAM/...
            "market_value_eur": mv[0] if mv else None,
            "rating": swot[0] if swot else None,
            "classification": swot[1] if swot else None,
            "strengths": swot[2] if swot else None,
            "weaknesses": swot[3] if swot else None,
            "areas_of_improvement": swot[4] if swot else None,
            "career": career,           # seasons present in this warehouse
        }

    # ----- use case 4: cross-year progression ----------------------------- #
    # README default comparison stats per position group. FotMob-sourced stats
    # (dribbles/big-chances/passes/duels/tackles/interceptions/recoveries) are
    # only populated from 2020/21 on; earlier seasons show goals/assists only.
    DEFAULT_PROGRESSION_STATS = {
        "FWD": ["ga_per90", "dribbles_completed", "chances_created"],
        "MID": ["ga_per90", "big_chances_created", "passes_completed", "duels_won"],
        "DEF": ["tackles", "interceptions", "duels_won", "recoveries"],
        "GK":  ["ga_per90", "pass_accuracy_pct"],
    }
    _PROGRESSION_BASE = ["season", "team", "games", "minutes", "goals", "assists"]

    def player_progression(self, player: str, stats: list[str] | None = None) -> pd.DataFrame:
        pid = self.find_player_id(player)
        if pid is None:
            return pd.DataFrame()
        grp = self.con.execute(
            "SELECT position_group FROM players WHERE player_id=?", [pid]
        ).fetchone()[0]
        # only allow real columns of the season-stats view (guards custom input)
        allowed = {r[1] for r in self.con.execute(
            "PRAGMA table_info('v_player_season_stats')").fetchall()}
        cols = stats or self.DEFAULT_PROGRESSION_STATS.get(grp, ["ga_per90", "goals", "assists"])
        cols = [c for c in cols if c in allowed and c not in self._PROGRESSION_BASE]
        select_cols = ", ".join(self._PROGRESSION_BASE + cols)
        return self.con.execute(
            f"SELECT {select_cols} FROM v_player_season_stats "
            "WHERE player_id = ? ORDER BY season",
            [pid],
        ).df()

    # ----- use case 5: player comparison ---------------------------------- #
    # FotMob-enrichment stat columns (used by stat_leaders to validate input).
    ENRICH_STATS = {
        "big_chances_created", "big_chances_missed", "dribbles_completed",
        "dribble_success_pct", "tackles", "interceptions", "recoveries",
        "passes_completed", "pass_accuracy_pct", "fotmob_rating",
        "duels_won", "duels_won_pct",
    }

    def compare_players(self, players: list[str], season: str = FOCUS_SEASON,
                        stats: list[str] | None = None) -> pd.DataFrame:
        """Compare players side-by-side for one season on custom stats, or the
        same position-based default stats as cross-year progression (use case 4).
        Pulls from v_player_season_stats so both sources are already merged."""
        ids = [i for i in (self.find_player_id(p, season) for p in players) if i is not None]
        if not ids:
            return pd.DataFrame()
        groups = [r[0] for r in self.con.execute(
            f"SELECT position_group FROM players WHERE player_id IN ({','.join(['?']*len(ids))})",
            ids).fetchall()]
        allowed = {r[1] for r in self.con.execute(
            "PRAGMA table_info('v_player_season_stats')").fetchall()}
        if stats:
            cols = [c for c in stats if c in allowed]
        elif len(set(groups)) == 1:                      # all same position -> its defaults
            cols = self.DEFAULT_PROGRESSION_STATS.get(groups[0], [])
        else:                                            # mixed positions -> generic set
            cols = ["ga_per90", "goals", "assists", "chances_created"]
        base = ["games", "minutes", "goals", "assists"]
        statcols = base + [c for c in cols if c in allowed and c not in base]
        select_cols = ", ".join(f"v.{c}" for c in statcols)
        placeholders = ",".join(["?"] * len(ids))
        df = self.con.execute(
            f"""
            SELECT pl.player_name, v.team, pl.position_group, {select_cols}
            FROM v_player_season_stats v JOIN players pl USING(player_id)
            WHERE v.player_id IN ({placeholders}) AND v.season = ?
            ORDER BY v.minutes DESC
            """,
            ids + [season],
        ).df()
        # one row per player, transposed so stats are rows and players are columns
        df = df.drop_duplicates("player_name", keep="first").set_index("player_name")
        return df.T

    def stat_leaders(self, stat: str, league_key: str | None = None,
                     season: str = FOCUS_SEASON, limit: int = 10,
                     min_minutes: int = 600) -> pd.DataFrame:
        """League leaders for any FotMob enrichment stat (e.g. 'tackles',
        'big_chances_created', 'interceptions')."""
        if stat not in self.ENRICH_STATS:
            raise ValueError(f"{stat} is not an enrichment stat; choose from {sorted(self.ENRICH_STATS)}")
        where = "e.season = ? AND e.minutes_played >= ?"
        params: list = [season, min_minutes]
        if league_key:
            where += " AND e.league_key = ?"
            params.append(league_key)
        return self.con.execute(
            f"""
            SELECT pl.player_name, t.team_name AS team, l.league_name AS league,
                   e.{stat} AS {stat}, e.minutes_played AS minutes, e.fotmob_rating
            FROM player_enrichment e
            JOIN players pl USING(player_id)
            JOIN leagues l ON l.league_key = e.league_key
            LEFT JOIN player_season_stats ps
                   ON ps.player_id = e.player_id AND ps.season = e.season
                  AND ps.league_key = e.league_key
            LEFT JOIN teams t ON t.team_id = ps.team_id
            WHERE {where} AND e.{stat} IS NOT NULL
            ORDER BY e.{stat} DESC LIMIT {int(limit)}
            """,
            params,
        ).df()

    # ----- use case 6: team performance / standings ----------------------- #
    def league_standings(self, league_key: str, season: str = FOCUS_SEASON) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT s.league_position AS pos, t.team_name AS team,
                   s.matches_played AS mp, s.wins AS w, s.draws AS d, s.losses AS l,
                   s.goals_for AS gf, s.goals_against AS ga, s.goal_difference AS gd,
                   s.points AS pts, s.xg_for, s.xg_against, s.expected_points AS xpts
            FROM team_season_stats s JOIN teams t USING(team_id)
            WHERE s.league_key = ? AND s.season = ?
            ORDER BY s.league_position
            """,
            [league_key, season],
        ).df()

    def team_form(self, team: str, season: str = FOCUS_SEASON, last: int = 5) -> pd.DataFrame:
        tid = self.find_team_id(team)
        if tid is None:
            return pd.DataFrame()
        return self.con.execute(
            """
            SELECT tms.match_date::DATE AS date,
                   CASE WHEN tms.is_home THEN 'H' ELSE 'A' END AS venue,
                   opp.team_name AS opponent,
                   tms.goals_for AS gf, tms.goals_against AS ga,
                   CASE WHEN tms.points=3 THEN 'W' WHEN tms.points=1 THEN 'D' ELSE 'L' END AS result,
                   round(tms.xg_for,2) AS xg_for, round(tms.xg_against,2) AS xg_against
            FROM team_match_stats tms JOIN teams opp ON opp.team_id = tms.opponent_team_id
            WHERE tms.team_id = ? AND tms.season = ? AND tms.goals_for IS NOT NULL
            ORDER BY tms.match_date DESC LIMIT ?
            """,
            [tid, season, last],
        ).df()

    # ----- use case 7: team information ----------------------------------- #
    def team_info(self, team: str, season: str = FOCUS_SEASON) -> dict:
        tid = self.find_team_id(team)
        if tid is None:
            return {}
        head = self.con.execute(
            """
            SELECT t.team_name, t.team_code, l.league_name, l.country
            FROM teams t JOIN leagues l USING(league_key) WHERE t.team_id=?
            """,
            [tid],
        ).fetchone()
        squad = self.con.execute(
            """
            SELECT pl.player_name, ps.position_group, ps.matches, ps.minutes,
                   ps.goals, ps.assists
            FROM player_season_stats ps JOIN players pl USING(player_id)
            WHERE ps.team_id = ? AND ps.season = ?
            ORDER BY ps.minutes DESC
            """,
            [tid, season],
        ).df()
        return {
            "team_name": head[0],
            "team_code": head[1],
            "league": head[2],
            "country": head[3],
            "manager": None,   # not available from Understat (see NOTES.md)
            "venue": None,     # not available from Understat (see NOTES.md)
            "squad": squad,
        }

    # ----- use case 8: search --------------------------------------------- #
    def search_players(self, query: str, season: str = FOCUS_SEASON, limit: int = 10) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT pl.player_name, t.team_name AS team, l.league_name AS league,
                   ps.position_group, ps.goals, ps.assists, ps.minutes
            FROM player_season_stats ps
            JOIN players pl USING(player_id)
            JOIN teams t USING(team_id)
            JOIN leagues l ON l.league_key = ps.league_key
            WHERE strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))
              AND ps.season = ?
            ORDER BY ps.minutes DESC LIMIT ?
            """,
            [query, season, limit],
        ).df()

    def search_teams(self, query: str) -> pd.DataFrame:
        return self.con.execute(
            """
            SELECT t.team_name, t.team_code, l.league_name AS league, l.country
            FROM teams t JOIN leagues l USING(league_key)
            WHERE strip_accents(lower(t.team_name)) LIKE strip_accents(lower('%'||?||'%'))
            ORDER BY t.team_name
            """,
            [query],
        ).df()

    @staticmethod
    def _season_n_back(n: int, ref: str = FOCUS_SEASON) -> str:
        """Season code n seasons back (inclusive) from ref, e.g. 6 from '2526' -> '2021'."""
        start = int(ref[:2]) - (n - 1)
        return f"{start % 100:02d}{(start + 1) % 100:02d}"

    def _has_ucl_matches(self) -> bool:
        return bool(self.con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name='ucl_matches'").fetchone())

    def search_matches(self, team_a: str, team_b: str, since: str | None = None) -> pd.DataFrame:
        """Head-to-head fixtures between two teams, most recent first.

        Unions domestic league fixtures (`matches`) with Champions League meetings
        (`ucl_matches`). `since` is a lower-bound season code; defaults to 12 seasons.
        """
        ta, tb = self.find_team_id(team_a), self.find_team_id(team_b)
        if ta is None or tb is None:
            return pd.DataFrame()
        if since is None:
            since = self._season_n_back(12)   # all 12 seasons in `matches` (1415–2526)
        ucl_part = ""
        params = [since, ta, tb, tb, ta]
        if self._has_ucl_matches():
            ucl_part = """
            UNION ALL
            SELECT u.match_date::DATE AS date, u.season, h.team_name AS home, a.team_name AS away,
                   u.home_goals, u.away_goals, NULL AS home_xg, NULL AS away_xg, 'UCL' AS comp
            FROM ucl_matches u
            JOIN teams h ON h.team_id = u.home_team_id
            JOIN teams a ON a.team_id = u.away_team_id
            WHERE u.season >= ?
              AND ((u.home_team_id=? AND u.away_team_id=?) OR (u.home_team_id=? AND u.away_team_id=?))
            """
            params += [since, ta, tb, tb, ta]
        return self.con.execute(
            f"""
            SELECT date, season, home, away, home_goals, away_goals, home_xg, away_xg, comp FROM (
              SELECT m.match_date::DATE AS date, m.season, h.team_name AS home, a.team_name AS away,
                     m.home_goals, m.away_goals, round(m.home_xg,2) AS home_xg,
                     round(m.away_xg,2) AS away_xg, 'League' AS comp
              FROM matches m
              JOIN teams h ON h.team_id = m.home_team_id
              JOIN teams a ON a.team_id = m.away_team_id
              WHERE m.season >= ?
                AND ((m.home_team_id=? AND m.away_team_id=?) OR (m.home_team_id=? AND m.away_team_id=?))
                AND m.is_result
              {ucl_part}
            )
            ORDER BY date DESC
            """,
            params,
        ).df()

    # ----- web UI bundles (Atlastra frontend) ----------------------------- #
    # Group the rating-engine metric_labels into the 6 radar axes the UI shows.
    # Labels must match those stored in player_radar_metrics (pipeline.profile
    # PRETTY) -- every player is scored on all of these, so no axis is ever empty.
    # An entry is either a label (equal weight) or a (label, weight) tuple; missing
    # metrics drop out and the remaining weights renormalise (see _radar_values).
    RADAR_AXES = {
        "Chance Creation": ["shot creation", "key passes", "goal creation",
                            "expected assists", "crosses to box"],
        "Progression": ["progressive passes", "progressive carries",
                        "progressive receptions", "progressive pass accuracy"],
        "Passing": ["pass completion %", "forward pass %", "passes into final third"],
        "Finishing": ["non-penalty xG", "non-penalty goals", "finishing",
                      "shots on target", "touches in box"],
        "Defending": ["tackles + interceptions", "tackle win %", "recoveries",
                      "interceptions (adj)", "blocks + clearances"],
        "Dribbling": [("take-ons", 0.8), ("take-on %", 0.2)],
    }

    def _radar_values(self, pcts: dict) -> list[tuple]:
        """(axis, value) per RADAR_AXES, value = weighted mean of the present
        metric percentiles (None if the player has none of an axis's metrics)."""
        out = []
        for axis, keys in self.RADAR_AXES.items():
            num = den = 0.0
            for k in keys:
                label, w = k if isinstance(k, tuple) else (k, 1.0)
                if label in pcts:
                    num += w * pcts[label]
                    den += w
            out.append((axis, round(num / den) if den else None))
        return out

    def web_overview(self) -> dict:
        row = self.con.execute("""
            SELECT (SELECT COUNT(*) FROM leagues),
                   (SELECT COUNT(*) FROM teams),
                   (SELECT COUNT(DISTINCT player_id) FROM player_season_stats),
                   (SELECT COUNT(*) FROM matches)
        """).fetchone()
        return {"leagues": row[0], "teams": row[1], "players": row[2],
                "matches": row[3], "stats_tracked": 250}

    def web_rankings(self, limit: int = 10, season: str = FOCUS_SEASON) -> list[dict]:
        # combined-League rating (same engine the directory + profile use) so the
        # Top-10 rail agrees with the rest of the app; full names via players join.
        df = self.con.execute("""
            SELECT pl.player_name AS player, f.team,
                   COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                   c.rating, pe.fpid
            FROM player_ratings_combined c
            JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = c.player_id
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = c.player_id
            WHERE c.scope = 'league' AND c.season = ?
            ORDER BY c.rating DESC, c.percentile DESC LIMIT ?
        """, [season, limit]).df()
        return [{"rank": i + 1, "player": r.player, "team": r.team,
                 "position": r.position, "rating": int(r.rating),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for i, r in enumerate(df.itertuples())]

    def web_trending(self, limit: int = 5, season: str = FOCUS_SEASON,
                     window: int = 6, min_season_min: int = 720,
                     min_recent_min: int = 270, min_recent_ga: int = 3) -> list[dict]:
        """Players in hot recent form: their last `window` appearances out-produce
        their own season baseline. Momentum, not just quality -- so it differs from
        the Top-10 rail. Score = recent attacking output per-90 plus half the lift
        over the player's season per-90, where output = goals + assists + half the
        underlying xG + xA (actuals reward the streak, expected stops one-game noise
        from dominating). Gated to rated players with a real recent end-product
        (>= `min_recent_ga` goal involvements) so the list stays credible.

        Per-match data is Understat (domestic leagues) only -- see player_match_log."""
        df = self.con.execute(f"""
            WITH log AS (
                SELECT player_id, minutes, goals, assists, xg, xa,
                       row_number() OVER (PARTITION BY player_id
                           ORDER BY match_date DESC, game_id DESC) AS rn
                FROM player_match_log WHERE season = ? AND minutes > 0
            ),
            season AS (
                SELECT player_id, sum(minutes) AS smin,
                       sum(goals + assists + 0.5 * (xg + xa)) AS sc
                FROM log GROUP BY player_id HAVING sum(minutes) >= ?
            ),
            recent AS (
                SELECT player_id, sum(minutes) AS rmin,
                       sum(goals + assists + 0.5 * (xg + xa)) AS rc,
                       sum(goals + assists) AS rga, count(*) AS rgames
                FROM log WHERE rn <= {window} GROUP BY player_id
                HAVING sum(minutes) >= ? AND sum(goals + assists) >= ?
            )
            SELECT pl.player_name AS player,
                   COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                   f.team, c.rating, pe.fpid, r.rga, r.rgames,
                   (r.rc / r.rmin * 90)
                     + 0.5 * ((r.rc / r.rmin * 90) - (s.sc / s.smin * 90)) AS score
            FROM recent r
            JOIN season s USING(player_id)
            JOIN player_ratings_combined c
                 ON c.player_id = r.player_id AND c.season = ? AND c.scope = 'league'
            JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = r.player_id
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = r.player_id
            WHERE (r.rc / r.rmin * 90) >= (s.sc / s.smin * 90) * 1.05
            ORDER BY score DESC LIMIT ?
        """, [season, min_season_min, min_recent_min, min_recent_ga, season, limit]).df()
        return [{"rank": i + 1, "player": r.player, "team": r.team,
                 "position": r.position, "rating": int(r.rating),
                 "recent_ga": int(r.rga), "recent_games": int(r.rgames),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for i, r in enumerate(df.itertuples())]

    # fine position groups (player_ratings_combined.position_group) -> display label,
    # in the order the Rankings page shows them.
    POSITION_RANK_GROUPS = [
        ("ST", "Strikers"), ("W", "Wingers"), ("AM", "Attacking Mids"),
        ("CM", "Central Mids"), ("DM", "Defensive Mids"),
        ("FB", "Full-backs"), ("CB", "Centre-backs"), ("GK", "Goalkeepers"),
    ]

    def web_position_rankings(self, limit: int = 20, season: str = FOCUS_SEASON,
                              scope: str = "league") -> dict:
        """Top-N players per fine position group by the combined rating for a scope
        ('league' or 'ucl'). Returns ordered groups, each with a ranked player list."""
        scope = "ucl" if scope == "ucl" else "league"
        df = self.con.execute("""
            WITH r AS (
                SELECT c.player_id, c.position_group AS grp, c.rating, c.classification,
                       row_number() OVER (PARTITION BY c.position_group
                           ORDER BY c.rating DESC, c.percentile DESC) AS rn
                FROM player_ratings_combined c
                WHERE c.scope = ? AND c.season = ?)
            SELECT r.grp, r.rn, r.rating, r.classification,
                   pl.player_name AS player, f.team,
                   COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position, pe.fpid
            FROM r JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = r.player_id
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = r.player_id
            WHERE r.rn <= ?
            ORDER BY r.grp, r.rn
        """, [scope, season, limit]).df()
        by_grp: dict = {}
        for r in df.itertuples():
            by_grp.setdefault(r.grp, []).append({
                "rank": int(r.rn), "player": r.player, "team": r.team, "position": r.position,
                "rating": int(r.rating), "classification": r.classification,
                "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)})
        return {"groups": [{"key": k, "label": lbl, "players": by_grp[k]}
                           for k, lbl in self.POSITION_RANK_GROUPS if k in by_grp]}

    def web_alltime_seasons(self, scope: str = "combined", limit: int = 20) -> list[dict]:
        """Best individual SEASONS of all time (across every backfilled season) by
        rating. scope: 'league' / 'ucl' (top player-seasons by that scope's rating)
        or 'combined' (minutes-weighted League+UCL blend, requires both, so it's the
        best all-round campaigns). Each row links to that player's profile at that
        season."""
        common = """
            JOIN players pl USING(player_id)
            LEFT JOIN v_player_season_stats v ON v.player_id = s.player_id AND v.season = s.season
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = s.player_id
        """
        if scope in ("league", "ucl"):
            df = self.con.execute(f"""
                WITH s AS (
                    SELECT player_id, season, rating, position_group,
                           row_number() OVER (ORDER BY rating DESC, percentile DESC) AS rn
                    FROM player_ratings_combined WHERE scope = ?)
                SELECT s.rn, s.season, s.rating, s.position_group, pl.player_name AS player,
                       v.team, pe.fpid
                FROM s {common} WHERE s.rn <= ? ORDER BY s.rn
            """, [scope, limit]).df()
        else:   # combined: minutes-weighted blend, both scopes required
            df = self.con.execute(f"""
                WITH pv AS (
                    SELECT player_id, season,
                           max(CASE WHEN scope='league' THEN rating END)  AS lg_r,
                           max(CASE WHEN scope='league' THEN minutes END) AS lg_m,
                           max(CASE WHEN scope='ucl'    THEN rating END)  AS ucl_r,
                           max(CASE WHEN scope='ucl'    THEN minutes END) AS ucl_m,
                           max(CASE WHEN scope='league' THEN position_group END) AS position_group
                    FROM player_ratings_combined GROUP BY player_id, season),
                s AS (
                    SELECT player_id, season, position_group,
                           round((lg_r*lg_m + ucl_r*ucl_m) / NULLIF(lg_m+ucl_m, 0)) AS rating,
                           row_number() OVER (ORDER BY (lg_r*lg_m + ucl_r*ucl_m)
                               / NULLIF(lg_m+ucl_m, 0) DESC) AS rn
                    FROM pv WHERE lg_r IS NOT NULL AND ucl_r IS NOT NULL)
                SELECT s.rn, s.season, s.rating, s.position_group, pl.player_name AS player,
                       v.team, pe.fpid
                FROM s {common} WHERE s.rn <= ? ORDER BY s.rn
            """, [limit]).df()
        return [{"rank": int(r.rn), "player": r.player, "team": r.team,
                 "position": r.position_group, "rating": int(r.rating),
                 "season": _fmt_season(r.season), "season_code": r.season,
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for r in df.itertuples()]

    def web_national_teams(self) -> list[dict]:
        """National teams from the international feed (live_matches: WC + qualifiers +
        major tournaments). Each with its flag code + recent record/form and a click
        target (latest finished match, else next fixture). Sorted by points then GD
        over the available window (small — it's just the current international window)."""
        agg = self.con.execute("""
            WITH m AS (
                SELECT event_id, start_timestamp, status_type, home_team AS team,
                       home_team_id AS team_id, home_country AS cc, home_score AS gf, away_score AS ga,
                       CASE winner_code WHEN 1 THEN 'W' WHEN 2 THEN 'L' WHEN 3 THEN 'D' END AS res
                FROM live_matches WHERE tournament_group = 'International' AND home_country IS NOT NULL
                UNION ALL
                SELECT event_id, start_timestamp, status_type, away_team,
                       away_team_id, away_country, away_score, home_score,
                       CASE winner_code WHEN 2 THEN 'W' WHEN 1 THEN 'L' WHEN 3 THEN 'D' END
                FROM live_matches WHERE tournament_group = 'International' AND away_country IS NOT NULL)
            SELECT team, any_value(cc) AS cc, any_value(team_id) AS team_id,
                   count(*) FILTER (WHERE status_type = 'finished') AS played,
                   count(*) FILTER (WHERE res = 'W') AS w,
                   count(*) FILTER (WHERE res = 'D') AS d,
                   count(*) FILTER (WHERE res = 'L') AS l,
                   COALESCE(SUM(gf) FILTER (WHERE status_type = 'finished'), 0) AS gf,
                   COALESCE(SUM(ga) FILTER (WHERE status_type = 'finished'), 0) AS ga,
                   max(CASE WHEN status_type = 'finished' THEN event_id END) AS last_event,
                   arg_min(CASE WHEN status_type = 'notstarted' THEN event_id END,
                           CASE WHEN status_type = 'notstarted' THEN start_timestamp END) AS next_event
            FROM m GROUP BY team
        """).df()
        # last-5 form (most recent first) per team
        formdf = self.con.execute("""
            WITH m AS (
                SELECT start_timestamp, home_team AS team,
                       CASE winner_code WHEN 1 THEN 'W' WHEN 2 THEN 'L' WHEN 3 THEN 'D' END AS res
                FROM live_matches WHERE tournament_group = 'International' AND home_country IS NOT NULL
                  AND status_type = 'finished'
                UNION ALL
                SELECT start_timestamp, away_team,
                       CASE winner_code WHEN 2 THEN 'W' WHEN 1 THEN 'L' WHEN 3 THEN 'D' END
                FROM live_matches WHERE tournament_group = 'International' AND away_country IS NOT NULL
                  AND status_type = 'finished'),
            o AS (SELECT team, res, row_number() OVER (PARTITION BY team ORDER BY start_timestamp DESC) rn FROM m)
            SELECT team, string_agg(res, '' ORDER BY rn) AS form FROM o WHERE rn <= 5 GROUP BY team
        """).df()
        form = {r.team: list(r.form) for r in formdf.itertuples()}
        rows = [{
            "team": r.team, "country_code": r.cc, "team_id": _i(r.team_id),
            "fifa_rank": self.fifa_rank(r.team),
            "played": _i(r.played), "w": _i(r.w), "d": _i(r.d), "l": _i(r.l),
            "gf": _i(r.gf), "ga": _i(r.ga), "gd": _i(r.gf) - _i(r.ga),
            "points": _i(r.w) * 3 + _i(r.d),
            "form": form.get(r.team, []),
            "event_id": _i(r.last_event) if pd.notna(r.last_event) else (
                _i(r.next_event) if pd.notna(r.next_event) else None),
        } for r in agg.itertuples()]
        rows.sort(key=lambda x: (-x["points"], -x["gd"], x["team"]))
        return rows

    # ---- Champions League hub (use of ucl_matches + v_stats_ucl) ----
    def web_ucl_seasons(self) -> list[dict]:
        """Seasons available for the Champions League hub, newest first."""
        rows = self.con.execute(
            "SELECT DISTINCT season FROM ucl_matches ORDER BY season DESC").fetchall()
        return [{"value": r[0], "label": _fmt_season(r[0])} for r in rows]

    @staticmethod
    def _ucl_round_meta(rnd: str):
        """Raw round name -> (phase, display label). phase is one of
        'knockout' / 'league' / 'qualifying' so the UI can split the bracket from
        the league/group phase and the qualifiers."""
        ko = {"Round of 16": "Round of 16", "Quarterfinals": "Quarter-finals",
              "Semifinals": "Semi-finals", "Final": "Final"}
        if rnd in ko:
            return "knockout", ko[rnd]
        low = (rnd or "").lower()
        if "qualif" in low or "playoff" in low:
            return "qualifying", rnd
        if low.startswith("round "):           # group stage / new league phase
            return "league", "Matchday " + rnd.split()[-1]
        return "league", rnd

    @staticmethod
    def _ucl_round_ties(matches: list[dict]) -> list[dict]:
        """Fold a knockout round's matches into ties (pairing the two legs of a
        two-legged tie), summing each team's goals across legs. Ties keep the order
        their first leg was played."""
        ties, idx = [], {}
        for m in matches:
            key = frozenset((m["home"], m["away"]))
            if key not in idx:
                idx[key] = len(ties)
                ties.append({"a": m["home"], "b": m["away"], "legs": [], "agg": {}, "live": False})
            t = ties[idx[key]]
            t["legs"].append(m)
            if m.get("live"):                        # any in-play leg marks the tie live
                t["live"] = True
            if m["home_goals"] is not None:
                t["agg"][m["home"]] = t["agg"].get(m["home"], 0) + m["home_goals"]
                t["agg"][m["away"]] = t["agg"].get(m["away"], 0) + m["away_goals"]
        return ties

    @staticmethod
    def _order_ko_ties(ko_ties: dict, present: list) -> dict:
        """Order each round's ties so a left-to-right bracket aligns: the deepest
        round present seeds the order, then each earlier round is sorted by the
        display position of the later-round tie it feeds — which keeps the two
        feeders of any tie adjacent (and centred between them in the UI)."""
        team_idx = {r: {} for r in present}        # team -> its tie index in round r
        for r in present:
            for i, t in enumerate(ko_ties[r]):
                team_idx[r][t["a"]] = i
                team_idx[r][t["b"]] = i
        order = {present[-1]: list(range(len(ko_ties[present[-1]])))}
        for r, later in zip(reversed(present[:-1]), reversed(present)):
            pos = {ti: p for p, ti in enumerate(order[later])}
            def feeder_key(i, _r=r, _later=later, _pos=pos):
                t = ko_ties[_r][i]
                for tm in (t["a"], t["b"]):
                    j = team_idx[_later].get(tm)
                    if j is not None:
                        return (_pos.get(j, 999), i)
                return (999, i)
            order[r] = sorted(range(len(ko_ties[r])), key=feeder_key)
        return {r: [ko_ties[r][i] for i in order[r]] for r in present}

    @staticmethod
    def _build_bracket(ko_ties: dict, seq: list, labels: dict, decorate,
                       order_index: dict | None = None) -> list[dict]:
        """Generic knockout-bracket assembler shared by the UCL & World Cup hubs.

        ko_ties: round name -> list of ties (from _ucl_round_ties). seq: full round
        order. labels: round -> display label. decorate(team_or_None) -> dict of
        per-team display fields (e.g. {'logo': url} or {'cc': 'BR'}), merged in as
        a_<key>/b_<key>/winner_<key>.

        Each tie's winner is whoever ADVANCED (appears in the next round) — robust to
        away-goals / extra-time / penalties; the last round falls back to scoreline.
        Rounds past the deepest played are shown as a skeleton through the Final,
        seeded with the teams that have already advanced (else TBD)."""
        present = [r for r in seq if r in ko_ties]
        if not present:
            return []
        if order_index:                              # explicit visual order (e.g. WC cup tree)
            def _seq(t):
                ev = t["legs"][-1].get("event_id") if t.get("legs") else None
                return (order_index.get(ev, 10**9),)
            ordered = {r: sorted(ko_ties[r], key=_seq) for r in present}
        else:
            ordered = SoccerDB._order_ko_ties(ko_ties, present)

        def side(prefix, team, agg):
            d = {prefix: team, prefix + "_agg": agg}
            for k, v in (decorate(team) or {}).items():
                d[prefix + "_" + k] = v
            return d

        def tie_dict(a, b, ga, gb, winner, legs, live=False):
            wt = a if winner == "a" else b if winner == "b" else None
            d = {"winner": winner, "winner_team": wt, "live": live, "two_legs": len(legs) > 1,
                 "legs": [{"home": l["home"], "away": l["away"],
                           "home_goals": l["home_goals"], "away_goals": l["away_goals"]}
                          for l in legs],
                 "event_id": legs[-1]["event_id"] if legs else None}
            d.update(side("a", a, ga))
            d.update(side("b", b, gb))
            for k, v in (decorate(wt) or {}).items():
                d["winner_" + k] = v
            return d

        result = {}                                  # round -> display ties
        for i, rnd in enumerate(present):
            nxt = present[i + 1] if i + 1 < len(present) else None
            next_teams = (set().union(*({t["a"], t["b"]} for t in ko_ties[nxt]))
                          if nxt else set())
            out = []
            for t in ordered[rnd]:
                a, b, agg = t["a"], t["b"], t["agg"]
                ga, gb = agg.get(a), agg.get(b)
                live = t.get("live", False)
                if nxt:                              # winner = whoever advanced
                    winner = "a" if a in next_teams else "b" if b in next_teams else None
                elif not live and ga is not None and gb is not None and ga != gb:
                    winner = "a" if ga > gb else "b"  # deepest round: scoreline, but not mid-match
                else:
                    winner = None
                out.append(tie_dict(a, b, ga, gb, winner, t["legs"], live))
            result[rnd] = out

        # extend the skeleton from the deepest played round through the Final
        for rnd in seq[seq.index(present[-1]) + 1:]:
            prev = result[seq[seq.index(rnd) - 1]]
            out = []
            for j in range(0, len(prev), 2):
                w1 = prev[j]["winner_team"]
                w2 = prev[j + 1]["winner_team"] if j + 1 < len(prev) else None
                out.append(tie_dict(w1, w2, None, None, None, []))
            result[rnd] = out

        full = seq[seq.index(present[0]):]           # earliest present → Final
        return [{"round": r, "label": labels[r], "ties": result[r]} for r in full]

    def _ucl_bracket(self, df, logos: dict, phase_start) -> list[dict]:
        """Champions League knockout bracket (Round of 16 → Final): single/two-legged
        ties decorated with club crests. See _build_bracket for the shared logic."""
        seq = ["Round of 16", "Quarterfinals", "Semifinals", "Final"]
        ko_ties = {}
        for rnd in seq:
            sub = df[(df["round"] == rnd) & (df["date"] >= phase_start)]
            if sub.empty:
                continue
            ms = [{"home": r.home, "away": r.away, "event_id": _i(r.event_id),
                   "home_goals": _i(r.home_goals), "away_goals": _i(r.away_goals)}
                  for r in sub.sort_values("date").itertuples()]
            ko_ties[rnd] = self._ucl_round_ties(ms)
        labels = {"Round of 16": "Round of 16", "Quarterfinals": "Quarter-finals",
                  "Semifinals": "Semi-finals", "Final": "Final"}
        return self._build_bracket(ko_ties, seq, labels,
                                   lambda t: {"logo": logos.get(t)} if t else {})

    def web_ucl_competition(self, season: str = FOCUS_SEASON) -> dict:
        """Champions League bundle for one season: every match grouped by round in
        chronological order, each round tagged knockout / league / qualifying, plus
        the champion (winner of the Final, once decided). Crests resolve for the
        top-5 clubs we track; everyone else falls back to initials in the UI."""
        df = self.con.execute("""
            SELECT event_id, match_date::DATE AS date, round, home_name AS home, away_name AS away,
                   home_goals, away_goals
            FROM ucl_matches WHERE season = ?
            ORDER BY match_date, round
        """, [season]).df()
        if df.empty:
            return {"available": False, "season": _fmt_season(season),
                    "season_code": season, "rounds": [], "champion": None}
        names = set(df["home"]) | set(df["away"])
        logos = {n: self.team_logo(n) for n in names}
        meta = {rnd: self._ucl_round_meta(rnd) for rnd in df["round"].unique()}
        # Older seasons' source data mislabels a few pre-season minnow qualifiers with
        # main-round names (e.g. a June match tagged "Semifinals"). Real knockout ties
        # only happen after the group/league phase kicks off, so use that as a floor.
        league_dates = [d for r, d in zip(df["round"], df["date"]) if meta[r][0] == "league"]
        phase_start = min(league_dates) if league_dates else df["date"].min()
        # canonical display order: qualifying -> league phase (by matchday) -> knockout
        ko_order = {"Round of 16": 0, "Quarterfinals": 1, "Semifinals": 2, "Final": 3}
        def sort_key(rnd):
            phase = meta[rnd][0]
            if phase == "knockout":
                return (2, ko_order.get(rnd, 9), 0)
            if phase == "league":
                digits = "".join(c for c in rnd if c.isdigit())
                return (1, int(digits) if digits else 0, 0)
            return (0, 0, df[df["round"] == rnd]["date"].min().toordinal())
        rounds = []
        for rnd in sorted(df["round"].unique(), key=sort_key):
            phase, label = meta[rnd]
            sub = df[df["round"] == rnd]
            if phase == "knockout":
                sub = sub[sub["date"] >= phase_start]   # drop mislabeled qualifier noise
            if sub.empty:
                continue
            rounds.append({"round": rnd, "label": label, "phase": phase,
                           "matches": [{"date": str(r.date), "home": r.home, "away": r.away,
                                        "event_id": _i(r.event_id),
                                        "home_logo": logos.get(r.home), "away_logo": logos.get(r.away),
                                        "home_goals": _i(r.home_goals), "away_goals": _i(r.away_goals),
                                        "is_result": pd.notna(r.home_goals)} for r in sub.itertuples()]})
        # champion: the Final's higher-scoring side, only once a decisive result exists
        champion = None
        fin = df[df["round"] == "Final"]
        if not fin.empty:
            r = fin.iloc[-1]
            if pd.notna(r.home_goals) and pd.notna(r.away_goals) and r.home_goals != r.away_goals:
                w = r.home if r.home_goals > r.away_goals else r.away
                champion = {"team": w, "team_logo": logos.get(w)}
        return {"available": True, "season": _fmt_season(season), "season_code": season,
                "rounds": rounds, "champion": champion,
                "bracket": self._ucl_bracket(df, logos, phase_start)}

    # stat -> (label, format). UCL leaders mirror the league-leaders board over v_stats_ucl.
    _UCL_LEADER_STATS = [
        ("goals", "Goals", "int"), ("assists", "Assists", "int"),
        ("ga", "Goal Involvements", "int"), ("xg", "Expected Goals (xG)", "dec"),
        ("xa", "Expected Assists (xA)", "dec"), ("chances_created", "Chances Created", "int"),
        ("big_chances_created", "Big Chances Created", "int"), ("shots", "Shots", "int"),
        ("dribbles_completed", "Dribbles", "int"), ("tackles", "Tackles", "int"),
        ("interceptions", "Interceptions", "int"), ("duels_won", "Duels Won", "int"),
        ("clearances", "Clearances", "int"), ("passes_completed", "Passes Completed", "int"),
        ("rating", "Avg Match Rating", "dec"),
    ]

    def web_ucl_leaders(self, season: str = FOCUS_SEASON, top: int = 3,
                        min_minutes: int = 270) -> dict:
        """Top players in every stat for a Champions League season (top scorers,
        assisters, creators, …) from v_stats_ucl — top N per stat, modest minutes
        floor since UCL samples are small. Rows link to the player profile."""
        df = self.con.execute("""
            SELECT pl.player_name AS player, u.team, u.minutes, u.goals, u.assists,
                   (u.goals + u.assists) AS ga, u.xg, u.xa, u.chances_created,
                   u.big_chances_created, u.shots, u.dribbles_completed, u.tackles,
                   u.interceptions, u.duels_won, u.clearances, u.passes_completed,
                   u.rating, pe.fpid
            FROM v_stats_ucl u
            JOIN players pl USING(player_id)
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = u.player_id
            WHERE u.season = ? AND u.minutes >= ?
        """, [season, min_minutes]).df()
        if df.empty:
            return {"available": False, "leaders": []}

        def fmt(v, kind):
            return (str(int(round(v))) if kind == "int"
                    else f"{v:.1f}" if kind == "dec" else f"{round(v)}%")
        leaders = []
        for key, label, kind in self._UCL_LEADER_STATS:
            if key not in df.columns:
                continue
            sub = df.dropna(subset=[key])
            if key != "rating":                 # everyone has a rating; only show positive counts
                sub = sub[sub[key] > 0]
            sub = sub.sort_values(key, ascending=False).head(top)
            if sub.empty:
                continue
            leaders.append({"key": key, "label": label,
                            "top": [{"player": r.player, "team": r.team,
                                     "photo": self.player_photo(int(r.fpid) if pd.notna(r.fpid) else None),
                                     "value": fmt(getattr(r, key), kind)} for r in sub.itertuples()]})
        return {"available": True, "leaders": leaders}

    # ---- World Cup hub (wc_matches + wc_standings, snapshotted by load_wc) ----
    def web_wc_seasons(self) -> list[dict]:
        """World Cups available for the hub, newest first (e.g. 2026, 2022, …)."""
        rows = self.con.execute(
            "SELECT DISTINCT season FROM wc_matches ORDER BY season DESC").fetchall()
        return [{"value": r[0], "label": r[0]} for r in rows]

    # raw round -> (phase, display label). World Cup knockout order, group matchdays
    # as 'Round N'. The third-place play-off is knockout-phase but NOT in the bracket.
    _WC_KO = {"Round of 32": (0, "Round of 32"), "Round of 16": (1, "Round of 16"),
              "Quarterfinals": (2, "Quarter-finals"), "Semifinals": (3, "Semi-finals"),
              "Match for 3rd place": (4, "Third-place play-off"), "Final": (5, "Final")}

    @classmethod
    def _wc_round_meta(cls, rnd: str):
        if rnd in cls._WC_KO:
            return "knockout", cls._WC_KO[rnd][1]
        low = (rnd or "").lower()
        if low.startswith("round "):
            return "group", "Matchday " + rnd.split()[-1]
        return "group", rnd

    def web_worldcup(self, season: str) -> dict:
        """World Cup bundle for one edition: matches grouped by round (group
        matchdays → knockout, chronological), the knockout bracket (R32/R16 → Final),
        the group standings tables, and the champion once the Final is decided.
        National teams render via their ISO country code (flag)."""
        df = self.con.execute("""
            SELECT event_id, match_date::DATE AS date, round,
                   home_name AS home, home_cc, away_name AS away, away_cc,
                   home_goals, away_goals, home_pens, away_pens, winner_code
            FROM wc_matches WHERE season = ?
            ORDER BY match_date, round
        """, [season]).df()
        if df.empty:
            return {"available": False, "season": season, "rounds": [],
                    "bracket": [], "groups": [], "champion": None}
        # Real-time overlay: the live feed (pipeline/load_live) tracks the running WC
        # far faster than the static wc_matches snapshot, so for any match in both,
        # prefer the live score / winner / status and resolve knockout slots as teams
        # advance — keeping the bracket and results current while matches play out.
        df["live"] = False
        try:
            lv = self.con.execute("""
                SELECT event_id, home_team, home_country, away_team, away_country,
                       home_score, away_score, winner_code, status_type
                FROM live_matches
                WHERE tournament_key = 'WC' OR tournament_name ILIKE '%world cup%'
            """).df()
        except Exception:                            # noqa: BLE001 -- table may not exist
            lv = pd.DataFrame()
        lmap = {int(r.event_id): r for r in lv.itertuples()} if not lv.empty else {}
        for i in df.index:
            eid = df.at[i, "event_id"]
            lm = None if pd.isna(eid) else lmap.get(int(eid))
            if lm is None:
                continue
            if lm.status_type in ("inprogress", "finished") and lm.home_score is not None:
                df.at[i, "home_goals"], df.at[i, "away_goals"] = lm.home_score, lm.away_score
            if lm.winner_code is not None:
                df.at[i, "winner_code"] = lm.winner_code
            if lm.status_type == "inprogress":
                df.at[i, "live"] = True
            if lm.home_country and not df.at[i, "home_cc"]:   # fill a placeholder slot
                df.at[i, "home"], df.at[i, "home_cc"] = lm.home_team, lm.home_country
            if lm.away_country and not df.at[i, "away_cc"]:
                df.at[i, "away"], df.at[i, "away_cc"] = lm.away_team, lm.away_country
        cc = {}
        for r in df.itertuples():
            cc.setdefault(r.home, r.home_cc)
            cc.setdefault(r.away, r.away_cc)

        # rounds for the Results tab: group matchdays first (by number), then knockout
        def sort_key(rnd):
            phase = self._wc_round_meta(rnd)[0]
            if phase == "knockout":
                return (1, self._WC_KO.get(rnd, (9,))[0])
            digits = "".join(ch for ch in rnd if ch.isdigit())
            return (0, int(digits) if digits else 0)
        rounds = []
        for rnd in sorted(df["round"].dropna().unique(), key=sort_key):
            phase, label = self._wc_round_meta(rnd)
            sub = df[df["round"] == rnd]
            rounds.append({"round": rnd, "label": label, "phase": phase,
                           "matches": [{"date": str(r.date), "home": r.home, "away": r.away,
                                        "home_cc": r.home_cc, "away_cc": r.away_cc,
                                        "home_rank": self.fifa_rank(r.home), "away_rank": self.fifa_rank(r.away),
                                        "event_id": _i(r.event_id),
                                        "home_goals": _i(r.home_goals), "away_goals": _i(r.away_goals),
                                        "home_pens": _i(r.home_pens), "away_pens": _i(r.away_pens),
                                        "is_result": pd.notna(r.home_goals)} for r in sub.itertuples()]})

        # bracket (single-leg ties): reuse the shared assembler, decorating with flags
        seq = ["Round of 32", "Round of 16", "Quarterfinals", "Semifinals", "Final"]
        ko_ties = {}
        for rnd in seq:
            sub = df[df["round"] == rnd]
            if sub.empty:
                continue
            ms = [{"home": r.home, "away": r.away, "event_id": _i(r.event_id),
                   "home_goals": _i(r.home_goals), "away_goals": _i(r.away_goals),
                   "live": bool(r.live)}
                  for r in sub.sort_values("date").itertuples()]
            ko_ties[rnd] = self._ucl_round_ties(ms)
        labels = {r: self._WC_KO[r][1] for r in seq}
        # true visual order from the SofaScore cup tree (event_id -> position); lets
        # the bracket align while knockout slots are still 'W##' placeholders.
        try:
            obr = self.con.execute(
                "SELECT event_id, round_order * 1000 + seq AS pos FROM wc_bracket WHERE season = ?",
                [season]).fetchall()
            order_index = {int(e): int(p) for e, p in obr} or None
        except Exception:  # noqa: BLE001 -- table may not exist yet
            order_index = None
        bracket = self._build_bracket(
            ko_ties, seq, labels,
            lambda t: {"cc": cc.get(t), "rank": self.fifa_rank(t)} if t else {},
            order_index=order_index)

        # group standings tables (Group A, B, … then any extra ranking table last)
        sdf = self.con.execute("""
            SELECT group_name, position, team, cc, played, w, d, l, gf, ga, pts
            FROM wc_standings WHERE season = ? ORDER BY group_name, position
        """, [season]).df()
        groups = []
        for gname in sorted(sdf["group_name"].unique(),
                            key=lambda g: (not g.startswith("Group"), g)):
            sub = sdf[sdf["group_name"] == gname]
            groups.append({"name": gname, "rows": [
                {"position": _i(r.position), "team": r.team, "cc": r.cc,
                 "rank": self.fifa_rank(r.team),
                 "played": _i(r.played), "w": _i(r.w), "d": _i(r.d), "l": _i(r.l),
                 "gf": _i(r.gf), "ga": _i(r.ga), "gd": (_i(r.gf) or 0) - (_i(r.ga) or 0),
                 "pts": _i(r.pts)}
                for r in sub.itertuples()]})

        # champion via winner_code (handles a Final settled on penalties, where the
        # 90-min scoreline is level); also patch the bracket's Final highlight.
        champion = None
        fin = df[df["round"] == "Final"]
        if not fin.empty:
            r = fin.iloc[-1]
            w = r.home if r.winner_code == 1 else r.away if r.winner_code == 2 else None
            if w:
                champion = {"team": w, "cc": cc.get(w)}
                last = bracket[-1] if bracket else None
                if last and last["round"] == "Final" and last["ties"]:
                    t = last["ties"][0]
                    if t["winner"] is None and w in (t["a"], t["b"]):
                        t["winner"] = "a" if w == t["a"] else "b"
                        t["winner_team"] = w
        return {"available": True, "season": season, "rounds": rounds,
                "bracket": bracket, "groups": groups, "champion": champion}

    # stat_key -> (label, format). Order = display order on the World Cup leaders tab.
    _WC_LEADER_STATS = [
        ("rating", "Avg Match Rating", "dec"), ("goals", "Goals", "int"),
        ("assists", "Assists", "int"), ("goalsAssistsSum", "Goal Involvements", "int"),
        ("expectedGoals", "Expected Goals (xG)", "dec"),
        ("expectedAssists", "Expected Assists (xA)", "dec"),
        ("bigChancesCreated", "Big Chances Created", "int"), ("totalShots", "Shots", "int"),
        ("shotsOnTarget", "Shots on Target", "int"), ("keyPasses", "Key Passes", "int"),
        ("successfulDribbles", "Dribbles", "int"), ("tackles", "Tackles", "int"),
        ("interceptions", "Interceptions", "int"), ("clearances", "Clearances", "int"),
        ("saves", "Saves", "int"),
    ]

    def web_wc_leaders(self, season: str, top: int = 3) -> dict:
        """World Cup tournament stat leaders (top scorers, assisters, creators, …) for
        one edition, from the SofaScore top-players snapshot in wc_leaders. Each player
        carries their national flag (country code resolved from wc_matches)."""
        df = self.con.execute("""
            SELECT l.stat_key, l.rank, l.player, l.team, l.value, l.appearances,
                   COALESCE(m.home_cc, m2.away_cc) AS cc
            FROM wc_leaders l
            LEFT JOIN (SELECT DISTINCT season, home_name, home_cc FROM wc_matches) m
                   ON m.season = l.season AND m.home_name = l.team
            LEFT JOIN (SELECT DISTINCT season, away_name, away_cc FROM wc_matches) m2
                   ON m2.season = l.season AND m2.away_name = l.team
            WHERE l.season = ? AND l.rank <= ?
            ORDER BY l.stat_key, l.rank
        """, [season, top]).df()
        if df.empty:
            return {"available": False, "leaders": []}

        def fmt(v, kind):
            return str(int(round(v))) if kind == "int" else f"{v:.1f}"
        leaders = []
        for key, label, kind in self._WC_LEADER_STATS:
            sub = df[df["stat_key"] == key]
            if sub.empty:
                continue
            leaders.append({"key": key, "label": label,
                            "top": [{"player": r.player, "team": r.team, "cc": r.cc,
                                     "value": fmt(r.value, kind)} for r in sub.itertuples()]})
        return {"available": True, "leaders": leaders}

    _FIFA_FPID = None

    def _fotmob_name_index(self):
        """{folded full name / 'initial|surname' / surname -> fotmob_player_id} for photos.
        Built once from player_enrichment (static), so FIFA squad names can resolve a photo."""
        if SoccerDB._FIFA_FPID is not None:
            return SoccerDB._FIFA_FPID
        idx = {}
        try:
            rows = self.con.execute(
                "SELECT pl.player_name, max(pe.fotmob_player_id) FROM players pl "
                "JOIN player_enrichment pe ON pe.player_id = pl.player_id "
                "WHERE pe.fotmob_player_id IS NOT NULL GROUP BY pl.player_name").fetchall()
            for name, fpid in rows:
                if fpid is None:
                    continue
                f = _fold(name)
                t = f.split()
                idx.setdefault(f, fpid)
                if len(t) >= 2:
                    idx.setdefault(t[0][0] + "|" + t[-1], fpid)
                    idx.setdefault(t[-1], fpid)          # surname
                    idx.setdefault(t[-2], fpid)          # alt surname (e.g. Mbappe-Lottin)
        except Exception:                                # noqa: BLE001
            pass
        SoccerDB._FIFA_FPID = idx
        return idx

    _ATLAS_IDX = None

    def atlas_rating_index(self):
        """{folded name -> best current-season Atlas rating (max of league & UCL scope)}.
        Used by the Tactics Lab to detect breakout seasons: if a player's Atlas
        league/UCL rating clears their FIFA overall, they over-performed and get a boost."""
        if SoccerDB._ATLAS_IDX is not None:
            return SoccerDB._ATLAS_IDX
        idx = {}
        try:
            rows = self.con.execute(
                "SELECT pl.player_name, max(prc.rating) FROM player_ratings_combined prc "
                "JOIN players pl ON pl.player_id = prc.player_id "
                "WHERE prc.season = ? AND prc.minutes >= 450 "
                "GROUP BY pl.player_name", [FOCUS_SEASON]).fetchall()
            for name, rating in rows:
                if rating is None:
                    continue
                f = _fold(name)
                t = f.split()
                idx[f] = max(idx.get(f, 0), int(rating))
                if len(t) >= 2:
                    key = t[0][0] + "|" + t[-1]
                    idx[key] = max(idx.get(key, 0), int(rating))
        except Exception:                                # noqa: BLE001
            pass
        SoccerDB._ATLAS_IDX = idx
        return idx

    # deepest UCL round reached maps to a "campaign quality" signal (a finalist beat elite
    # sides; a group/playoff exit did not). Keyed by the round labels stored in ucl_matches.
    _UCL_DEPTH = {"Final": 1.0, "Semifinals": 0.6, "Quarterfinals": 0.2, "Round of 16": -0.2,
                  "Playoff round": -0.5, "League phase": -0.7, "Group stage": -0.7,
                  "Qualifying": -0.9, "Preliminary": -0.9}
    # our Tactics Lab team names differ from the names stored in ucl_matches — map the ones
    # that aren't a plain substring (e.g. "PSG" is stored as "Paris Saint-Germain").
    _UCL_ALIAS = {"PSG": "Paris Saint-Germain", "Internazionale": "Inter",
                  "Bayern München": "Bayern", "Atlético Madrid": "Atlético",
                  "Borussia Dortmund": "Dortmund", "Milan": "Milan"}

    def team_form_rating(self, team: str, season: str = FOCUS_SEASON, last: int = 6) -> dict:
        """Recent-form rating for a CLUB from its latest league AND UCL results — a signed
        score (~-1 weak … +1 strong, 0 = average) the Tactics Lab uses to nudge a matchup.
        Combines points-per-game + xG differential (league) with UCL results + how deep the
        club went (a finalist outranks a group-stage exit). Returns {} for teams we can't map."""
        def _clip(v, lo=-1.0, hi=1.0):
            return max(lo, min(hi, v))

        def _norm_ppg(ppg):
            return _clip((ppg - 1.5) / 1.5)                    # 1.5 ppg ≈ average form

        out = {"form": 0.0}
        # ---- league: team_match_stats (has xG) ----
        try:
            lf = self.team_form(team, season, last)
        except Exception:                                       # noqa: BLE001
            lf = None
        league_score = None
        if lf is not None and len(lf):
            pts = lf["result"].map({"W": 3, "D": 1, "L": 0})
            ppg = float(pts.mean())
            xgd = float((lf["xg_for"] - lf["xg_against"]).mean())
            league_score = 0.6 * _norm_ppg(ppg) + 0.4 * _clip(xgd / 1.5)
            out["league"] = {"score": round(league_score, 3), "ppg": round(ppg, 2),
                             "xgdiff": round(xgd, 2), "n": int(len(lf)),
                             "record": "".join(lf["result"].tolist()[::-1])}   # oldest→newest
        # ---- UCL: ucl_matches (results + how far they got) ----
        ucl_score = None
        tok = self._UCL_ALIAS.get(team, team)
        try:
            latest = self.con.execute("SELECT max(season) FROM ucl_matches").fetchone()[0]
            rows = self.con.execute(
                """SELECT home_name, away_name, home_goals, away_goals, round
                   FROM ucl_matches
                   WHERE season = ? AND home_goals IS NOT NULL
                     AND (home_name ILIKE '%'||?||'%' OR away_name ILIKE '%'||?||'%')
                   ORDER BY match_date DESC LIMIT ?""",
                [latest, tok, tok, last]).fetchall()
        except Exception:                                       # noqa: BLE001
            rows = []
        if rows:
            ftok = _fold(tok)
            def _is_home(name):
                return ftok in _fold(name)
            pts, results, depth, best_round = [], [], -0.7, None
            for hn, an, hg, ag, rnd in rows:
                is_home = _is_home(hn)
                gf, ga = (hg, ag) if is_home else (ag, hg)
                pts.append(3 if gf > ga else 1 if gf == ga else 0)
                results.append("W" if gf > ga else "D" if gf == ga else "L")
                dv = self._UCL_DEPTH.get(rnd, -0.5)
                if dv >= depth:
                    depth, best_round = dv, rnd
            ppg = sum(pts) / len(pts)
            ucl_score = 0.4 * _norm_ppg(ppg) + 0.6 * depth
            out["ucl"] = {"score": round(ucl_score, 3), "ppg": round(ppg, 2),
                          "depth": round(depth, 2), "n": len(rows),
                          "record": "".join(results[::-1]), "best_round": best_round}
        # ---- combine (both matter equally, per the design) ----
        parts = [s for s in (league_score, ucl_score) if s is not None]
        if parts:
            if league_score is not None and ucl_score is not None:
                out["form"] = round(0.5 * league_score + 0.5 * ucl_score, 3)
            else:
                out["form"] = round(parts[0], 3)
        return out

    def tactics_squad(self, team: str, season: str = FOCUS_SEASON) -> list[dict]:
        """A team's squad for the Tactics Lab. Clubs are built from the FIFA / EA FC roster
        (current transfers, ability-based ratings + attributes); national teams fall back to
        World Cup data. Photos are resolved from our FotMob enrichment by name where possible."""
        from webapp import fifa
        roster = fifa.club_squad(team)
        if roster:
            idx = self._fotmob_name_index()

            def photo_for(c):
                for nm in (c["n"], c.get("ln")):         # try short then long name
                    if not nm:
                        continue
                    t = _fold(nm).split()
                    if not t:
                        continue
                    for k in (_fold(nm), (t[0][0] + "|" + t[-1]) if len(t) >= 2 else None, t[-1]):
                        fp = idx.get(k) if k else None
                        if fp:
                            return self.player_photo(fp)
                return None
            out = []
            for c in roster:
                out.append({
                    "player": c["n"], "position": c["pos"], "rating": c["o"],
                    "minutes": None, "photo": photo_for(c),
                    "fifa": {k: c[k] for k in ("o", "pac", "sho", "pas", "dri", "def", "phy", "hea")},
                    "foot": c.get("foot"), "per90": {}, "pct": {},
                })
            return out
        return self._tactics_squad_national(team)

    def _tac_photo_for_name(self, name: str):
        """FotMob photo for a bare player name via the enrichment name index (folded full /
        initial+surname / surname). Lets a what-if player carry a photo into any squad."""
        idx = self._fotmob_name_index()
        t = _fold(name).split()
        for k in (_fold(name), (t[0][0] + "|" + t[-1]) if len(t) >= 2 else None,
                  t[-1] if t else None):
            fp = idx.get(k) if k else None
            if fp:
                return self.player_photo(fp)
        return None

    # ---- past seasons in the Lab -------------------------------------------------- #
    # The warehouse holds twelve seasons (1415 onward), and the Atlas rating scale is stable
    # across all of them (the median season rates ~53 and the best player ~92 in every one),
    # so a 2014/15 player-season is directly comparable to a 2025/26 one. What ISN'T
    # comparable is the EA FC scale the Lab runs on — Atlas sits ~21 points under it. This
    # maps one to the other empirically: for the CURRENT season, where players have both
    # numbers, take the median EA FC overall at each Atlas level and interpolate. So a
    # historical season is priced at whatever a player rating that well is worth today.
    _SEASON_RATINGS: dict = {}                   # (season, group) -> sorted Atlas ratings
    _FIFA_SCALE = None                           # group -> sorted EA FC overalls today ('' = all)

    def atlas_to_fifa(self, atlas: float, season: str, group: str | None = None) -> int:
        """A season rating expressed on today's EA FC overall scale, matched on RANK: where
        that number placed among the season's regulars, read off at the same place in the
        current EA FC distribution. Matching on value instead would flatten the icons —
        Atlas and EA FC correlate loosely, so the median card at Atlas 90 is only an 83, and
        prime Ronaldo would come out worse than a current squad player. Rank keeps the tail:
        the best player of 2014/15 is priced like the best player of today."""
        # Both halves are read WITHIN the player's position where the sample allows it. A
        # holding midfielder and a striker are not scored on the same curve at either end:
        # the season rating rewards output, and the EA FC scale is shaped differently per
        # position too (its centre-backs top out lower than its attacking midfielders). The
        # Atlas percentiles turn out nearly identical by position, so most of the correction
        # comes from the card side — worth a couple of points to defenders and holders, not
        # a transformation. A thin group (fewer than 40 rated that season) falls back to the
        # whole league rather than rank someone against a handful.
        key = (season, group or "")
        if key not in SoccerDB._SEASON_RATINGS:
            sql = ("SELECT max(rating) FROM player_ratings_combined WHERE season = ? "
                   "AND minutes >= 900" + (" AND position_group = ?" if group else "")
                   + " GROUP BY player_id")
            rows = self.con.execute(sql, [season, group] if group else [season]).fetchall()
            vals = sorted(float(r[0]) for r in rows if r[0])
            SoccerDB._SEASON_RATINGS[key] = vals if len(vals) >= 40 else None
        if SoccerDB._FIFA_SCALE is None:
            from webapp import fifa
            SoccerDB._FIFA_SCALE = {}
            rows = self.con.execute(
                "SELECT p.player_name, any_value(r.position_group) FROM player_ratings_combined r "
                "JOIN players p ON p.player_id = r.player_id "
                "WHERE r.season = ? AND r.minutes >= 600 GROUP BY 1", [FOCUS_SEASON]).fetchall()
            everyone = []
            for nm, grp in rows:
                c = fifa.match(nm)
                if c and c.get("o"):
                    everyone.append(float(c["o"]))
                    if grp:
                        SoccerDB._FIFA_SCALE.setdefault(grp, []).append(float(c["o"]))
            for g in list(SoccerDB._FIFA_SCALE):
                SoccerDB._FIFA_SCALE[g] = sorted(SoccerDB._FIFA_SCALE[g])
                if len(SoccerDB._FIFA_SCALE[g]) < 40:
                    del SoccerDB._FIFA_SCALE[g]
            SoccerDB._FIFA_SCALE[""] = sorted(everyone)
        peers = SoccerDB._SEASON_RATINGS.get(key) or SoccerDB._SEASON_RATINGS.get((season, ""))
        if peers is None:                                    # league-wide list not built yet
            rows = self.con.execute(
                "SELECT max(rating) FROM player_ratings_combined WHERE season = ? "
                "AND minutes >= 900 GROUP BY player_id", [season]).fetchall()
            peers = sorted(float(r[0]) for r in rows if r[0])
            SoccerDB._SEASON_RATINGS[(season, "")] = peers
        scale = SoccerDB._FIFA_SCALE.get(group or "") or SoccerDB._FIFA_SCALE.get("") or []
        if not peers or not scale:
            return int(round(float(atlas or 70)))
        x = float(atlas or 0)
        below = sum(1 for r in peers if r < x) + 0.5 * sum(1 for r in peers if r == x)
        pct = below / len(peers)
        idx = min(len(scale) - 1, max(0, int(round(pct * (len(scale) - 1)))))
        return int(round(scale[idx]))

    _SEASON_SQL = """
        WITH agg AS (
          SELECT s.player_id, any_value(s.team) team, sum(s.minutes) mins, sum(s.goals) g,
                 sum(s.assists) a, sum(s.xg) xg, sum(s.xa) xa, sum(s.shots) sh,
                 sum(s.chances_created) cc, sum(s.big_chances_created) bcc,
                 sum(s.dribbles_completed) drb, sum(s.tackles) tkl, sum(s.interceptions) intc,
                 sum(s.passes_completed) pc, sum(s.clearances) clr,
                 sum(s.duels_won_pct*s.minutes)/nullif(sum(s.minutes),0) dwp,
                 sum(s.pass_accuracy_pct*s.minutes)/nullif(sum(s.minutes),0) pap,
                 sum(s.aerial_duels_won_pct*s.minutes)/nullif(sum(s.minutes),0) awp,
                 sum(s.dribble_success_pct*s.minutes)/nullif(sum(s.minutes),0) dsp
          FROM v_stats_combined_player s WHERE s.season = ? GROUP BY s.player_id)
        SELECT p.player_name, p.primary_position, a.*, max(r.rating) rating,
               any_value(r.position_group) pos_group, max(pe.fpid) fpid
        FROM agg a JOIN players p ON p.player_id = a.player_id
        LEFT JOIN player_ratings_combined r ON r.player_id = a.player_id AND r.season = ?
        LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                   WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
               ON pe.player_id = a.player_id
        WHERE strip_accents(lower(p.player_name)) = strip_accents(lower(?)) AND a.mins >= 300
        GROUP BY ALL ORDER BY a.mins DESC LIMIT 1
    """

    def tactics_player_seasons(self, name: str, limit: int = 14) -> list[dict]:
        """Every season this player has real minutes in, newest first — each one fieldable
        in the Lab as the player he was that year."""
        # NB: the ratings table carries one row per scope (league / UCL), so it has to be
        # collapsed BEFORE the join — joining it raw fans the stats out and doubles the
        # goal totals (2021/22 Ronaldo came back with 48).
        rows = self.con.execute("""
            WITH st AS (
              SELECT s.player_id, s.season, any_value(s.team) team, sum(s.minutes) mins,
                     sum(s.goals) g, sum(s.assists) a
              FROM v_stats_combined_player s JOIN players p ON p.player_id = s.player_id
              WHERE strip_accents(lower(p.player_name)) = strip_accents(lower(?))
              GROUP BY s.player_id, s.season),
            rt AS (SELECT player_id, season, max(rating) rating,
                          any_value(position_group) grp
                   FROM player_ratings_combined GROUP BY player_id, season)
            SELECT st.season, st.team, st.mins, st.g, st.a, rt.rating, rt.grp
            FROM st LEFT JOIN rt ON rt.player_id = st.player_id AND rt.season = st.season
            WHERE st.mins >= 600 ORDER BY st.season DESC LIMIT ?""", [name, limit]).fetchall()
        out = []
        for season, team, mins, g, a, rating, grp in rows:
            out.append({"season": season, "label": _season_label(season), "team": team,
                        "minutes": _i(mins), "goals": _i(g), "assists": _i(a),
                        "rating": self.atlas_to_fifa(rating, season, grp) if rating else None})
        return out

    def tactics_player_season(self, name: str, season: str) -> dict | None:
        """The player as he was in one specific season: that year's per-90 output driving the
        attributes, and that year's rating converted to today's scale. Reads the `players`
        dimension rather than the current-season profile view, so someone who has since left
        the top five leagues — a 2014/15 Ronaldo, a 2016/17 Hazard — still resolves."""
        from webapp import fifa
        df = self.con.execute(self._SEASON_SQL, [season, season, name]).df()
        if df.empty:
            return None
        r = next(df.itertuples())
        mins = r.mins or 1

        def p90(v):
            return round((v or 0) / mins * 90, 3)
        # `players.primary_position` is only F/M/D/GK (plus a sub flag); the ratings table
        # carries the fine family (ST/W/AM/CM/DM/FB/CB/GK) per season, which is what the
        # engine's slots and roles are keyed on.
        fam = (r.pos_group or {"FWD": "ST", "MID": "CM", "DEF": "CB", "GK": "GK"}.get(
            (r.primary_position or "").split()[0].replace("F", "FWD").replace("M", "MID")
            .replace("D", "DEF"), "CM")) or "CM"
        overall = self.atlas_to_fifa(r.rating, season, fam) if r.rating else 70
        # Keepers are the one position the season ratings can't price. The rating engine
        # barely covers them historically — 3 to 8 per season against ~1500 outfielders —
        # so ranking one against that handful put a 2014/15 Neuer on 71. Where the keeper
        # has an EA FC card, use it: it's ability-based and stable, which is exactly what's
        # missing. (Outfield positions are rated in the hundreds each, with near-identical
        # medians, so they keep the season conversion.)
        gk_card = fifa.match(r.player_name) if fam == "GK" else None
        if gk_card and gk_card.get("o"):
            overall = int(gk_card["o"])
        out = {
            "player": f"{r.player_name} ({_season_label(season)})",
            "position": fam, "rating": overall,
            "minutes": _i(r.mins), "photo": self.player_photo(r.fpid),
            "season": season, "season_label": _season_label(season), "team": r.team,
            "per90": {"goals": p90(r.g), "assists": p90(r.a), "xg": p90(r.xg),
                      "xa": p90(r.xa), "shots": p90(r.sh), "chances": p90(r.cc),
                      "big_chances": p90(r.bcc), "dribbles": p90(r.drb),
                      "tackles": p90(r.tkl), "interceptions": p90(r.intc),
                      "passes": p90(r.pc), "clearances": p90(r.clr)},
            "pct": {"duels": _r(r.dwp, 0), "passing": _r(r.pap, 0),
                    "aerial": _r(r.awp, 0), "dribble": _r(r.dsp, 0)},
        }
        out["fifa"] = ({k: gk_card[k] for k in ("o", "pac", "sho", "pas", "dri", "def", "phy", "hea")}
                       if gk_card else _synth_card(overall, fam, out))
        return out

    def tactics_player(self, name: str, season: str | None = None) -> dict | None:
        """Resolve ANY player by name into a Tactics-Lab engine player dict (FIFA attrs +
        photo + position), independent of team — so a user can drop e.g. Rodri into any
        squad as a 'what-if' transfer. Prefers the FIFA/EA FC card (same source the club
        squads use); falls back to DB season stats when the player has no FIFA card.
        With `season`, fields the player as he was that year instead (see above)."""
        from webapp import fifa
        if season and season != FOCUS_SEASON:
            return self.tactics_player_season(name, season)
        c = fifa.match(name)
        if c:
            return {
                "player": name, "position": c.get("pos") or "CM", "rating": c["o"],
                "minutes": None, "photo": self._tac_photo_for_name(name),
                "fifa": {k: c[k] for k in ("o", "pac", "sho", "pas", "dri", "def", "phy", "hea")},
                "per90": {}, "pct": {},
            }
        return self._tactics_player_db(name)

    def _tactics_player_db(self, name: str, season: str = FOCUS_SEASON) -> dict | None:
        """DB season-stats fallback for a single player with no FIFA card (per-90 + pct so
        the engine's stat-derived attributes still work)."""
        q = """
        WITH agg AS (
          SELECT player_id, sum(minutes) mins, sum(goals) g, sum(assists) a,
                 sum(xg) xg, sum(xa) xa, sum(shots) sh, sum(chances_created) cc,
                 sum(big_chances_created) bcc, sum(dribbles_completed) drb,
                 sum(tackles) tkl, sum(interceptions) intc, sum(passes_completed) pc,
                 sum(clearances) clr,
                 sum(duels_won_pct*minutes)/nullif(sum(minutes),0) dwp,
                 sum(pass_accuracy_pct*minutes)/nullif(sum(minutes),0) pap,
                 sum(aerial_duels_won_pct*minutes)/nullif(sum(minutes),0) awp,
                 sum(dribble_success_pct*minutes)/nullif(sum(minutes),0) dsp
          FROM v_stats_combined_player WHERE season = ? GROUP BY player_id)
        SELECT f.player, COALESCE(f.detailed_position, f.main_position) AS pos, f.rating,
               a.mins, a.g, a.a, a.xg, a.xa, a.sh, a.cc, a.bcc, a.drb, a.tkl, a.intc,
               a.pc, a.clr, a.dwp, a.pap, a.awp, a.dsp, pe.fpid
        FROM v_player_profile_full f JOIN agg a ON a.player_id = f.player_id
        LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                   WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
               ON pe.player_id = f.player_id
        WHERE lower(f.player) = lower(?) AND a.mins >= 200 AND f.rating IS NOT NULL
        ORDER BY a.mins DESC LIMIT 1
        """
        df = self.con.execute(q, [season, name]).df()
        if df.empty:
            return None
        r = next(df.itertuples())
        mins = r.mins or 1

        def p90(v):
            return round((v or 0) / mins * 90, 3)
        return {
            "player": r.player, "position": r.pos, "rating": _i(r.rating),
            "minutes": _i(r.mins), "photo": self.player_photo(r.fpid),
            "per90": {"goals": p90(r.g), "assists": p90(r.a), "xg": p90(r.xg),
                      "xa": p90(r.xa), "shots": p90(r.sh), "chances": p90(r.cc),
                      "big_chances": p90(r.bcc), "dribbles": p90(r.drb),
                      "tackles": p90(r.tkl), "interceptions": p90(r.intc),
                      "passes": p90(r.pc), "clearances": p90(r.clr)},
            "pct": {"duels": _r(r.dwp, 0), "passing": _r(r.pap, 0),
                    "aerial": _r(r.awp, 0), "dribble": _r(r.dsp, 0)},
        }

    def tactics_search(self, q: str, limit: int = 12) -> list[dict]:
        """Name search for the Tactics Lab 'add player' box — DB players (nice names +
        photos) enriched with the FIFA rating/position the engine uses. Ordered by quality."""
        q = (q or "").strip()
        if len(q) < 2:
            return []
        from webapp import fifa
        rows = self.con.execute(
            "SELECT f.player, COALESCE(f.detailed_position, f.main_position) AS pos, "
            "       f.rating, f.team, pe.fpid "
            "FROM v_player_profile_full f "
            "LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment "
            "  WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe "
            "  ON pe.player_id = f.player_id "
            "WHERE strip_accents(lower(f.player)) LIKE ? AND f.rating IS NOT NULL "
            "ORDER BY f.rating DESC NULLS LAST LIMIT ?",
            ["%" + _fold(q) + "%", limit]).fetchall()
        out, seen = [], set()
        for player, pos, rating, team, fpid in rows:
            c = fifa.match(player)
            seen.add(player.lower())
            out.append({"player": player,
                        "position": (c.get("pos") if c else pos) or "CM",
                        "rating": (c["o"] if c else (_i(rating) or 70)),
                        "team": team, "photo": self.player_photo(fpid)})
        # Players who have LEFT the top five leagues aren't in the current-season profile at
        # all, so a search for Ronaldo or Hazard used to come back empty. Anyone with a real
        # season in the warehouse is fieldable, so they belong in the box too — offered at
        # their best season, with the rest a click away.
        if len(out) < limit:
            past = self.con.execute("""
                WITH st AS (
                  SELECT s.player_id, s.season, sum(s.minutes) mins
                  FROM v_stats_combined_player s GROUP BY s.player_id, s.season),
                rt AS (SELECT player_id, season, max(rating) rating,
                              any_value(position_group) grp
                       FROM player_ratings_combined GROUP BY player_id, season),
                best AS (
                  SELECT st.player_id, st.season, st.mins, rt.rating, rt.grp,
                         row_number() OVER (PARTITION BY st.player_id
                                            ORDER BY rt.rating DESC NULLS LAST) rn
                  FROM st LEFT JOIN rt ON rt.player_id = st.player_id AND rt.season = st.season
                  WHERE st.mins >= 900 AND st.season <> ?)
                SELECT p.player_name, b.season, b.rating, b.grp, any_value(v.team) team,
                       max(pe.fpid) fpid
                FROM best b JOIN players p ON p.player_id = b.player_id
                LEFT JOIN v_stats_combined_player v
                       ON v.player_id = b.player_id AND v.season = b.season
                LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                           WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                       ON pe.player_id = b.player_id
                WHERE b.rn = 1 AND strip_accents(lower(p.player_name)) LIKE ?
                GROUP BY p.player_name, b.season, b.rating, b.grp
                ORDER BY b.rating DESC NULLS LAST LIMIT ?""",
                [FOCUS_SEASON, "%" + _fold(q) + "%", limit]).fetchall()
            for player, season, rating, grp, team, fpid in past:
                if player.lower() in seen or len(out) >= limit:
                    continue
                seen.add(player.lower())
                out.append({"player": player, "position": grp or "CM", "season": season,
                            "season_label": _season_label(season),
                            "rating": self.atlas_to_fifa(rating, season, grp) if rating else 70,
                            "team": team, "photo": self.player_photo(fpid), "past": True})
        return out

    def tactics_league_table(self, team: str, proj_points, games: int) -> dict | None:
        """Real current league table with the user's team's points replaced by the Tactics-Lab
        full-season projection; every OTHER team's current pace is extrapolated to the same number
        of games, then re-sorted. Reads as 'if the season plays out at current form, but YOUR side
        performs as the model projects.'"""
        tid = self.find_team_id(team)
        if tid is None:
            return None
        row = self.con.execute("SELECT team_name, league_key FROM teams WHERE team_id = ?", [tid]).fetchone()
        if not row:
            return None
        canon, lk = row
        df = self.league_standings(lk, FOCUS_SEASON)
        if df.empty:
            return None
        recs = []
        for r in df.itertuples():
            mp = r.mp or 1
            is_user = (r.team == canon)
            full = int(proj_points) if (is_user and proj_points is not None) else round((r.pts or 0) / mp * games)
            recs.append({"team": r.team, "pts": int(full), "is_user": bool(is_user),
                         "played": int(r.mp or 0), "logo": self.team_logo(r.team)})
        recs.sort(key=lambda x: -x["pts"])
        for i, rec in enumerate(recs):
            rec["pos"] = i + 1
        return {"league_key": lk, "games": games, "team": canon, "table": recs}

    def tactics_stat_leaders(self, names: list[str], games: int = 38, avail: float = 0.85) -> list[dict]:
        """Projected full-season stat leaders among the given players (the XI): each player's real
        current-season per-90 output extrapolated over `games` at ~`avail` of the minutes, top 5 per
        category. Real data only — players with <200' this season are excluded (too small to project)."""
        ids = {}
        for n in names:
            pid = self.find_player_id(n)
            if pid and pid not in ids:
                ids[pid] = n
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        q = f"""
          SELECT a.player_id, sum(a.minutes) mins, sum(a.goals) g, sum(a.assists) a2,
                 sum(a.xg) xg, sum(a.chances_created) cc, sum(a.big_chances_created) bcc,
                 sum(a.dribbles_completed) drb, max(pe.fpid) fpid
          FROM v_stats_combined_player a
          LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                     WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe ON pe.player_id = a.player_id
          WHERE a.season = ? AND a.player_id IN ({ph}) GROUP BY a.player_id
        """
        df = self.con.execute(q, [FOCUS_SEASON, *ids.keys()]).df()
        factor = games * avail
        players = []
        for r in df.itertuples():
            mins = r.mins or 0
            if mins < 200:                       # too small a sample to project a season
                continue

            def pj(v):
                return (v or 0) / mins * 90 * factor
            g, a = pj(r.g), pj(r.a2)
            players.append({
                "player": ids[r.player_id],
                "photo": self.player_photo(int(r.fpid)) if pd.notna(r.fpid) else None,
                "goals": g, "assists": a, "ga": g + a, "chances": pj(r.cc),
                "big_chances": pj(r.bcc), "dribbles": pj(r.drb), "xg": pj(r.xg),
            })
        cats = [("goals", "Goals", 0), ("assists", "Assists", 0), ("ga", "Goals + Assists", 0),
                ("chances", "Chances Created", 0), ("big_chances", "Big Chances Created", 0),
                ("dribbles", "Dribbles", 0), ("xg", "xG", 1)]
        out = []
        for key, label, dp in cats:
            top = sorted(players, key=lambda p: -p[key])[:5]
            rows = [{"player": p["player"], "photo": p["photo"],
                     "value": round(p[key], dp) if dp else int(round(p[key]))}
                    for p in top if p[key] > 0.5]
            if rows:
                out.append({"key": key, "label": label, "top": rows})
        return out

    def _tactics_squad_db(self, team: str, season: str = FOCUS_SEASON) -> list[dict]:
        """Legacy DB-derived squad (Understat/FotMob season stats). Kept as a reference; the
        live path now uses FIFA rosters for clubs."""
        q = """
        WITH agg AS (
          SELECT player_id, sum(minutes) mins, sum(goals) g, sum(assists) a,
                 sum(xg) xg, sum(xa) xa, sum(shots) sh, sum(chances_created) cc,
                 sum(big_chances_created) bcc, sum(dribbles_completed) drb,
                 sum(tackles) tkl, sum(interceptions) intc, sum(passes_completed) pc,
                 sum(clearances) clr,
                 sum(duels_won_pct*minutes)/nullif(sum(minutes),0) dwp,
                 sum(pass_accuracy_pct*minutes)/nullif(sum(minutes),0) pap,
                 sum(aerial_duels_won_pct*minutes)/nullif(sum(minutes),0) awp,
                 sum(dribble_success_pct*minutes)/nullif(sum(minutes),0) dsp
          FROM v_stats_combined_player WHERE season = ? GROUP BY player_id)
        SELECT f.player, COALESCE(f.detailed_position, f.main_position) AS pos, f.rating,
               a.mins, a.g, a.a, a.xg, a.xa, a.sh, a.cc, a.bcc, a.drb, a.tkl, a.intc,
               a.pc, a.clr, a.dwp, a.pap, a.awp, a.dsp, pe.fpid
        FROM v_player_profile_full f JOIN agg a ON a.player_id = f.player_id
        LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                   WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
               ON pe.player_id = f.player_id
        WHERE lower(f.team) = lower(?) AND a.mins >= 300 AND f.rating IS NOT NULL
        ORDER BY f.rating DESC NULLS LAST LIMIT 26
        """
        df = self.con.execute(q, [season, team]).df()
        out = []
        for r in df.itertuples():
            mins = r.mins or 1
            def p90(v):
                return round((v or 0) / mins * 90, 3)
            out.append({
                "player": r.player, "position": r.pos, "rating": _i(r.rating),
                "minutes": _i(r.mins), "photo": self.player_photo(r.fpid),
                "per90": {"goals": p90(r.g), "assists": p90(r.a), "xg": p90(r.xg),
                          "xa": p90(r.xa), "shots": p90(r.sh), "chances": p90(r.cc),
                          "big_chances": p90(r.bcc), "dribbles": p90(r.drb),
                          "tackles": p90(r.tkl), "interceptions": p90(r.intc),
                          "passes": p90(r.pc), "clearances": p90(r.clr)},
                "pct": {"duels": _r(r.dwp, 0), "passing": _r(r.pap, 0),
                        "aerial": _r(r.awp, 0), "dribble": _r(r.dsp, 0)},
            })
        return self._tactics_compress_ratings(out or self._tactics_squad_national(team))

    @staticmethod
    def _tactics_compress_ratings(squad: list[dict]) -> list[dict]:
        """The Atlas rating is percentile-harsh (a squad player at a giant club can read
        44). For the Tactics Lab we compress ratings toward the squad's own level and
        apply a team-quality-scaled floor — so a world-class squad bottoms out ~60 and
        the spread is tighter, while weaker teams keep lower floors. Order-preserving."""
        rts = [p["rating"] for p in squad if p.get("rating") is not None]
        if len(rts) < 3:
            return squad
        rts_sorted = sorted(rts, reverse=True)
        team_level = sum(rts_sorted[:11]) / len(rts_sorted[:11])   # core-XI strength
        mean = sum(rts) / len(rts)
        floor = max(42, min(63, round(team_level - 13)))           # world-class → ~60
        for p in squad:
            r = p.get("rating")
            if r is None:
                continue
            comp = 0.55 * r + 0.45 * mean                          # pull toward squad mean
            p["rating"] = int(max(floor, min(96, round(comp))))
        return squad

    def _tactics_squad_national(self, team: str) -> list[dict]:
        """National-team squad for the Tactics Lab, from the latest World Cup's
        wc_player_stats (coarse G/D/M/F positions; the engine maps those to slots).
        Rating uses the same manual WC override the WC directory shows."""
        row = self.con.execute("SELECT max(season) FROM wc_player_stats").fetchone()
        season = row[0] if row and row[0] else None
        if season is None:
            return []
        df = self.con.execute("""
            SELECT player, position, atlas_rating, player_id, minutes, goals, assists, xg,
                   shots, chances_created, big_chances_created, dribbles_completed, tackles,
                   interceptions, passes_completed, pass_accuracy_pct, duels_won_pct
            FROM wc_player_stats
            WHERE season = ? AND lower(team) = lower(?) AND atlas_rating IS NOT NULL
            ORDER BY atlas_rating DESC LIMIT 26
        """, [season, team]).df()
        ov = self._WC_RATING_OVERRIDE
        out = []
        for r in df.itertuples():
            mins = r.minutes or 1
            def p90(v):
                return round((v or 0) / mins * 90, 3)
            rating = int(ov.get(str(r.player).lower(), r.atlas_rating))
            out.append({
                "player": r.player, "position": r.position, "rating": rating,
                "minutes": _i(r.minutes), "photo": self.player_photo(r.player_id),
                "per90": {"goals": p90(r.goals), "assists": p90(r.assists), "xg": p90(r.xg),
                          "xa": 0.0, "shots": p90(r.shots), "chances": p90(r.chances_created),
                          "big_chances": p90(r.big_chances_created), "dribbles": p90(r.dribbles_completed),
                          "tackles": p90(r.tackles), "interceptions": p90(r.interceptions),
                          "passes": p90(r.passes_completed), "clearances": 0.0},
                "pct": {"duels": _r(r.duels_won_pct, 0), "passing": _r(r.pass_accuracy_pct, 0),
                        "aerial": 0, "dribble": 0},
            })
        return out

    # tab groups for the Players directory -> rating position_groups
    PLAYER_GROUPS = {"FWD": ["ST", "W"], "MID": ["AM", "CM", "DM"],
                     "DEF": ["FB", "CB"], "GK": ["GK"]}

    def web_players(self, group: str = "all", search: str | None = None,
                    limit: int = 24, season: str = FOCUS_SEASON,
                    scope: str = "league") -> list[dict]:
        """Top-rated players for the directory grid: full name, team, position,
        rating/classification, market value and this season's G/A. Uses
        v_player_profile_full (player_id-keyed, full names).

        scope='league' -> top-5-league players with the combined-League rating
        (same number the profile page leads with) and domestic G/A; players the
        combined engine doesn't cover (notably GKs) fall back to the datamb
        rating. scope='ucl' -> only Champions League players, showing the
        combined-UCL rating and their UCL G/A."""
        if scope == "ucl":
            return self._web_players_ucl(group, search, limit, season)
        if scope == "former":
            return self._web_players_former(group, search, limit)
        if scope == "wc":
            return self._web_players_wc(group, search, limit)
        # params order matches the ?-placeholders below: v.season, c.season, [group], [search], limit
        where, params = ["f.player_id IS NOT NULL"], [season, season]
        if group and group != "all" and group in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[group]
            where.append(f"f.main_position IN ({','.join(['?'] * len(gs))})")
            params += gs
        if search:
            where.append("strip_accents(lower(f.player)) LIKE strip_accents(lower('%'||?||'%'))")
            params.append(search)
        params.append(limit)
        # use the Understat full name (pl.player_name) -- f.player is datamb's
        # abbreviated form ('K. Mbappé') which find_player_id can't resolve.
        df = self.con.execute(f"""
            SELECT pl.player_name AS player, f.team,
                   COALESCE(f.detailed_position, f.main_position) AS position,
                   COALESCE(c.rating, f.rating) AS rating,
                   COALESCE(c.classification, f.classification) AS classification,
                   f.market_value_eur, v.goals, v.assists, pe.fpid
            FROM v_player_profile_full f
            JOIN players pl ON pl.player_id = f.player_id
            LEFT JOIN v_player_season_stats v
                   ON v.player_id = f.player_id AND v.season = ?
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = f.player_id AND c.scope = 'league' AND c.season = ?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = f.player_id
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(c.rating, f.rating) DESC, f.market_value_eur DESC NULLS LAST
            LIMIT ?
        """, params).df()
        return [{"player": r.player, "team": r.team, "position": r.position,
                 "rating": _i(r.rating), "classification": r.classification,
                 "market_value_eur": None if pd.isna(r.market_value_eur) else float(r.market_value_eur),
                 "goals": _i(r.goals), "assists": _i(r.assists),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for r in df.itertuples()]

    def _web_players_ucl(self, group, search, limit, season) -> list[dict]:
        """UCL directory: players with a combined-UCL rating, showing that rating
        and their Champions League G/A (from v_stats_ucl)."""
        where = ["c.scope = 'ucl'", "c.season = ?"]
        params = [season, season]                     # u.season (JOIN), then c.season
        if group and group != "all" and group in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[group]
            where.append(f"COALESCE(f.main_position, pl.position_group) IN ({','.join(['?'] * len(gs))})")
            params += gs
        if search:
            where.append("strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))")
            params.append(search)
        params.append(limit)
        df = self.con.execute(f"""
            SELECT pl.player_name AS player, COALESCE(u.team, f.team) AS team,
                   COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                   c.rating, c.classification, f.market_value_eur,
                   u.goals, u.assists, pe.fpid
            FROM player_ratings_combined c
            JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = c.player_id
            LEFT JOIN v_stats_ucl u ON u.player_id = c.player_id AND u.season = ?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = c.player_id
            WHERE {' AND '.join(where)}
            ORDER BY c.rating DESC, f.market_value_eur DESC NULLS LAST
            LIMIT ?
        """, params).df()
        return [{"player": r.player, "team": r.team, "position": r.position,
                 "rating": _i(r.rating), "classification": r.classification,
                 "market_value_eur": None if pd.isna(r.market_value_eur) else float(r.market_value_eur),
                 "goals": _i(r.goals), "assists": _i(r.assists),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for r in df.itertuples()]

    # frontend position group -> SofaScore position-group code (wc_player_stats.position)
    _WC_GROUP = {"FWD": "F", "MID": "M", "DEF": "D", "GK": "G"}
    _WC_POS_LABEL = {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}
    # artificial World Cup rating overrides (player name lower-cased -> rating).
    # Applied at read time so they reorder the grid and survive WC data refreshes.
    _WC_RATING_OVERRIDE = {"ousmane dembélé": 88, "bradley barcola": 78,
                           "dayot upamecano": 76, "william saliba": 74,
                           "jules koundé": 70, "lucas digne": 56, "pedri": 68}

    def _wc_photo(self, name):
        """FotMob photo for a WC player whose name doesn't join the players table
        directly — resolve via the fuzzy find_player_id (the same resolver the card's
        profile link uses), so e.g. 'Kylian Mbappé' -> 'Kylian Mbappe-Lottin'."""
        pid = self.find_player_id(name)
        if pid is None:
            return None
        row = self.con.execute(
            "SELECT max(fotmob_player_id) FROM player_enrichment WHERE player_id = ?",
            [pid]).fetchone()
        return self.player_photo(row[0] if row and row[0] is not None else None)

    def _web_players_wc(self, group, search, limit) -> list[dict]:
        """World Cup directory: the top-rated players of the most recent World Cup by
        their stats-based Atlas WC rating (0-99, wc_player_stats.atlas_rating —
        [[wc-rating-engine]]), shown with their tournament goals & assists. National
        teams carry a flag (country code from wc_standings) instead of a club crest.
        Photos are name-matched to a FotMob club photo (fuzzy fallback for names the
        players table stores differently). `_WC_RATING_OVERRIDE` artificially boosts
        selected players' ratings."""
        row = self.con.execute("SELECT max(season) FROM wc_player_stats").fetchone()
        season = row[0] if row and row[0] else None
        if season is None:
            return []
        # rating override as a SQL CASE so ORDER BY / LIMIT see the boosted value
        ov = self._WC_RATING_OVERRIDE
        ov_case = "".join(" WHEN lower(p.player) = ? THEN ?" for _ in ov)
        rating_sql = f"CASE{ov_case} ELSE p.atlas_rating END" if ov else "p.atlas_rating"
        ov_params = [v for name, r in ov.items() for v in (name, r)]
        where = ["p.season = ?", "p.atlas_rating IS NOT NULL"]
        params = ov_params + [season, season]          # CASE, then w.season (JOIN), p.season
        if group and group != "all" and group in self._WC_GROUP:
            where.append("p.position = ?")
            params.append(self._WC_GROUP[group])
        if search:
            where.append("strip_accents(lower(p.player)) LIKE strip_accents(lower('%'||?||'%'))")
            params.append(search)
        params.append(limit)
        df = self.con.execute(f"""
            SELECT p.player, p.team, p.position, {rating_sql} AS rating,
                   p.atlas_class AS classification, p.goals, p.assists, w.cc, pe.fpid
            FROM wc_player_stats p
            LEFT JOIN (SELECT DISTINCT team, cc FROM wc_standings WHERE season = ?) w
                   ON lower(w.team) = lower(p.team)
            LEFT JOIN players pl ON lower(pl.player_name) = lower(p.player)
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = pl.player_id
            WHERE {' AND '.join(where)}
            ORDER BY rating DESC, p.rating DESC NULLS LAST
            LIMIT ?
        """, params).df()
        out = []
        for r in df.itertuples():
            fpid = None if pd.isna(r.fpid) else int(r.fpid)
            photo = self.player_photo(fpid) if fpid else self._wc_photo(r.player)
            boosted = r.player.lower() in self._WC_RATING_OVERRIDE
            out.append({"player": r.player, "team": r.team,
                        "position": self._WC_POS_LABEL.get(r.position, r.position),
                        "rating": _i(r.rating),
                        "classification": self._tier(_i(r.rating)) if boosted else r.classification,
                        "cc": None if pd.isna(r.cc) else r.cc, "market_value_eur": None,
                        "goals": _i(r.goals), "assists": _i(r.assists), "photo": photo})
        return out

    @staticmethod
    def _tier(rating) -> str:
        """Absolute rating -> classification label (rank-free, for synthetic numbers
        like the combined rating; mirrors rate.py _classify's tiers)."""
        if rating is None:
            return ""
        if rating >= 90: return "World-Class"
        if rating >= 80: return "Elite"
        if rating >= 65: return "Above Average"
        if rating >= 50: return "Average"
        return "Below Average"

    def _web_players_former(self, group, search, limit) -> list[dict]:
        """'Former Players' directory: notable players whose last top-5-league
        season is before FOCUS_SEASON (left for another league, retired, etc. --
        Messi, Ronaldo, Suárez …). Each card shows that player's BEST former season
        by a COMBINED League+UCL rating (minutes-weighted blend of the two scope
        ratings -- same 'Combined' idea as the profile's stat scopes), with that
        season's advanced stats. Built on the per-season backfilled ratings +
        per-season positions ([[atlastra-webapp]], [[combined-ucl-league-ratings]])."""
        where = ["x.rn = 1", "x.lg_r IS NOT NULL"]
        params: list = [FOCUS_SEASON]                 # the < FOCUS_SEASON filter
        if group and group != "all" and group in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[group]
            where.append(f"x.position IN ({','.join(['?'] * len(gs))})")
            params += gs
        if search:
            where.append("strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))")
            params.append(search)
        params.append(limit)
        df = self.con.execute(f"""
            WITH latest AS (
                SELECT player_id, max(season) AS last_season
                FROM player_ratings_combined GROUP BY player_id),
            former AS (SELECT player_id FROM latest WHERE last_season < ?),
            seas AS (   -- pivot league & ucl scope rows into one row per season
                SELECT c.player_id, c.season,
                       max(CASE WHEN scope='league' THEN rating END)  AS lg_r,
                       max(CASE WHEN scope='league' THEN minutes END) AS lg_m,
                       max(CASE WHEN scope='ucl'    THEN rating END)  AS ucl_r,
                       max(CASE WHEN scope='ucl'    THEN minutes END) AS ucl_m,
                       max(CASE WHEN scope='league' THEN position_group END) AS position
                FROM player_ratings_combined c JOIN former USING (player_id)
                GROUP BY c.player_id, c.season),
            comb AS (   -- minutes-weighted League+UCL blend; pick each player's best
                SELECT *, CASE WHEN ucl_r IS NULL THEN lg_r
                               ELSE round((lg_r*lg_m + ucl_r*ucl_m)
                                          / NULLIF(lg_m + ucl_m, 0)) END AS comb_r
                FROM seas),
            x AS (
                SELECT *, row_number() OVER (PARTITION BY player_id
                           ORDER BY comb_r DESC, lg_m DESC) AS rn FROM comb)
            SELECT pl.player_name AS player, x.season, x.position,
                   x.comb_r AS rating, x.lg_r AS rating_league, x.ucl_r AS rating_ucl,
                   v.team, v.goals, v.assists, v.xg, v.xa,
                   v.key_passes, v.dribbles_completed, pe.fpid
            FROM x
            JOIN players pl USING (player_id)
            LEFT JOIN v_player_season_stats v
                   ON v.player_id = x.player_id AND v.season = x.season
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = x.player_id
            WHERE {' AND '.join(where)}
            -- players who didn't feature in the UCL that season sort to the bottom
            -- (their "combined" is league-only, so it isn't comparable to a true blend)
            ORDER BY (x.ucl_r IS NULL), x.comb_r DESC, v.goals DESC NULLS LAST
            LIMIT ?
        """, params).df()
        return [{"player": r.player, "team": r.team, "position": r.position,
                 "season": _fmt_season(r.season), "season_code": r.season,
                 "rating": _i(r.rating), "classification": self._tier(_i(r.rating)),
                 "rating_league": _i(r.rating_league), "rating_ucl": _i(r.rating_ucl),
                 "goals": _i(r.goals), "assists": _i(r.assists),
                 "xg": _r(r.xg, 1), "xa": _r(r.xa, 1),
                 "key_passes": _i(r.key_passes), "dribbles": _i(r.dribbles_completed),
                 "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                for r in df.itertuples()]

    def web_spotlight(self, season: str = FOCUS_SEASON) -> dict:
        def top(col, tbl="v_player_season_stats", rnd=0):
            r = self.con.execute(
                f"SELECT pl.player_name, v.{col} AS val, v.team, "
                "  (SELECT max(fotmob_player_id) FROM player_enrichment e "
                "   WHERE e.player_id = v.player_id AND e.fotmob_player_id IS NOT NULL) AS fpid "
                f"FROM {tbl} v JOIN players pl USING(player_id) "
                f"WHERE v.season = ? AND v.{col} IS NOT NULL ORDER BY v.{col} DESC LIMIT 1",
                [season]).fetchone()
            return {"player": r[0], "value": round(float(r[1]), rnd) if rnd else int(r[1]),
                    "photo": self.player_photo(r[3]), "team_logo": self.team_logo(r[2])} if r else None
        return {
            "top_scorer": top("goals"), "top_assists": top("assists"),
            "most_xg": top("xg", rnd=1), "most_chances": top("chances_created"),
            "most_dribbles": top("dribbles_completed"),
        }

    def _discover_pool(self, season: str):
        """Top-rated league players (rating + this season's G/A + photo) -- the pool
        the player-spotlight and head-to-head discover cards sample from."""
        return self.con.execute("""
            SELECT pl.player_name AS name, pl.position_group AS grp, c.rating,
                   c.classification, COALESCE(f.detailed_position, f.main_position) AS pos,
                   f.team, v.goals, v.assists, pe.fpid
            FROM player_ratings_combined c
            JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = c.player_id
            LEFT JOIN v_player_season_stats v ON v.player_id = c.player_id AND v.season = ?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = c.player_id
            WHERE c.scope='league' AND c.season = ? AND c.rating IS NOT NULL
            ORDER BY c.rating DESC LIMIT 50
        """, [season, season]).df()

    def _player_card(self, r) -> dict:
        ga = []
        if not pd.isna(r.goals) and r.goals:
            ga.append(f"{int(r.goals)} goal{'s' if r.goals != 1 else ''}")
        if not pd.isna(r.assists) and r.assists:
            ga.append(f"{int(r.assists)} assist{'s' if r.assists != 1 else ''}")
        return {"player": r["name"], "team": None if pd.isna(r.team) else r.team,
                "position": None if pd.isna(r.pos) else r.pos, "rating": _i(r.rating),
                "photo": self.player_photo(None if pd.isna(r.fpid) else int(r.fpid)),
                "team_logo": self.team_logo(None if pd.isna(r.team) else r.team),
                "goals": _i(r.goals), "assists": _i(r.assists),
                "line": " · ".join(ga) if ga else None}

    def web_discover(self, season: str = FOCUS_SEASON) -> dict:
        """One random 'discover' card for signed-out visitors -- rotates between a
        player spotlight, a head-to-head, a stat leader and a recent result so the
        home page feels alive without an account. Returns {} if nothing is available."""
        def player():
            df = self._discover_pool(season)
            if df.empty:
                return None
            r = df.iloc[random.randrange(len(df))]
            return {"type": "player", "kicker": "Player Spotlight",
                    "blurb": (None if pd.isna(r.classification) else r.classification)
                    or "One of the season's standouts", **self._player_card(r)}

        def compare():
            df = self._discover_pool(season)
            if df.empty:
                return None
            grps = [g for g, n in df.groupby("grp").size().items() if n >= 2]
            if not grps:
                return None
            sub = df[df["grp"] == random.choice(grps)]
            i, j = random.sample(range(len(sub)), 2)
            a, b = self._player_card(sub.iloc[i]), self._player_card(sub.iloc[j])
            return {"type": "compare", "kicker": "Head-to-Head", "a": a, "b": b}

        def stat():
            metrics = [("goals", "Most Goals", "goals", 0), ("assists", "Most Assists", "assists", 0),
                       ("xg", "Highest xG", "xG", 1), ("chances_created", "Most Chances Created", "created", 0),
                       ("dribbles_completed", "Most Dribbles", "dribbles", 0)]
            col, label, unit, rnd = random.choice(metrics)
            r = self.con.execute(f"""
                SELECT pl.player_name, v.{col} AS val, v.team, pe.fpid
                FROM v_player_season_stats v JOIN players pl USING(player_id)
                LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                           WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                       ON pe.player_id = v.player_id
                WHERE v.season = ? AND v.{col} IS NOT NULL ORDER BY v.{col} DESC LIMIT 1
            """, [season]).fetchone()
            if not r:
                return None
            val = round(float(r[1]), rnd) if rnd else int(r[1])
            return {"type": "stat", "kicker": "Stat Leader", "label": label,
                    "player": r[0], "value": val, "unit": unit, "team": r[2],
                    "team_logo": self.team_logo(r[2]), "photo": self.player_photo(r[3])}

        def match():
            df = self.con.execute("""
                SELECT event_id, home_team, away_team, home_score, away_score,
                       home_pens, away_pens, tournament_name
                FROM live_matches
                WHERE status_type='finished' AND home_score IS NOT NULL
                ORDER BY start_timestamp DESC LIMIT 20
            """).df()
            if df.empty:
                return None
            r = df.iloc[random.randrange(len(df))]
            return {"type": "match", "kicker": "Recent Result",
                    "event_id": _i(r.event_id), "competition": r.tournament_name,
                    "home": r.home_team, "away": r.away_team,
                    "home_logo": self.team_logo(r.home_team), "away_logo": self.team_logo(r.away_team),
                    "home_score": _i(r.home_score), "away_score": _i(r.away_score),
                    "home_pens": None if pd.isna(r.home_pens) else int(r.home_pens),
                    "away_pens": None if pd.isna(r.away_pens) else int(r.away_pens)}

        builders = [player, compare, stat, match]
        random.shuffle(builders)
        for b in builders:                       # try kinds in random order; skip empties
            try:
                card = b()
            except Exception:                    # noqa: BLE001 -- never break the home page
                card = None
            if card:
                return card
        return {}

    def web_live(self, limit_recent: int = 40, limit_upcoming: int = 40) -> dict:
        """Live / upcoming / recent matches from the live_matches table
        (pipeline/load_live.py). Three buckets, each a flat list ordered for the UI;
        live first by kickoff, upcoming soonest-first, recent newest-first."""
        empty = {"live": [], "upcoming": [], "recent": [], "updated_at": None}
        try:
            df = self.con.execute("""
                SELECT event_id, tournament_key, tournament_name, tournament_group,
                       round_name, start_timestamp, status_type, status_desc, minute,
                       home_team, home_country, away_team, away_country,
                       home_score, away_score, home_pens, away_pens, winner_code,
                       CAST(updated_at AS VARCHAR) AS updated_at
                FROM live_matches
            """).df()
        except Exception:  # noqa: BLE001 -- table may not exist yet
            return empty
        if df.empty:
            return empty

        def match(r) -> dict:
            none = lambda v: None if pd.isna(v) else v
            return {
                "event_id": int(r.event_id),
                "competition": r.tournament_name, "group": r.tournament_group,
                "round": none(r.round_name), "kickoff_ts": int(r.start_timestamp),
                "status": r.status_type, "status_desc": none(r.status_desc),
                "minute": None if pd.isna(r.minute) else int(r.minute),
                "home": r.home_team, "home_logo": self.team_logo(r.home_team),
                "home_country": none(r.home_country), "home_rank": self.fifa_rank(r.home_team),
                "home_score": None if pd.isna(r.home_score) else int(r.home_score),
                "away": r.away_team, "away_logo": self.team_logo(r.away_team),
                "away_country": none(r.away_country), "away_rank": self.fifa_rank(r.away_team),
                "away_score": None if pd.isna(r.away_score) else int(r.away_score),
                "home_pens": None if pd.isna(r.home_pens) else int(r.home_pens),
                "away_pens": None if pd.isna(r.away_pens) else int(r.away_pens),
                "winner": None if pd.isna(r.winner_code) else int(r.winner_code),
            }

        live = [match(r) for r in df[df.status_type == "inprogress"]
                .sort_values("start_timestamp").itertuples()]
        # 'delayed' (kickoff pushed back but still to be played) would otherwise
        # match no bucket and vanish -- show it with the upcoming fixtures.
        upcoming = [match(r) for r in df[df.status_type.isin(["notstarted", "delayed"])]
                    .sort_values("start_timestamp").head(limit_upcoming).itertuples()]
        recent = [match(r) for r in df[df.status_type == "finished"]
                  .sort_values("start_timestamp", ascending=False).head(limit_recent).itertuples()]
        return {"live": live, "upcoming": upcoming, "recent": recent,
                "updated_at": df["updated_at"].max()}   # latest row (live overlay), not first

    def _team_form(self, team_name: str, season: str, n: int = 5) -> list:
        """Last-n results as W/D/L, oldest→newest (for a form string)."""
        form = self.con.execute("""
            SELECT CASE WHEN points=3 THEN 'W' WHEN points=1 THEN 'D' ELSE 'L' END
            FROM team_match_stats tms JOIN teams t USING(team_id)
            WHERE t.team_name = ? AND tms.season = ? AND tms.goals_for IS NOT NULL
            ORDER BY tms.match_date DESC LIMIT ?
        """, [team_name, season, n]).df().iloc[:, 0].tolist()
        return list(reversed(form))

    def web_standings(self, league_key: str, season: str = FOCUS_SEASON, limit: int = 6) -> list[dict]:
        df = self.league_standings(league_key, season).head(limit)
        return [{"pos": int(r.pos), "team": r.team, "p": int(r.mp),
                 "w": int(r.w), "d": int(r.d), "l": int(r.l),
                 "gd": int(r.gd), "pts": int(r.pts),
                 "form": self._team_form(r.team, season)}
                for r in df.itertuples()]

    # league key -> display name, for the Teams page tabs
    LEAGUE_TABS = [("ENG-Premier League", "Premier League"), ("ESP-La Liga", "La Liga"),
                   ("ITA-Serie A", "Serie A"), ("GER-Bundesliga", "Bundesliga"),
                   ("FRA-Ligue 1", "Ligue 1")]

    def web_leagues(self) -> list[dict]:
        return [{"key": k, "name": n} for k, n in self.LEAGUE_TABS]

    def web_seasons(self) -> list[dict]:
        """Seasons available for league standings/fixtures/leaders, newest first."""
        rows = self.con.execute(
            "SELECT DISTINCT season FROM team_season_stats ORDER BY season DESC").fetchall()
        return [{"value": r[0], "label": _fmt_season(r[0])} for r in rows]

    def web_league_table(self, league_key: str, season: str = FOCUS_SEASON) -> list[dict]:
        """Full league standings (use case 6) — every team, with crest, xG/xPts
        and recent form. Rows link to the team page."""
        df = self.league_standings(league_key, season)
        return [{"pos": int(r.pos), "team": r.team, "team_logo": self.team_logo(r.team),
                 "p": int(r.mp), "w": int(r.w), "d": int(r.d), "l": int(r.l),
                 "gf": int(r.gf), "ga": int(r.ga), "gd": int(r.gd), "pts": int(r.pts),
                 "xg_for": _r(r.xg_for, 1), "xg_against": _r(r.xg_against, 1),
                 "xpts": _r(r.xpts, 1), "form": self._team_form(r.team, season)}
                for r in df.itertuples()]

    # stat -> (label, format, is_rate). Rates need a higher minutes floor.
    _LEADER_STATS = [
        ("goals", "Goals", "int", False), ("assists", "Assists", "int", False),
        ("ga", "Goal Involvements", "int", False), ("xg", "Expected Goals (xG)", "dec", False),
        ("xa", "Expected Assists (xA)", "dec", False), ("chances_created", "Chances Created", "int", False),
        ("big_chances_created", "Big Chances Created", "int", False), ("key_passes", "Key Passes", "int", False),
        ("shots", "Shots", "int", False), ("dribbles_completed", "Dribbles", "int", False),
        ("tackles", "Tackles", "int", False), ("interceptions", "Interceptions", "int", False),
        ("recoveries", "Recoveries", "int", False), ("duels_won", "Duels Won", "int", False),
        ("passes_completed", "Passes Completed", "int", False),
        ("goals_per90", "Goals per 90", "dec", True), ("duels_won_pct", "Duels Won %", "pct", True),
        ("pass_accuracy_pct", "Pass Accuracy %", "pct", True), ("dribble_success_pct", "Dribble Success %", "pct", True),
    ]

    def web_league_leaders(self, league_key: str, season: str = FOCUS_SEASON,
                           top: int = 3, min_minutes: int = 450) -> dict:
        """Use case 1/6: leaders in every stat (top scorers, assisters, creators,
        dribblers, defenders, …) — top N players per stat. Pass league_key='all'
        (or empty) for the combined top-5 leagues.
        """
        import pandas as pd
        all5 = not league_key or league_key.lower() == "all"
        if all5:
            league_label, where, params = "Top 5 Leagues", "", [season, min_minutes]
        else:
            nm = self.con.execute("SELECT league_name FROM leagues WHERE league_key=?", [league_key]).fetchone()
            league_label = nm[0] if nm else league_key
            where, params = "p.league_key=? AND ", [league_key, season, min_minutes]
        cols = ", ".join(f"v.{k}" for k, *_ in self._LEADER_STATS if k not in ("ga",))
        df = self.con.execute(
            f"""SELECT v.player_id, pl.player_name, v.team, v.minutes, v.ga, {cols}, pe.fpid
                FROM v_player_season_stats v
                JOIN player_season_stats p ON p.player_id=v.player_id AND p.season=v.season
                JOIN players pl ON pl.player_id=v.player_id
                LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                           WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                       ON pe.player_id=v.player_id
                WHERE {where}v.season=? AND v.minutes>=?""",
            params).df()
        if df.empty:
            return {"available": False, "league": league_label, "leaders": []}

        # rate stats need a volume floor so a tiny-sample fluke (e.g. a keeper at
        # 100% on one dribble) can't top the chart.
        rate_min = {"goals_per90": ("goals", 5), "duels_won_pct": ("duels_won", 50),
                    "pass_accuracy_pct": ("passes_completed", 500),
                    "dribble_success_pct": ("dribbles_completed", 20)}

        def fmt(v, kind):
            return (str(int(round(v))) if kind == "int"
                    else f"{v:.1f}" if kind == "dec" else f"{round(v)}%")
        leaders = []
        for key, label, kind, is_rate in self._LEADER_STATS:
            if key not in df.columns:
                continue
            sub = df[df["minutes"] >= 900] if is_rate else df
            if key in rate_min:
                vcol, vmin = rate_min[key]
                sub = sub[sub[vcol] >= vmin]
            sub = sub.dropna(subset=[key])
            if not is_rate:
                sub = sub[sub[key] > 0]
            sub = sub.sort_values(key, ascending=False).head(top)
            if sub.empty:
                continue
            leaders.append({"key": key, "label": label, "unit": "" if kind != "pct" else "%",
                            "top": [{"player": r.player_name, "team": r.team,
                                     "photo": self.player_photo(int(r.fpid) if pd.notna(r.fpid) else None),
                                     "value": fmt(getattr(r, key), kind)} for r in sub.itertuples()]})
        return {"available": True, "league": league_label, "leaders": leaders}

    def web_league_fixtures(self, league_key: str, season: str = FOCUS_SEASON) -> dict:
        """All fixtures/results for a league this season — upcoming first (if any),
        then results most-recent-first."""
        df = self.con.execute(
            """SELECT m.match_date::DATE AS date, h.team_name AS home, a.team_name AS away,
                      m.home_goals, m.away_goals, round(m.home_xg,2) AS home_xg,
                      round(m.away_xg,2) AS away_xg, m.is_result
               FROM matches m
               JOIN teams h ON h.team_id = m.home_team_id
               JOIN teams a ON a.team_id = m.away_team_id
               WHERE m.league_key=? AND m.season=? ORDER BY m.match_date""",
            [league_key, season]).df()
        if df.empty:
            return {"available": False, "matches": []}
        names = set(df["home"]) | set(df["away"])
        logos = {n: self.team_logo(n) for n in names}
        rows = [{"date": str(r.date), "home": r.home, "away": r.away,
                 "home_logo": logos.get(r.home), "away_logo": logos.get(r.away),
                 "home_goals": _i(r.home_goals), "away_goals": _i(r.away_goals),
                 "home_xg": _r(r.home_xg, 2), "away_xg": _r(r.away_xg, 2),
                 "is_result": bool(r.is_result)} for r in df.itertuples()]
        upcoming = [m for m in rows if not m["is_result"]]
        results = [m for m in rows if m["is_result"]][::-1]
        return {"available": True, "matches": upcoming + results}

    def web_team(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """Team performance bundle (use case 6): standing, record, goals, xG/xPts,
        form, recent results and top scorers."""
        tid = self.find_team_id(name)
        if tid is None:
            return {}
        head = self.con.execute("""
            SELECT t.team_name, t.team_code, t.league_key, l.league_name, l.country
            FROM teams t JOIN leagues l USING(league_key) WHERE t.team_id = ?
        """, [tid]).fetchone()
        s = self.con.execute("""
            SELECT league_position, matches_played, wins, draws, losses, goals_for,
                   goals_against, goal_difference, points, xg_for, xg_against, expected_points
            FROM team_season_stats WHERE team_id = ? AND season = ?
        """, [tid, season]).fetchone()
        n_teams = self.con.execute(
            "SELECT COUNT(*) FROM team_season_stats WHERE league_key = ? AND season = ?",
            [head[2], season]).fetchone()[0]
        rf = self.team_form(head[0], season, last=8)
        results = [{"date": str(r.date), "venue": r.venue, "opponent": r.opponent,
                    "opponent_logo": self.team_logo(r.opponent),
                    "gf": int(r.gf), "ga": int(r.ga), "result": r.result,
                    "xg_for": _r(r.xg_for, 2), "xg_against": _r(r.xg_against, 2)}
                   for r in rf.itertuples()]
        scorers = self.con.execute("""
            SELECT pl.player_name, ps.goals, ps.assists
            FROM player_season_stats ps JOIN players pl USING(player_id)
            WHERE ps.team_id = ? AND ps.season = ?
            ORDER BY ps.goals DESC, ps.assists DESC LIMIT 5
        """, [tid, season]).df()
        # use case 7: manager + venue (FotMob), and full squad
        meta = self.con.execute(
            "SELECT manager, venue, city, capacity, opened, surface "
            "FROM team_meta WHERE team_id = ?", [tid]).fetchone()
        squad = self.con.execute("""
            SELECT pl.player_name, ps.position_group, ps.matches, ps.minutes,
                   ps.goals, ps.assists, pe.fpid, bio.fotmob_age
            FROM player_season_stats ps
            JOIN players pl USING(player_id)
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = ps.player_id
            LEFT JOIN player_bio bio ON bio.player_id = ps.player_id
            WHERE ps.team_id = ? AND ps.season = ?
            ORDER BY ps.minutes DESC
        """, [tid, season]).df()
        ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
        squad_rows = [{"player": r.player_name, "position_group": r.position_group,
                       "apps": _i(r.matches), "minutes": _i(r.minutes),
                       "goals": _i(r.goals), "assists": _i(r.assists),
                       "age": _i(r.fotmob_age), "photo": self.player_photo(r.fpid)}
                      for r in squad.itertuples()]
        squad_rows.sort(key=lambda p: (ORDER.get(p["position_group"], 4), -(p["minutes"] or 0)))
        return {
            "team": head[0], "team_code": head[1], "league_key": head[2],
            "league": head[3], "country": head[4], "team_logo": self.team_logo(head[0]),
            "season": _fmt_season(season), "n_teams": n_teams,
            "manager": meta[0] if meta else None,
            "venue": meta[1] if meta else None, "city": meta[2] if meta else None,
            "capacity": _i(meta[3]) if meta else None,
            "opened": _i(meta[4]) if meta else None,
            "surface": meta[5] if meta else None,
            "stats": None if not s else {
                "position": _i(s[0]), "played": _i(s[1]), "wins": _i(s[2]),
                "draws": _i(s[3]), "losses": _i(s[4]), "goals_for": _i(s[5]),
                "goals_against": _i(s[6]), "goal_difference": _i(s[7]), "points": _i(s[8]),
                "xg_for": _r(s[9], 1), "xg_against": _r(s[10], 1), "xpts": _r(s[11], 1)},
            "form": self._team_form(head[0], season),
            "results": results,
            "top_scorers": [{"player": r.player_name, "goals": _i(r.goals),
                             "assists": _i(r.assists)} for r in scorers.itertuples()],
            "squad": squad_rows,
        }

    def web_search(self, query: str, season: str = FOCUS_SEASON, limit: int = 12) -> dict:
        """Use case 8: unified player + team search for one query string."""
        q = (query or "").strip()
        if not q:
            return {"query": q, "players": [], "teams": []}
        # Search ALL seasons (so former players surface too); show each player's
        # most-recent season's club/stats. Active players (current season) rank
        # first, then by most recent season, then minutes.
        pdf = self.con.execute("""
            WITH m AS (
                SELECT ps.player_id, pl.player_name, max(ps.season) AS last_season
                FROM player_season_stats ps JOIN players pl USING(player_id)
                WHERE strip_accents(lower(pl.player_name)) LIKE strip_accents(lower('%'||?||'%'))
                GROUP BY ps.player_id, pl.player_name)
            SELECT m.player_name, m.last_season,
                   arg_max(t.team_name, ps.minutes) AS team,
                   any_value(ps.position_group) AS position,
                   SUM(ps.goals) AS goals, SUM(ps.assists) AS assists,
                   SUM(ps.minutes) AS minutes, max(pe.fpid) AS fpid
            FROM m
            JOIN player_season_stats ps ON ps.player_id = m.player_id AND ps.season = m.last_season
            JOIN teams t USING(team_id)
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = m.player_id
            GROUP BY m.player_id, m.player_name, m.last_season
            ORDER BY (m.last_season = ?) DESC, m.last_season DESC, minutes DESC
            LIMIT ?
        """, [q, season, limit]).df()
        tdf = self.con.execute("""
            SELECT t.team_name, l.league_name AS league, l.country
            FROM teams t JOIN leagues l USING(league_key)
            WHERE strip_accents(lower(t.team_name)) LIKE strip_accents(lower('%'||?||'%'))
            ORDER BY t.team_name LIMIT 8
        """, [q]).df()
        # national teams from the international feed (link to /nat.html via SofaScore id)
        ndf = self.con.execute("""
            SELECT team, any_value(cc) AS cc, any_value(team_id) AS team_id, count(*) AS n
            FROM (
                SELECT home_team AS team, home_country AS cc, home_team_id AS team_id
                FROM live_matches WHERE tournament_group = 'International' AND home_country IS NOT NULL
                UNION ALL
                SELECT away_team, away_country, away_team_id
                FROM live_matches WHERE tournament_group = 'International' AND away_country IS NOT NULL)
            WHERE strip_accents(lower(team)) LIKE strip_accents(lower('%'||?||'%'))
            GROUP BY team ORDER BY n DESC, team LIMIT 8
        """, [q]).df()
        return {
            "query": q,
            "players": [{"player": r.player_name, "team": r.team, "position": r.position,
                         "team_logo": self.team_logo(r.team), "photo": self.player_photo(r.fpid),
                         "goals": _i(r.goals), "assists": _i(r.assists),
                         "season": _fmt_season(r.last_season),
                         "former": r.last_season != season}
                        for r in pdf.itertuples()],
            "teams": [{"team": r.team_name, "league": r.league, "country": r.country,
                       "team_logo": self.team_logo(r.team_name)} for r in tdf.itertuples()],
            "national": [{"team": r.team, "team_id": _i(r.team_id), "cc": r.cc}
                         for r in ndf.itertuples()],
        }

    def web_match_search(self, team_a: str, team_b: str, since: str | None = None) -> dict:
        """Use case 8: head-to-head fixtures between two teams, most recent first.

        Spans all 12 seasons (1415–2526) by default.
        """
        ta, tb = self.find_team_id(team_a), self.find_team_id(team_b)
        if ta is None or tb is None:
            return {"team_a": team_a if ta else None, "team_b": team_b if tb else None,
                    "matches": []}
        names = dict(self.con.execute(
            "SELECT team_id, team_name FROM teams WHERE team_id IN (?, ?)", [ta, tb]).fetchall())
        df = self.search_matches(names[ta], names[tb], since)
        matches = [{"date": str(r.date), "season": _fmt_season(r.season), "comp": r.comp,
                    "home": r.home, "away": r.away,
                    "home_logo": self.team_logo(r.home), "away_logo": self.team_logo(r.away),
                    "home_goals": _i(r.home_goals), "away_goals": _i(r.away_goals),
                    "home_xg": _r(r.home_xg, 2), "away_xg": _r(r.away_xg, 2)}
                   for r in df.itertuples()]
        return {
            "team_a": {"name": names[ta], "logo": self.team_logo(names[ta])},
            "team_b": {"name": names[tb], "logo": self.team_logo(names[tb])},
            "matches": matches,
        }

    # ----- use case 10: player archetypes -------------------------------- #
    GROUP_LABELS = {"ST": "Strikers", "W": "Wingers", "AM": "Attacking Mids",
                    "CM": "Central Mids", "DM": "Defensive Mids", "FB": "Full-Backs",
                    "CB": "Centre-Backs", "GK": "Goalkeepers"}

    def _archetype_def(self, name: str):
        for grp, archs in ARCHETYPES.items():
            for a in archs:
                if a["name"].lower() == name.lower():
                    return grp, a
        return None, None

    def _player_tendencies(self, pid: int, top: int = 5, min_pct: int = 55) -> list:
        """Use case 9: the on-ball actions this player does most relative to position
        peers (highest-percentile TENDENCIES), as name + per-90 value + percentile."""
        df = self.con.execute(
            "SELECT tendency, value, percentile FROM player_tendencies "
            "WHERE player_id = ? AND percentile >= ? ORDER BY percentile DESC LIMIT ?",
            [pid, min_pct, top]).df()
        return [{"name": r.tendency, "value": _r(r.value, 2), "percentile": _i(r.percentile)}
                for r in df.itertuples()]

    def _player_heatmap(self, pid: int, season: str = FOCUS_SEASON):
        """Binned SofaScore season heatmap grid (rows×cols of 0-1 density), or None."""
        try:
            r = self.con.execute(
                "SELECT grid FROM player_heatmap WHERE player_id = ? AND season = ?",
                [pid, season]).fetchone()
        except Exception:  # noqa: BLE001 -- table not built yet
            return None
        if not r or not r[0]:
            return None
        import json
        try:
            return json.loads(r[0])
        except Exception:  # noqa: BLE001
            return None

    def _player_archetype(self, pid: int) -> dict:
        """Archetype label + fit + signature traits + similar players for a player."""
        a = self.con.execute(
            "SELECT position_group, archetype, fit, archetype2, fit2 "
            "FROM player_archetypes WHERE player_id = ?", [pid]).fetchone()
        if not a:
            return {}
        grp, defn = self._archetype_def(a[1])
        # signature traits: the role's "high" metrics where the player ranks well
        src = "player_profile_metrics" if a[0] == "GK" else "player_radar_metrics"
        pm = dict(self.con.execute(
            f"SELECT metric_label, percentile FROM {src} WHERE player_id = ?", [pid]).fetchall())
        traits = sorted(((m, pm[m]) for m in (defn["high"] if defn else []) if m in pm),
                        key=lambda x: -x[1])
        traits = [{"label": m, "pct": _i(p)} for m, p in traits if p is not None and p >= 55][:4]
        sim = self.con.execute("""
            SELECT pl.player_name, s.similarity,
                   COALESCE(f.detailed_position, f.main_position) AS position, f.team,
                   COALESCE(c.rating, f.rating) AS rating, pe.fpid
            FROM player_similar s JOIN players pl ON pl.player_id = s.similar_player_id
            LEFT JOIN v_player_profile_full f ON f.player_id = s.similar_player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = s.similar_player_id AND c.scope='league' AND c.season=?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = s.similar_player_id
            WHERE s.player_id = ? ORDER BY s.rank LIMIT 6
        """, [FOCUS_SEASON, pid]).df()
        return {
            "archetype": a[1], "fit": _i(a[2]),
            "archetype2": a[3], "fit2": _i(a[4]),
            "group": grp, "group_label": self.GROUP_LABELS.get(grp, grp),
            "blurb": defn["blurb"] if defn else None,
            "traits": traits,
            "similar": [{"player": r.player_name, "similarity": _r(r.similarity, 0),
                         "position": r.position, "team": r.team, "rating": _i(r.rating),
                         "photo": self.player_photo(r.fpid)} for r in sim.itertuples()],
        }

    def web_archetypes(self) -> list[dict]:
        """All archetypes grouped by position, with blurb, signature metrics and
        how many players carry each (for the explorer)."""
        cmap = dict(self.con.execute(
            "SELECT archetype, COUNT(*) FROM player_archetypes GROUP BY archetype").fetchall())
        return [{"group": grp, "group_label": self.GROUP_LABELS.get(grp, grp),
                 "archetypes": [{"name": a["name"], "blurb": a["blurb"],
                                 "signature": a["high"], "count": cmap.get(a["name"], 0)}
                                for a in archs]}
                for grp, archs in ARCHETYPES.items()]

    def web_team_of_season(self, season: str = FOCUS_SEASON, min_minutes: int = 1500) -> dict:
        """Best XI in a 4-3-3 by average match rating (FotMob/SofaScore, minutes-
        weighted across competitions), one player per detailed-position slot."""
        df = self.con.execute("""
            SELECT pl.player_name AS name,
                   COALESCE(f.detailed_position, f.main_position) AS pos,
                   cp.rating AS avg_rating, f.team, cp.minutes, pe.fpid,
                   COALESCE(c.rating, f.rating) AS atlastra
            FROM v_stats_combined_player cp JOIN players pl USING(player_id)
            JOIN v_player_profile_full f ON f.player_id = cp.player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = cp.player_id AND c.scope='league' AND c.season = cp.season
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = cp.player_id
            WHERE cp.season = ? AND cp.rating IS NOT NULL AND cp.minutes >= ?
            ORDER BY cp.rating DESC, cp.minutes DESC
        """, [season, min_minutes]).df()
        used = set()

        def pick(positions, n):
            out = []
            for r in df.itertuples():
                if len(out) >= n:
                    break
                if r.name not in used and r.pos in positions:
                    used.add(r.name)
                    out.append(r)
            return out

        # left-to-right per line: LB-CB-CB-RB ; LW-ST-RW
        lines = {"GK": pick({"GK"}, 1),
                 "DEF": pick({"LB"}, 1) + pick({"CB"}, 2) + pick({"RB"}, 1),
                 "MID": pick({"AM", "CM", "DM"}, 3),
                 "FWD": pick({"LW"}, 1) + pick({"ST"}, 1) + pick({"RW"}, 1)}

        def fmt(rows):
            return [{"player": r.name, "position": r.pos, "avg_rating": _r(r.avg_rating, 2),
                     "team": r.team, "team_logo": self.team_logo(r.team),
                     "photo": self.player_photo(r.fpid), "rating": _i(r.atlastra)}
                    for r in rows]
        return {"formation": "4-3-3", "season": _fmt_season(season),
                "lines": [{"label": k, "players": fmt(v)} for k, v in lines.items()]}

    _WC_LINE = {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}  # SofaScore pos -> pitch line

    def web_team_of_week(self, season: str | None = None, min_minutes: int = 150) -> dict:
        """World Cup best XI (4-3-3) ranked by actual World Cup average match
        rating. Source is wc_player_stats (SofaScore per-player tournament rating,
        scraped per position group and pushed in alongside the rest of the WC
        feed). Players need `min_minutes` in the tournament to clear small-sample
        noise; the top-rated per position fill GK / 4 DEF / 3 MID / 3 FWD. National
        flag (or a name-matched club photo) on each chip; badge shows the rating."""
        if season is None:
            row = self.con.execute("SELECT max(season) FROM wc_player_stats").fetchone()
            season = row[0] if row and row[0] else None
        if season is None:
            return {"edition": None, "formation": "4-3-3", "lines": []}

        # photo keys off wc_player_stats.player_id, which IS a FotMob id (WC data is
        # FotMob-sourced) -> player_photo resolves the CDN image directly, no fragile
        # name-join (which missed "Mbappé" vs our "Mbappe-Lottin", accents, etc.).
        df = self.con.execute("""
            SELECT p.player, p.team AS nat, p.position AS pos, p.rating,
                   p.minutes, p.appearances, p.player_id, w.cc
            FROM wc_player_stats p
            LEFT JOIN (SELECT DISTINCT team, cc FROM wc_standings WHERE season = ?) w
                   ON lower(w.team) = lower(p.team)
            WHERE p.season = ? AND p.minutes >= ?
        """, [season, season, min_minutes]).df()
        if df.empty:
            return {"edition": str(season), "formation": "4-3-3", "lines": []}

        df = df.sort_values(["rating", "minutes"], ascending=False)
        used = set()

        def pick(pos, n):
            out = []
            for r in df.itertuples():
                if len(out) >= n:
                    break
                if r.player not in used and r.pos == pos:
                    used.add(r.player)
                    out.append(r)
            return out

        lines = {"GK": pick("G", 1), "DEF": pick("D", 4),
                 "MID": pick("M", 3), "FWD": pick("F", 3)}

        def fmt(rows):
            return [{"player": r.player, "position": self._WC_LINE.get(r.pos, r.pos),
                     "team": r.nat, "cc": r.cc, "rating": _r(r.rating, 1),
                     "apps": _i(r.appearances),
                     "photo": self.player_photo(int(r.player_id) if pd.notna(r.player_id) else None)}
                    for r in rows]
        return {"edition": str(season), "formation": "4-3-3",
                "lines": [{"label": k, "players": fmt(v)} for k, v in lines.items()]}

    def web_archetype(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """One archetype: definition + its top players (by fit, then rating)."""
        grp, defn = self._archetype_def(name)
        if not defn:
            return {}
        df = self.con.execute("""
            SELECT pl.player_name, a.fit,
                   COALESCE(f.detailed_position, f.main_position) AS position, f.team,
                   COALESCE(c.rating, f.rating) AS rating, pe.fpid,
                   f.team AS team2
            FROM player_archetypes a JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = a.player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = a.player_id AND c.scope='league' AND c.season=?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = a.player_id
            WHERE a.archetype = ?
            ORDER BY rating DESC NULLS LAST, a.fit DESC NULLS LAST LIMIT 24
        """, [season, defn["name"]]).df()
        return {
            "name": defn["name"], "group": grp,
            "group_label": self.GROUP_LABELS.get(grp, grp),
            "blurb": defn["blurb"], "signature": defn["high"],
            "players": [{"player": r.player_name, "fit": _i(r.fit), "position": r.position,
                         "team": r.team, "team_logo": self.team_logo(r.team),
                         "rating": _i(r.rating), "photo": self.player_photo(r.fpid)}
                        for r in df.itertuples()],
        }

    # ---- Scout / Player Finder (use case: discovery) ----------------------
    # key -> (v_player_season_stats column, label, fmt). fmt in int/f1/f2/pct.
    SCOUT_METRICS = {
        "rating": (None, "Atlastra Rating", "int"),
        "goals": ("goals", "Goals", "int"),
        "assists": ("assists", "Assists", "int"),
        "ga": ("ga", "Goals + Assists", "int"),
        "xg": ("xg", "xG", "f1"),
        "xa": ("xa", "xA", "f1"),
        "goals_per90": ("goals_per90", "Goals / 90", "f2"),
        "xa_per90": ("xa_per90", "xA / 90", "f2"),
        "ga_per90": ("ga_per90", "G+A / 90", "f2"),
        "key_passes": ("key_passes", "Key Passes", "int"),
        "chances_created": ("chances_created", "Chances Created", "int"),
        "big_chances_created": ("big_chances_created", "Big Chances Created", "int"),
        "dribbles_completed": ("dribbles_completed", "Dribbles", "int"),
        "tackles": ("tackles", "Tackles", "int"),
        "interceptions": ("interceptions", "Interceptions", "int"),
        "recoveries": ("recoveries", "Recoveries", "int"),
        "duels_won_pct": ("duels_won_pct", "Duels Won %", "pct"),
        "pass_accuracy_pct": ("pass_accuracy_pct", "Pass Accuracy %", "pct"),
    }

    def web_scout(self, pos: str = "all", metric: str = "rating",
                  max_value_m: float = 0, min_minutes: int = 450,
                  max_age: int = 0, min_rating: int = 0,
                  limit: int = 40, season: str = FOCUS_SEASON) -> dict:
        """Player Finder: filter the player pool by position, budget (market-value
        ceiling, €m), reliability (min minutes), age and rating, ranked by a chosen
        metric. Returns the metric catalogue + matching players."""
        metric = metric if metric in self.SCOUT_METRICS else "rating"
        col = self.SCOUT_METRICS[metric][0]
        rating_sql = "COALESCE(c.rating, f.rating)"
        metric_sql = rating_sql if col is None else f"v.{col}"
        # placeholder order matches the query text: c.season (LEFT JOIN) then
        # v.season (WHERE), then the conditional filters, then LIMIT.
        where = ["f.player_id IS NOT NULL", "v.season = ?"]
        params: list = [season, season]                      # c.season, v.season
        if pos and pos != "all" and pos in self.PLAYER_GROUPS:
            gs = self.PLAYER_GROUPS[pos]
            where.append(f"f.main_position IN ({','.join(['?'] * len(gs))})")
            params += gs
        if min_minutes:
            where.append("v.minutes >= ?"); params.append(min_minutes)
        if max_value_m:
            where.append("f.market_value_eur IS NOT NULL AND f.market_value_eur <= ?")
            params.append(max_value_m * 1_000_000)
        if max_age:
            where.append("b.fotmob_age IS NOT NULL AND b.fotmob_age <= ?")
            params.append(max_age)
        if min_rating:
            where.append(f"{rating_sql} >= ?"); params.append(min_rating)
        where.append(f"{metric_sql} IS NOT NULL")
        params.append(limit)
        df = self.con.execute(f"""
            SELECT pl.player_name AS player, f.team,
                   COALESCE(f.detailed_position, f.main_position) AS position,
                   {rating_sql} AS rating, f.classification, f.market_value_eur,
                   b.fotmob_age AS age, b.nationality, v.minutes,
                   {metric_sql} AS metric_val, pe.fpid
            FROM v_player_profile_full f
            JOIN players pl ON pl.player_id = f.player_id
            JOIN v_player_season_stats v ON v.player_id = f.player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = f.player_id AND c.scope='league' AND c.season=?
            LEFT JOIN player_bio b ON b.player_id = f.player_id
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) AS fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = f.player_id
            WHERE {' AND '.join(where)}
            ORDER BY metric_val DESC NULLS LAST, {rating_sql} DESC NULLS LAST
            LIMIT ?
        """, params).df()
        fmt = self.SCOUT_METRICS[metric][2]
        def mv(v):
            if v is None or pd.isna(v):
                return None
            return _i(v) if fmt == "int" else _r(v, 2 if fmt == "f2" else 1)
        players = [{"player": r.player, "team": r.team, "position": r.position,
                    "rating": _i(r.rating), "classification": r.classification,
                    "market_value_eur": None if pd.isna(r.market_value_eur) else float(r.market_value_eur),
                    "age": _i(r.age), "nationality": r.nationality, "minutes": _i(r.minutes),
                    "metric_val": mv(r.metric_val),
                    "photo": self.player_photo(r.fpid), "team_logo": self.team_logo(r.team)}
                   for r in df.itertuples()]
        return {
            "metrics": [{"key": k, "label": lbl} for k, (_c, lbl, _f) in self.SCOUT_METRICS.items()],
            "groups": [{"key": "all", "label": "All positions"}]
                      + [{"key": g, "label": g} for g in self.PLAYER_GROUPS],
            "metric": metric, "metric_label": self.SCOUT_METRICS[metric][1],
            "metric_fmt": fmt, "count": len(players), "players": players,
        }

    # ---- Team style fingerprint (use case: team identity) -----------------
    STYLE_AXES = [
        ("atk", "Attack"), ("def", "Defence"), ("press", "Pressing"),
        ("pen", "Penetration"), ("fin", "Finishing"), ("ctrl", "Control"),
    ]

    def web_team_options(self, season: str = FOCUS_SEASON) -> list[dict]:
        """Every team with season stats, for the style-comparison pickers."""
        df = self.con.execute("""
            SELECT t.team_name, l.league_name
            FROM team_season_stats s JOIN teams t USING(team_id)
            JOIN leagues l ON l.league_key = t.league_key
            WHERE s.season = ? ORDER BY l.league_name, t.team_name
        """, [season]).df()
        return [{"team": r.team_name, "league": r.league_name,
                 "team_logo": self.team_logo(r.team_name)} for r in df.itertuples()]

    def web_team_style(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """A team's playing-style fingerprint: six axes scored 0-100 as the
        percentile of the team's per-match profile against every team in the
        season (cross-league). Axes: attack (xG for), defence (xG against,
        inverted), pressing (PPDA, inverted), penetration (deep completions),
        finishing (goals minus xG), control (share of total xG)."""
        tid = self.find_team_id(name)
        if tid is None:
            return {}
        row = self.con.execute("""
            WITH agg AS (
              SELECT t.team_id, t.team_name, l.league_name,
                     avg(m.xg_for) xgf, avg(m.xg_against) xga, avg(m.ppda) ppda,
                     avg(m.deep_completions) deep, avg(m.goals_for - m.xg_for) fin,
                     avg(m.xg_for) / NULLIF(avg(m.xg_for) + avg(m.xg_against), 0) ctrl
              FROM team_match_stats m JOIN teams t USING(team_id)
              JOIN leagues l ON l.league_key = t.league_key
              WHERE m.season = ? GROUP BY 1, 2, 3
            ), ranked AS (
              SELECT *,
                100*percent_rank() OVER (ORDER BY xgf)       s_atk,
                100*percent_rank() OVER (ORDER BY xga DESC)  s_def,
                100*percent_rank() OVER (ORDER BY ppda DESC) s_press,
                100*percent_rank() OVER (ORDER BY deep)      s_pen,
                100*percent_rank() OVER (ORDER BY fin)       s_fin,
                100*percent_rank() OVER (ORDER BY ctrl)      s_ctrl
              FROM agg
            )
            SELECT * FROM ranked WHERE team_id = ?
        """, [season, tid]).df()
        if row.empty:
            return {}
        r = row.iloc[0]
        scores = {"atk": r.s_atk, "def": r.s_def, "press": r.s_press,
                  "pen": r.s_pen, "fin": r.s_fin, "ctrl": r.s_ctrl}
        raws = {"atk": _r(r.xgf, 2), "def": _r(r.xga, 2), "press": _r(r.ppda, 1),
                "pen": _r(r.deep, 1), "fin": _r(r.fin, 2), "ctrl": _i(r.ctrl * 100)}
        return {
            "team": r.team_name, "league": r.league_name,
            "team_logo": self.team_logo(r.team_name),
            "axes": [{"key": k, "label": lbl, "score": _i(scores[k]), "raw": raws[k]}
                     for k, lbl in self.STYLE_AXES],
        }

    # cumulative stats we expose per scope (frontend derives per-90 from these)
    _SCOPE_COUNTS = ["games", "minutes", "goals", "assists", "xg", "xa", "shots",
                     "chances_created", "big_chances_created", "dribbles_completed",
                     "duels_won", "tackles", "interceptions", "passes_completed"]
    _SCOPE_RATES = ["pass_accuracy_pct", "duels_won_pct"]   # minutes-weighted %

    # Understat's per-season position code -> readable label. The code is a
    # space-joined set (e.g. "F M S"); the first non-sub token is the primary.
    _UNDERSTAT_POS_LABEL = {"GK": "Goalkeeper", "D": "Defender",
                            "M": "Midfielder", "F": "Forward"}
    # coarse group (player_position_history.coarse_group) -> readable fallback word
    _COARSE_POS_LABEL = {"GK": "Goalkeeper", "DEF": "Defender",
                         "MID": "Midfielder", "FWD": "Forward"}

    def _player_season_position(self, pid: int, season: str) -> str | None:
        """The player's position for ONE season -- how a career position CHANGE
        surfaces (Bellingham CM→AM→ST→AM; Kimmich FB→CM). Prefers the stat-derived
        FINE group from `player_position_history` (ST/W/AM/CM/DM/CB/FB/GK, inferred
        from that season's stat profile -- see pipeline/positions_history.py), and
        falls back to the readable coarse word when that season couldn't be split
        (pre-2020/21, no FotMob defensive stats). Final fallback: Understat's raw
        per-season position string."""
        row = self.con.execute(
            "SELECT fine_position, coarse_group FROM player_position_history "
            "WHERE player_id = ? AND season = ?", [pid, season]).fetchone()
        if row:
            if row[0]:                                  # fine code, e.g. "AM"
                return row[0]
            if row[1]:                                  # coarse -> "Midfielder"
                return self._COARSE_POS_LABEL.get(row[1])
        us = self.con.execute(
            "SELECT position FROM player_season_stats "
            "WHERE player_id = ? AND season = ? AND position IS NOT NULL "
            "ORDER BY minutes DESC LIMIT 1", [pid, season]).fetchone()
        if not us or not us[0]:
            return None
        for tok in str(us[0]).split():
            if tok in ("S", "Sub"):
                continue
            return self._UNDERSTAT_POS_LABEL.get(tok)
        return None

    def _player_stat_scopes(self, pid: int, season: str = FOCUS_SEASON) -> dict:
        """Cumulative totals split into league / ucl / combined, from the canonical
        v_stats_combined (row-stacked by competition). Combined = league + ucl
        (counts summed, % rates minutes-weighted). Scopes with no minutes are
        omitted so the UI can disable them."""
        rate_sql = ", ".join(
            f"SUM({c} * minutes) FILTER (WHERE {c} IS NOT NULL) "
            f"/ NULLIF(SUM(minutes) FILTER (WHERE {c} IS NOT NULL), 0) AS {c}"
            for c in self._SCOPE_RATES)
        df = self.con.execute(f"""
            SELECT CASE WHEN competition = 'UCL' THEN 'ucl' ELSE 'league' END AS scp,
                   {', '.join(f'SUM({c}) AS {c}' for c in self._SCOPE_COUNTS)}, {rate_sql}
            FROM v_stats_combined WHERE player_id = ? AND season = ?
            GROUP BY scp
        """, [pid, season]).df()
        scopes = {}
        for r in df.itertuples():
            d = {c: _r(getattr(r, c), 2) for c in self._SCOPE_COUNTS}
            for c in self._SCOPE_RATES:
                d[c] = _i(getattr(r, c))
            if d["minutes"]:
                scopes[r.scp] = d
        lg, ucl = scopes.get("league"), scopes.get("ucl")
        if lg and ucl:
            comb = {c: round((lg[c] or 0) + (ucl[c] or 0), 2) for c in self._SCOPE_COUNTS}
            tm = (lg["minutes"] or 0) + (ucl["minutes"] or 0)
            for c in self._SCOPE_RATES:
                comb[c] = round(((lg[c] or 0) * (lg["minutes"] or 0)
                                 + (ucl[c] or 0) * (ucl["minutes"] or 0)) / tm) if tm else None
            scopes["combined"] = comb
        elif lg or ucl:
            scopes["combined"] = dict(lg or ucl)
        wc = self._wc_scope_row(pid, season)          # World Cup edition in this season
        if wc:
            scopes["worldcup"] = wc
        return scopes

    def _wc_scope_row(self, pid: int, season: str) -> dict | None:
        """World Cup tournament totals as a stat scope for the profile tiles, for the
        edition that falls in `season` (2526->2026, 2223->2022, 1718->2018). Matched by
        folded name tokens like wc_rating_for (SofaScore-id namespace != ours). Returns
        a scope dict shaped like the league/ucl ones; only xa isn't in the WC feed so it
        stays None -> that tile shows '—'."""
        edition = self._WC_EDITIONS.get(season)
        if edition is None:
            return None
        nm = self.con.execute("SELECT player_name FROM players WHERE player_id=?",
                              [pid]).fetchone()
        if not nm:
            return None
        ours = set(_fold(nm[0]).replace("-", " ").split())
        if not ours:
            return None
        rows = self.con.execute(
            "SELECT player, appearances, minutes, goals, assists, xg, shots, "
            "chances_created, big_chances_created, dribbles_completed, tackles, "
            "interceptions, passes_completed, pass_accuracy_pct, duels_won_pct, duels_won "
            "FROM wc_player_stats WHERE season = ?", [edition]).fetchall()
        best = None
        for row in rows:
            wt = set(_fold(row[0]).replace("-", " ").split())
            if (wt == ours or len(wt & ours) >= 2) and (best is None or (row[2] or 0) > (best[2] or 0)):
                best = row
        if not best or not best[2]:                   # no WC minutes -> omit the scope
            return None
        (_p, apps, mins, goals, ast, xg, shots, cc, bcc, drb,
         tk, intc, pc, pa, dwp, dw) = best
        return {"games": _i(apps), "minutes": _i(mins), "goals": _i(goals),
                "assists": _i(ast), "xg": _r(xg, 2), "xa": None, "shots": _i(shots),
                "chances_created": _i(cc), "big_chances_created": _i(bcc),
                "dribbles_completed": _i(drb), "duels_won": _i(dw),
                "tackles": _i(tk), "interceptions": _i(intc), "passes_completed": _i(pc),
                "pass_accuracy_pct": _i(pa), "duels_won_pct": _i(dwp)}

    def _wc_tile_percentiles(self, pid: int, season: str) -> dict:
        """WC-FIELD percentiles for the profile's stat tiles under the World Cup scope:
        rank the player's WC stats against same-position WC players (that edition, >=90
        min). Per-90 for counts, rates as-is; a stat is ranked only when the player AND
        >=5 peers have it (so pass%/duels% stay honest, no bogus 100ths)."""
        edition = self._WC_EDITIONS.get(season)
        if edition is None:
            return {}
        nm = self.con.execute("SELECT player_name FROM players WHERE player_id=?", [pid]).fetchone()
        if not nm:
            return {}
        ours = set(_fold(nm[0]).replace("-", " ").split())
        if not ours:
            return {}
        df = self.con.execute(
            "SELECT player, position, minutes, goals, assists, xg, chances_created, "
            "big_chances_created, dribbles_completed, duels_won, tackles, interceptions, "
            "pass_accuracy_pct, duels_won_pct FROM wc_player_stats "
            "WHERE season=? AND minutes>=90", [edition]).df()
        if df.empty:
            return {}
        mine_i = None
        for i, p in enumerate(df["player"]):
            wt = set(_fold(p).replace("-", " ").split())
            if (wt == ours or len(wt & ours) >= 2) and (
                    mine_i is None or df["minutes"].iloc[i] > df["minutes"].iloc[mine_i]):
                mine_i = i
        if mine_i is None:
            return {}
        my = df.iloc[mine_i]
        grp = df[df["position"] == my["position"]]
        gmins = grp["minutes"].clip(lower=1)
        per90 = ["goals", "assists", "xg", "chances_created", "big_chances_created",
                 "dribbles_completed", "duels_won", "tackles", "interceptions"]
        out = {}
        for k in per90 + ["pass_accuracy_pct", "duels_won_pct"]:
            raw = pd.to_numeric(grp[k], errors="coerce")
            myv = pd.to_numeric(pd.Series([my[k]]), errors="coerce").iloc[0]
            have = raw.notna()
            if pd.isna(myv) or have.sum() < 5:
                continue
            if k in per90:
                vals = raw[have] / gmins[have] * 90
                mine = myv / max(my["minutes"], 1) * 90
            else:
                vals, mine = raw[have], myv
            out[k] = int(round((vals <= mine).mean() * 100))
        return out

    # per-stat percentile vs same-position peers (per-90 basis; rates as-is), so the
    # tiles can show where a player sits — peak of the stat = 100th percentile.
    def _progressive_stats(self, pid: int, season: str) -> dict:
        """Progressive passes & carries (per-90 + position percentile) from the
        datamb/Wyscout set. Read straight from player_wyscout joined to player_id via
        the datamb-name crosswalk the profile tables carry -- so it works for EVERY
        position (player_profile_metrics keeps only each line's SWOT metrics, so it
        misses progressive passing for e.g. strikers). FOCUS_SEASON, top-5 domestic."""
        g = self.con.execute("SELECT position_group FROM players WHERE player_id=?", [pid]).fetchone()
        if not g or not g[0]:
            return {}
        df = self.con.execute("""
            WITH xwalk AS (SELECT DISTINCT player_id, player
                           FROM player_profile_metrics WHERE season = ?)
            SELECT x.player_id, pl.position_group AS grp,
                   w.progressive_passes_per_90 AS pp, w.progressive_carries_per_90 AS pc
            FROM player_wyscout w
            JOIN xwalk x ON x.player = w.player
            JOIN players pl ON pl.player_id = x.player_id
            WHERE w.season = ?
        """, [season, season]).df()
        if pid not in set(df["player_id"]):
            return {}
        grp = df.loc[df["player_id"] == pid, "grp"].iloc[0]
        peers = df[df["grp"] == grp]
        out = {}
        for key, col in (("progressive_passes", "pp"), ("progressive_carries", "pc")):
            v = df.loc[df["player_id"] == pid, col].iloc[0]
            if pd.isna(v):
                continue
            out[key] = {"per90": _r(v, 2)}
            c = peers[col].dropna()
            if len(c) >= 5:                          # percentile within position line
                out[key]["pct"] = int(round((c <= v).mean() * 100))
        return out

    # Minutes a player needs in a scope before he is ranked in it, and before his own
    # number is ranked against it. A UCL campaign is a fraction of a league season --
    # eight league-phase matches now, six group games before -- so the league bar would
    # empty the UCL field of everyone but the finalists.
    _TILE_MIN_MINUTES = {"league": 600, "combined": 600, "ucl": 270}
    _TILE_PER90 = ["goals", "assists", "xg", "xa", "chances_created", "big_chances_created",
                   "dribbles_completed", "duels_won", "tackles", "interceptions"]
    _TILE_RATE = ["duels_won_pct", "pass_accuracy_pct"]

    def _tile_percentiles(self, pid: int, season: str) -> dict:
        """Per-stat percentile vs same-position peers, ONE MAP PER SCOPE:
        {"league": {...}, "ucl": {...}, "combined": {...}}.

        The tiles switch between League, UCL and Combined numbers, so the bar under them
        has to switch too. It used to be a single map built from the player's DOMESTIC
        season against domestic peers, which meant flipping to UCL changed the number and
        left the percentile alone -- Vinicius Junior's 2022/23 read 64th under both, when
        his league goals/90 is 64th among league forwards and his UCL goals/90 is 85th
        among UCL ones. A percentile that doesn't move when its population does is worse
        than no percentile.

        Peers are ranked on the same aggregation the tiles display (counts summed,
        rates minutes-weighted), so the comparison is like with like, and grouped by the
        player's position IN THAT SEASON rather than the one he plays now -- the profile
        already labels past seasons that way, so a converted winger is not ranked against
        the strikers he later became one of.
        """
        counts = ", ".join(f"SUM(x.{c}) AS {c}" for c in self._TILE_PER90)
        rates = ", ".join(
            f"SUM(x.{c} * x.minutes) FILTER (WHERE x.{c} IS NOT NULL) "
            f"/ NULLIF(SUM(x.minutes) FILTER (WHERE x.{c} IS NOT NULL), 0) AS {c}"
            for c in self._TILE_RATE)
        # His position for THIS season (stat-derived history, falling back to the standing
        # group where that season was never split). Resolved first and passed in, so the
        # field query filters on it instead of materialising every position's rows.
        pos = self.con.execute("""
            SELECT COALESCE(h.coarse_group, p.position_group)
            FROM players p
            LEFT JOIN player_position_history h ON h.player_id = p.player_id AND h.season = ?
            WHERE p.player_id = ?
        """, [season, pid]).fetchone()
        if not pos or not pos[0]:
            return {}
        # The field is the same for every player of a position in a season, and the tables
        # behind it (Understat + FotMob enrichment + UCL) only change on a pipeline
        # rebuild, which comes with a restart -- so it is computed once per (season,
        # position) rather than on every profile view. A rebuild while the server is up
        # would leave this stale until it restarts.
        ck = (season, pos[0])
        if ck in _TILE_FIELD:
            df = _TILE_FIELD[ck]
        else:
            df = self._tile_field(season, pos[0], counts, rates)
            if len(_TILE_FIELD) < 256:                # bounded: 12 seasons x a few groups
                _TILE_FIELD[ck] = df
        if df.empty or pid not in set(df["player_id"]):
            return {}
        out = {}
        for scope in ("league", "ucl", "combined"):
            pct = self._tile_scope_pct(df[df["scp"] == scope], pid,
                                       self._TILE_MIN_MINUTES[scope])
            if pct:
                out[scope] = pct
        return out

    def _tile_field(self, season, pos, counts, rates):
        """Every player of one position in one season, aggregated per scope: league and
        UCL by competition, then the same rows again ungrouped for combined."""
        return self.con.execute(f"""
            WITH x AS (
                SELECT s.player_id, s.minutes, {', '.join('s.' + c for c in self._TILE_PER90)},
                       {', '.join('s.' + c for c in self._TILE_RATE)},
                       CASE WHEN s.competition = 'UCL' THEN 'ucl' ELSE 'league' END AS scp
                FROM v_stats_combined s
                JOIN players p USING(player_id)
                LEFT JOIN player_position_history h
                       ON h.player_id = s.player_id AND h.season = s.season
                WHERE s.season = ? AND COALESCE(h.coarse_group, p.position_group) = ?
            )
            SELECT x.scp, x.player_id, SUM(x.minutes) AS minutes, {counts}, {rates}
            FROM x GROUP BY 1, 2
            UNION ALL
            SELECT 'combined', x.player_id, SUM(x.minutes) AS minutes, {counts}, {rates}
            FROM x GROUP BY 2
        """, [season, pos]).df()

    def _tile_scope_pct(self, grp, pid: int, min_minutes: int) -> dict:
        """Rank one player against a scope's field. His own row is kept even when he is
        under the bar -- the bar is there to keep a 90-minute cameo out of the FIELD, not
        to refuse him a percentile."""
        if grp is None or grp.empty or pid not in set(grp["player_id"]):
            return {}
        field = grp[(grp["minutes"] >= min_minutes) | (grp["player_id"] == pid)]
        if len(field) < 5:
            return {}
        field = field.reset_index(drop=True)
        me_i = field.index[field["player_id"] == pid][0]
        mins = field["minutes"].clip(lower=1)
        out = {}
        for k in self._TILE_PER90 + self._TILE_RATE:
            raw = pd.to_numeric(field[k], errors="coerce")
            my = raw.loc[me_i]
            have = raw.notna()
            # A stat that isn't tracked for this season (e.g. duels before 2025/26 are
            # NULL for everyone) must NOT be ranked: the old code fillna(0)'d it, so
            # every player tied at 0 and scored a bogus 100th percentile. Only rank
            # when the player AND enough peers actually have the stat; otherwise omit
            # it so the tile simply drops its percentile bar (pctBar handles null).
            if pd.isna(my) or have.sum() < 5:
                continue
            if k in self._TILE_RATE:
                vals, myv = raw[have], my
            else:                                          # per-90
                vals = raw[have] / mins[have] * 90
                myv = my / mins.loc[me_i] * 90
            # A field with one value in it cannot rank anyone: every goalkeeper has
            # scored the same zero goals, and "<= mine" made all of them the 100th
            # percentile for it. No spread, no bar.
            if vals.nunique() <= 1:
                continue
            out[k] = int(round((vals <= myv).mean() * 100))
        return out

    def web_player(self, name: str, career_stat: str = "xa",
                   season: str | None = None) -> dict:
        prof = self.player_profile(name)
        if not prof:
            return {}
        pid = self.find_player_id(name)
        # The season selector drives the parts that have real history (stat tiles,
        # cumulative scopes, avg match rating, the common-metric League/UCL gauges).
        # The datamb analysis (composite rating, radar, SWOT, archetype, heatmap,
        # signature actions, market value) only exists for FOCUS_SEASON and stays
        # pinned there -- see web_player's `pinned_season`/`is_current` outputs.
        seasons_avail = [r[0] for r in self.con.execute(
            "SELECT DISTINCT season FROM v_stats_combined_player "
            "WHERE player_id = ? ORDER BY season DESC", [pid]).fetchall()]
        if season not in seasons_avail:
            season = FOCUS_SEASON if FOCUS_SEASON in seasons_avail else (
                seasons_avail[0] if seasons_avail else FOCUS_SEASON)
        # selected-season tiles from the COMBINED (domestic + UCL) per-player view
        t = self.con.execute("""
            SELECT games, goals, assists, xg, xa, chances_created, big_chances_created,
                   dribbles_completed, minutes, pass_accuracy_pct, rating
            FROM v_stats_combined_player WHERE player_id = ? AND season = ?
        """, [pid, season]).fetchone()
        avg_rating = _r(t[10], 2) if t and t[10] is not None else None   # FotMob/SofaScore
        # League + UCL ratings (common-metric, comparable; see rate_combined.py)
        cr = self.con.execute("""
            SELECT scope, rating, classification, percentile, minutes
            FROM player_ratings_combined WHERE player_id = ? AND season = ?
        """, [pid, season]).df()
        ratings = {r.scope: {"rating": _i(r.rating), "classification": r.classification,
                             "percentile": _i(r.percentile), "minutes": _i(r.minutes)}
                   for r in cr.itertuples()}
        # World Cup rating, when the selected season is a WC year and the player
        # actually featured (same gauge treatment as League / UCL).
        wc = self.wc_rating_for(pid, season)
        if wc:
            ratings["worldcup"] = {"rating": wc["rating"], "classification": wc["classification"],
                                   "apps": wc["apps"], "minutes": wc["minutes"]}
        tiles = {}
        if t:
            mins = t[8] or 0
            p90 = lambda v: _r((v or 0) / mins * 90, 2) if mins else None  # noqa: E731
            tiles = {"apps": _i(t[0]),            # Apps stays a count
                     "goals": p90(t[1]), "assists": p90(t[2]),
                     "xg": p90(t[3]), "xa": p90(t[4]),
                     "chances_created": p90(t[5]), "big_chances_created": p90(t[6]),
                     "dribbles_per90": p90(t[7]),
                     "pass_accuracy": _i(t[9])}    # already a %
        # Radar + SWOT. Current season = the full datamb analysis (6 axes, vs same
        # position). Past 2020/21+ seasons = the reduced per-season radar/SWOT
        # (player_radar_hist / player_swot_hist, 5 axes, Understat+FotMob only --
        # see pipeline/profile_history.py). Older seasons have neither. hist_level
        # tells the UI which (current | reduced | none).
        _AXIS_ORDER = ["Finishing", "Chance Creation", "Dribbling", "Passing", "Defending"]
        if season == FOCUS_SEASON:
            pm = self.con.execute(
                "SELECT metric_label, percentile FROM player_radar_metrics WHERE player_id = ?",
                [pid]).df()
            pcts = dict(zip(pm.metric_label, pm.percentile))
            radar = [{"axis": a, "value": v} for a, v in self._radar_values(pcts)]
            strengths = _split(prof["strengths"])
            weaknesses = _split(prof["weaknesses"])
            areas = _split(prof["areas_of_improvement"])
            hist_level = "current"
        else:
            rh = self.con.execute(
                "SELECT axis, value FROM player_radar_hist WHERE player_id=? AND season=?",
                [pid, season]).df()
            if len(rh):
                rmap = dict(zip(rh.axis, rh.value))
                radar = [{"axis": a, "value": _i(rmap[a])} for a in _AXIS_ORDER if a in rmap]
                sw = self.con.execute(
                    "SELECT strengths, weaknesses, areas_of_improvement "
                    "FROM player_swot_hist WHERE player_id=? AND season=?", [pid, season]).fetchone()
                strengths = _split(sw[0]) if sw else []
                weaknesses = _split(sw[1]) if sw else []
                areas = _split(sw[2]) if sw else []
                hist_level = "reduced"
            else:
                radar, strengths, weaknesses, areas = [], [], [], []
                hist_level = "none"
        # career progression of one stat; team = the SELECTED season's club (so a
        # past-season view shows the right crest, e.g. Bellingham at Dortmund), not
        # just the latest team.
        prog = self.player_progression(name, stats=[career_stat])
        if career_stat not in prog.columns:        # unknown stat -> refetch with a safe default
            career_stat = "ga_per90"
            prog = self.player_progression(name, stats=[career_stat])
        career = [{"season": _fmt_season(r.season), "value": _r(getattr(r, career_stat))}
                  for r in prog.itertuples() if pd.notna(getattr(r, career_stat))]
        team = None
        if len(prog):
            match = prog[prog["season"] == season]
            team = (match.iloc[-1]["team"] if len(match) else prog.iloc[-1]["team"])
        # rank-in-group + percentile (player_ratings_v2 is datamb-name keyed; bridge
        # via player_profile_metrics which carries both datamb name and player_id)
        ctx = self.con.execute("""
            SELECT r.rank_in_group, r.percentile FROM player_ratings_v2 r
            JOIN (SELECT DISTINCT player, player_id FROM player_profile_metrics) m
              ON m.player = r.player AND r.position_group = (
                 SELECT position_group FROM player_profile_metrics WHERE player_id = ? LIMIT 1)
            WHERE m.player_id = ? LIMIT 1
        """, [pid, pid]).fetchone()
        bio = self.con.execute(
            "SELECT nationality, country_code, date_of_birth, fotmob_age "
            "FROM player_bio WHERE player_id=?", [pid]).fetchone()
        fpid = self.con.execute(
            "SELECT max(fotmob_player_id) FROM player_enrichment "
            "WHERE player_id=? AND fotmob_player_id IS NOT NULL", [pid]).fetchone()
        # Position for the SELECTED season. Current season keeps the rich FotMob
        # detail (LW/RW/CAM); past seasons use Understat's per-season position so a
        # career position change actually shows (coarse, but real for all players).
        if season == FOCUS_SEASON:
            pos_group, pos_detail = prof["position_group"], prof.get("detailed_position")
        else:
            season_pos = self._player_season_position(pid, season)
            pos_group, pos_detail = (season_pos or prof["position_group"]), None
        # Progressive passing/carrying (datamb/Wyscout per-90 vs position). Only the
        # current season has the datamb dataset, and it isn't split by competition, so
        # inject the same per-90 value into every stat scope + the tile percentiles so
        # the Per-90 grid can render it like any other rate stat. The World Cup scope
        # is EXCLUDED -- datamb is domestic-league data, not tournament data.
        scopes = self._player_stat_scopes(pid, season)
        tile_pct = self._tile_percentiles(pid, season)
        if season == FOCUS_SEASON and scopes:
            for key, info in self._progressive_stats(pid, season).items():
                per90 = info["per90"]
                for sck, sc in scopes.items():    # one datamb dataset -> same rate in all scopes
                    if sck == "worldcup":         # domestic data doesn't apply to the WC
                        continue
                    sc[key] = per90               # per-90 value (Per-90 grid)
                    if sc.get("minutes"):         # season total (Total grid) = rate x minutes/90
                        sc[key + "_total"] = round(per90 * sc["minutes"] / 90)
                    # The datamb set is domestic and isn't split by competition, so the
                    # same rate -- and the same domestic percentile -- goes in every
                    # scope that shows it. The tooltip says which field it ranks against.
                    if "pct" in info:
                        tile_pct.setdefault(sck, {})
                        tile_pct[sck][key] = tile_pct[sck][key + "_total"] = info["pct"]
        return {
            "name": prof["player_name"], "team": team,
            "photo": self.player_photo(fpid[0] if fpid else None),
            "photo_credit": self._photo_credit(pid),  # CC attribution if licensed
            "team_logo": self.team_logo(team),
            "position_group": pos_group,           # per-season (Understat) for past seasons
            "detailed_position": pos_detail,        # LW/RW/LB/RB/CAM/... (FotMob, current season only)
            "age": self._player_age(prof["player_name"]) or (bio[3] if bio else None),
            "nationality": bio[0] if bio else None,
            "country_code": bio[1] if bio else None,
            "date_of_birth": bio[2] if bio else None,
            "market_value_eur": prof["market_value_eur"],
            "rating": prof["rating"], "classification": prof["classification"],
            "rank_in_group": ctx[0] if ctx else None,
            "percentile": round(ctx[1]) if ctx and ctx[1] is not None else None,
            "ratings": ratings,  # {"league": {...}, "ucl": {...}}  common-metric
            "avg_rating": avg_rating,  # FotMob/SofaScore average match rating (all comps)
            "tiles": tiles, "radar": radar,
            # per-stat percentile vs position peers, ONE MAP PER SCOPE, so the bar under
            # a tile ranks against the competition whose number the tile is showing
            "tile_pct": tile_pct,
            "wc_tile_pct": self._wc_tile_percentiles(pid, season),  # vs the WC field (WC scope)
            "stats_scopes": scopes,          # league/ucl/combined cumulative
            "archetype": self._player_archetype(pid),  # use case 10: role + traits + similar
            "signature_actions": self._player_tendencies(pid),  # use case 9
            "heatmap": self._player_heatmap(pid, season),  # season-aware (past seasons scraped from SofaScore)
            "worldcups": self._player_worldcups(pid),  # WC record across editions (card)
            "career_stat": career_stat, "career": career,
            # season selector: which season the stats/gauges above reflect, the
            # available list, and whether the pinned analysis matches it
            "season": season,
            "seasons": [{"value": s, "label": _fmt_season(s)} for s in seasons_avail],
            "is_current": season == FOCUS_SEASON,
            "pinned_season": _fmt_season(FOCUS_SEASON),
            "hist_level": hist_level,   # current | reduced | none (radar/SWOT/heatmap)
            "strengths": strengths,
            "weaknesses": weaknesses,
            "areas_of_improvement": areas,
        }

    # nice labels for the comparison table (use case 5)
    COMPARE_STAT_LABELS = {
        "games": "Apps", "minutes": "Minutes", "goals": "Goals", "assists": "Assists",
        "ga_per90": "G+A per 90", "chances_created": "Chances Created",
        "big_chances_created": "Big Chances Created", "dribbles_completed": "Dribbles",
        "dribble_success_pct": "Dribble %", "passes_completed": "Passes Completed",
        "pass_accuracy_pct": "Pass Accuracy %", "duels_won": "Duels Won",
        "duels_won_pct": "Duels Won %", "tackles": "Tackles",
        "interceptions": "Interceptions", "recoveries": "Recoveries",
        "xg": "xG", "xa": "xA",
    }

    def web_compare(self, names: list[str], stats: list[str] | None = None,
                    season_tags: list[str] | None = None,
                    scope: str = "combined") -> dict:
        """Use case 5: side-by-side comparison of 2-3 players on user-chosen
        stats (or the position-based defaults from use case 4), plus an
        overlaid percentile radar.

        `scope` picks the competition the stats are drawn from -- 'league', 'ucl',
        'combined' (all categories, the default), or 'worldcup' -- using the SAME
        per-competition scope engine as the profile tiles (_player_stat_scopes), so
        Compare and the profile agree. A player with no data for the chosen scope in
        their season shows "—" across the board.

        Each player can be pinned to a DIFFERENT season -- e.g. peak-Pedri 24/25
        vs a young Bellingham 20/21. Requested seasons arrive as index-tagged
        params ("<i>:<code>", where i is the position in `names`) so a blank /
        omitted one just falls back to the player's latest season, without the
        array-alignment fragility of parallel lists (parse_qs drops blanks)."""
        # index -> requested season code, from the "<i>:<code>" tags
        req = {}
        for t in (season_tags or []):
            k, sep, v = str(t).partition(":")
            if sep and k.isdigit() and v:
                req[int(k)] = v

        # de-dupe (case-insensitive), preserve order + original index, cap at 3
        seen, pairs = set(), []
        for i, n in enumerate(names or []):
            n = (n or "").strip()
            if not n or n.lower() in seen:
                continue
            seen.add(n.lower())
            pairs.append((n, req.get(i)))

        # resolve each to a player id + the season to actually use for it
        resolved = []
        for name, want in pairs[:3]:
            pid = self.find_player_id(name)               # resolution is season-independent
            if pid is None:
                continue
            avail = [r[0] for r in self.con.execute(
                "SELECT DISTINCT season FROM v_player_season_stats "
                "WHERE player_id=? ORDER BY season DESC", [pid]).fetchall()]
            if not avail:
                continue
            sel = want if want in avail else (
                FOCUS_SEASON if FOCUS_SEASON in avail else avail[0])
            resolved.append({"query": name, "pid": pid, "season": sel, "avail": avail})

        if len(resolved) < 2:
            return {"players": [], "stats": [], "radar_axes": [], "season": None}

        groups = {}
        for r in resolved:
            g = self.con.execute(
                "SELECT position_group FROM players WHERE player_id=?", [r["pid"]]).fetchone()
            groups[r["pid"]] = g[0] if g else None
        scope = scope if scope in ("league", "ucl", "combined", "worldcup") else "combined"
        # stat vocabulary = the per-competition scope keys (+ derived ga_per90); this
        # replaces the old v_player_season_stats columns so Compare matches the profile
        # tiles. xa/duels_won-count aren't in the WC feed -> "—" under the WC scope.
        allowed = set(self._SCOPE_COUNTS) | set(self._SCOPE_RATES) | {"ga_per90"}  # xa "—" under WC
        distinct_groups = {g for g in groups.values() if g}
        if len(distinct_groups) == 1:
            default_cols = self.DEFAULT_PROGRESSION_STATS.get(next(iter(distinct_groups)), [])
        else:                                   # mixed positions -> generic set
            default_cols = ["ga_per90", "chances_created", "tackles", "duels_won"]
        base = ["games", "minutes", "goals", "assists"]
        # default position stats always show; user-chosen stats are ADDED on top
        defaults, added = [], []
        for c in default_cols:
            if c in allowed and c not in base and c not in defaults:
                defaults.append(c)
        for c in (stats or []):
            if c in allowed and c not in base and c not in defaults and c not in added:
                added.append(c)
        added_set = set(added)
        statcols = base + defaults + added

        def radar_for(pid, season):
            """(radar values, season the radar actually came from). Percentiles are
            only computed for the current radar season (the Wyscout source is a
            current-season snapshot), so an earlier pick falls back to the latest
            radar season -- we report which one so the UI can label it honestly
            instead of implying the shape is from the selected season."""
            pm = self.con.execute(
                "SELECT metric_label, percentile FROM player_radar_metrics "
                "WHERE player_id=? AND season=?", [pid, season]).df()
            rsea = season
            if pm.empty:                                  # no radar for that season -> latest
                row = self.con.execute(
                    "SELECT max(season) FROM player_radar_metrics WHERE player_id=?",
                    [pid]).fetchone()
                rsea = row[0] if row and row[0] else None
                if rsea:
                    pm = self.con.execute(
                        "SELECT metric_label, percentile FROM player_radar_metrics "
                        "WHERE player_id=? AND season=?", [pid, rsea]).df()
            pcts = dict(zip(pm.metric_label, pm.percentile))
            return [v for _, v in self._radar_values(pcts)], rsea

        players, statvals = [], {}
        for k, rp in enumerate(resolved):
            pid, season = rp["pid"], rp["season"]
            # rating = combined-League (datamb fallback), same as directory/profile,
            # for THIS player's chosen season
            h = self.con.execute("""
                SELECT pl.player_name, f.team,
                       COALESCE(f.detailed_position, f.main_position) AS position,
                       COALESCE(c.rating, f.rating) AS rating,
                       COALESCE(c.classification, f.classification) AS classification,
                       f.market_value_eur
                FROM v_player_profile_full f JOIN players pl ON pl.player_id = f.player_id
                LEFT JOIN player_ratings_combined c
                       ON c.player_id = f.player_id AND c.scope = 'league' AND c.season = ?
                WHERE f.player_id = ?
            """, [season, pid]).fetchone()
            bio = self.con.execute(
                "SELECT country_code FROM player_bio WHERE player_id=?", [pid]).fetchone()
            fpid = self.con.execute(
                "SELECT max(fotmob_player_id) FROM player_enrichment "
                "WHERE player_id=? AND fotmob_player_id IS NOT NULL", [pid]).fetchone()
            sv = dict(self._player_stat_scopes(pid, season).get(scope) or {})
            if sv.get("minutes"):                         # derive G+A per 90 for this scope
                m = sv["minutes"]
                sv["ga_per90"] = round(((sv.get("goals") or 0) + (sv.get("assists") or 0)) / m * 90, 2)
            statvals[k] = sv
            # team is season-specific (transfers), so prefer the chosen season's team
            # (most minutes) over the profile's current club
            trow = self.con.execute(
                "SELECT team FROM v_player_season_stats WHERE player_id=? AND season=? "
                "AND team IS NOT NULL ORDER BY minutes DESC LIMIT 1",
                [pid, season]).fetchone()
            team = (trow[0] if trow else None) or (h[1] if h else None)
            radar_vals, radar_season = radar_for(pid, season)
            players.append({
                "name": h[0] if h else rp["query"], "query": rp["query"], "team": team,
                "position": h[2] if h else groups.get(pid),
                "rating": _i(h[3]) if h else None,
                "classification": h[4] if h else None,
                "market_value_eur": None if (not h or pd.isna(h[5])) else float(h[5]),
                "country_code": bio[0] if bio else None,
                "photo": self.player_photo(fpid[0] if fpid else None),
                "team_logo": self.team_logo(team),
                "radar": radar_vals,
                # the season the radar shape actually reflects (may differ from the
                # stat season -- percentiles only exist for the latest radar season)
                "radar_season": radar_season,
                "radar_season_label": _fmt_season(radar_season) if radar_season else None,
                "radar_is_current": radar_season != season,
                "season": season,
                "season_label": _fmt_season(season),
                "seasons": [{"value": s, "label": _fmt_season(s)} for s in rp["avail"]],
            })

        stat_rows = []
        for c in statcols:
            raw = [statvals[k].get(c) for k in range(len(resolved))]
            vals = [None if (v is None or pd.isna(v)) else round(float(v), 2) for v in raw]
            numeric = [v for v in vals if v is not None]
            best = max(numeric) if numeric else None
            best_index = next((i for i, v in enumerate(vals) if v == best), None) \
                if best is not None else None
            stat_rows.append({
                "key": c,
                "label": self.COMPARE_STAT_LABELS.get(c, c.replace("_", " ").title()),
                "values": vals, "best_index": best_index,
                "added": c in added_set})

        # a single top-level season label only when every player shares one season
        seasons_used = {rp["season"] for rp in resolved}
        return {"players": players, "stats": stat_rows, "scope": scope,
                "radar_axes": list(self.RADAR_AXES.keys()),
                "season": _fmt_season(next(iter(seasons_used))) if len(seasons_used) == 1 else None}

    # ---------------------------------------------------------------- use case 8
    #  "Find the Next X" -- match a legend's style template against current players
    #  in the same percentile space the live similarity engine uses.
    def web_legends(self) -> list[dict]:
        return legend_list()

    def web_find_next(self, legend_key: str, limit: int = 12,
                      min_minutes: int = 900, min_rating: int = 60) -> dict:
        import math
        legend = LEGENDS.get(legend_key)
        if not legend:
            return {"available": False, "error": "Unknown legend."}
        axes = list(self.RADAR_AXES)
        groups = legend["match_groups"]
        ph = ",".join(["?"] * len(groups))
        df = self.con.execute(
            f"SELECT player_id, metric_label, percentile FROM player_radar_metrics "
            f"WHERE season = ? AND position_group IN ({ph})",
            [FOCUS_SEASON, *groups]).df()
        if df.empty:
            return {"available": False, "error": "No comparison pool for this position."}
        label2axis = {}
        for ax, keys in self.RADAR_AXES.items():
            for k in keys:
                label2axis[k[0] if isinstance(k, tuple) else k] = ax
        df["axis"] = df["metric_label"].map(label2axis)
        df = df.dropna(subset=["axis"])
        piv = df.groupby(["player_id", "axis"])["percentile"].mean().unstack("axis")
        for ax in axes:
            if ax not in piv.columns:
                piv[ax] = 50.0
        piv = piv[axes].fillna(50.0)

        L = [float(legend["axes"][ax]) for ax in axes]
        Lc = [v - 50 for v in L]
        Ln = math.sqrt(sum(v * v for v in Lc)) or 1e-9

        ids = [int(i) for i in piv.index]
        mph = ",".join(["?"] * len(ids))
        meta = self.con.execute(
            f"""SELECT c.player_id, pl.player_name, c.rating, c.minutes,
                       COALESCE(f.detailed_position, f.main_position, c.position_group) pos,
                       f.team, f.market_value_eur AS mv, pe.fpid
                FROM player_ratings_combined c JOIN players pl USING(player_id)
                LEFT JOIN v_player_profile_full f ON f.player_id = c.player_id
                LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                           WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                       ON pe.player_id = c.player_id
                WHERE c.scope='league' AND c.season=? AND c.minutes>=? AND c.rating>=?
                  AND c.player_id IN ({mph})""",
            [FOCUS_SEASON, min_minutes, min_rating, *ids]).df()
        metam = {int(r.player_id): r for r in meta.itertuples()}

        rows = []
        for pid, row in piv.iterrows():
            pid = int(pid)
            m = metam.get(pid)
            if m is None:
                continue
            P = [float(row[ax]) for ax in axes]
            Pc = [v - 50 for v in P]
            Pn = math.sqrt(sum(v * v for v in Pc))
            if Pn == 0:
                continue
            cos = sum(a * b for a, b in zip(Lc, Pc)) / (Ln * Pn)
            rows.append({"player": m.player_name, "team": m.team, "position": m.pos,
                         "rating": _i(m.rating),
                         "market_value_eur": None if pd.isna(m.mv) else float(m.mv),
                         "similarity": int(round(max(0.0, cos) * 100)), "_cos": cos,
                         "photo": self.player_photo(m.fpid),
                         "axes": [{"axis": ax, "value": _i(P[i])} for i, ax in enumerate(axes)]})
        rows.sort(key=lambda r: -r["_cos"])
        for r in rows:
            r.pop("_cos", None)
        return {"available": True,
                "legend": {"key": legend_key, "name": legend["name"], "club": legend["club"],
                           "era": legend["era"], "pos": legend["pos"], "blurb": legend["blurb"],
                           "axes": [{"axis": ax, "value": legend["axes"][ax]} for ax in axes]},
                "axes_order": axes, "groups": groups, "matches": rows[:limit]}

    # ---------------------------------------------------------------- use case 4
    #  "Best XI on a Budget" -- maximise total rating across a formation's positional
    #  slots subject to a market-value cap (a sporting-director simulator).
    # Each formation is an ordered list of OUTFIELD lines, back -> front, every line
    # listing its slot categories left -> right (GK is implicit at the back). This
    # drives BOTH selection counts and the pitch layout, so wingers sit on the right
    # line per formation (front line in 4-3-3, midfield line in 4-4-2, etc.).
    _FORMATIONS = {
        "4-3-3":   [["FB", "CB", "CB", "FB"], ["MID", "MID", "MID"], ["W", "ST", "W"]],
        "4-4-2":   [["FB", "CB", "CB", "FB"], ["W", "MID", "MID", "W"], ["ST", "ST"]],
        "4-2-3-1": [["FB", "CB", "CB", "FB"], ["MID", "MID"], ["W", "MID", "W"], ["ST"]],
        "4-1-4-1": [["FB", "CB", "CB", "FB"], ["MID"], ["W", "MID", "MID", "W"], ["ST"]],
        "4-3-1-2": [["FB", "CB", "CB", "FB"], ["MID", "MID", "MID"], ["MID"], ["ST", "ST"]],
        "4-5-1":   [["FB", "CB", "CB", "FB"], ["W", "MID", "MID", "MID", "W"], ["ST"]],
        "3-5-2":   [["CB", "CB", "CB"], ["FB", "MID", "MID", "MID", "FB"], ["ST", "ST"]],
        "3-4-3":   [["CB", "CB", "CB"], ["FB", "MID", "MID", "FB"], ["W", "ST", "W"]],
        "3-4-2-1": [["CB", "CB", "CB"], ["FB", "MID", "MID", "FB"], ["W", "W"], ["ST"]],
        "5-3-2":   [["FB", "CB", "CB", "CB", "FB"], ["MID", "MID", "MID"], ["ST", "ST"]],
    }
    _GRP2CAT = {"GK": "GK", "CB": "CB", "FB": "FB", "DM": "MID", "CM": "MID",
                "AM": "MID", "W": "W", "ST": "ST"}
    _CAT_ORDER = ["GK", "CB", "FB", "MID", "W", "ST"]
    _CAT_LABEL = {"GK": "Goalkeeper", "CB": "Centre-Back", "FB": "Full-Back",
                  "MID": "Midfield", "W": "Winger", "ST": "Striker"}

    def web_best_xi(self, budget_m: float, formation: str = "4-3-3",
                    min_minutes: int = 600) -> dict:
        lines_def = self._FORMATIONS.get(formation)
        if not lines_def:
            return {"available": False, "error": "Unknown formation."}
        form = {"GK": 1}                          # slot counts per category (GK implicit)
        for line in lines_def:
            for cat in line:
                form[cat] = form.get(cat, 0) + 1
        B = min(int(round(budget_m)), 3000)
        if B <= 0:
            return {"available": False, "error": "Budget must be positive."}

        df = self.con.execute(
            """SELECT c.player_id, pl.player_name, c.position_group grp, c.rating,
                      f.team, f.market_value_eur AS mv,
                      COALESCE(f.detailed_position, f.main_position, c.position_group) pos, pe.fpid
               FROM player_ratings_combined c JOIN players pl USING(player_id)
               LEFT JOIN v_player_profile_full f ON f.player_id = c.player_id
               LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                          WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                      ON pe.player_id = c.player_id
               WHERE c.scope='league' AND c.season=? AND c.minutes>=?
                 AND f.market_value_eur IS NOT NULL AND f.market_value_eur > 0""",
            [FOCUS_SEASON, min_minutes]).df()

        pools = {cat: [] for cat in self._CAT_ORDER}
        meta = {}
        for r in df.itertuples():
            cat = self._GRP2CAT.get(r.grp)
            if not cat or form.get(cat, 0) == 0:
                continue
            cost = max(1, int(round(r.mv / 1e6)))
            pid = int(r.player_id)
            pools[cat].append((pid, int(r.rating), cost))
            meta[pid] = {"player": r.player_name, "team": r.team, "position": r.pos,
                         "rating": int(r.rating), "value_eur": float(r.mv), "value_m": cost,
                         "cat": cat, "photo": self.player_photo(r.fpid)}

        cats = [c for c in self._CAT_ORDER if form.get(c, 0) > 0]
        min_cost, short = 0, []
        for cat in cats:
            k = form[cat]
            cheap = sorted(pools[cat], key=lambda x: x[2])
            if len(cheap) < k:
                short.append(cat)
            else:
                min_cost += sum(c for _, _, c in cheap[:k])
        if short:
            return {"available": False,
                    "error": "Not enough priced players for: "
                             + ", ".join(self._CAT_LABEL[c] for c in short) + "."}
        if B < min_cost:
            return {"available": False, "min_budget": min_cost,
                    "error": f"Budget too low — this formation needs at least "
                             f"€{min_cost}M of priced players."}

        # Spending beyond the cost of the unconstrained best XI can't raise the
        # rating, so cap the DP budget there -> stays fast even at huge budgets.
        maxneeded = sum(sum(c for _, _, c in sorted(pools[cat], key=lambda x: -x[1])[:form[cat]])
                        for cat in cats)
        budget = B               # what the user actually set (for display)
        B = min(B, maxneeded)    # DP bound
        NEG = -1

        def cat_dp(pool, k):
            rating = [[NEG] * (B + 1) for _ in range(k + 1)]
            picks = [[None] * (B + 1) for _ in range(k + 1)]
            rating[0][0] = 0
            picks[0][0] = ()
            for pid, rt, cost in pool:
                if cost > B:
                    continue
                for j in range(k - 1, -1, -1):
                    rj, pj, rj1, pj1 = rating[j], picks[j], rating[j + 1], picks[j + 1]
                    for c in range(B - cost, -1, -1):
                        if rj[c] == NEG:
                            continue
                        v = rj[c] + rt
                        c2 = c + cost
                        if v > rj1[c2]:
                            rj1[c2] = v
                            pj1[c2] = pj[c] + (pid,)
            return rating[k], picks[k]

        comb_r = [NEG] * (B + 1)
        comb_p = [None] * (B + 1)
        comb_r[0] = 0
        comb_p[0] = ()
        for cat in cats:
            Ec, Ep = cat_dp(pools[cat], form[cat])
            nr = [NEG] * (B + 1)
            npk = [None] * (B + 1)
            for a in range(B + 1):
                if comb_r[a] == NEG:
                    continue
                ra, pa = comb_r[a], comb_p[a]
                for b in range(B - a + 1):
                    if Ec[b] == NEG:
                        continue
                    v = ra + Ec[b]
                    c = a + b
                    if v > nr[c]:
                        nr[c] = v
                        npk[c] = pa + Ep[b]
            comb_r, comb_p = nr, npk

        best_c = max(range(B + 1), key=lambda c: comb_r[c])
        if comb_r[best_c] == NEG or comb_p[best_c] is None:
            return {"available": False,
                    "error": "Could not assemble a valid XI within that budget."}
        xi = [meta[pid] for pid in comb_p[best_c]]
        # group the picked players by category (best first) and lay them out onto the
        # formation's lines, back -> front, so the pitch shape matches the formation.
        by_cat: dict[str, list] = {}
        for pid in comb_p[best_c]:
            by_cat.setdefault(meta[pid]["cat"], []).append(pid)
        for cat in by_cat:
            by_cat[cat].sort(key=lambda pid: -meta[pid]["rating"])
        lines = [[meta[by_cat["GK"].pop()]]]      # GK line at the back
        for line in lines_def:
            row = []
            for cat in line:
                if by_cat.get(cat):
                    p = meta[by_cat[cat].pop()]
                    p["x"] = (len(row) + 0.5) / len(line)   # 0..1 across the pitch
                    row.append(p)
            lines.append(row)
        total = sum(p["rating"] for p in xi)
        spent_m = sum(p["value_m"] for p in xi)
        return {"available": True, "formation": formation, "budget_m": budget,
                "spent_m": spent_m, "remaining_m": budget - spent_m,
                "spent_eur": sum(p["value_eur"] for p in xi),
                "total_rating": total, "avg_rating": round(total / len(xi), 1),
                "min_budget": min_cost,
                "lines": lines,                   # back -> front, GK first
                "xi": xi}

    # ---------------------------------------------------------------- use case 24
    #  Collectible player card -- compact payload the frontend renders to a
    #  shareable/downloadable image (rating, top-5 percentile stats, archetype).
    _AXIS_CODE = {"Chance Creation": "CRE", "Progression": "PRG", "Passing": "PAS",
                  "Finishing": "FIN", "Defending": "DEF", "Dribbling": "DRI"}

    def web_card(self, name: str, season: str | None = None) -> dict:
        p = self.web_player(name, season=season)
        if not p or not p.get("name"):
            return {"available": False, "error": "Player not found."}
        radar = sorted(p.get("radar") or [], key=lambda a: -a["value"])
        stats = [{"code": self._AXIS_CODE.get(a["axis"], a["axis"][:3].upper()),
                  "label": a["axis"], "value": int(a["value"])} for a in radar[:5]]
        ratings = p.get("ratings") or {}
        card_rating = ((ratings.get("league") or {}).get("rating")
                       or (ratings.get("ucl") or {}).get("rating") or p.get("rating"))
        arch = p.get("archetype") or {}
        return {"available": True, "name": p["name"],
                "position": p.get("detailed_position") or p.get("position_group"),
                "team": p.get("team"), "nationality": p.get("nationality"),
                "rating": int(card_rating) if card_rating else None,
                "composite": p.get("rating"), "classification": p.get("classification"),
                "archetype": arch.get("archetype"), "fit": arch.get("fit"),
                "photo": p.get("photo"), "market_value_eur": p.get("market_value_eur"),
                "season": p.get("season"), "stats": stats}

    # ---------------------------------------------------------------- Guess the Rating
    #  A content/game endpoint: hand the client a batch of random outfield players
    #  (decent minutes, recognizable enough that the per-90 line is meaningful) with
    #  their season per-90 stat card and the Atlastra league rating as the hidden
    #  answer. The user reads the line, guesses the rating, and is scored on how
    #  close they land — the points logic lives client-side.
    # SQL selecting the columns a guess card needs, shared by the random game, the
    # seeded daily challenge and the higher-or-lower feed.
    _GUESS_COLS = """SELECT pl.player_name AS nm, c.position_group AS grp, c.rating,
                  v.team, v.games, v.minutes, v.goals, v.assists,
                  v.xg_per90, v.xa_per90, v.shots, v.key_passes, v.dribbles_completed,
                  v.tackles, v.interceptions, v.pass_accuracy_pct, v.duels_won_pct,
                  wy.prog_passes, wy.prog_carries,
                  b.nationality, b.country_code, b.fotmob_age AS age,
                  (SELECT max(fotmob_player_id) FROM player_enrichment e
                   WHERE e.player_id = c.player_id AND e.fotmob_player_id IS NOT NULL) AS fpid
           FROM player_ratings_combined c
           JOIN v_player_season_stats v ON v.player_id = c.player_id AND v.season = c.season
           JOIN players pl ON pl.player_id = c.player_id
           LEFT JOIN player_bio b ON b.player_id = c.player_id
           LEFT JOIN (   -- progressive passing/carrying (datamb/Wyscout) via name crosswalk
               SELECT x.player_id, w.season,
                      max(w.progressive_passes_per_90) AS prog_passes,
                      max(w.progressive_carries_per_90) AS prog_carries
               FROM player_wyscout w
               JOIN (SELECT DISTINCT player_id, player FROM player_profile_metrics) x
                    ON x.player = w.player
               GROUP BY x.player_id, w.season
           ) wy ON wy.player_id = c.player_id AND wy.season = c.season
           WHERE c.scope='league' AND c.season = ? AND c.position_group <> 'GK'
             AND v.minutes >= ? AND c.rating >= ?"""

    def _guess_pool(self, count, min_minutes, min_rating, season, salt=None):
        """Batch of guessable players. With `salt` the order is a deterministic hash
        of player_id+salt (stable across runs/threads, for a shared daily); without
        it, plain random(). DuckDB's threaded random() ignores setseed, so a hash
        ordering is the only reproducible option."""
        if salt is not None:
            order, extra = "hash(CAST(c.player_id AS VARCHAR) || ?)", [salt]
        else:
            order, extra = "random()", []
        return self.con.execute(
            self._GUESS_COLS + f" ORDER BY {order} LIMIT ?",
            [season, min_minutes, min_rating, *extra, count]).df()

    def _guess_card(self, r) -> dict:
        """Build one guess round (stat card + hidden rating answer) from a df row."""
        # `x or 0` is NaN-unsafe (pandas NaN is truthy), so coerce explicitly --
        # a stray NaN would otherwise serialize to an invalid `NaN` JSON token.
        num = lambda v: 0.0 if v is None or pd.isna(v) else float(v)
        mins = num(r.minutes)

        def per90(total):
            return round(num(total) / mins * 90, 2) if mins else 0.0
        pct = lambda v: (str(round(float(v), 1)) + "%") if not pd.isna(v) else "—"
        stats = [
            {"label": "Minutes", "value": int(mins)},
            {"label": "Appearances", "value": int(num(r.games))},
            {"label": "Goals", "value": int(num(r.goals))},
            {"label": "Assists", "value": int(num(r.assists))},
            {"label": "xG / 90", "value": round(num(r.xg_per90), 2)},
            {"label": "xA / 90", "value": round(num(r.xa_per90), 2)},
            {"label": "Shots / 90", "value": per90(r.shots)},
            {"label": "Key passes / 90", "value": per90(r.key_passes)},
            {"label": "Dribbles / 90", "value": per90(r.dribbles_completed)},
            {"label": "Tackles + Int / 90", "value": per90((r.tackles or 0) + (r.interceptions or 0))},
            {"label": "Pass accuracy", "value": pct(r.pass_accuracy_pct)},
            {"label": "Duels won", "value": pct(r.duels_won_pct)},
            # progressive stats are absent for ~30% of the pool (no datamb match) -> None
            {"label": "Prog. passes / 90",
             "value": None if pd.isna(r.prog_passes) else round(float(r.prog_passes), 2)},
            {"label": "Prog. carries / 90",
             "value": None if pd.isna(r.prog_carries) else round(float(r.prog_carries), 2)},
        ]
        return {
            "name": r.nm, "rating": int(r.rating),
            "team": r.team, "team_logo": self.team_logo(r.team),
            "position": self.GROUP_LABELS.get(r.grp, r.grp), "position_code": r.grp,
            "nationality": None if pd.isna(r.nationality) else r.nationality,
            "country_code": None if pd.isna(r.country_code) else r.country_code,
            "age": None if pd.isna(r.age) else int(r.age),
            "photo": self.player_photo(None if pd.isna(r.fpid) else int(r.fpid)),
            "stats": stats,
        }

    def web_guess_rounds(self, count: int = 8, min_minutes: int = 1100,
                         min_rating: int = 66, season: str = FOCUS_SEASON) -> dict:
        df = self._guess_pool(count, min_minutes, min_rating, season)
        if df.empty:
            return {"available": False, "error": "No players available for the game."}
        return {"available": True, "season": season,
                "rounds": [self._guess_card(r) for r in df.itertuples()]}

    def web_daily_challenge(self, date_str: str, rounds: int = 5,
                            season: str = FOCUS_SEASON) -> dict:
        """The same `rounds` players for everyone on a given date (seeded by date),
        biased to recognizable names — the basis of the cross-user leaderboard."""
        df = self._guess_pool(rounds, 1200, 70, season, salt="daily:" + date_str)
        if df.empty:
            return {"available": False, "error": "No players available."}
        return {"available": True, "date": date_str, "season": season, "game": "daily",
                "rounds": [self._guess_card(r) for r in df.itertuples()]}

    # ---------------------------------------------------------------- Guess the Player (Wordle-style)
    #  One mystery player; the client reveals clues one at a time and the user
    #  guesses the name. `date_str` makes it a shared daily puzzle; None = practice.
    def web_player_quiz(self, date_str: str | None = None, season: str = FOCUS_SEASON) -> dict:
        import unicodedata
        # famous-ish pool so the puzzle is guessable; the shared daily skews to bigger
        # names (deterministic hash order by date), practice is a broader random pick.
        if date_str is not None:
            order, extra = "hash(CAST(c.player_id AS VARCHAR) || ?)", ["quiz:" + date_str]
            min_min, min_rt = 1900, 80
        else:
            order, extra = "random()", []
            min_min, min_rt = 1400, 73
        r = self.con.execute(
            self._GUESS_COLS + f" AND v.minutes >= {min_min} AND c.rating >= {min_rt} "
            f"ORDER BY {order} LIMIT 1",
            [season, 0, 0, *extra]).df()
        if r.empty:
            return {"available": False, "error": "No player available."}
        row = next(r.itertuples())
        mins = float(row.minutes or 0)
        ga = int(row.goals or 0) + int(row.assists or 0)
        nat = None if pd.isna(row.nationality) else row.nationality
        cc = None if pd.isna(row.country_code) else row.country_code
        age = None if pd.isna(row.age) else int(row.age)
        # progressive clues — broad first, increasingly specific
        clues = [{"label": "Position", "value": self.GROUP_LABELS.get(row.grp, row.grp)}]
        if nat:
            clues.append({"label": "Nationality", "value": nat, "country_code": cc})
        if age:
            clues.append({"label": "Age", "value": age})
        clues.append({"label": "This season", "value": f"{int(row.games or 0)} apps · {int(mins)} mins"})
        clues.append({"label": "Goals + Assists", "value": f"{int(row.goals or 0)}G {int(row.assists or 0)}A ({ga})"})
        clues.append({"label": "Club", "value": row.team})

        def norm(s):
            s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
            return "".join(ch for ch in s if ch.isalnum() or ch == " ").strip()
        full = norm(row.nm)
        accept = {full, full.replace(" ", "")}
        toks = full.split()
        if len(toks) > 1:
            accept.add(toks[-1])                          # surname
            accept.add(" ".join(toks[-2:]))               # last two names
        return {"available": True, "season": season, "daily": date_str is not None,
                "date": date_str, "game": "quiz",
                "answer": row.nm, "accept": sorted(a for a in accept if a),
                "team": row.team, "team_logo": self.team_logo(row.team),
                "photo": self.player_photo(None if pd.isna(row.fpid) else int(row.fpid)),
                "rating": int(row.rating), "max_guesses": len(clues), "clues": clues}

    # ---------------------------------------------------------------- Draft Battle (Build-a-XI)
    #  Candidate pools (priced, rating-ranked) per formation slot category, plus the
    #  formation's pitch layout. The client drafts an XI within a budget; scoring vs
    #  the optimal XI uses web_best_xi.
    def web_draft_pool(self, formation: str = "4-3-3", min_minutes: int = 600,
                       per_cat: int = 60, season: str = FOCUS_SEASON) -> dict:
        lines_def = self._FORMATIONS.get(formation)
        if not lines_def:
            return {"available": False, "error": "Unknown formation."}
        form = {"GK": 1}
        for line in lines_def:
            for cat in line:
                form[cat] = form.get(cat, 0) + 1
        df = self.con.execute(
            """SELECT c.player_id pid, pl.player_name nm, c.position_group grp, c.rating,
                      f.team, f.market_value_eur mv,
                      COALESCE(f.detailed_position, f.main_position, c.position_group) pos,
                      b.fotmob_age age, b.nationality,
                      (SELECT max(fotmob_player_id) FROM player_enrichment e
                       WHERE e.player_id = c.player_id AND e.fotmob_player_id IS NOT NULL) fpid
               FROM player_ratings_combined c JOIN players pl ON pl.player_id = c.player_id
               JOIN v_player_profile_full f ON f.player_id = c.player_id
               LEFT JOIN player_bio b ON b.player_id = c.player_id
               WHERE c.scope='league' AND c.season=? AND c.minutes>=?
                 AND f.market_value_eur IS NOT NULL AND f.market_value_eur > 0""",
            [season, min_minutes]).df()
        pools = {cat: [] for cat in self._CAT_ORDER}
        for r in df.itertuples():
            cat = self._GRP2CAT.get(r.grp)
            if not cat or form.get(cat, 0) == 0:
                continue
            pools[cat].append({
                "id": int(r.pid), "player": r.nm, "team": r.team, "position": r.pos,
                "rating": int(r.rating), "value_m": max(1, int(round(r.mv / 1e6))),
                "value_eur": float(r.mv), "age": None if pd.isna(r.age) else int(r.age),
                "nationality": None if pd.isna(r.nationality) else r.nationality,
                "photo": self.player_photo(None if pd.isna(r.fpid) else int(r.fpid)),
                "team_logo": self.team_logo(r.team)})
        cats = {}
        for cat, lst in pools.items():
            if form.get(cat, 0) == 0:
                continue
            lst.sort(key=lambda p: (-p["rating"], p["value_m"]))
            cats[cat] = lst[:per_cat]
        # pitch slot layout (back -> front, GK first), each slot tagged with its category
        slots = [[{"cat": "GK", "x": 0.5}]]
        for line in lines_def:
            slots.append([{"cat": cat, "x": (i + 0.5) / len(line)} for i, cat in enumerate(line)])
        cheapest = sum(min((p["value_m"] for p in cats[c]), default=0) * form[c] for c in form)
        return {"available": True, "season": season, "formation": formation,
                "formations": list(self._FORMATIONS), "slots": slots,
                "cat_labels": self._CAT_LABEL, "form_counts": form,
                "min_budget_m": cheapest, "candidates": cats}

    # ---------------------------------------------------------------- Football DNA Map
    #  Project every outfielder onto a 2D "style map" so distance == dissimilarity.
    #  Position-agnostic per-90 features, z-scored globally, then PCA to 2 axes
    #  (oriented so x = goal threat, y = passing/defensive volume).
    def web_dna_map(self, min_minutes: int = 900) -> dict:
        import numpy as np
        df = self.con.execute(
            """SELECT v.player_id, pl.player_name AS pname, c.position_group AS grp, c.rating,
                      v.minutes, v.xg_per90, v.xa_per90, v.shots, v.key_passes, v.chances_created,
                      v.dribbles_completed, v.tackles, v.interceptions, v.recoveries,
                      v.passes_completed, v.duels_won, v.pass_accuracy_pct, v.duels_won_pct,
                      v.team, a.archetype, pe.fpid
               FROM v_player_season_stats v
               JOIN player_ratings_combined c
                 ON c.player_id = v.player_id AND c.scope='league' AND c.season = v.season
               JOIN players pl ON pl.player_id = v.player_id
               LEFT JOIN player_archetypes a ON a.player_id = v.player_id
               LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                          WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                      ON pe.player_id = v.player_id
               WHERE v.season = ? AND v.minutes >= ? AND c.position_group <> 'GK'""",
            [FOCUS_SEASON, min_minutes]).df()
        if df.empty:
            return {"available": False, "error": "No data for the map."}

        def p90(col):
            return (df[col] / df["minutes"] * 90).to_numpy(float)
        feats = np.column_stack([
            df["xg_per90"].to_numpy(float), df["xa_per90"].to_numpy(float),
            p90("shots"), p90("key_passes"), p90("chances_created"), p90("dribbles_completed"),
            p90("tackles"), p90("interceptions"), p90("recoveries"), p90("passes_completed"),
            p90("duels_won"), df["pass_accuracy_pct"].to_numpy(float) / 100,
            df["duels_won_pct"].to_numpy(float) / 100,
        ])
        feats = np.nan_to_num(feats)
        Z = (feats - feats.mean(0)) / (feats.std(0) + 1e-9)
        Z = Z - Z.mean(0)
        U, Sg, _ = np.linalg.svd(Z, full_matrices=False)
        coords = U[:, :2] * Sg[:2]
        var = [float(Sg[i] ** 2 / (Sg ** 2).sum() * 100) for i in range(2)]
        # deterministic orientation: x -> goal threat, y -> passing/defensive volume
        attack = Z[:, 0] + Z[:, 2] + Z[:, 1]                  # xg + shots + xa
        build = Z[:, 9] + Z[:, 6] + Z[:, 7]                   # passes + tackles + interceptions
        if np.corrcoef(coords[:, 0], attack)[0, 1] < 0:
            coords[:, 0] *= -1
        if np.corrcoef(coords[:, 1], build)[0, 1] < 0:
            coords[:, 1] *= -1

        pts = []
        for i, r in enumerate(df.itertuples()):
            pts.append({"name": r.pname, "x": round(float(coords[i, 0]), 2),
                        "y": round(float(coords[i, 1]), 2),
                        "group": r.grp, "group_label": self.GROUP_LABELS.get(r.grp, r.grp),
                        "rating": int(r.rating), "team": r.team,
                        "archetype": None if pd.isna(r.archetype) else r.archetype,
                        "fpid": None if pd.isna(r.fpid) else int(r.fpid)})
        return {"available": True, "count": len(pts),
                "variance": [round(v, 1) for v in var],
                "axes": {"x": "Goal threat", "y": "Passing & defensive volume"},
                "points": pts}

    # ---------------------------------------------------------------- use case 19
    #  Data-driven match preview: form + recent xG, key player matchups, head-to-head,
    #  and an xG-based Poisson win/draw/win + scoreline projection.
    _AREA = {"ST": "ATT", "W": "ATT", "AM": "MID", "CM": "MID", "DM": "MID",
             "CB": "DEF", "FB": "DEF", "GK": "GK"}

    def _team_key_players(self, team: str) -> dict:
        rows = self.con.execute(
            """SELECT pl.player_name, c.position_group AS grp, c.rating,
                      COALESCE(v.goals, 0) AS goals, COALESCE(v.assists, 0) AS assists, pe.fpid
               FROM player_ratings_combined c JOIN players pl USING(player_id)
               JOIN v_player_profile_full f ON f.player_id = c.player_id
               LEFT JOIN v_player_season_stats v ON v.player_id = c.player_id AND v.season = ?
               LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                          WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                      ON pe.player_id = c.player_id
               WHERE c.scope='league' AND c.season = ? AND f.team = ?
               ORDER BY c.rating DESC""", [FOCUS_SEASON, FOCUS_SEASON, team]).df()
        out = {}
        for r in rows.itertuples():
            area = self._AREA.get(r.grp, "MID")
            if area not in out:
                out[area] = {"player": r.player_name, "position": r.grp, "rating": int(r.rating),
                             "goals": int(r.goals or 0), "assists": int(r.assists or 0),
                             "photo": self.player_photo(r.fpid)}
        return out

    @staticmethod
    def _poisson_pred(hs: dict, ats: dict) -> dict:
        import math

        def rate(s, k):
            p = s.get("played") or 0
            return ((s.get(k) or 0) / p) if p else 1.2
        hxgf, hxga = rate(hs, "xg_for"), rate(hs, "xg_against")
        axgf, axga = rate(ats, "xg_for"), rate(ats, "xg_against")
        HOME = 1.10
        lh = max(0.2, (hxgf + axga) / 2 * HOME)
        la = max(0.2, (axgf + hxga) / 2 / HOME)
        pm = lambda l, k: math.exp(-l) * l ** k / math.factorial(k)
        ph = pd = pa = 0.0
        best = (1, 1, -1.0)
        for i in range(9):
            for j in range(9):
                p = pm(lh, i) * pm(la, j)
                if i > j:
                    ph += p
                elif i == j:
                    pd += p
                else:
                    pa += p
                if p > best[2]:
                    best = (i, j, p)
        tot = (ph + pd + pa) or 1
        return {"home_win": round(ph / tot * 100), "draw": round(pd / tot * 100),
                "away_win": round(pa / tot * 100), "xg_home": round(lh, 2), "xg_away": round(la, 2),
                "scoreline": f"{best[0]}-{best[1]}"}

    def _h2h_summary(self, home: str, away: str) -> dict:
        m = self.web_match_search(home, away)
        matches = m.get("matches") or []
        hw = dw = aw = gh = ga = 0
        for x in matches:
            if x["home"] == home:
                hg, ag = x["home_goals"], x["away_goals"]
            else:
                hg, ag = x["away_goals"], x["home_goals"]
            if hg is None or ag is None:   # unplayed fixture (e.g. future UCL tie)
                continue
            gh += hg; ga += ag
            if hg > ag:
                hw += 1
            elif hg == ag:
                dw += 1
            else:
                aw += 1
        return {"played": hw + dw + aw, "home_wins": hw, "draws": dw, "away_wins": aw,
                "goals_home": gh, "goals_away": ga, "recent": matches[:6]}

    def web_match_preview(self, home: str, away: str) -> dict:
        ht, at = self.web_team(home), self.web_team(away)
        if not ht.get("team") or not at.get("team"):
            return {"available": False, "error": "Couldn't find one of those teams."}

        def pack(t):
            s = t.get("stats") or {}
            played = s.get("played") or 0
            res = (t.get("results") or [])[:6]
            n = len(res) or 1
            return {
                "name": t["team"], "logo": t.get("team_logo"), "league": t.get("league"),
                "position": s.get("position"), "played": played, "points": s.get("points"),
                "wins": s.get("wins"), "draws": s.get("draws"), "losses": s.get("losses"),
                "gf": s.get("goals_for"), "ga": s.get("goals_against"),
                "xgf_pg": round(s["xg_for"] / played, 2) if played and s.get("xg_for") is not None else None,
                "xga_pg": round(s["xg_against"] / played, 2) if played and s.get("xg_against") is not None else None,
                "rec_xgf": round(sum(r["xg_for"] for r in res) / n, 2) if res else None,
                "rec_xga": round(sum(r["xg_against"] for r in res) / n, 2) if res else None,
                "form": t.get("form") or [],
                "recent": [{"opponent": r["opponent"], "venue": r["venue"], "gf": r["gf"],
                            "ga": r["ga"], "result": r["result"], "xg_for": r["xg_for"],
                            "xg_against": r["xg_against"]} for r in res],
                "key": self._team_key_players(t["team"]),
            }
        return {"available": True, "home": pack(ht), "away": pack(at),
                "prediction": self._poisson_pred(ht.get("stats") or {}, at.get("stats") or {}),
                "h2h": self._h2h_summary(ht["team"], at["team"])}

    # ---------------------------------------------------------------- use case 2
    #  Big Game Index — split a player's output vs top-half ("big games") and
    #  bottom-half opposition, to flag big-game players vs flat-track bullies.
    @staticmethod
    def _per90(v, mins):
        return round(v / mins * 90, 2) if mins else 0.0

    def _big_game_badge(self, big90, weak90, big_min, weak_min):
        if big_min < 360 or weak_min < 360:
            return None
        if big90 - weak90 >= 0.10 and big90 >= 0.30:
            return "Big-Game Player"
        if weak90 >= 0.45 and big90 <= weak90 * 0.55:
            return "Flat-Track Bully"
        return None

    def _big_game_rows(self, season, min_split_minutes=360, name=None):
        where = "WHERE l.season = ?"
        args = [season]
        if name:
            pid = self.find_player_id(name, season)
            if not pid:
                return []
            where += " AND l.player_id = ?"
            args.append(pid)
        df = self.con.execute(f"""
            WITH agg AS (
              SELECT l.player_id,
                SUM(CASE WHEN opp_top_half THEN minutes ELSE 0 END) AS big_min,
                SUM(CASE WHEN opp_top_half THEN goals+assists ELSE 0 END) AS big_ga,
                SUM(CASE WHEN opp_top_half THEN xg+xa ELSE 0 END) AS big_xgi,
                SUM(CASE WHEN opp_top_half THEN 1 ELSE 0 END) AS big_apps,
                SUM(CASE WHEN NOT opp_top_half THEN minutes ELSE 0 END) AS weak_min,
                SUM(CASE WHEN NOT opp_top_half THEN goals+assists ELSE 0 END) AS weak_ga,
                SUM(CASE WHEN NOT opp_top_half THEN xg+xa ELSE 0 END) AS weak_xgi,
                SUM(CASE WHEN NOT opp_top_half THEN 1 ELSE 0 END) AS weak_apps
              FROM player_match_log l {where} GROUP BY l.player_id)
            SELECT a.*, pl.player_name,
                   COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                   f.team, c.rating, pe.fpid
            FROM agg a JOIN players pl USING(player_id)
            LEFT JOIN v_player_profile_full f ON f.player_id = a.player_id
            LEFT JOIN player_ratings_combined c
                   ON c.player_id = a.player_id AND c.scope='league' AND c.season = ?
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = a.player_id
        """, [*args, season]).df()
        out = []
        for r in df.itertuples():
            if not name and (r.big_min < min_split_minutes or r.weak_min < min_split_minutes):
                continue
            big90, weak90 = self._per90(r.big_ga, r.big_min), self._per90(r.weak_ga, r.weak_min)
            out.append({
                "player": r.player_name, "team": r.team, "position": r.position,
                "rating": None if pd.isna(r.rating) else int(r.rating),
                "photo": self.player_photo(r.fpid),
                "big": {"apps": int(r.big_apps), "minutes": int(r.big_min), "ga": int(r.big_ga),
                        "ga90": big90, "xgi90": self._per90(r.big_xgi, r.big_min)},
                "weak": {"apps": int(r.weak_apps), "minutes": int(r.weak_min), "ga": int(r.weak_ga),
                         "ga90": weak90, "xgi90": self._per90(r.weak_xgi, r.weak_min)},
                "delta": round(big90 - weak90, 2),
                "badge": self._big_game_badge(big90, weak90, r.big_min, r.weak_min),
            })
        return out

    def web_big_game_board(self, season: str = FOCUS_SEASON, limit: int = 18) -> dict:
        if not self._table_exists("player_match_log"):
            return {"available": False, "error": "Match-log data not loaded yet."}
        rows = [r for r in self._big_game_rows(season) if r["team"]]   # current-squad players only
        if not rows:
            return {"available": False, "error": "No match-log data for this season yet."}
        # big-game players: meaningful big output AND step up; bullies: fade vs strong
        clutch = [r for r in rows if r["badge"] == "Big-Game Player"]
        clutch.sort(key=lambda r: (-r["delta"], -r["big"]["ga90"]))
        bully = [r for r in rows if r["badge"] == "Flat-Track Bully" and r["weak"]["ga"] >= 3]
        bully.sort(key=lambda r: (r["delta"], -r["weak"]["ga90"]))
        return {"available": True, "season": season, "count": len(rows),
                "clutch": clutch[:limit], "bully": bully[:limit]}

    def web_big_game_player(self, name: str, season: str = FOCUS_SEASON) -> dict:
        if not self._table_exists("player_match_log"):
            return {"available": False}
        rows = self._big_game_rows(season, name=name)
        if not rows:
            return {"available": False}
        r = rows[0]
        enough = r["big"]["minutes"] >= 270 and r["weak"]["minutes"] >= 270
        return {"available": enough, **r, "season": season}

    # ---------------------------------------------------------------- finishing
    #  Goals vs Expected (xG) — finishing over/under-performance for a player-season,
    #  from the Understat per-match log. Quantifies how clinical (or wasteful) a
    #  finisher has been vs what his chances were worth, ranked against position peers,
    #  with a cumulative Goals-vs-xG timeline for the profile chart. Domestic only.
    _FINISHING_MIN_SHOTS = 12   # need enough attempts for the differential to mean anything

    @staticmethod
    def _finishing_verdict(pct, diff):
        """A peer-percentile verdict (pct = percentile of G-xG per 90 within position);
        falls back to the raw differential when there is no cohort to rank against."""
        if pct is not None:
            if pct >= 85:  return "Elite Finisher", "good"
            if pct >= 68:  return "Clinical Finisher", "good"
            if pct >= 55:  return "Efficient Finisher", "good"
            if pct >= 42:  return "Finishes to Expectation", "neutral"
            if pct >= 25:  return "Below Expectation", "bad"
            return "Wasteful Finishing", "bad"
        if diff >= 2.0:   return "Overperforming xG", "good"
        if diff <= -2.0:  return "Underperforming xG", "bad"
        return "Finishes to Expectation", "neutral"

    def web_finishing(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """Finishing profile: Goals vs xG (over/under-performance), conversion, a
        percentile verdict vs same-position peers, and a cumulative-timeline for the
        chart. Hidden (available=False) for players without enough shots this season."""
        if not self._table_exists("player_match_log"):
            return {"available": False}
        pid = self.find_player_id(name, season)
        if not pid:
            return {"available": False}
        rows = self.con.execute("""
            SELECT l.match_date, l.is_home, l.goals, l.shots, l.xg,
                   COALESCE(t.team_name, 'Opponent') AS opp
            FROM player_match_log l
            LEFT JOIN teams t ON t.team_id = l.opponent_team_id
            WHERE l.player_id = ? AND l.season = ? AND l.minutes > 0
            ORDER BY l.match_date, l.game_id
        """, [pid, season]).fetchall()
        if not rows:
            return {"available": False}

        goals = sum(int(r[2]) for r in rows)
        shots = sum(int(r[3]) for r in rows)
        xg = sum(float(r[4]) for r in rows)
        if shots < self._FINISHING_MIN_SHOTS:
            return {"available": False}

        # cumulative Goals-vs-xG timeline (one point per appearance, chronological)
        timeline, cg, cx = [], 0, 0.0
        for r in rows:
            cg += int(r[2]); cx += float(r[4])
            timeline.append({
                "date": str(r[0]), "home": bool(r[1]), "opp": r[5],
                "goals": int(r[2]), "xg": round(float(r[4]), 2),
                "cum_goals": cg, "cum_xg": round(cx, 2),
            })

        # percentile of (G - xG) per 90 within the player's coarse position group
        # (only shooters with a meaningful sample are in the cohort).
        pr = self.con.execute("""
            WITH agg AS (
              SELECT l.player_id, pl.position_group AS grp,
                     SUM(l.goals) - SUM(l.xg) AS diff, SUM(l.minutes) AS mins
              FROM player_match_log l JOIN players pl USING(player_id)
              WHERE l.season = ? AND pl.position_group IS NOT NULL
              GROUP BY 1, 2
              HAVING SUM(l.shots) >= ? AND SUM(l.minutes) > 0
            ),
            ranked AS (
              SELECT player_id, grp,
                     ROUND(100 * percent_rank() OVER (
                         PARTITION BY grp ORDER BY diff / mins)) AS pct,
                     COUNT(*) OVER (PARTITION BY grp) AS cohort
              FROM agg)
            SELECT pct, cohort FROM ranked WHERE player_id = ?
        """, [season, self._FINISHING_MIN_SHOTS, pid]).fetchone()
        pct = int(pr[0]) if pr and pr[0] is not None else None
        cohort = int(pr[1]) if pr and pr[1] is not None else None

        pos = self.con.execute("""
            SELECT COALESCE(f.detailed_position, f.main_position, pl.position_group)
            FROM players pl LEFT JOIN v_player_profile_full f ON f.player_id = pl.player_id
            WHERE pl.player_id = ?""", [pid]).fetchone()
        diff = round(goals - xg, 1)
        verdict, verdict_class = self._finishing_verdict(pct, goals - xg)
        return {
            "available": True, "season": season, "position": (pos[0] if pos else None),
            "goals": goals, "xg": round(xg, 1), "diff": diff,
            "shots": shots, "conversion": round(100 * goals / shots, 1) if shots else 0.0,
            "shots_per_goal": round(shots / goals, 1) if goals else None,
            "percentile": pct, "cohort": cohort,
            "verdict": verdict, "verdict_class": verdict_class,
            "timeline": timeline,
        }

    # ------------------------------------------------------- fair market value
    #  A GradientBoosting model (ml/train_market_value.py) estimates a player's
    #  "fair" value from age + Atlastra rating + per-90 output + league, then flags
    #  over/under-valuation vs the actual Transfermarkt value. Predictions + per-player
    #  local value-drivers are precomputed into player_value_model / _drivers / _meta.
    def _value_meta(self, season: str) -> dict | None:
        if not self._table_exists("player_value_meta"):
            return None
        m = self.con.execute(
            "SELECT n, cv_r2, cv_mae_m FROM player_value_meta WHERE season = ?",
            [season]).fetchone()
        return {"n": int(m[0]), "r2": float(m[1]), "mae_m": float(m[2])} if m else None

    def web_value_model(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """Fair-value verdict + explainable value-drivers for one player (profile card)."""
        if not self._table_exists("player_value_model"):
            return {"available": False}
        pid = self.find_player_id(name, season)
        if not pid:
            return {"available": False}
        r = self.con.execute(
            """SELECT actual_eur, predicted_eur, ratio, verdict, verdict_class
               FROM player_value_model WHERE player_id = ? AND season = ?""",
            [pid, season]).fetchone()
        if not r:
            return {"available": False}          # only the ~488 valued players are modelled
        drivers = self.con.execute(
            """SELECT label, impact_m FROM player_value_drivers
               WHERE player_id = ? AND season = ? ORDER BY abs(impact_m) DESC LIMIT 5""",
            [pid, season]).fetchall()
        return {
            "available": True, "season": season,
            "actual_eur": int(r[0]), "predicted_eur": int(r[1]), "ratio": round(r[2], 3),
            "verdict": r[3], "verdict_class": r[4],
            "drivers": [{"label": d[0], "impact_m": round(d[1], 1)} for d in drivers],
            "model": self._value_meta(season),
        }

    def web_value_board(self, season: str = FOCUS_SEASON, limit: int = 15) -> dict:
        """Most under- and over-valued players by the model (a scouting leaderboard)."""
        if not self._table_exists("player_value_model"):
            return {"available": False, "error": "Value model not trained yet."}

        def fetch(order: str) -> list:
            df = self.con.execute(f"""
                SELECT pl.player_name AS player,
                       COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                       f.team, c.rating, pe.fpid,
                       m.actual_eur, m.predicted_eur, m.ratio, m.verdict
                FROM player_value_model m JOIN players pl USING(player_id)
                LEFT JOIN v_player_profile_full f ON f.player_id = m.player_id
                LEFT JOIN player_ratings_combined c
                       ON c.player_id = m.player_id AND c.scope='league' AND c.season = ?
                LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                           WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                       ON pe.player_id = m.player_id
                WHERE m.season = ? ORDER BY m.ratio {order} LIMIT ?
            """, [season, season, limit]).df()
            out = []
            for r in df.itertuples():
                out.append({
                    "player": r.player, "position": r.position, "team": r.team,
                    "rating": None if pd.isna(r.rating) else int(r.rating),
                    "photo": self.player_photo(r.fpid),
                    "actual_eur": int(r.actual_eur), "predicted_eur": int(r.predicted_eur),
                    "ratio": round(float(r.ratio), 2), "verdict": r.verdict,
                })
            return out

        return {"available": True, "season": season, "model": self._value_meta(season),
                "undervalued": fetch("DESC"), "overvalued": fetch("ASC")}

    # -------------------------------------------------------- career trajectory
    #  The one forward-looking player model (ml/train_trajectory.py): where a
    #  player's Atlastra rating goes NEXT season, plus the odds he is still a
    #  top-5 regular at all. Trained on transitions up to 2021/22 and scored on
    #  the three seasons after that, against the persistence baseline ("he'll be
    #  exactly as good as he was"), which is the only baseline worth beating.
    #  Projections, drivers and the measured aging curve are all precomputed.
    def _trajectory_meta(self) -> dict | None:
        if not self._table_exists("trajectory_meta"):
            return None
        m = self.con.execute("""SELECT target_season, n_train, n_test, test_seasons,
                                       mae, base_mae, skill_pct, r2, direction_acc,
                                       band, avail_auc, n_projected,
                                       big_move, big_up_n, big_up_pred, big_up_real,
                                       big_dn_n, big_dn_pred, big_dn_real,
                                       interval_pct, coverage, coverage_const
                                FROM trajectory_meta LIMIT 1""").fetchone()
        if not m:
            return None
        return {"target_season": m[0], "target_label": season_label(m[0]),
                "n_train": int(m[1]), "n_test": int(m[2]), "test_seasons": m[3],
                "mae": round(float(m[4]), 2), "base_mae": round(float(m[5]), 2),
                "skill_pct": round(float(m[6]), 1), "r2": round(float(m[7]), 3),
                "direction_acc": round(float(m[8]), 3), "band": round(float(m[9]), 1),
                "avail_auc": round(float(m[10]), 3), "n_projected": int(m[11]),
                # how the boards' own biggest calls fared on the held-out seasons,
                # split by direction (a +20 and a -20 would cancel if pooled)
                "big_move": round(float(m[12]), 1),
                "big_up": {"n": int(m[13]), "pred": round(float(m[14]), 1),
                           "real": round(float(m[15]), 1)},
                "big_dn": {"n": int(m[16]), "pred": round(float(m[17]), 1),
                           "real": round(float(m[18]), 1)},
                # the error bar's own report card: an 80% interval has to contain
                # the truth 80% of the time, so say how often it actually did
                "interval_pct": int(m[19]), "coverage": round(float(m[20]), 3),
                "coverage_const": round(float(m[21]), 3)}

    def web_trajectory(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """Next-season projection + drivers + the aging curve for this player's
        position, with his own age marked (profile card)."""
        if not self._table_exists("player_trajectory"):
            return {"available": False}
        pid = self.find_player_id(name, season)
        if not pid:
            return {"available": False}
        r = self.con.execute(
            """SELECT target_season, rating_now, projected, delta, band, p_present,
                      age, verdict, verdict_class, blurb, lo, hi
               FROM player_trajectory WHERE player_id = ? AND season = ?""",
            [pid, season]).fetchone()
        if not r:
            return {"available": False}      # below the rating minutes bar this season
        drivers = self.con.execute(
            """SELECT label, impact FROM player_trajectory_drivers
               WHERE player_id = ? AND season = ? ORDER BY abs(impact) DESC LIMIT 5""",
            [pid, season]).fetchall()

        grp = self.con.execute(
            """SELECT position_group FROM player_ratings_combined
               WHERE player_id = ? AND season = ? AND scope='league'""",
            [pid, season]).fetchone()
        curve = self._aging_curve(grp[0] if grp else "ALL")

        # his own rating history, so the card can draw where he has been
        hist = self.con.execute(
            """SELECT season, rating FROM player_ratings_combined
               WHERE player_id = ? AND scope='league' ORDER BY season""", [pid]).fetchall()
        return {
            "available": True, "season": season, "target_season": r[0],
            "target_label": season_label(r[0]),
            "rating_now": int(r[1]), "projected": round(float(r[2]), 1),
            "delta": round(float(r[3]), 1), "band": round(float(r[4]), 1),
            # the interval is asymmetric — a high-rated player has more room
            # below him than above — so the card gets both ends, not one ±
            "lo": round(float(r[10]), 1), "hi": round(float(r[11]), 1),
            "p_present": round(float(r[5]), 3),
            "age": None if r[6] is None else round(float(r[6]), 1),
            "verdict": r[7], "verdict_class": r[8], "blurb": r[9],
            "drivers": [{"label": d[0], "impact": round(float(d[1]), 2)} for d in drivers],
            "history": [{"season": h[0], "label": season_label(h[0]), "rating": int(h[1])}
                        for h in hist],
            "curve": curve, "position_group": grp[0] if grp else None,
            "model": self._trajectory_meta(),
        }

    def _aging_curve(self, position_group: str = "ALL") -> list[dict]:
        """Measured mean season-on-season rating change by age. Data, not a model —
        falls back to the all-positions curve where a group is too thin."""
        if not self._table_exists("trajectory_aging_curve"):
            return []
        rows = self.con.execute(
            """SELECT age, mean_delta, n FROM trajectory_aging_curve
               WHERE position_group = ? ORDER BY age""", [position_group]).fetchall()
        if len(rows) < 6:
            rows = self.con.execute(
                """SELECT age, mean_delta, n FROM trajectory_aging_curve
                   WHERE position_group = 'ALL' ORDER BY age""").fetchall()
        return [{"age": int(a), "delta": round(float(d), 2), "n": int(n)} for a, d, n in rows]

    def web_aging_curves(self) -> dict:
        """Every measured aging curve, for the chart on the Trajectory page."""
        if not self._table_exists("trajectory_aging_curve"):
            return {"available": False}
        df = self.con.execute(
            """SELECT position_group, age, mean_delta, n FROM trajectory_aging_curve
               ORDER BY position_group, age""").df()
        out: dict[str, list] = {}
        for r in df.itertuples():
            out.setdefault(r.position_group, []).append(
                {"age": int(r.age), "delta": round(float(r.mean_delta), 2), "n": int(r.n)})
        # the peak is where the curve last crosses from gaining to losing
        peaks = {}
        for grp, pts in out.items():
            cross = [p["age"] for p, q in zip(pts, pts[1:]) if p["delta"] >= 0 > q["delta"]]
            peaks[grp] = cross[-1] if cross else None
        return {"available": True, "curves": out, "peaks": peaks,
                "model": self._trajectory_meta()}

    def web_trajectory_board(self, season: str = FOCUS_SEASON, limit: int = 15,
                             min_minutes: int = 900) -> dict:
        """Risers, fallers and breakout candidates by the trajectory model."""
        if not self._table_exists("player_trajectory"):
            return {"available": False, "error": "Trajectory model not trained yet."}

        def fetch(where: str, order: str, params: list) -> list:
            df = self.con.execute(f"""
                SELECT pl.player_name AS player,
                       COALESCE(f.detailed_position, f.main_position, pl.position_group) AS position,
                       f.team, pe.fpid,
                       t.rating_now, t.projected, t.delta, t.band, t.lo, t.hi,
                       t.p_present, t.age, t.verdict, t.verdict_class
                FROM player_trajectory t
                JOIN players pl USING(player_id)
                JOIN player_ratings_combined c
                     ON c.player_id = t.player_id AND c.scope='league' AND c.season = t.season
                LEFT JOIN v_player_profile_full f ON f.player_id = t.player_id
                LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                           WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                       ON pe.player_id = t.player_id
                WHERE t.season = ? AND c.minutes >= ? {where}
                ORDER BY {order} LIMIT ?
            """, [season, min_minutes] + params + [limit]).df()
            out = []
            for r in df.itertuples():
                out.append({
                    "player": r.player, "position": r.position, "team": r.team,
                    "photo": self.player_photo(r.fpid),
                    "rating_now": int(r.rating_now), "projected": round(float(r.projected), 1),
                    "delta": round(float(r.delta), 1), "band": round(float(r.band), 1),
                    "lo": round(float(r.lo), 1), "hi": round(float(r.hi), 1),
                    "p_present": round(float(r.p_present), 3),
                    "age": None if pd.isna(r.age) else round(float(r.age), 1),
                    "verdict": r.verdict, "verdict_class": r.verdict_class,
                })
            return out

        return {
            "available": True, "season": season, "model": self._trajectory_meta(),
            "risers": fetch("", "t.delta DESC", []),
            "fallers": fetch("", "t.delta ASC", []),
            # breakouts: young, not yet at the top, and projected to CLIMB — the
            # players the model expects to arrive rather than the ones already
            # there. The rise is a filter, not just a sort key: ordering a young
            # cohort by projected rating alone lists the best of them, which puts
            # players the model expects to decline under a "breakout" heading.
            "breakouts": fetch("AND t.age IS NOT NULL AND t.age <= 23 "
                               "AND t.rating_now < 80 AND t.delta > 0",
                               "t.projected DESC", []),
            # the ones the availability head doubts will still be top-5 regulars
            "at_risk": fetch("", "t.p_present ASC", []),
        }

    # ---------------------------------------------------- projected league table
    #  "Where does this club finish if nobody moves?" Next season's squad is not
    #  knowable, so that assumption is the feature rather than a limitation of it —
    #  it is exactly the counterfactual a Squad Planner user wants before deciding
    #  whether to sign anyone.
    #
    #  Squad strength is the minutes-weighted rating of a club's top 14, and the
    #  map from strength to points is fitted on real final tables rather than
    #  assumed. Held out it lands within 9.5 points against 9.8 for assuming this
    #  season's table repeats — better, but not by much, and the page says so.
    #
    #  The points model is fitted AND served on measured squad strength. An earlier
    #  version fitted the coefficient on measured strength and then fed it
    #  projected strength at serve time, which is a different variable wearing the
    #  same coefficient; the honest gain from that substitution was about a point
    #  of MAE and it cost internal consistency. The trajectory instead appears as
    #  its own column — how far each squad's strength is projected to move — which
    #  is the part a planner actually acts on, and is not laundered through a
    #  coefficient it did not earn.
    SQUAD_DEPTH = 14

    def _table_fit(self):
        """Fit next-season points on (this season's points, projected strength).
        Returns (coefficients, held-out MAE, persistence MAE) or None."""
        if not self._table_exists("player_trajectory"):
            return None
        df = self.con.execute(f"""
            WITH sq AS (
                SELECT ps.player_id, ps.season,
                       ARG_MAX(ps.team_id, ps.minutes) AS team_id,
                       sum(ps.minutes) AS mins
                FROM player_season_stats ps GROUP BY 1, 2),
            rated AS (
                SELECT sq.team_id, sq.season, sq.mins, c.rating,
                       row_number() OVER (PARTITION BY sq.team_id, sq.season
                                          ORDER BY sq.mins DESC) AS rn
                FROM sq JOIN player_ratings_combined c
                     ON c.player_id = sq.player_id AND c.season = sq.season
                    AND c.scope = 'league'),
            strength AS (
                SELECT team_id, season,
                       sum(rating * mins) / NULLIF(sum(mins), 0) AS str_now,
                       count(*) AS n
                FROM rated WHERE rn <= {self.SQUAD_DEPTH} GROUP BY 1, 2)
            SELECT s.team_id, s.season, s.str_now, t.points AS pts_now,
                   n.points AS pts_next
            FROM strength s
            JOIN team_season_stats t ON t.team_id = s.team_id AND t.season = s.season
            JOIN team_season_stats n ON n.team_id = s.team_id
                 AND CAST(n.season AS INT) = CAST(s.season AS INT) + 101
            WHERE s.n >= {self.SQUAD_DEPTH}
        """).df()
        if len(df) < 100:
            return None
        A = np.column_stack([np.ones(len(df)), df["pts_now"].values, df["str_now"].values])
        y = df["pts_next"].values.astype(float)
        # held out by time, like everything else that claims a number here
        tr = (df["season"] <= "2122").values
        coef, *_ = np.linalg.lstsq(A[tr], y[tr], rcond=None)
        pred = A[~tr] @ coef
        mae = float(np.abs(y[~tr] - pred).mean())
        mae_pers = float(np.abs(y[~tr] - df["pts_now"].values[~tr]).mean())
        full, *_ = np.linalg.lstsq(A, y, rcond=None)
        return {"coef": full.tolist(), "mae": round(mae, 2),
                "mae_persistence": round(mae_pers, 2), "n": len(df)}

    def web_projected_table(self, league_key: str | None = None,
                            season: str = FOCUS_SEASON) -> dict:
        """Next season's table if every squad stayed exactly as it is."""
        fit = self._table_fit()
        if not fit:
            return {"available": False, "error": "Not enough history to fit a table projection."}
        a, b, c = fit["coef"]

        df = self.con.execute(f"""
            WITH sq AS (
                SELECT ps.player_id, ARG_MAX(ps.team_id, ps.minutes) AS team_id,
                       sum(ps.minutes) AS mins
                FROM player_season_stats ps WHERE ps.season = ? GROUP BY 1),
            rated AS (
                SELECT sq.team_id, sq.mins, c.rating,
                       COALESCE(t.projected, c.rating) AS proj,
                       row_number() OVER (PARTITION BY sq.team_id ORDER BY sq.mins DESC) AS rn
                FROM sq
                JOIN player_ratings_combined c ON c.player_id = sq.player_id
                     AND c.season = ? AND c.scope = 'league'
                LEFT JOIN player_trajectory t ON t.player_id = sq.player_id AND t.season = ?)
            SELECT r.team_id, tm.team_name, tm.league_key,
                   sum(r.rating * r.mins) / NULLIF(sum(r.mins), 0) AS str_now,
                   sum(r.proj * r.mins) / NULLIF(sum(r.mins), 0) AS str_proj,
                   count(*) AS n, ts.points AS pts_now, ts.league_position AS pos_now
            FROM rated r
            JOIN teams tm ON tm.team_id = r.team_id
            JOIN team_season_stats ts ON ts.team_id = r.team_id AND ts.season = ?
            WHERE r.rn <= {self.SQUAD_DEPTH}
            GROUP BY 1, 2, 3, ts.points, ts.league_position
            HAVING count(*) >= {self.SQUAD_DEPTH}
        """, [season, season, season, season]).df()
        if league_key:
            df = df[df["league_key"] == league_key]
        if df.empty:
            return {"available": False, "error": "No club with a full rated squad."}

        df["proj_pts"] = a + b * df["pts_now"] + c * df["str_now"]
        df["str_drift"] = df["str_proj"] - df["str_now"]
        out = {}
        for lg, g in df.groupby("league_key"):
            g = g.sort_values("proj_pts", ascending=False).reset_index(drop=True)
            out[lg] = [{
                "pos": i + 1, "team": r.team_name, "team_logo": self.team_logo(r.team_name),
                "projected_points": int(round(r.proj_pts)),
                "points_now": int(r.pts_now), "position_now": int(r.pos_now),
                "move": int(r.pos_now) - (i + 1),
                "squad_strength": round(float(r.str_now), 1),
                # reported beside the table, not folded into it: where the
                # trajectory model expects this squad's level to be heading
                "strength_drift": round(float(r.str_drift), 1),
            } for i, r in enumerate(g.itertuples())]
        return {"available": True, "season": season, "season_label": season_label(season),
                "tables": out, "model": fit,
                "target_label": season_label(f"{int(season[:2]) + 1:02d}{int(season[2:]) + 1:02d}")}

    # --------------------------------------------------- danger men for a fixture
    #  Previews used to name a club's best players by season rating, which gives
    #  the same answer every week whoever they play. This asks the fixture-specific
    #  question — who is likely to score or assist in THIS match — from the model
    #  in ml/train_match_contribution.py.
    _CONTRIB = None

    def _contrib_model(self):
        """Load the fitted model once per process; None if it was never trained."""
        if SoccerDB._CONTRIB is None:
            try:
                from joblib import load
                from ml.train_match_contribution import ART
                art = load(ART)
                SoccerDB._CONTRIB = (art["model"], art["feats"])
            except Exception:  # noqa: BLE001
                SoccerDB._CONTRIB = (None, None)
        return SoccerDB._CONTRIB

    def web_match_danger(self, home: str, away: str, top: int = 5,
                         season: str = FOCUS_SEASON) -> dict:
        """Most likely goal involvements on each side of one fixture."""
        model, feats = self._contrib_model()
        if model is None:
            return {"available": False, "error": "Match model not trained yet."}
        hid, aid = self.find_team_id(home), self.find_team_id(away)
        if not hid or not aid:
            return {"available": False, "error": "Could not resolve both clubs."}

        from ml.train_match_contribution import predict_fixture
        names = dict(self.con.execute(
            "SELECT player_id, player_name FROM players").fetchall())
        photos = dict(self.con.execute(
            """SELECT player_id, max(fotmob_player_id) FROM player_enrichment
               WHERE fotmob_player_id IS NOT NULL GROUP BY 1""").fetchall())

        def side(tid, oid, is_home):
            df = predict_fixture(self.con, tid, oid, is_home, season, model, feats)
            out = []
            for r in df.head(top).itertuples():
                out.append({
                    "player": names.get(int(r.player_id), "?"),
                    "prob": round(float(r.p), 3),
                    "photo": self.player_photo(photos.get(int(r.player_id))),
                    "form_ga": None if pd.isna(r.ga_form) else int(r.ga_form),
                    "apps_form": int(r.apps_form or 0),
                })
            return out

        meta = None
        if self._table_exists("player_match_contribution_meta"):
            m = self.con.execute(
                """SELECT auc, auc_rating, hit_top3, hit_top3_rating, n_test
                   FROM player_match_contribution_meta LIMIT 1""").fetchone()
            if m:
                meta = {"auc": round(float(m[0]), 3), "auc_rating": round(float(m[1]), 3),
                        "hit_top3": round(float(m[2]), 3),
                        "hit_top3_rating": round(float(m[3]), 3), "n_test": int(m[4])}
        return {"available": True, "home": home, "away": away, "season": season,
                "home_players": side(hid, aid, True), "away_players": side(aid, hid, False),
                "model": meta}

    # ------------------------------------------------------------- availability
    #  Derived from the match log, not scraped: a club match the player did not
    #  appear in is an absence, a run of them is a spell (pipeline/build_absences).
    #  Deliberately never called an injury — a long suspension looks identical from
    #  here, and only the length is actually observed.
    def web_availability(self, name: str, season: str = FOCUS_SEASON) -> dict:
        """Availability this season, the spells behind it, and career history."""
        if not self._table_exists("player_availability"):
            return {"available": False}
        pid = self.find_player_id(name, season)
        if not pid:
            return {"available": False}

        cur = self.con.execute("""
            SELECT sum(window_matches) AS window_matches, sum(played) AS played,
                   max(longest_spell) AS longest_spell, sum(matches_missed) AS missed
            FROM player_availability WHERE player_id = ? AND season = ?""",
            [pid, season]).fetchone()
        if not cur or not cur[0]:
            return {"available": False}
        window, played = int(cur[0]), int(cur[1])

        spells = self.con.execute("""
            SELECT s.start_date, s.end_date, s.matches_missed, t.team_name
            FROM player_absence_spell s LEFT JOIN teams t USING(team_id)
            WHERE s.player_id = ? AND s.season = ? AND s.matches_missed >= 3
            ORDER BY s.matches_missed DESC LIMIT 6""", [pid, season]).fetchall()

        hist = self.con.execute("""
            SELECT season, sum(window_matches) AS w, sum(played) AS p,
                   max(longest_spell) AS longest
            FROM player_availability WHERE player_id = ?
            GROUP BY 1 ORDER BY 1""", [pid]).fetchall()
        career = [{"season": h[0], "label": season_label(h[0]),
                   "pct": round(100.0 * int(h[2]) / max(1, int(h[1])), 1),
                   "played": int(h[2]), "window": int(h[1]), "longest": int(h[3] or 0)}
                  for h in hist]
        seasons_long = sum(1 for c in career if c["longest"] >= 10)

        pct = round(100.0 * played / max(1, window), 1)
        verdict, cls = (("Ever-present", "great") if pct >= 90 else
                        ("Reliable", "good") if pct >= 75 else
                        ("In and out", "neutral") if pct >= 50 else
                        ("Rarely available", "bad"))
        return {
            "available": True, "season": season, "season_label": season_label(season),
            "window_matches": window, "played": played, "pct": pct,
            "missed": int(cur[3] or 0), "longest_spell": int(cur[2] or 0),
            "verdict": verdict, "verdict_class": cls,
            "spells": [{"from": str(s[0])[:10], "to": str(s[1])[:10],
                        "matches": int(s[2]), "team": s[3]} for s in spells],
            "career": career, "seasons_with_long_absence": seasons_long,
            "risk": self._absence_risk(),
        }

    def _absence_risk(self) -> list[dict] | None:
        """The measured relationship between a long absence this season and another
        next season. A table, not a per-player score: a model on this target beat
        reading one column by 0.015 AUC, which is not enough to put a number on a
        person. Recomputed here so the page always matches the warehouse."""
        if not self._table_exists("player_availability"):
            return None
        # Restricted to the rated cohort. Pooling every player-season instead lets
        # in fringe players whose whole "window" is a handful of appearances, for
        # whom a long absence is near-automatic; that pushed the headline rates to
        # 18/26/34% and put the page at odds with the model, which reports the same
        # relationship on rated players. Same population, same numbers.
        rows = self.con.execute("""
            WITH a AS (SELECT av.player_id, av.season, max(av.longest_spell) AS spell
                       FROM player_availability av
                       JOIN player_ratings_combined c
                            ON c.player_id = av.player_id AND c.season = av.season
                           AND c.scope = 'league'
                       GROUP BY 1, 2),
            pairs AS (
                SELECT a.spell AS prior, b.spell AS nxt
                FROM a JOIN a b ON b.player_id = a.player_id
                   AND CAST(b.season AS INT) = CAST(a.season AS INT) + 101)
            SELECT CASE WHEN prior >= 10 THEN '10+'
                        WHEN prior >= 5  THEN '5-9' ELSE 'none' END AS bucket,
                   count(*) AS n,
                   round(avg(CASE WHEN nxt >= 10 THEN 1.0 ELSE 0.0 END), 3) AS rate
            FROM pairs GROUP BY 1""").fetchall()
        order = {"none": 0, "5-9": 1, "10+": 2}
        out = sorted(({"prior": r[0], "n": int(r[1]), "rate": float(r[2])} for r in rows),
                     key=lambda d: order.get(d["prior"], 9))
        return out or None

    # ------------------------------------------------------------ squad planner
    #  "What does this squad need, and when?" — the question none of the other
    #  features can answer, because they all describe the present. It joins three
    #  things that already exist: who is at the club now, where the trajectory
    #  model expects each of them to be next season, and what the measured aging
    #  curve says about the three years after that. A unit that is fine today and
    #  30 years old is a problem the league table will not show for two years.
    #
    #  The benchmark is measured, not invented: for each position, take every
    #  top-5 club's BEST player there and use the 80th percentile of that. It is
    #  literally "what a strong club has in this position this season".
    UNIT_LABELS = {"GK": "Goalkeeper", "CB": "Centre-back", "FB": "Full-back",
                   "DM": "Defensive midfield", "CM": "Central midfield",
                   "AM": "Attacking midfield", "W": "Winger", "ST": "Striker"}
    PLAN_MIN_MINUTES = 450          # below this a player is not really a squad option

    #  Positions cover for one another, and a planner that ignores that produces
    #  nonsense: Manchester United's midfield is classified DM and AM, with nobody
    #  labelled CM, and the first version of this duly told a club with Casemiro,
    #  Ugarte, Mainoo, Fernandes and Mount that its top priority was a central
    #  midfielder. So an adjacent player counts toward a unit at a discount — he
    #  can do the job, just not as naturally as a specialist.
    COVERS = {
        "GK": {}, "CB": {"FB": 0.85}, "FB": {"CB": 0.85, "W": 0.80},
        "DM": {"CM": 0.90, "CB": 0.80}, "CM": {"DM": 0.90, "AM": 0.90},
        "AM": {"CM": 0.90, "W": 0.85}, "W": {"AM": 0.85, "ST": 0.80, "FB": 0.75},
        "ST": {"W": 0.80, "AM": 0.75},
    }
    COVER_DEPTH_WEIGHT = 0.5        # an out-of-position body is half a squad option
    MAX_COVER = 3                   # and you cannot field six centre-backs at full-back

    def _aging_deltas(self) -> dict:
        """(position_group, age) -> mean season-on-season rating change. The
        measured curve, used to age a squad forward past the model's one season."""
        if not self._table_exists("trajectory_aging_curve"):
            return {}
        return {(r[0], int(r[1])): float(r[2]) for r in self.con.execute(
            "SELECT position_group, age, mean_delta FROM trajectory_aging_curve").fetchall()}

    def _age_forward(self, curve: dict, grp: str, age: float | None, years: int) -> float:
        """Rating points a player of this age and position typically gains or loses
        over `years` more seasons. A cohort average, not a personal projection —
        the model only speaks for one season ahead, and this is labelled as such."""
        if age is None:
            return 0.0
        total = 0.0
        for k in range(years):
            a = int(round(age)) + k
            step = curve.get((grp, a), curve.get(("ALL", a)))
            if step is None:            # past the end of the curve: hold the last
                step = curve.get((grp, 38), curve.get(("ALL", 38), 0.0))
            total += step
        return total

    def web_squad_plan(self, name: str, season: str = FOCUS_SEASON,
                       horizon: int = 3, targets_per_unit: int = 4) -> dict:
        """Squad-gap analysis for one club: where it is short now, where it will be
        short once the current squad ages, and who could fix it."""
        if not self._table_exists("player_trajectory"):
            return {"available": False, "error": "Trajectory model not trained yet."}
        tid = self.find_team_id(name)
        if not tid:
            return {"available": False, "error": f"No club matching {name!r}."}
        head = self.con.execute(
            """SELECT t.team_name, t.league_key, l.league_name
               FROM teams t JOIN leagues l USING(league_key) WHERE t.team_id = ?""",
            [tid]).fetchone()

        squad = self.con.execute("""
            SELECT pl.player_name AS player, c.position_group AS grp, c.rating,
                   c.minutes, t.projected, t.delta, t.lo, t.hi, t.p_present, t.age,
                   t.verdict, t.verdict_class, pe.fpid
            FROM player_season_stats ps
            JOIN players pl USING(player_id)
            JOIN player_ratings_combined c
                 ON c.player_id = ps.player_id AND c.season = ps.season AND c.scope = 'league'
            LEFT JOIN player_trajectory t
                 ON t.player_id = ps.player_id AND t.season = ps.season
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = ps.player_id
            WHERE ps.team_id = ? AND ps.season = ? AND ps.minutes >= ?
            ORDER BY c.rating DESC
        """, [tid, season, self.PLAN_MIN_MINUTES]).df()
        if squad.empty:
            return {"available": False, "error": f"No rated squad for {head[0]} in {season}."}

        bench = {r[0]: float(r[1]) for r in self.con.execute("""
            WITH s AS (
                SELECT ps.team_id, c.position_group AS grp, c.rating
                FROM player_season_stats ps
                JOIN player_ratings_combined c
                     ON c.player_id = ps.player_id AND c.season = ps.season AND c.scope='league'
                WHERE ps.season = ?),
            best AS (SELECT grp, team_id, max(rating) AS r FROM s GROUP BY 1, 2)
            SELECT grp, quantile_cont(r, 0.8) FROM best GROUP BY 1
        """, [season]).fetchall()}

        curve = self._aging_deltas()
        tgt_season = self.con.execute(
            "SELECT target_season FROM player_trajectory LIMIT 1").fetchone()

        def _p(r) -> dict:
            proj = None if pd.isna(r.projected) else round(float(r.projected), 1)
            return {"player": r.player, "group": r.grp, "rating": int(r.rating),
                    "minutes": int(r.minutes),
                    "age": None if pd.isna(r.age) else round(float(r.age), 1),
                    "projected": proj,
                    "delta": None if pd.isna(r.delta) else round(float(r.delta), 1),
                    "p_present": None if pd.isna(r.p_present) else round(float(r.p_present), 3),
                    "verdict": None if pd.isna(r.verdict) else r.verdict,
                    "verdict_class": None if pd.isna(r.verdict_class) else r.verdict_class,
                    "in_horizon": None if proj is None or pd.isna(r.age) else
                        round(proj + self._age_forward(curve, r.grp, float(r.age) + 1, horizon - 1), 1),
                    "photo": self.player_photo(r.fpid)}

        players = [_p(r) for r in squad.itertuples()]
        # how deep a typical top-5 club is at each position — measured, so "thin"
        # means "thinner than everyone else" rather than a number someone guessed
        depth_norm = {r[0]: float(r[1]) for r in self.con.execute("""
            WITH s AS (
                SELECT ps.team_id, c.position_group AS grp
                FROM player_season_stats ps
                JOIN player_ratings_combined c
                     ON c.player_id = ps.player_id AND c.season = ps.season AND c.scope='league'
                WHERE ps.season = ? AND ps.minutes >= ?),
            d AS (SELECT grp, team_id, count(*) AS n FROM s GROUP BY 1, 2)
            SELECT grp, median(n) FROM d GROUP BY 1
        """, [season, self.PLAN_MIN_MINUTES]).fetchall()}

        units = []
        for grp, label in self.UNIT_LABELS.items():
            own = [p for p in players if p["group"] == grp]
            covers = self.COVERS.get(grp, {})
            # an adjacent player enters this unit at a discount, tagged so the UI
            # can say "covering from central midfield" rather than imply he is one
            # A covering player keeps his real rating on display — showing an
            # 83-rated midfielder as "71" because he is being counted at 0.85 in
            # this unit misrepresents the player. The discount lives in the
            # eff_* fields, which is what the unit maths reads.
            cover = []
            for p in players:
                w = covers.get(p["group"])
                if not w:
                    continue
                q = dict(p, cover_from=p["group"], cover_weight=w,
                         eff_rating=round(p["rating"] * w, 1))
                for k in ("projected", "in_horizon"):
                    q[f"eff_{k}"] = None if p[k] is None else round(p[k] * w, 1)
                cover.append(q)
            cover.sort(key=lambda q: -q["eff_rating"])
            cover = cover[:self.MAX_COVER]
            for p in own:                       # specialists are counted in full
                p["eff_rating"] = float(p["rating"])
                p["eff_projected"], p["eff_in_horizon"] = p["projected"], p["in_horizon"]
            ps = own + cover
            b = round(bench.get(grp, 70.0), 1)
            want = round(depth_norm.get(grp, 3.0))
            if not ps:
                units.append({
                    "group": grp, "label": label, "depth": 0, "target_depth": want,
                    "benchmark": b, "best_now": None, "best_next": None,
                    "best_horizon": None, "gap_now": b, "gap_next": b, "gap_horizon": b,
                    "mean_age": None, "priority": round(b + 12, 1), "players": [],
                    "reasons": [f"No rated {label.lower()} played {self.PLAN_MIN_MINUTES}+ minutes, "
                                f"and nobody in an adjacent position to cover"],
                })
                continue
            best_now = round(max(p["eff_rating"] for p in ps), 1)
            nxt = [p["eff_projected"] for p in ps if p["eff_projected"] is not None]
            hz = [p["eff_in_horizon"] for p in ps if p["eff_in_horizon"] is not None]
            best_next = max(nxt) if nxt else None
            best_hz = max(hz) if hz else None
            # a squad's age at a position is the age of the players actually
            # holding it, so weight by minutes rather than counting heads
            aged = [(p["age"], p["minutes"]) for p in own if p["age"] is not None]
            mean_age = (round(sum(a * m for a, m in aged) / sum(m for _, m in aged), 1)
                        if aged else None)

            gap_now = round(b - best_now, 1)
            gap_next = round(b - best_next, 1) if best_next is not None else gap_now
            gap_hz = round(b - best_hz, 1) if best_hz is not None else gap_next
            eff_depth = len(own) + self.COVER_DEPTH_WEIGHT * len(cover)
            short = max(0.0, want - eff_depth)

            reasons = []
            if gap_now > 4:
                reasons.append(f"Best {label.lower()} rates {best_now} against {b:.0f} "
                               f"at a strong club — {gap_now:.0f} short today")
            if gap_hz - gap_now > 3:
                reasons.append(f"Widens to {gap_hz:.0f} short within {horizon} seasons "
                               f"as the current group ages")
            if short >= 1:
                spec = f"{len(own)} specialist{'s' if len(own) != 1 else ''}"
                extra = f" plus {len(cover)} covering from elsewhere" if cover else ""
                reasons.append(f"Thin: {spec}{extra} with real minutes, against "
                               f"{want} at a typical top-5 club")
            if mean_age is not None and mean_age >= 30:
                reasons.append(f"Minutes here average {mean_age:.0f} years old")
            declining = [p for p in own if (p["delta"] or 0) <= -3.5]
            if declining:
                reasons.append(", ".join(p["player"] for p in declining[:3])
                               + (" are" if len(declining) > 1 else " is")
                               + " projected to drop sharply")
            leaving = [p for p in own if (p["p_present"] or 1) < 0.5]
            if leaving:
                reasons.append(", ".join(p["player"] for p in leaving[:3])
                               + " may not be a top-5 regular next season")
            if not reasons:
                reasons.append("Covered — at or above a strong club's level, with depth")

            # priority: the future gap matters most, then today's, then depth
            priority = round(max(0.0, gap_hz) * 1.0 + max(0.0, gap_now) * 0.6 + short * 3.0, 1)
            units.append({
                "group": grp, "label": label, "depth": len(own),
                "cover_depth": len(cover), "target_depth": want,
                "benchmark": b, "best_now": best_now, "best_next": best_next,
                "best_horizon": best_hz, "gap_now": gap_now, "gap_next": gap_next,
                "gap_horizon": gap_hz, "mean_age": mean_age, "priority": priority,
                "reasons": reasons,
                "players": sorted(ps, key=lambda p: -p["eff_rating"]),
            })
        units.sort(key=lambda u: -u["priority"])

        needs = [u for u in units if u["priority"] > 0][:3]
        targets = {u["group"]: self._plan_targets(
            tid, u["group"], u["best_next"] or u["best_now"] or 0, season, targets_per_unit)
            for u in needs}

        return {
            "available": True, "team": head[0], "league": head[2],
            "league_key": head[1], "team_logo": self.team_logo(head[0]),
            "season": _fmt_season(season), "horizon": horizon,
            "target_label": season_label(tgt_season[0]) if tgt_season else None,
            "units": units, "targets": targets,
            "needs": [u["group"] for u in needs],
            "model": self._trajectory_meta(),
        }

    def _plan_targets(self, tid: int, grp: str, beat: float,
                      season: str, limit: int) -> list[dict]:
        """Players at other clubs who would raise this position next season.
        Ranked by projected rating; market value shown where we have one (only
        the ~500 most valuable players are priced, so most come back unpriced)."""
        df = self.con.execute("""
            SELECT pl.player_name AS player, tm.team_name AS team, c.rating,
                   t.projected, t.delta, t.age, t.verdict, t.verdict_class,
                   mv.market_value_eur AS value, pe.fpid
            FROM player_trajectory t
            JOIN players pl USING(player_id)
            JOIN player_ratings_combined c
                 ON c.player_id = t.player_id AND c.season = t.season AND c.scope='league'
            JOIN (SELECT player_id, ARG_MAX(team_id, minutes) AS team_id
                  FROM player_season_stats WHERE season = ? GROUP BY 1) cur
                 ON cur.player_id = t.player_id
            JOIN teams tm ON tm.team_id = cur.team_id
            LEFT JOIN player_market_value mv
                 ON mv.player_id = t.player_id AND mv.season = t.season
            LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                       WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                   ON pe.player_id = t.player_id
            WHERE t.season = ? AND c.position_group = ? AND cur.team_id <> ?
              AND c.minutes >= 900 AND t.projected > ?
            ORDER BY t.projected DESC LIMIT ?
        """, [season, season, grp, tid, beat, limit]).df()
        return [{
            "player": r.player, "team": r.team, "rating": int(r.rating),
            "projected": round(float(r.projected), 1), "delta": round(float(r.delta), 1),
            "age": None if pd.isna(r.age) else round(float(r.age), 1),
            "verdict": r.verdict, "verdict_class": r.verdict_class,
            "value_eur": None if pd.isna(r.value) else int(r.value),
            "photo": self.player_photo(r.fpid),
        } for r in df.itertuples()]

    def web_squad_key_players(self, names: list, top: int = 3, season: str = FOCUS_SEASON) -> list:
        """Match SofaScore squad names to our warehouse and return the top-rated few
        (for national-team match previews — most internationals play in the top-5)."""
        seen, found = set(), []
        for nm in (names or [])[:30]:
            pid = self.find_player_id(nm, season)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            r = self.con.execute(
                """SELECT pl.player_name,
                          COALESCE(f.detailed_position, f.main_position, pl.position_group) AS pos,
                          COALESCE(c.rating, f.rating) AS rating, f.team, pe.fpid
                   FROM players pl
                   LEFT JOIN v_player_profile_full f ON f.player_id = pl.player_id
                   LEFT JOIN player_ratings_combined c
                          ON c.player_id = pl.player_id AND c.scope='league' AND c.season = ?
                   LEFT JOIN (SELECT player_id, max(fotmob_player_id) fpid FROM player_enrichment
                              WHERE fotmob_player_id IS NOT NULL GROUP BY player_id) pe
                          ON pe.player_id = pl.player_id
                   WHERE pl.player_id = ?""", [season, pid]).fetchone()
            if r and r[2] is not None:
                found.append({"player": r[0], "position": r[1], "rating": int(r[2]),
                              "club": r[3], "photo": self.player_photo(r[4])})
        found.sort(key=lambda x: -x["rating"])
        return found[:top]

    def _table_exists(self, name: str) -> bool:
        return bool(self.con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]).fetchone())

    def _player_age(self, datamb_name: str) -> int | None:
        r = self.con.execute(
            "SELECT age FROM player_wyscout WHERE player = ? AND age IS NOT NULL LIMIT 1",
            [datamb_name]).fetchone()
        return int(r[0]) if r else None
