"""
FIFA / EA FC 26 player ratings — stable, ability-based (not season-dependent), used to
drive the Tactics Lab instead of our percentile Atlas ratings. Provides each player's
overall + the six FIFA attributes (pace, shooting, passing, dribbling, defending, physic)
plus heading, which the tactics engine turns into unit strengths.

Data: data/fifa_ratings.json (built from the EA FC 26 dataset). Matched by folded name
with a 3-tier fallback (full name → first-initial+surname → surname), keeping the highest
overall on collisions.
"""
import json
import sys
import unicodedata
from pathlib import Path

try:
    from config import DATA_DIR
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import DATA_DIR

_DATA = None


def _load():
    global _DATA
    if _DATA is None:
        try:
            _DATA = json.loads((DATA_DIR / "fifa_ratings.json").read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            _DATA = {"full": {}, "il": {}, "last": {}}
        _index_mononyms(_DATA)
    return _DATA


def _index_mononyms(d: dict) -> None:
    """Make single-name cards reachable by surname.

    EA FC files a lot of players under one name — Rodri, Pedri, Raphinha, Koke, Oyarzabal —
    and the surname index is built from the last token of a MULTI-word name, so 425 of those
    cards were reachable only by typing the mononym exactly. Every source that supplies a
    full name therefore missed them: "Mikel Oyarzabal" found nothing and fell through to a
    tournament-performance rating of 64 against his real card of 82.

    Kept in its OWN index rather than folded into the surname one, and consulted only when
    the caller asks. Merging it fixed the national squads and cost the club backtest — log
    loss 1.0004 -> 1.0029, points MAE 7.80 -> 8.07 — because a club roster's surname that
    happens to equal somebody's mononym then resolved to the wrong player. A source that
    hands us a full name wants this; one that hands us a squad list does not.
    """
    full = d.get("full") or {}
    d["mono"] = {n: c for n, c in full.items() if " " not in n and n not in (d.get("last") or {})}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", "").replace("-", " ").split())


def match(name: str, mononyms: bool = False):
    """Return a FIFA card dict {o,pac,sho,pas,dri,def,phy,hea,foot} or None.

    `mononyms` adds a final tier for players EA FC files under a single name — Rodri, Pedri,
    Oyarzabal. Off by default: it is right when the caller has a full name and wrong when it
    has a squad list, where a common surname would start resolving to a mononym card
    belonging to somebody else. See _index_mononyms.
    """
    d = _load()
    f = _fold(name)
    t = f.split()
    c = d["full"].get(f)
    if not c and len(t) >= 2:
        c = d["il"].get(t[0][0] + "|" + t[-1])
    if not c and t:
        c = d["last"].get(t[-1])
    if not c and mononyms and t:
        c = (d.get("mono") or {}).get(t[-1])
    return c


def available() -> bool:
    return bool(_load().get("full"))


def club_squad(team: str) -> list:
    """Current FIFA roster for a supported club (keyed by our team name), newest transfers
    included. Each entry: {n,ln,o,pos,pac,sho,pas,dri,def,phy,hea,foot}. [] if not a club."""
    return _load().get("clubs", {}).get(team, [])


def is_club(team: str) -> bool:
    return team in _load().get("clubs", {})
