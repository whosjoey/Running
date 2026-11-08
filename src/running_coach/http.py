"""Shared HTTP helper with on-disk caching for immutable resources."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from . import config
from .oauth import get_access_token


class ApiError(RuntimeError):
    pass


def _cache_path(key: str):
    d = config.home() / "cache"
    d.mkdir(exist_ok=True)
    return d / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def get_json(provider: str, url: str, params: dict[str, Any] | None = None,
             cache_ttl: float | None = None) -> Any:
    """GET with bearer auth. cache_ttl=None → no cache; float('inf') → cache forever."""
    key = provider + url + json.dumps(params or {}, sort_keys=True)
    cp = _cache_path(key)
    if cache_ttl and cp.exists() and (time.time() - cp.stat().st_mtime) < cache_ttl:
        return json.loads(cp.read_text())

    token = get_access_token(provider)
    for attempt in range(3):
        r = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if r.status_code == 429:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            usage = r.headers.get("X-RateLimit-Usage") or r.headers.get("X-RateLimit-Remaining")
            raise ApiError(f"{provider} rate limit hit (usage: {usage}). Try again in ~15 minutes.")
        if r.status_code >= 500 and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        break
    if r.status_code == 404:
        raise ApiError(f"{provider}: not found — {url}")
    if r.status_code >= 400:
        raise ApiError(f"{provider} API error {r.status_code}: {r.text[:300]}")
    data = r.json()
    if cache_ttl:
        cp.write_text(json.dumps(data))
    return data
