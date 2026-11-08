"""OAuth2 authorization-code flow for Strava and WHOOP, with token refresh.

Run once per provider:  `running-coach auth strava`  /  `running-coach auth whoop`
A browser opens, you approve, a tiny local server on localhost catches the redirect,
and tokens are stored in ~/.running-coach/tokens.json (chmod 600).
"""
from __future__ import annotations

import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any

import httpx

from . import config


@dataclass(frozen=True)
class Provider:
    name: str
    auth_url: str
    token_url: str
    scope: str
    scope_sep: str
    client_id_env: str
    client_secret_env: str

    @property
    def client_id(self) -> str:
        v = config.env(self.client_id_env)
        if not v:
            raise RuntimeError(f"{self.client_id_env} is not set. See README → Setup.")
        return v

    @property
    def client_secret(self) -> str:
        v = config.env(self.client_secret_env)
        if not v:
            raise RuntimeError(f"{self.client_secret_env} is not set. See README → Setup.")
        return v


STRAVA = Provider(
    name="strava",
    auth_url="https://www.strava.com/oauth/authorize",
    token_url="https://www.strava.com/oauth/token",
    scope="read,read_all,profile:read_all,activity:read_all",
    scope_sep=",",
    client_id_env="STRAVA_CLIENT_ID",
    client_secret_env="STRAVA_CLIENT_SECRET",
)

WHOOP = Provider(
    name="whoop",
    auth_url="https://api.prod.whoop.com/oauth/oauth2/auth",
    token_url="https://api.prod.whoop.com/oauth/oauth2/token",
    scope="offline read:recovery read:cycles read:workout read:sleep read:profile read:body_measurement",
    scope_sep=" ",
    client_id_env="WHOOP_CLIENT_ID",
    client_secret_env="WHOOP_CLIENT_SECRET",
)

PROVIDERS = {"strava": STRAVA, "whoop": WHOOP}


def redirect_uri() -> str:
    return f"http://localhost:{config.oauth_port()}/callback"


def _normalize(resp: dict[str, Any]) -> dict[str, Any]:
    out = dict(resp)
    if "expires_at" not in out and "expires_in" in out:
        out["expires_at"] = int(time.time()) + int(out["expires_in"])
    return out


def authorize(provider_name: str, open_browser: bool = True, timeout: int = 300) -> dict[str, Any]:
    p = PROVIDERS[provider_name]
    state = secrets.token_urlsafe(16)  # WHOOP requires >= 8 chars
    params = {
        "client_id": p.client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": p.scope,
        "state": state,
    }
    if p is STRAVA:
        params["approval_prompt"] = "auto"
    url = f"{p.auth_url}?{urllib.parse.urlencode(params)}"

    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                result.update({k: v[0] for k, v in q.items()})
                ok = "code" in q and q.get("state", [""])[0] == state
                body = ("<h2>Connected! You can close this tab.</h2>" if ok
                        else f"<h2>Authorization failed: {q.get('error', ['state mismatch'])[0]}</h2>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body.encode())
                done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):  # silence
            pass

    server = http.server.HTTPServer(("localhost", config.oauth_port()), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\nOpen this URL to authorize {p.name}:\n\n  {url}\n")
    if open_browser:
        webbrowser.open(url)
    try:
        if not done.wait(timeout):
            raise TimeoutError("Timed out waiting for the OAuth redirect.")
    finally:
        server.shutdown()

    if "error" in result:
        raise RuntimeError(f"{p.name} authorization error: {result['error']}")
    if result.get("state") != state:
        raise RuntimeError("OAuth state mismatch — aborting.")

    r = httpx.post(p.token_url, data={
        "client_id": p.client_id,
        "client_secret": p.client_secret,
        "code": result["code"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
    }, timeout=30)
    r.raise_for_status()
    tokens = _normalize(r.json())
    tokens.pop("athlete", None)
    config.save_tokens(p.name, tokens)
    return tokens


def get_access_token(provider_name: str) -> str:
    """Return a valid access token, refreshing it if it expires within 2 minutes."""
    p = PROVIDERS[provider_name]
    tokens = config.load_tokens(p.name)
    if not tokens:
        raise RuntimeError(
            f"{p.name} is not connected. Run `running-coach auth {p.name}` in a terminal first."
        )
    if tokens.get("expires_at", 0) - 120 > time.time():
        return tokens["access_token"]
    data = {
        "client_id": p.client_id,
        "client_secret": p.client_secret,
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }
    if p is WHOOP:
        data["scope"] = "offline"
    r = httpx.post(p.token_url, data=data, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(
            f"Could not refresh {p.name} token ({r.status_code}: {r.text[:200]}). "
            f"Re-run `running-coach auth {p.name}`."
        )
    new = _normalize(r.json())
    new.setdefault("refresh_token", tokens["refresh_token"])
    config.save_tokens(p.name, new)
    return new["access_token"]


def status() -> dict[str, Any]:
    out = {}
    for name, p in PROVIDERS.items():
        t = config.load_tokens(name)
        out[name] = {
            "credentials_configured": bool(config.env(p.client_id_env) and config.env(p.client_secret_env)),
            "connected": bool(t),
            "token_expires_at": t.get("expires_at") if t else None,
        }
    return out
