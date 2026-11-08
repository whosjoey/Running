"""Thin WHOOP Developer API v2 client (read-only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .http import get_json

BASE = "https://api.prod.whoop.com/developer"


def _get(path: str, params: dict | None = None, cache_ttl: float | None = None) -> Any:
    return get_json("whoop", BASE + path, params, cache_ttl)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _paged(path: str, days: int, end: datetime | None = None, max_records: int = 2000) -> list[dict]:
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params: dict[str, Any] = {"start": _iso(start), "end": _iso(end), "limit": 25}
    out: list[dict] = []
    while True:
        page = _get(path, params, cache_ttl=300)
        out.extend(page.get("records", []))
        token = page.get("next_token")
        if not token or len(out) >= max_records:
            return out
        params["nextToken"] = token


def profile() -> dict:
    return _get("/v2/user/profile/basic", cache_ttl=86400)


def body_measurement() -> dict:
    return _get("/v2/user/measurement/body", cache_ttl=3600)


def recoveries(days: int = 30) -> list[dict]:
    return _paged("/v2/recovery", days)


def cycles(days: int = 30) -> list[dict]:
    return _paged("/v2/cycle", days)


def sleeps(days: int = 30) -> list[dict]:
    return _paged("/v2/activity/sleep", days)


def workouts(days: int = 30) -> list[dict]:
    return _paged("/v2/activity/workout", days)


def raw_get(path: str, params: dict | None = None) -> Any:
    if not path.startswith("/"):
        path = "/" + path
    return _get(path, params)
