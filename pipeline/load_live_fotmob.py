"""
Live matches + fixtures feed, sourced from FotMob (server-side, NO proxy).

SofaScore's api host hard-403s datacenter IPs, so its live feed can only be
scraped from a residential IP (the Mac push-ingest). FotMob does NOT datacenter-
block — its signed `/api/data/matches` endpoint returns `200` straight from the
EC2 box — so this rebuilds the same `live_matches` table from FotMob and can run
on the server 24/7 with no Mac and no proxy.

It writes the identical schema as pipeline/load_live.py (same DDL/COLS), so
SoccerDB.web_live() and the whole live UI work unchanged.

    python -m pipeline.load_live_fotmob        # one refresh
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, LIVE_DAYS_BACK, LIVE_DAYS_AHEAD
from analytics.queries import connect_retry
from pipeline.fotmob_auth import FotmobAuth
from pipeline.load_live import COLS, _setup   # reuse the live_matches schema + setup

# FotMob league primaryId -> (our key, display name, group). Names/groups match the
# SofaScore feed exactly so team_logo/fifa_rank lookups and live.js compRank agree.
COVERED = {
    77: ("WC", "FIFA World Cup", "International"),
    50: ("EURO", "UEFA EURO", "International"),
    44: ("COPA", "Copa América", "International"),
    42: ("UCL", "UEFA Champions League", "Champions League"),
    47: ("EPL", "Premier League", "Top 5 Leagues"),
    87: ("LALIGA", "La Liga", "Top 5 Leagues"),
    55: ("SERIEA", "Serie A", "Top 5 Leagues"),
    54: ("BUNDESLIGA", "Bundesliga", "Top 5 Leagues"),
    53: ("LIGUE1", "Ligue 1", "Top 5 Leagues"),
}
# qualifying leagues carry their own primaryId; map onto the parent comp and flag
# the round as "Qualification" so live.js's compLabel/compRank drop them lower.
QUAL = {
    10611: ("UCL", "UEFA Champions League", "Champions League"),   # UCL qualification
}

# knockout stage code -> readable round name
STAGE = {"final": "Final", "1/2": "Semi-final", "1/4": "Quarter-final",
         "1/8": "Round of 16", "1/16": "Round of 32", "1/32": "Round of 64"}

# National-team name -> ISO alpha-2 (for the flag emoji web_live renders). Covers the
# WC/EURO/Copa nations we surface; an unmapped nation just shows no flag (graceful).
NAT_ISO = {
    "USA": "US", "Canada": "CA", "Mexico": "MX", "Costa Rica": "CR", "Panama": "PA",
    "Honduras": "HN", "Jamaica": "JM", "Brazil": "BR", "Argentina": "AR", "Uruguay": "UY",
    "Colombia": "CO", "Chile": "CL", "Peru": "PE", "Ecuador": "EC", "Paraguay": "PY",
    "Venezuela": "VE", "Bolivia": "BO", "England": "GB-ENG", "Scotland": "GB-SCT",
    "Wales": "GB-WLS", "France": "FR", "Spain": "ES", "Portugal": "PT", "Germany": "DE",
    "Italy": "IT", "Netherlands": "NL", "Belgium": "BE", "Croatia": "HR", "Denmark": "DK",
    "Switzerland": "CH", "Austria": "AT", "Poland": "PL", "Ukraine": "UA", "Serbia": "RS",
    "Sweden": "SE", "Norway": "NO", "Czechia": "CZ", "Czech Republic": "CZ", "Turkey": "TR",
    "Türkiye": "TR", "Greece": "GR", "Hungary": "HU", "Romania": "RO", "Slovenia": "SI",
    "Slovakia": "SK", "Republic of Ireland": "IE", "Ireland": "IE", "Iceland": "IS",
    "Finland": "FI", "Albania": "AL", "Morocco": "MA", "Senegal": "SN", "Tunisia": "TN",
    "Algeria": "DZ", "Egypt": "EG", "Nigeria": "NG", "Ghana": "GH", "Cameroon": "CM",
    "Ivory Coast": "CI", "Côte d'Ivoire": "CI", "Mali": "ML", "South Africa": "ZA",
    "Japan": "JP", "South Korea": "KR", "Korea Republic": "KR", "Australia": "AU",
    "Iran": "IR", "Saudi Arabia": "SA", "Qatar": "QA", "Iraq": "IQ", "Uzbekistan": "UZ",
    "Jordan": "JO", "New Zealand": "NZ",
}

_auth = FotmobAuth()


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _minute(st: dict):
    """Live clock minute from FotMob's liveTime (e.g. short '89’' / long '88:06').
    None at half-time / breaks (no liveTime while play is stopped)."""
    lt = st.get("liveTime") or {}
    for field in ("short", "long"):
        s = str(lt.get(field) or "")
        digits = ""
        for ch in s:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        if digits:
            return int(digits)
    return None


def _status(st: dict):
    """(status_type, status_desc) in the live_matches vocabulary."""
    reason = (st.get("reason") or {})
    if st.get("finished"):
        return "finished", reason.get("long") or "Full-Time"
    if st.get("started"):
        if st.get("ongoing"):
            return "inprogress", "In progress"
        return "inprogress", "Half-time"           # started, paused, not finished
    return "notstarted", reason.get("long") or "Not started"


def _map(m: dict, key: str, name: str, group: str, is_qual: bool):
    st = m.get("status") or {}
    if st.get("cancelled"):
        return None                                # postponed/cancelled -> drop
    eid = _int(m.get("id"))
    if eid is None:
        return None
    home, away = m.get("home") or {}, m.get("away") or {}
    stype, sdesc = _status(st)
    minute = _minute(st) if stype == "inprogress" else None
    hs, as_ = _int(home.get("score")), _int(away.get("score"))
    winner = None
    if stype == "finished" and hs is not None and as_ is not None:
        winner = 1 if hs > as_ else 2 if as_ > hs else 3
    round_name = "Qualification" if is_qual else STAGE.get(m.get("tournamentStage"))
    ts = m.get("timeTS")
    start_ts = int(ts / 1000) if ts else None
    intl = group == "International"
    hname, aname = home.get("name"), away.get("name")
    # COLS order: event_id, tournament_key/name/group, round_name, start_timestamp,
    # status_type/desc/minute, home_team/id/country, away_team/id/country,
    # home_score, away_score, home_pens, away_pens, winner_code, updated_at
    return (eid, key, name, group, round_name, start_ts, stype, sdesc, minute,
            hname, _int(home.get("id")), NAT_ISO.get(hname) if intl else None,
            aname, _int(away.get("id")), NAT_ISO.get(aname) if intl else None,
            hs, as_, None, None, winner, datetime.utcnow())


def fetch_rows() -> list:
    """All covered matches across the [today-BACK, today+AHEAD] window."""
    rows, seen = [], set()
    today = date.today()
    for delta in range(-LIVE_DAYS_BACK, LIVE_DAYS_AHEAD + 1):
        d = today + timedelta(days=delta)
        try:
            data = _auth.get(f"/api/data/matches?date={d:%Y%m%d}")
        except Exception as e:                     # noqa: BLE001
            print(f"  ! {d}: {type(e).__name__} {str(e)[:80]}", flush=True)
            continue
        for L in (data.get("leagues") or []):
            pid = L.get("primaryId")
            meta = COVERED.get(pid) or QUAL.get(pid)
            if not meta:
                continue
            key, name, group = meta
            for m in (L.get("matches") or []):
                row = _map(m, key, name, group, pid in QUAL)
                if row and row[0] not in seen:     # dedupe by event_id across dates
                    seen.add(row[0])
                    rows.append(row)
    return rows


def refresh() -> tuple:
    """Rebuild live_matches wholesale from FotMob. Returns (n_rows, n_live)."""
    rows = fetch_rows()
    con = connect_retry(DB_PATH, read_only=False)
    try:
        _setup(con)
        con.execute("DELETE FROM live_matches")
        if rows:
            # explicit column list -> maps by name (the table's physical order has the
            # penalty columns appended at the end from an old ALTER; COLS differs)
            con.executemany(
                f"INSERT INTO live_matches ({','.join(COLS)}) "
                f"VALUES ({','.join(['?'] * len(COLS))})", rows)
    finally:
        con.close()
    n_live = sum(1 for r in rows if r[6] == "inprogress")
    return len(rows), n_live


if __name__ == "__main__":
    n, live = refresh()
    print(f"FotMob live refresh: {n} matches ({live} in-play)")
