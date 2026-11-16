"""Shared helpers: settings from .env, HTTP JSON calls, France Travail token."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()


def http_json(url, data=None, headers=None, retries=3):
    """GET (or POST when data is given) and decode JSON; retries on rate limits and server errors."""
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code} on {url.split('?')[0]}: {e.read().decode()[:200]}") from None


_token = {"value": None, "expires": 0}


def france_travail_token(scope="api_labonneboitev2 search office"):
    """OAuth2 client-credentials token, cached until shortly before it expires."""
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ENV["FRANCE_TRAVAIL_CLIENT_ID"],
        "client_secret": ENV["FRANCE_TRAVAIL_CLIENT_SECRET"],
        "scope": scope,
    }).encode()
    r = http_json("https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire", data,
                  {"Content-Type": "application/x-www-form-urlencoded"})
    _token.update(value=r["access_token"], expires=time.time() + r.get("expires_in", 1500))
    return _token["value"]
