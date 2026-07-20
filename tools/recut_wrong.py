"""
For each clip the verifier flagged WRONG, re-ask Gemini (strictly) for a corrected
timestamp where THIS player himself performs the labelled skill — or declare it
unfindable. Writes /tmp/recut.json: [{player,i,file,skill,new_t|None,onball}].
Run on the server. Reads /tmp/clip_verify.json.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp import gemini                                 # noqa: E402
from webapp import signature_skills as ss                 # noqa: E402

CLUB = {
    "Harry Kane": "Bayern Munich", "Michael Olise": "Bayern Munich", "Lamine Yamal": "Barcelona",
    "Kylian Mbappe-Lottin": "Real Madrid", "Declan Rice": "Arsenal", "Ousmane Dembélé": "PSG",
    "Luis Díaz": "Bayern Munich", "Khvicha Kvaratskhelia": "PSG",
    "Bruno Fernandes": "Manchester United", "Vitinha": "PSG", "Erling Haaland": "Manchester City",
    "Arda Güler": "Real Madrid", "Pedri": "Barcelona", "Rayan Cherki": "Manchester City",
    "Nico Paz": "Como", "Raphinha": "Barcelona", "Nuno Mendes": "PSG",
    "Vinícius Júnior": "Real Madrid", "Julián Álvarez": "Atlético Madrid",
    "Joshua Kimmich": "Bayern Munich", "Achraf Hakimi": "PSG", "Yan Diomande": "RB Leipzig",
    "Antoine Semenyo": "Bournemouth", "Jude Bellingham": "Real Madrid", "João Neves": "PSG",
}


def _secs(t):
    s = str(t or "").strip()
    if ":" in s:
        try:
            m, sec = s.split(":")[-2:]
            return int(m) * 60 + int(float(sec))
        except Exception:                                 # noqa: BLE001
            return None
    try:
        return int(float(s))
    except Exception:                                     # noqa: BLE001
        return None


_VIDEO = {}


def video_for(player):
    if player not in _VIDEO:
        con = ss._conn()
        row = con.execute("SELECT video FROM sig WHERE player=?", [player]).fetchone()
        con.close()
        _VIDEO[player] = row[0] if row else None
    return _VIDEO[player]


def requery(rec):
    player, skill = rec["player"], rec["skill"]
    club = CLUB.get(player, "")
    video = video_for(player)
    if not video:
        return {**rec, "new_t": None, "onball": "no video"}
    prompt = (
        f"This video should feature the footballer {player} ({club}). Watch it and find the "
        f"SINGLE clearest moment where {player} HIMSELF performs this exact action: \"{skill}\". "
        f"Rules: it must be {player} on the ball (NOT a teammate), and the action must actually "
        f"complete on screen (not a celebration or build-up). Reply JSON only: "
        f"{{\"found\": true/false, \"time\": \"mm:ss\" (start of that play), "
        f"\"onball\": \"who is on the ball\"}}. If {player} never clearly performs it, found=false.")
    raw = gemini.analyze_youtube(video, prompt)
    j = gemini.extract_json(raw) or {}
    t = _secs(j.get("time")) if j.get("found") else None
    print(f"{'NEW t='+str(t) if t is not None else 'DROP':10s} {player:22s} {skill[:32]}", flush=True)
    return {**rec, "new_t": t, "onball": str(j.get("onball", ""))[:40]}


def main():
    v = json.loads(Path("/tmp/clip_verify.json").read_text())
    wrong = [r for r in v if r["match"] is False]
    with ThreadPoolExecutor(max_workers=2) as pool:      # free tier is rate-limited
        out = list(pool.map(requery, wrong))
    Path("/tmp/recut.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    fixable = sum(1 for r in out if r["new_t"] is not None)
    print(f"\n{len(out)} wrong: {fixable} got a new timestamp, {len(out)-fixable} unfindable -> /tmp/recut.json")


if __name__ == "__main__":
    main()
