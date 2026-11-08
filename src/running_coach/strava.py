"""Thin Strava API v3 client (read-only)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .http import get_json

BASE = "https://www.strava.com/api/v3"
FOREVER = float("inf")
STREAM_KEYS = "time,distance,latlng,altitude,velocity_smooth,heartrate,cadence,watts,temp,moving,grade_smooth"
RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}


def _get(path: str, params: dict | None = None, cache_ttl: float | None = None) -> Any:
    return get_json("strava", BASE + path, params, cache_ttl)


def athlete() -> dict:
    return _get("/athlete", cache_ttl=3600)


def athlete_stats(athlete_id: int) -> dict:
    return _get(f"/athletes/{athlete_id}/stats", cache_ttl=1800)


def athlete_zones() -> dict:
    return _get("/athlete/zones", cache_ttl=86400)


def list_activities(after: datetime | None = None, before: datetime | None = None,
                    page: int = 1, per_page: int = 200) -> list[dict]:
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if after:
        params["after"] = int(after.timestamp())
    if before:
        params["before"] = int(before.timestamp())
    return _get("/athlete/activities", params, cache_ttl=300)


def iter_activities(days: int = 30, sport_types: set[str] | None = RUN_TYPES,
                    before: datetime | None = None) -> Iterator[dict]:
    after = (before or datetime.now(timezone.utc)) - timedelta(days=days)
    page = 1
    while True:
        batch = list_activities(after=after, before=before, page=page)
        for a in batch:
            if sport_types is None or a.get("sport_type", a.get("type")) in sport_types:
                yield a
        if len(batch) < 200:
            return
        page += 1


def activity(activity_id: int) -> dict:
    return _get(f"/activities/{activity_id}", {"include_all_efforts": "true"}, cache_ttl=FOREVER)


def laps(activity_id: int) -> list[dict]:
    return _get(f"/activities/{activity_id}/laps", cache_ttl=FOREVER)


def activity_zones(activity_id: int) -> list[dict]:
    return _get(f"/activities/{activity_id}/zones", cache_ttl=FOREVER)


def streams(activity_id: int, keys: str = STREAM_KEYS) -> dict[str, list]:
    """Returns {key: data_list}. Missing sensors are simply absent."""
    raw = _get(f"/activities/{activity_id}/streams",
               {"keys": keys, "key_by_type": "true"}, cache_ttl=FOREVER)
    if isinstance(raw, list):  # older format
        return {s["type"]: s["data"] for s in raw}
    return {k: v["data"] for k, v in raw.items() if isinstance(v, dict) and "data" in v}


def raw_get(path: str, params: dict | None = None) -> Any:
    if not path.startswith("/"):
        path = "/" + path
    return _get(path, params)
