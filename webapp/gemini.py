"""
Minimal Gemini API client (stdlib only) — reads GEMINI_API_KEY from the environment.

Two entry points: generate(text) for plain text tasks, and analyze_youtube(url, text)
which passes a YouTube URL straight to Gemini (it fetches & watches the video itself —
no download needed on our side). Transient 503/429s are retried with backoff.
"""
import json
import os
import time
import urllib.error
import urllib.request

MODEL = "gemini-flash-latest"
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def _post(parts, temperature=0.3, model=MODEL, retries=5, timeout=150):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    url = f"{_BASE}/{model}:generateContent?key={key}"
    body = json.dumps({"contents": [{"parts": parts}],
                       "generationConfig": {"temperature": temperature}}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
            d = json.load(urllib.request.urlopen(req, timeout=timeout))
            cands = d.get("candidates") or []
            if cands:
                return "".join(p.get("text", "") for p in cands[0]["content"]["parts"]).strip()
            return None
        except urllib.error.HTTPError as e:
            if e.code in (503, 429, 500) and attempt < retries - 1:
                time.sleep(min(30, 5 * (attempt + 1)))   # patient for free-tier rate limits
                continue
            return None
        except Exception:                                # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(1.5)
                continue
            return None
    return None


def generate(prompt: str, temperature=0.4, model=MODEL):
    """Plain text generation."""
    return _post([{"text": prompt}], temperature, model)


def analyze_youtube(youtube_url: str, prompt: str, temperature=0.2, model=MODEL):
    """Ask Gemini about a YouTube video by URL — it fetches and watches it itself."""
    return _post([{"text": prompt}, {"fileData": {"fileUri": youtube_url}}], temperature, model)


def extract_json(text: str):
    """Pull a JSON value out of a model reply (tolerates ```json fences / stray prose)."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t[3:]
        t = t[4:] if t[:4].lower() == "json" else t
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:                            # noqa: BLE001
                pass
    return None
