"""
"Ask Atlastra" -- a natural-language Q&A agent over the site's own data.

Deliberately NOT text-to-SQL: the LLM never sees or writes SQL. Instead it picks
from a small, explicit whitelist of TOOLS, each a thin wrapper around an existing,
already-tested SoccerDB method (analytics/queries.py) -- so every answer is
grounded in infrastructure the rest of the site already relies on, and there is
no query the model could construct that isn't something this codebase already
runs safely. Two Gemini calls per question: one to ROUTE (pick a tool + args, or
decline), one to SYNTHESIZE a plain-English answer from that tool's real data.

Follows the same Gemini-preferred / offline-fallback convention as
webapp/scout_ai.py and webapp/weekly_recap.py (see those for the Claude-fallback
pattern -- omitted here for now since ANTHROPIC_API_KEY isn't provisioned; add a
fallback branch in _route()/_synthesize() the same way if that changes).
"""
import json
import sys

try:
    from config import LEAGUES
except ModuleNotFoundError:  # pragma: no cover
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import LEAGUES

MAX_QUESTION_LEN = 300
VALID_LEAGUES = set(LEAGUES.keys())


# ---- tool executors ---------------------------------------------------------
# Each takes (db, args) and returns a compact, JSON-serializable dict/list --
# never raises (callers wrap in try/except too, but each validates its own
# inputs rather than trusting the model's args blindly).

def _tool_league_leaders(db, args):
    lk = str(args.get("league_key") or "all").strip()
    if lk.lower() != "all" and lk not in VALID_LEAGUES:
        lk = "all"
    return db.web_league_leaders(lk, top=5)


def _tool_player_profile(db, args):
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "no player name given"}
    d = db.web_player(name)
    if not d:
        return {"error": f"no player found matching '{name}'"}
    keep = ["name", "team", "position_group", "detailed_position", "age", "nationality",
            "market_value_eur", "rating", "classification", "percentile", "ratings",
            "avg_rating", "tiles", "archetype", "strengths", "weaknesses",
            "areas_of_improvement", "season"]
    return {k: d[k] for k in keep if d.get(k) is not None}


def _tool_compare_players(db, args):
    names = [n.strip() for n in (args.get("names") or []) if isinstance(n, str) and n.strip()][:3]
    if len(names) < 2:
        return {"error": "need at least 2 player names to compare"}
    return db.web_compare(names)


def _tool_player_progression(db, args):
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "no player name given"}
    df = db.player_progression(name)
    if df.empty:
        return {"error": f"no season history found for '{name}'"}
    return df.to_dict(orient="records")


def _tool_team_standings(db, args):
    lk = str(args.get("league_key") or "").strip()
    if lk not in VALID_LEAGUES:
        return {"error": f"league_key must be one of {sorted(VALID_LEAGUES)}"}
    return db.web_league_table(lk)


def _tool_team_info(db, args):
    name = str(args.get("name") or "").strip()
    if not name:
        return {"error": "no team name given"}
    d = db.web_team(name)
    if not d:
        return {"error": f"no team found matching '{name}'"}
    d = dict(d)
    if d.get("squad"):  # trim the full squad list to the essentials for the prompt
        d["squad"] = [{"player": p.get("player"), "position_group": p.get("position_group"),
                       "goals": p.get("goals"), "assists": p.get("assists")}
                      for p in d["squad"]]
    return d


def _tool_search(db, args):
    q = str(args.get("query") or "").strip()
    if not q:
        return {"error": "no query given"}
    return db.web_search(q)


TOOLS = {
    "league_leaders": {
        "desc": "Top players in EVERY tracked stat (goals, assists, xG, xA, chances created, "
                "dribbles, tackles, interceptions, pass accuracy, etc) for one league or all "
                "top-5 combined. Use for 'who leads/has the most X' questions.",
        "params": {"league_key": "one of " + ", ".join(sorted(VALID_LEAGUES)) + ", or 'all'"},
        "fn": _tool_league_leaders,
    },
    "player_profile": {
        "desc": "Full profile for ONE named player: rating, percentile, per-90 stats, "
                "strengths/weaknesses, archetype, market value, team. Use for 'tell me about "
                "X' or single-player questions.",
        "params": {"name": "the player's name"},
        "fn": _tool_player_profile,
    },
    "compare_players": {
        "desc": "Side-by-side stat comparison of 2-3 named players.",
        "params": {"names": "a list of 2-3 player names"},
        "fn": _tool_compare_players,
    },
    "player_progression": {
        "desc": "One named player's season-by-season stat trend across their career -- use "
                "for trajectory/development/improving-or-declining questions.",
        "params": {"name": "the player's name"},
        "fn": _tool_player_progression,
    },
    "team_standings": {
        "desc": "Full current league table for one league (position, points, record, goals).",
        "params": {"league_key": "one of " + ", ".join(sorted(VALID_LEAGUES))},
        "fn": _tool_team_standings,
    },
    "team_info": {
        "desc": "One named team's current standing, manager, venue, and squad.",
        "params": {"name": "the team's name"},
        "fn": _tool_team_info,
    },
    "search": {
        "desc": "Look up whether a player or team NAME exists and get its canonical spelling. "
                "Use this if you're unsure a name is right or no other tool clearly fits.",
        "params": {"query": "search text"},
        "fn": _tool_search,
    },
}

SYSTEM_ROUTE = """You are the routing brain for "Ask Atlastra," a soccer-stats Q&A assistant for a \
site covering the top-5 European leagues (Premier League, La Liga, Serie A, Bundesliga, Ligue 1). \
Given a user's question, decide which ONE tool (if any) supplies the data needed to answer it.

Available tools:
{tool_list}

Reply with ONLY a JSON object, no prose, no markdown fences:
{{"tool": "<name from the list above, or null>", "args": {{...that tool's params...}}, \
"direct_answer": "<ONLY used when tool is null: a brief, honest reply or polite decline>"}}

Rules:
- Pick exactly one tool. If the question needs data with no matching tool (transfer rumors, \
opinions, betting odds, predictions of future results, non-football topics), set tool to null and \
write a brief, honest direct_answer -- say what you can't do, and what you CAN help with instead.
- For a greeting or a question about what you can do, tool is null with a short, friendly \
direct_answer describing the kinds of questions you *can* answer.
- Pass player/team names exactly as the user wrote them -- don't try to correct spelling yourself.
- Only include the params the chosen tool defines."""

SYSTEM_SYNTH = """You are "Ask Atlastra," a soccer-stats assistant. Answer the user's question using \
ONLY the JSON data below -- it comes straight from the site's live database.

Rules:
- Never invent a number, name, or fact not present in the data.
- If the data doesn't actually answer the question (empty results, an "error" key, player/team not \
found), say so plainly and briefly -- don't guess or apologize at length.
- Be concise: 2-5 sentences, conversational prose. No markdown headers, no tables, no bullet lists.
- You may bold a player/team name at most once or twice for emphasis.
- Cite the specific numbers from the data naturally, as a knowledgeable analyst would."""


def _extract_obj(text: str) -> dict | None:
    """Pull the first top-level {...} JSON object out of a model reply.

    Depth-aware (unlike webapp.gemini.extract_json's naive first-'['-to-last-']'
    heuristic), because the routing reply's "args" often nests an array, e.g.
    {"tool": "compare_players", "args": {"names": ["A", "B"]}} -- extract_json's
    bracket search matches the FIRST '[' to the LAST ']' in the whole reply,
    which grabs just ["A", "B"] as if it were the outer object."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t[3:]
        t = t[4:] if t[:4].lower() == "json" else t
    start = t.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if in_str:
            if esc:
                esc = False          # this char is escaped -- consume it, reset
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                except (json.JSONDecodeError, ValueError):
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def _route(question: str) -> dict:
    from webapp import gemini
    tool_list = "\n".join(f"- {name}: {t['desc']} Params: {t['params']}"
                          for name, t in TOOLS.items())
    prompt = SYSTEM_ROUTE.format(tool_list=tool_list) + f"\n\nQuestion: {question}"
    raw = gemini.generate(prompt, temperature=0.1)
    return _extract_obj(raw) or {}


def _synthesize(question: str, data) -> str:
    from webapp import gemini
    payload = json.dumps(data, ensure_ascii=False, default=str)[:6000]
    prompt = f"{SYSTEM_SYNTH}\n\nQuestion: {question}\n\nData (JSON):\n{payload}"
    return (gemini.generate(prompt, temperature=0.3) or "").strip()


def ask(question: str, db_read_only: bool = True) -> dict:
    """Answer a natural-language question about the site's data.
    Returns {"answer": str, "tool_used": str | None}.

    `db_read_only` MUST match the mode the server's own DB pool already uses
    (its DB_READ_ONLY) -- DuckDB refuses a second in-process connection to the
    same file with a different read_only config than one already open (same
    gotcha as pipeline/load_team_info.py)."""
    question = (question or "").strip()[:MAX_QUESTION_LEN]
    if not question:
        return {"answer": "Ask me something about a player, team, or stat leaders "
                          "in the top-5 leagues.", "tool_used": None}

    from webapp import gemini
    if not gemini.available():
        return {"answer": "Ask Atlastra is temporarily offline (AI not configured) -- "
                          "try Search or Stat Leaders directly.", "tool_used": None}

    decision = _route(question)
    tool_name = decision.get("tool")
    if not tool_name or tool_name not in TOOLS:
        answer = str(decision.get("direct_answer") or "").strip()
        if not answer:
            answer = ("I can only answer questions about top-5-league players, teams, "
                      "and stat leaders right now.")
        return {"answer": answer, "tool_used": None}

    from analytics.queries import SoccerDB
    try:
        with SoccerDB(read_only=db_read_only) as db:
            data = TOOLS[tool_name]["fn"](db, decision.get("args") or {})
    except Exception as e:                                # noqa: BLE001
        print(f"ask_agent tool '{tool_name}': {type(e).__name__}: {str(e)[:200]}", flush=True)
        data = {"error": f"{type(e).__name__} while fetching data"}

    answer = _synthesize(question, data)
    if not answer:
        answer = "I found some data but couldn't put together an answer -- try rephrasing."
    return {"answer": answer, "tool_used": tool_name}


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Who leads the Premier League in assists?"
    print(json.dumps(ask(q), indent=2, ensure_ascii=False))
