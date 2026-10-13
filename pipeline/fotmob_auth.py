"""
FotMob request signing (`x-mas` header).

FotMob's `/api/data/*` endpoints reject unsigned requests. The browser signs
each call with an `x-mas` header that its own JS bundle builds like this
(reverse-engineered from the live `_app-*.js` chunk):

    body      = {"url": <relative path>, "code": <epoch ms>, "foo": <build hash>}
    signature = MD5( json(body) + <secret> ).upper()
    x-mas     = base64( json({"body": body, "signature": signature}) )

where <secret> is a long string literal (currently the "Three Lions" lyrics)
and <build hash> is a per-deploy constant (`production:<sha>`). Both can rotate,
so we extract them live from the JS bundle and fall back to last-known values.

Only relative-path requests to www.fotmob.com are signed; the per-stat lists on
data.fotmob.com are a public CDN and need no header.
"""
import base64
import hashlib
import json
import re
import time

import tls_requests

HOME = "https://www.fotmob.com"

# Last-known values (June 2026) -- used only if live extraction fails.
_FALLBACK_FOO = "production:c99cd39d6f05ac2979915be4c21d80b92fb25d84"
_FALLBACK_SECRET = None  # extraction is reliable; no hardcoded lyrics fallback

_APP_RE = re.compile(r'src="(/_next/static/chunks/pages/_app-[^"]+\.js)"')
_FOO_RE = re.compile(r'foo:"(production:[0-9a-f]+)"')
# The secret used to be a quoted string literal immediately before a `.concat()`
# chain building the signature; FotMob's Oct 2026 bundle switched the whole
# signature call to a template literal (`${JSON.stringify(r)}${c}`), with the
# secret itself now ALSO a backtick template literal assigned to a short
# identifier (`c=\`[Spoken Intro...\``). Match that shape generically (a short
# identifier assigned a long backtick string) rather than anchoring to the
# signature call's exact surrounding code, which is what broke -- the call
# shape is free to keep changing release to release, same as the single-letter
# variable names, but "a long run of real text in backticks" is a stable target.
_SECRET_RE = re.compile(r'[A-Za-z_$][\w$]*=`((?:[^`\\]|\\.){100,})`')


class FotmobAuth:
    """Builds `x-mas` headers, extracting the signing material once and caching it."""

    def __init__(self):
        self._foo = None
        self._secret = None

    # ---- signing material -------------------------------------------------
    def _ensure(self) -> None:
        if self._foo and self._secret:
            return
        try:
            self._extract_from_bundle()
        except Exception as e:  # pragma: no cover - network/parse failure
            if _FALLBACK_SECRET:
                self._foo, self._secret = _FALLBACK_FOO, _FALLBACK_SECRET
            else:
                raise RuntimeError(
                    "could not extract FotMob signing material from the JS bundle; "
                    "the page layout may have changed"
                ) from e

    def _extract_from_bundle(self) -> None:
        html = tls_requests.get(HOME + "/", timeout=25).text
        m = _APP_RE.search(html)
        if not m:
            raise ValueError("could not locate _app chunk")
        js = tls_requests.get(HOME + m.group(1), timeout=25).text
        foo = _FOO_RE.search(js)
        if not foo:
            raise ValueError("could not parse foo from _app chunk")
        # The secret-assignment pattern is free to shift shape release to release
        # (it already has once -- see _SECRET_RE's comment); the file-wide search
        # can hit unrelated long backtick strings elsewhere in a 500KB+ bundle, so
        # scope it to the same closure as `foo` by searching backward from foo's
        # position and taking the NEAREST preceding match, not the file's first.
        window_start = max(0, foo.start() - 10000)
        sec_matches = list(_SECRET_RE.finditer(js, window_start, foo.start()))
        if not sec_matches:
            raise ValueError("could not parse secret from _app chunk")
        self._foo = foo.group(1)
        # group(1) is raw template-literal content (real newlines, not JSON-escaped)
        # -- only backtick/backslash need unescaping, unlike the old quoted-string
        # shape which was valid JSON.
        raw = sec_matches[-1].group(1)
        self._secret = raw.replace("\\`", "`").replace("\\\\", "\\")

    # ---- public API -------------------------------------------------------
    def xmas(self, path: str) -> str:
        """Return the x-mas token for a relative API path (e.g. '/api/data/leagues?id=47')."""
        self._ensure()
        body = {"url": path, "code": int(time.time() * 1000), "foo": self._foo}
        body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        signature = hashlib.md5((body_json + self._secret).encode("utf-8")).hexdigest().upper()
        token = json.dumps({"body": body, "signature": signature},
                           separators=(",", ":"), ensure_ascii=False)
        return base64.b64encode(token.encode("utf-8")).decode()

    def get(self, path: str, timeout: int = 25):
        """GET a signed www.fotmob.com API path and return parsed JSON."""
        r = tls_requests.get(HOME + path, headers={"x-mas": self.xmas(path)}, timeout=timeout)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    # smoke test
    auth = FotmobAuth()
    data = auth.get("/api/data/leagues?id=47&season=2025%2F2026")
    print("OK — PL 2025/26 league payload keys:", list(data.keys())[:8])
    print("player stat categories:", len(data["stats"]["players"]))
