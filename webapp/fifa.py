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
    return _DATA


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", "").replace("-", " ").split())


def match(name: str):
    """Return a FIFA card dict {o,pac,sho,pas,dri,def,phy,hea,foot} or None."""
    d = _load()
    f = _fold(name)
    t = f.split()
    c = d["full"].get(f)
    if not c and len(t) >= 2:
        c = d["il"].get(t[0][0] + "|" + t[-1])
    if not c and t:
        c = d["last"].get(t[-1])
    return c


def available() -> bool:
    return bool(_load().get("full"))


def club_squad(team: str) -> list:
    """Current FIFA roster for a supported club (keyed by our team name), newest transfers
    included. Each entry: {n,ln,o,pos,pac,sho,pas,dri,def,phy,hea,foot}. [] if not a club."""
    return _load().get("clubs", {}).get(team, [])


def is_club(team: str) -> bool:
    return team in _load().get("clubs", {})
