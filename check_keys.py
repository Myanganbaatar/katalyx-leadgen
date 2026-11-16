"""Check that the keys in .env work, without printing them."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

env = dict(l.split("=", 1) for l in Path(".env").read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))
env = {k.strip(): v.strip() for k, v in env.items()}


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


for k in ("GEMINI_API_KEY", "FRANCE_TRAVAIL_CLIENT_ID", "FRANCE_TRAVAIL_CLIENT_SECRET"):
    print(k, "set" if env.get(k) else "MISSING", len(env.get(k, "")))

# Gemini: list models
s, body = call("https://generativelanguage.googleapis.com/v1beta/models?pageSize=100", headers={"x-goog-api-key": env.get("GEMINI_API_KEY", "")})
print("Gemini:", s, [m["name"] for m in json.loads(body).get("models", []) if "flash" in m["name"]][:8] if s == 200 else body[:200])

# France Travail: OAuth token, trying the scopes of the two APIs
for scope in ("api_labonneboitev2 search office", "api_labonneboitev2", "api_pagesemployeursv1"):
    data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": env.get("FRANCE_TRAVAIL_CLIENT_ID", ""),
                                   "client_secret": env.get("FRANCE_TRAVAIL_CLIENT_SECRET", ""), "scope": scope}).encode()
    s, body = call("https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire", data,
                   {"Content-Type": "application/x-www-form-urlencoded"})
    print("FT token", repr(scope), s, (json.loads(body).get("scope") if s == 200 else body[:160]))
