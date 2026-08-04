"""
Build the skills-detection corpus manifest.

For each of the 19 signature football skills, this searches YouTube (via the same
keyless scraper the site uses) for per-skill *compilation* videos — which are
weakly self-labelling: a "Best Rabonas" video is, by definition, a folder of
rabona clips. The output `skills_corpus.json` is the input to the clip harvester
(yt-dlp + PySceneDetect), which segments each video into labelled example clips.

Run:  python ml/build_skills_corpus.py
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from webapp.live_feed_fotmob import _yt_results   # keyless YouTube search

OUT = Path(__file__).resolve().parent / "skills_corpus.json"
PER_SKILL = 5

# id, name, category, difficulty, aliases, signature_players, search query, title tokens
# The query targets per-skill compilations; tokens confirm a result really is that
# skill (drops generic "top 50 skills" videos). Difficulty = expected CV difficulty.
SKILLS = [
    ("stepover", "Stepover (scissor)", "dribbling", "medium",
     ["scissor", "step over"], ["Cristiano Ronaldo", "Robinho"],
     "stepover skill compilation football", ["stepover", "step over", "step-over"]),
    ("elastico", "Elastico (flip-flap)", "dribbling", "hard",
     ["flip flap", "flip-flap", "snake"], ["Ronaldinho", "Neymar"],
     "best elasticos flip flap compilation football", ["elastico", "flip flap", "flip-flap", "flipflap"]),
    ("la_croqueta", "La Croqueta", "dribbling", "hard",
     ["croqueta"], ["Andrés Iniesta", "Luis Suárez"],
     "la croqueta skill compilation football", ["croqueta"]),
    ("roulette", "Roulette (Marseille turn)", "dribbling", "easy",
     ["marseille turn", "360", "spin move"], ["Zinedine Zidane", "Diego Maradona"],
     "roulette marseille turn compilation football", ["roulette", "marseille"]),
    ("cruyff_turn", "Cruyff turn", "dribbling", "medium",
     ["cruyff"], ["Johan Cruyff", "Lionel Messi"],
     "cruyff turn compilation football", ["cruyff"]),
    ("nutmeg", "Nutmeg (panna)", "dribbling", "medium",
     ["panna", "megs"], ["Luis Suárez", "Jack Grealish"],
     "best nutmegs compilation football", ["nutmeg", "panna"]),
    ("body_feint", "Body feint / shoulder drop", "dribbling", "hard",
     ["feint", "shoulder drop", "ankle breaker"], ["Lionel Messi", "Neymar"],
     "best body feints compilation football", ["body feint", "feint"]),
    ("chop", "Chop / cut inside", "dribbling", "medium",
     ["cut inside", "cr7 chop"], ["Arjen Robben", "Mohamed Salah"],
     "Robben cut inside chop skill compilation", ["cut inside", "chop", "robben"]),
    ("ball_roll", "Ball roll / sole drag", "dribbling", "medium",
     ["sole roll", "sole drag", "drag"], ["Andrés Iniesta", "Kevin De Bruyne"],
     "sole roll skill compilation football", ["sole roll", "ball roll", "roll skill", "sole rolls"]),

    ("rabona", "Rabona", "flair", "easy",
     ["cross-legged"], ["Ricardo Quaresma", "Ángel Di María"],
     "best rabona goals compilation football", ["rabona"]),
    ("rainbow_flick", "Rainbow flick", "flair", "easy",
     ["okocha", "reverse rainbow"], ["Neymar", "Jay-Jay Okocha"],
     "rainbow flick compilation football", ["rainbow"]),
    ("sombrero", "Sombrero flick", "flair", "medium",
     ["lollipop", "over the head"], ["Ronaldinho", "Neymar"],
     "best sombrero flick skill compilation football", ["sombrero"]),
    ("backheel", "Backheel / heel flick", "flair", "medium",
     ["back heel", "heel flick"], ["Zlatan Ibrahimović", "Thierry Henry"],
     "best backheel goals compilation football", ["backheel", "back heel", "back-heel"]),
    ("fake_shot", "Fake shot / stop-and-go", "flair", "hard",
     ["fake pass", "shot fake"], ["Neymar", "Eden Hazard"],
     "the fake shot skill compilation football", ["fake shot", "fake-shot"]),

    ("chip", "Chip / dink", "finishing", "easy",
     ["lob", "dink", "scoop"], ["Lionel Messi", "Kylian Mbappé"],
     "best chip lob goals compilation football", ["chip", "dink", "lob"]),
    ("trivela", "Trivela (outside-of-boot)", "finishing", "medium",
     ["outside of the boot", "outside boot"], ["Ricardo Quaresma", "Martin Ødegaard"],
     "trivela outside of the boot goals compilation", ["trivela", "outside of the boot", "outside boot"]),
    ("curler", "Curler (finesse)", "finishing", "medium",
     ["finesse", "curve", "bend"], ["Arjen Robben", "Martin Ødegaard"],
     "finesse curler top corner goals compilation", ["finesse", "curl", "curler", "curve"]),
    ("volley", "Volley / half-volley", "finishing", "easy",
     ["half-volley", "scissor kick"], ["Cristiano Ronaldo", "Zlatan Ibrahimović"],
     "best volley goals compilation football", ["volley"]),
    ("knuckleball", "Knuckleball / screamer", "finishing", "easy",
     ["screamer", "long range", "power shot"], ["Cristiano Ronaldo", "Harry Kane"],
     "best knuckleball long range goals compilation football", ["knuckleball", "knuckle", "screamer"]),
]


# extra queries for skills whose primary term is generic (results vary run-to-run);
# merged in only if the primary query yields too few confirmed compilations.
ALT_QUERIES = {
    "ball_roll": ["sole roll dribbling skill football", "5 sole rolls football skill"],
    "chop": ["the chop cut inside skill compilation football"],
    "fake_shot": ["fake shot dribbling skill compilation football"],
}


def _collect(query, tokens, picked, seen):
    res = _yt_results(query, sp="")
    toks = [t.lower() for t in tokens]
    matched = [v for v in res if any(t in (v["title"] or "").lower() for t in toks)]
    for v in matched:
        if v["id"] in seen:
            continue
        seen.add(v["id"])
        picked.append({"id": v["id"], "title": v["title"], "views": v["views"],
                       "channel": v.get("channel", ""), "url": f"https://youtu.be/{v['id']}"})
        if len(picked) >= PER_SKILL:
            break


def fetch(row):
    sid, name, cat, diff, aliases, players, query, tokens = row
    picked, seen = [], set()
    _collect(query, tokens, picked, seen)
    for alt in ALT_QUERIES.get(sid, []):                 # top up thin skills
        if len(picked) >= 3:
            break
        _collect(alt, tokens, picked, seen)
    return {"id": sid, "name": name, "category": cat, "difficulty": diff,
            "aliases": aliases, "signature_players": players, "query": query,
            "videos": picked[:PER_SKILL]}


def main():
    with ThreadPoolExecutor(max_workers=10) as pool:
        skills = list(pool.map(fetch, SKILLS))
    manifest = {"taxonomy_version": 1, "skill_count": len(skills),
                "per_skill_target": PER_SKILL, "source": "youtube_compilations",
                "skills": skills}
    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    thin = [s["id"] for s in skills if len(s["videos"]) < 3]
    total = sum(len(s["videos"]) for s in skills)
    print(f"wrote {OUT}  ({len(skills)} skills, {total} videos)")
    print("thin skills (<3 videos):", thin or "none")


if __name__ == "__main__":
    main()
