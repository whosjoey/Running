"""Exercise every MCP tool end-to-end with the Strava/WHOOP clients mocked out."""
import asyncio
from datetime import date, datetime, timedelta

import pytest

from test_analysis import make_recovery, make_run


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNING_COACH_HOME", str(tmp_path))
    from running_coach import config, server, strava, whoop
    config.save_tokens("strava", {"access_token": "x", "refresh_token": "y", "expires_at": 9e12})
    config.save_tokens("whoop", {"access_token": "x", "refresh_token": "y", "expires_at": 9e12})

    today = date.today()
    acts = []
    for i in range(90):
        d = today - timedelta(days=i)
        if i % 7 in (2, 5):
            continue
        acts.append({"id": 1000 + i, "name": f"Run {i}", "sport_type": "Run",
                     "start_date_local": d.isoformat() + "T07:00:00Z",
                     "distance": 10000 + (i % 3) * 2000, "moving_time": 3000 + (i % 3) * 600,
                     "elapsed_time": 3100, "average_speed": 3.33, "average_heartrate": 145 + (i % 4),
                     "max_heartrate": 175, "has_heartrate": True, "total_elevation_gain": 50,
                     "average_cadence": 86})
    run = make_run(km=12, fade=0.05, hr_start=140, hr_end=158)

    monkeypatch.setattr(strava, "iter_activities",
                        lambda days=30, sport_types=strava.RUN_TYPES, before=None:
                        iter([a for a in acts if a["start_date_local"][:10] >= (today - timedelta(days=days)).isoformat()]))
    monkeypatch.setattr(strava, "activity", lambda aid: {**acts[0], "id": aid, "laps": [], "best_efforts": []})
    monkeypatch.setattr(strava, "streams", lambda aid, keys=None: run)
    monkeypatch.setattr(strava, "athlete_zones", lambda: {"heart_rate": {"zones": []}})
    recs = make_recovery(days=60)
    for k, rr in enumerate(recs):
        rr["created_at"] = (today - timedelta(days=59 - k)).isoformat() + "T11:00:00.000Z"
    monkeypatch.setattr(whoop, "recoveries", lambda days=30: recs[-days:])
    monkeypatch.setattr(whoop, "sleeps", lambda days=30: [])
    monkeypatch.setattr(whoop, "cycles", lambda days=30: [{"start": "2026-01-01T00:00:00Z", "score": {"strain": 12.3, "kilojoule": 9000}}])
    monkeypatch.setattr(whoop, "workouts", lambda days=30: [{"start": "2026-01-01T07:00:00Z", "sport_name": "running",
        "score": {"strain": 10, "zone_durations": {"zone_zero_milli": 0, "zone_one_milli": 6e5, "zone_two_milli": 24e5,
                                                   "zone_three_milli": 3e5, "zone_four_milli": 3e5, "zone_five_milli": 6e4}}}])
    return server


def call(server, name, **kw):
    fn = getattr(server, name)
    fn = getattr(fn, "fn", fn)
    return fn(**kw)


@pytest.mark.parametrize("name,kw", [
    ("connection_status", {}),
    ("heart_rate_zones", {}),
    ("list_runs", {"days": 14}),
    ("analyze_run", {"activity_id": "latest"}),
    ("compare_runs", {"activity_ids": ["1", "2"]}),
    ("personal_bests", {"days": 30, "max_activities": 3}),
    ("activity_streams", {"activity_id": "1"}),
    ("training_load", {"days": 42}),
    ("intensity_distribution", {"source": "whoop"}),
    ("intensity_distribution", {"source": "strava", "max_activities": 3}),
    ("aerobic_fitness_trend", {"days": 90}),
    ("race_predictor", {"distance": "half", "time": "1:45:00"}),
    ("race_predictor", {}),
    ("training_paces", {"vdot": 50}),
    ("whoop_recovery", {}),
    ("whoop_sleep", {}),
    ("whoop_strain", {}),
    ("hrv_status", {}),
    ("readiness_check", {}),
    ("recovery_vs_performance", {"days": 50}),
    ("weekly_report", {}),
])
def test_tool_runs(srv, name, kw):
    out = call(srv, name, **kw)
    assert out


def test_analyze_run_content(srv):
    out = call(srv, "analyze_run", activity_id="123")
    assert len(out["splits"]) == 12
    assert out["heart_rate"]["decoupling"]["decoupling_pct"] > 0
    assert out["pacing"]["half_split_pct"] > 0


def test_update_profile_used(srv):
    call(srv, "update_profile", max_hr=192, resting_hr=48, lthr=172, units="mi")
    z = call(srv, "heart_rate_zones")
    assert "LTHR" in z["zones"]["method"]
    tl = call(srv, "training_load", days=28)
    assert tl["load_metric"].startswith("hrTSS")
    assert "distance_mi" in tl["weekly_volume"][0]


def test_tools_registered(srv):
    tools = asyncio.run(srv.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"analyze_run", "readiness_check", "strava_get", "whoop_get"} <= names


def test_install_claude_merges(tmp_path):
    import json
    from running_coach import cli
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"preferences": {"x": 1}, "mcpServers": {"other": {"command": "foo"}}}))
    cli.install_claude_desktop(cfg)
    data = json.loads(cfg.read_text())
    assert data["preferences"] == {"x": 1}
    assert set(data["mcpServers"]) == {"other", "running-coach"}
    assert (tmp_path / "claude_desktop_config.json.bak").exists()
    cli.install_claude_desktop(tmp_path / "new" / "c.json")
