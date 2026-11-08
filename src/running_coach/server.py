"""MCP server exposing Strava + WHOOP data and running analytics as Claude tools."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from . import config, oauth, strava, whoop
from .analysis import heart, load, pace, race, recovery
from .analysis.util import (KM, MILE, fmt_duration, fmt_pace, linreg, mean, pace_from_speed,
                            pearson, r, unit_m)

INSTRUCTIONS = """\
You are the user's running coach with live access to their Strava (activities, GPS/HR/cadence streams)
and WHOOP (recovery, HRV, resting HR, sleep, strain) data.
- Start with `connection_status` if unsure what's connected; if HR max/rest/LTHR are unknown, ask the
  user and save them with `update_profile` — zones, TRIMP and load metrics get much more accurate.
- For "how was my run": `analyze_run` (activity_id="latest" works).
- For "how's my training": `training_load`, `intensity_distribution`, `aerobic_fitness_trend`.
- For "should I train hard today": `readiness_check`.
- For race goals: `race_predictor`, `training_paces`.
- Anything else: `strava_get` / `whoop_get` reach any read-only API endpoint; `activity_streams`
  gives raw second-by-second data for custom analysis.
Explain metrics briefly in plain language, be specific with numbers, and give actionable advice.
Be honest about limitations (wrist HR noise, GPS errors, heuristics like ACWR)."""

mcp = _Server("running-coach", instructions=INSTRUCTIONS)


# ====================================================================== helpers

def _profile() -> config.AthleteProfile:
    return config.AthleteProfile.load()


def _units(units: str | None) -> str:
    return units or _profile().units or "km"


def _whoop_connected() -> bool:
    return bool(config.load_tokens("whoop"))


def _strava_connected() -> bool:
    return bool(config.load_tokens("strava"))


def _hr_params() -> dict[str, Any]:
    """Resolve max/resting/LTHR from profile, WHOOP and recent Strava data."""
    p = _profile()
    out: dict[str, Any] = {"lthr": p.lthr, "sex": p.sex, "sources": {}}
    rest = p.resting_hr
    if rest:
        out["sources"]["resting_hr"] = "profile"
    elif _whoop_connected():
        try:
            rows = recovery.recovery_rows(whoop.recoveries(14))
            vals = [x["rhr"] for x in rows if x["rhr"]]
            if vals:
                rest = round(mean(vals))
                out["sources"]["resting_hr"] = "WHOOP 14-day average"
        except Exception:
            pass
    if not rest:
        rest = 60
        out["sources"]["resting_hr"] = "default guess 60 (set it with update_profile)"
    mx = p.max_hr
    if mx:
        out["sources"]["max_hr"] = "profile"
    else:
        observed = None
        if _strava_connected():
            try:
                observed = max((a.get("max_heartrate") or 0 for a in strava.iter_activities(180)), default=0) or None
            except Exception:
                pass
        mx, how = heart.estimate_max_hr(p.birth_year, observed, date.today().year)
        mx = mx or 190
        out["sources"]["max_hr"] = how if how != "unknown" else "default guess 190 (set it with update_profile)"
    out["max_hr"], out["resting_hr"] = mx, rest
    return out


def _zones(hp: dict[str, Any]) -> dict[str, Any]:
    return heart.hr_zones(hp["max_hr"], hp["resting_hr"], hp["lthr"])


def _resolve_activity_id(activity_id: str | int) -> int:
    if str(activity_id).lower() in ("latest", "last", "recent"):
        for a in strava.iter_activities(60):
            return a["id"]
        raise ValueError("No runs found in the last 60 days.")
    return int(activity_id)


def _summary(a: dict, units: str) -> dict[str, Any]:
    um = unit_m(units)
    dist = a.get("distance") or 0
    mt = a.get("moving_time") or 0
    return {
        "id": a["id"], "name": a.get("name"), "date": a.get("start_date_local", "")[:16].replace("T", " "),
        "type": a.get("sport_type") or a.get("type"),
        f"distance_{units}": r(dist / um, 2), "moving_time": fmt_duration(mt),
        "elapsed_time": fmt_duration(a.get("elapsed_time")),
        "pace": fmt_pace(mt / (dist / um), units) if dist else None,
        "avg_hr": a.get("average_heartrate"), "max_hr": a.get("max_heartrate"),
        "elev_gain_m": a.get("total_elevation_gain"),
        "cadence_spm": r((a.get("average_cadence") or 0) * 2, 0) or None,
        "suffer_score": a.get("suffer_score"), "workout_type": a.get("workout_type"),
    }


def _activity_load(a: dict, hp: dict[str, Any]) -> float:
    dur = a.get("moving_time") or 0
    if a.get("average_heartrate"):
        return heart.trimp_summary(dur, a["average_heartrate"], hp["max_hr"], hp["resting_hr"], hp["sex"]) or 0
    # no HR: assume moderate aerobic (≈ 60% HRR)
    return heart.trimp_summary(dur, hp["resting_hr"] + 0.6 * (hp["max_hr"] - hp["resting_hr"]),
                               hp["max_hr"], hp["resting_hr"], hp["sex"]) or 0


def _load_package(days: int, include_all_sports: bool = True) -> dict[str, Any]:
    hp = _hr_params()
    history = max(days, 90) + 42
    acts = list(strava.iter_activities(history, sport_types=None if include_all_sports else strava.RUN_TYPES))
    end = date.today()
    items = [(date.fromisoformat(a["start_date_local"][:10]), _activity_load(a, hp)) for a in acts]
    use_hrtss = bool(hp["lthr"])
    if use_hrtss:
        items = [(d, heart.hrtss(v, hp["lthr"], hp["max_hr"], hp["resting_hr"], hp["sex"]) or 0) for d, v in items]
    daily = load.daily_series(items, end - timedelta(days=history), end)
    return {"hp": hp, "acts": acts, "daily": daily, "pmc": load.pmc(daily),
            "metric": "hrTSS (1h at LTHR = 100)" if use_hrtss else "Banister TRIMP (from avg HR)"}


# ====================================================================== setup / profile

@mcp.tool()
def connection_status() -> dict[str, Any]:
    """Show which services (Strava, WHOOP) are connected and the saved athlete profile."""
    return {"services": oauth.status(), "profile": _profile().to_dict(),
            "how_to_connect": "Run `running-coach auth strava` / `running-coach auth whoop` in a terminal."}


@mcp.tool()
def update_profile(max_hr: int | None = None, resting_hr: int | None = None, lthr: int | None = None,
                   birth_year: int | None = None, sex: str | None = None, units: str | None = None,
                   weight_kg: float | None = None, goal_race: str | None = None,
                   notes: str | None = None) -> dict[str, Any]:
    """Save athlete physiology & preferences used by all analyses. Only provided fields change.
    units: "km" or "mi". lthr: lactate-threshold HR (≈ avg HR of last 20 min of a hard 30-min solo effort).
    goal_race: free text like "sub-1:45 half, 2027-04-12"."""
    p = _profile()
    for k, v in dict(max_hr=max_hr, resting_hr=resting_hr, lthr=lthr, birth_year=birth_year, sex=sex,
                     units=units, weight_kg=weight_kg, goal_race=goal_race, notes=notes).items():
        if v is not None:
            setattr(p, k, v)
    p.save()
    return p.to_dict()


@mcp.tool()
def heart_rate_zones() -> dict[str, Any]:
    """The athlete's 5 HR zones (LTHR/Friel, Karvonen, or %max), plus Strava's own zones if available."""
    hp = _hr_params()
    out = {"zones": _zones(hp), "inputs": {k: hp[k] for k in ("max_hr", "resting_hr", "lthr")},
           "sources": hp["sources"]}
    try:
        out["strava_zones"] = strava.athlete_zones()
    except Exception as e:  # scope or no zones set
        out["strava_zones"] = f"unavailable ({e})"
    return out


# ====================================================================== Strava activities

@mcp.tool()
def list_runs(days: int = 30, include_other_sports: bool = False, units: str | None = None) -> dict[str, Any]:
    """List recent Strava activities (runs by default) with distance, pace, HR, elevation, cadence."""
    u = _units(units)
    acts = list(strava.iter_activities(days, sport_types=None if include_other_sports else strava.RUN_TYPES))
    rows = [_summary(a, u) for a in acts]
    tot = sum(a.get("distance", 0) for a in acts) / unit_m(u)
    return {"count": len(rows), f"total_{u}": r(tot, 1),
            "total_hours": r(sum(a.get("moving_time", 0) for a in acts) / 3600, 1), "activities": rows}


@mcp.tool()
def analyze_run(activity_id: str = "latest", units: str | None = None, split_m: float | None = None) -> dict[str, Any]:
    """Deep analysis of one run: splits (pace, grade-adjusted pace, HR, cadence, elevation), pacing
    strategy (negative/positive split, fade, variability), HR time-in-zone, aerobic decoupling (Pa:HR),
    efficiency factor, cardiac drift, TRIMP/hrTSS, cadence & stride, best efforts, Strava laps.
    activity_id: a Strava id or "latest". split_m: custom split length in meters (default 1 km / 1 mi)."""
    u = _units(units)
    aid = _resolve_activity_id(activity_id)
    a = strava.activity(aid)
    s = strava.streams(aid)
    hp = _hr_params()
    zones = _zones(hp)
    sp = pace.splits(s, u, split_m)
    out: dict[str, Any] = {"summary": _summary(a, u), "description": a.get("description"),
                           "device": a.get("device_name"), "splits": sp,
                           "pacing": pace.pacing_strategy(s, sp, u),
                           "elevation": pace.elevation_summary(s), "cadence": pace.cadence_analysis(s)}
    if s.get("heartrate"):
        tiz = heart.time_in_zones(s, zones["zones"])
        trimp = heart.trimp_stream(s, hp["max_hr"], hp["resting_hr"], hp["sex"])
        ef = heart.efficiency_factor(s)
        out["heart_rate"] = {
            "zones_method": zones["method"], "time_in_zones": tiz,
            "three_zone": heart.three_zone_from_five(tiz),
            "efficiency_factor": r(ef, 3),
            "decoupling": heart.aerobic_decoupling(s),
            "drift": heart.cardiac_drift(s),
            "trimp": r(trimp, 0),
            "hrtss": r(heart.hrtss(trimp, hp["lthr"], hp["max_hr"], hp["resting_hr"], hp["sex"]), 0) if hp["lthr"] else None,
            "hr_param_sources": hp["sources"],
        }
    else:
        out["heart_rate"] = "No HR stream on this activity."
    out["best_efforts_in_run"] = pace.best_efforts(s, units=u)
    if a.get("best_efforts"):
        out["strava_best_efforts"] = [{"name": e["name"], "time": fmt_duration(e["elapsed_time"]),
                                       "pr_rank": e.get("pr_rank")} for e in a["best_efforts"]]
    laps = a.get("laps") or []
    if len(laps) > 1:
        um = unit_m(u)
        out["laps"] = [{"lap": l["lap_index"], f"distance_{u}": r(l["distance"] / um, 2),
                        "time": fmt_duration(l["moving_time"]),
                        "pace": fmt_pace(l["moving_time"] / (l["distance"] / um), u) if l["distance"] else None,
                        "avg_hr": l.get("average_heartrate"), "max_hr": l.get("max_heartrate")} for l in laps]
    if a.get("segment_efforts"):
        out["segment_efforts"] = [{"segment": e["name"], "time": fmt_duration(e["elapsed_time"]),
                                   "pr_rank": e.get("pr_rank"), "kom_rank": e.get("kom_rank")}
                                  for e in a["segment_efforts"][:25]]
    if _whoop_connected():
        try:
            day = a["start_date_local"][:10]
            rec = [x for x in recovery.recovery_rows(whoop.recoveries(max(3, (date.today() - date.fromisoformat(day)).days + 2)))
                   if x["date"] == day]
            if rec:
                out["whoop_that_morning"] = rec[0]
        except Exception:
            pass
    return out


@mcp.tool()
def compare_runs(activity_ids: list[str], units: str | None = None) -> dict[str, Any]:
    """Side-by-side comparison of several runs: pace, GAP, HR, EF, decoupling, cadence, elevation, TRIMP.
    Great for 'same route, am I getting fitter?' questions."""
    u = _units(units)
    hp = _hr_params()
    rows = []
    for raw in activity_ids:
        aid = _resolve_activity_id(raw)
        a = strava.activity(aid)
        s = strava.streams(aid)
        sp = pace.splits(s, u)
        ps = pace.pacing_strategy(s, sp, u)
        dec = heart.aerobic_decoupling(s) if s.get("heartrate") else {}
        rows.append({**_summary(a, u),
                     "gap_first_half": ps.get("first_half_gap"), "gap_second_half": ps.get("second_half_gap"),
                     "split_pct": ps.get("gap_half_split_pct"),
                     "efficiency_factor": r(heart.efficiency_factor(s), 3) if s.get("heartrate") else None,
                     "decoupling_pct": dec.get("decoupling_pct"),
                     "trimp": r(heart.trimp_stream(s, hp["max_hr"], hp["resting_hr"], hp["sex"]), 0) if s.get("heartrate") else None})
    return {"runs": rows}


@mcp.tool()
def personal_bests(days: int = 365, units: str | None = None, max_activities: int = 80) -> dict[str, Any]:
    """Fastest efforts (400m … marathon) found anywhere inside runs over the period, using GPS streams.
    Costs one API call per run (cached forever afterwards); max_activities caps it."""
    u = _units(units)
    best: dict[str, dict] = {}
    n = 0
    for a in strava.iter_activities(days):
        if n >= max_activities:
            break
        n += 1
        try:
            s = strava.streams(a["id"], keys="time,distance")
        except Exception:
            continue
        for e in pace.best_efforts(s, units=u):
            cur = best.get(e["distance"])
            if not cur or e["time_s"] < cur["time_s"]:
                best[e["distance"]] = {**e, "activity_id": a["id"], "date": a["start_date_local"][:10],
                                       "activity": a.get("name")}
    order = list(pace.STANDARD_DISTANCES)
    return {"scanned_runs": n, "bests": sorted(best.values(), key=lambda x: order.index(x["distance"]))}


@mcp.tool()
def activity_streams(activity_id: str = "latest", keys: str = "time,distance,heartrate,velocity_smooth,cadence,altitude,grade_smooth",
                     every_n_seconds: int = 30) -> dict[str, Any]:
    """Raw time-series for custom analysis, downsampled to roughly one sample per `every_n_seconds`.
    velocity is m/s; cadence is per-leg (×2 for steps/min); grade is %."""
    aid = _resolve_activity_id(activity_id)
    s = strava.streams(aid, keys=keys if "time" in keys else "time," + keys)
    t = s.get("time", [])
    idx, last = [], -1e9
    for i, tt in enumerate(t):
        if tt - last >= every_n_seconds:
            idx.append(i)
            last = tt
    return {"activity_id": aid, "samples": len(idx), "original_samples": len(t),
            "streams": {k: [v[i] for i in idx] for k, v in s.items() if k != "latlng"}}


# ====================================================================== training load & fitness

@mcp.tool()
def training_load(days: int = 42, units: str | None = None, include_all_sports: bool = True) -> dict[str, Any]:
    """Fitness / fatigue / form (CTL/ATL/TSB performance-management chart), acute:chronic workload ratio,
    Foster monotony & strain, weekly mileage with ramp rate and long-run share. include_all_sports counts
    cross-training (bike, gym…) toward load, which is usually right for fatigue."""
    u = _units(units)
    pk = _load_package(days, include_all_sports)
    p = pk["pmc"]
    today = p[-1]
    runs = [a for a in pk["acts"] if (a.get("sport_type") or a.get("type")) in strava.RUN_TYPES]
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    wv = load.weekly_volume([a for a in runs if a["start_date_local"][:10] >= cutoff], u)
    ramp_flags = [w["week_of"] for w in wv[:-1] if (w["ramp_pct_vs_prev_week"] or 0) > 10]
    return {
        "load_metric": pk["metric"],
        "today": {**today, "form_interpretation": load.interpret_form(today["tsb"], today["ctl"])},
        "ctl_change_last_28d": r(today["ctl"] - p[-29]["ctl"], 1) if len(p) > 29 else None,
        "acwr": load.acwr(pk["daily"]),
        "monotony_strain": load.monotony_strain(pk["daily"]),
        "weekly_volume": wv,
        "weeks_ramped_over_10pct": ramp_flags,
        "pmc_last_days": p[-days:][::max(1, days // 30)],
        "hr_params": {k: pk["hp"][k] for k in ("max_hr", "resting_hr", "lthr")},
        "hr_param_sources": pk["hp"]["sources"],
    }


@mcp.tool()
def intensity_distribution(days: int = 28, source: str = "auto", max_activities: int = 40) -> dict[str, Any]:
    """Time in HR zones across all training (polarized 80/20 check). source: "whoop" (uses WHOOP workout
    zone durations — cheap), "strava" (HR streams, 1 call/run), or "auto" (WHOOP if connected)."""
    if source == "auto":
        source = "whoop" if _whoop_connected() else "strava"
    secs = [0.0] * 6 if source == "whoop" else [0.0] * 5
    n = 0
    if source == "whoop":
        keys = ["zone_zero_milli", "zone_one_milli", "zone_two_milli", "zone_three_milli",
                "zone_four_milli", "zone_five_milli"]
        for w in whoop.workouts(days):
            zd = (w.get("score") or {}).get("zone_durations") or {}
            if not zd:
                continue
            n += 1
            for k, key in enumerate(keys):
                secs[k] += (zd.get(key) or 0) / 1000
        names = ["Z0 (<50% max)", "Z1 50-60%", "Z2 60-70%", "Z3 70-80%", "Z4 80-90%", "Z5 90-100%"]
        low, mod, high = sum(secs[:3]), secs[3], sum(secs[4:])
    else:
        zones = _zones(_hr_params())["zones"]
        for a in strava.iter_activities(days):
            if n >= max_activities or not a.get("has_heartrate"):
                continue
            n += 1
            for k, z in enumerate(heart.time_in_zones(strava.streams(a["id"], "time,heartrate"), zones)):
                secs[k] += z["seconds"]
        names = [z["zone"] for z in zones]
        low, mod, high = secs[0] + secs[1], secs[2], secs[3] + secs[4]
    tot = sum(secs) or 1
    lo_p, mo_p, hi_p = low / tot * 100, mod / tot * 100, high / tot * 100
    if lo_p >= 75 and hi_p >= mo_p:
        verdict = "Polarized (~80/20) — the distribution most associated with endurance gains."
    elif lo_p >= 70:
        verdict = "Pyramidal — mostly easy with more tempo than high-intensity; also a solid model."
    elif mo_p > 25:
        verdict = "Threshold-heavy / 'grey zone' — too much moderate work; make easy days easier and hard days harder."
    else:
        verdict = "High-intensity heavy — risk of fatigue; add more low-intensity volume."
    return {"source": source, "workouts": n, "total_hours": r(tot / 3600, 1),
            "zones": [{"zone": nm, "hours": r(sx / 3600, 2), "pct": r(sx / tot * 100, 1)} for nm, sx in zip(names, secs)],
            "three_zone": {"low_pct": r(lo_p, 1), "moderate_pct": r(mo_p, 1), "high_pct": r(hi_p, 1)},
            "verdict": verdict}


@mcp.tool()
def aerobic_fitness_trend(days: int = 120, easy_max_pct_hrmax: float = 80, units: str | None = None) -> dict[str, Any]:
    """Is my aerobic engine improving? Tracks efficiency factor (speed per heartbeat) and pace at
    easy heart rates across runs, with a linear trend. Uses activity summaries only (cheap)."""
    u = _units(units)
    hp = _hr_params()
    cap = hp["max_hr"] * easy_max_pct_hrmax / 100
    rows = []
    for a in strava.iter_activities(days):
        hr, v = a.get("average_heartrate"), a.get("average_speed")
        if not hr or not v or hr > cap or (a.get("moving_time") or 0) < 1200:
            continue
        rows.append({"date": a["start_date_local"][:10], "name": a.get("name"), "avg_hr": hr,
                     "pace": fmt_pace(pace_from_speed(v, u), u), "ef": r(v * 60 / hr, 3),
                     "elev_per_km": r((a.get("total_elevation_gain") or 0) / (a["distance"] / KM), 1)})
    rows.sort(key=lambda x: x["date"])
    if len(rows) < 4:
        return {"note": "Not enough easy runs with HR in this window.", "runs": rows}
    d0 = date.fromisoformat(rows[0]["date"])
    xs = [(date.fromisoformat(x["date"]) - d0).days for x in rows]
    fit = linreg(xs, [x["ef"] for x in rows])
    first, last = mean(x["ef"] for x in rows[:len(rows)//3]), mean(x["ef"] for x in rows[-len(rows)//3:])
    return {"easy_hr_cap": round(cap), "runs_used": len(rows),
            "ef_first_third": r(first, 3), "ef_last_third": r(last, 3),
            "ef_change_pct": r((last - first) / first * 100, 1),
            "ef_trend_per_30d": r(fit[0] * 30, 4) if fit else None,
            "interpretation": "EF = meters/min per bpm. Rising EF at easy HR = better aerobic fitness. "
                              "Heat, hills, fatigue and wrist-HR glitches add noise — look at the trend, not single runs.",
            "runs": rows}


# ====================================================================== race prediction

def _parse_time(t: str) -> float:
    parts = [float(x) for x in t.split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def _parse_distance(d: str) -> float:
    key = d.strip().lower()
    alias = {"5k": 5000, "10k": 10000, "half": 21097.5, "half marathon": 21097.5, "hm": 21097.5,
             "marathon": 42195, "mile": MILE, "1 mile": MILE, "1k": 1000, "15k": 15000, "10 mile": 10 * MILE}
    if key in alias:
        return alias[key]
    if key.endswith("km"):
        return float(key[:-2]) * 1000
    if key.endswith("mi"):
        return float(key[:-2]) * MILE
    if key.endswith("m"):
        return float(key[:-1])
    return float(key) * 1000


@mcp.tool()
def race_predictor(distance: str | None = None, time: str | None = None, units: str | None = None,
                   lookback_days: int = 120) -> dict[str, Any]:
    """Predict race times (mile → marathon) with VDOT and Riegel, plus Daniels training paces.
    Give a recent race/time-trial (distance like "5k", "half", "10mi", "3000m"; time "21:30" or "1:45:00"),
    or omit both to auto-use your best recent effort from Strava (≥3 km)."""
    u = _units(units)
    if distance and time:
        d, t, src = _parse_distance(distance), _parse_time(time), "provided"
    else:
        best = None
        for a in strava.iter_activities(lookback_days):
            if (a.get("distance") or 0) < 3000:
                continue
            v = race.vdot(a["distance"], a["moving_time"])
            if not best or v > best[0]:
                best = (v, a)
        if not best:
            return {"note": "No runs ≥ 3 km found; provide distance & time."}
        d, t = best[1]["distance"], best[1]["moving_time"]
        src = f"best recent run: {best[1].get('name')} on {best[1]['start_date_local'][:10]} (moving time; a true race effort is more accurate)"
    pred = race.predictions(d, t, u)
    return {"based_on": {"distance_m": r(d, 0), "time": fmt_duration(t), "source": src},
            **pred, "training_paces": race.training_paces(pred["vdot"], u)}


@mcp.tool()
def training_paces(vdot: float | None = None, distance: str | None = None, time: str | None = None,
                   units: str | None = None) -> dict[str, Any]:
    """Daniels' training paces (Easy, Marathon, Threshold, Interval, Repetition) from a VDOT or a race result."""
    u = _units(units)
    if vdot is None:
        if not (distance and time):
            raise ValueError("Provide vdot, or distance + time.")
        vdot = race.vdot(_parse_distance(distance), _parse_time(time))
    return race.training_paces(vdot, u)


# ====================================================================== WHOOP

@mcp.tool()
def whoop_recovery(days: int = 14) -> dict[str, Any]:
    """Daily WHOOP recovery score, HRV (rMSSD), resting HR, SpO2 and skin temp."""
    rows = recovery.recovery_rows(whoop.recoveries(days))
    return {"days": len(rows), "avg_recovery": r(mean(x["recovery"] for x in rows), 0),
            "avg_hrv_ms": r(mean(x["hrv_ms"] for x in rows), 1),
            "avg_rhr": r(mean(x["rhr"] for x in rows), 1), "daily": rows}


@mcp.tool()
def whoop_sleep(days: int = 14) -> dict[str, Any]:
    """Sleep analysis: duration vs need, sleep debt, deep/REM share, efficiency, bedtime consistency,
    respiratory-rate changes (illness early-warning), plus nightly detail."""
    rows = recovery.sleep_rows(whoop.sleeps(days))
    return {"analysis": recovery.sleep_analysis(rows),
            "nightly": [{k: v for k, v in x.items() if not k.startswith("_")} for x in rows]}


@mcp.tool()
def whoop_strain(days: int = 14) -> dict[str, Any]:
    """Daily WHOOP day strain (0-21), calories and HR, plus individual WHOOP workouts with zone times."""
    cyc = []
    for c in whoop.cycles(days):
        sc = c.get("score") or {}
        cyc.append({"start": c.get("start", "")[:10], "strain": r(sc.get("strain"), 1),
                    "kcal": r((sc.get("kilojoule") or 0) / 4.184, 0), "avg_hr": sc.get("average_heart_rate"),
                    "max_hr": sc.get("max_heart_rate")})
    wos = []
    for w in whoop.workouts(days):
        sc = w.get("score") or {}
        zd = sc.get("zone_durations") or {}
        wos.append({"start": w.get("start", "")[:16], "sport": w.get("sport_name"), "strain": r(sc.get("strain"), 1),
                    "avg_hr": sc.get("average_heart_rate"), "max_hr": sc.get("max_heart_rate"),
                    "distance_km": r((sc.get("distance_meter") or 0) / 1000, 2) or None,
                    "zone_minutes": [r((zd.get(k) or 0) / 60000, 0) for k in
                                     ("zone_zero_milli", "zone_one_milli", "zone_two_milli",
                                      "zone_three_milli", "zone_four_milli", "zone_five_milli")] if zd else None})
    return {"cycles": sorted(cyc, key=lambda x: x["start"]), "workouts": wos}


@mcp.tool()
def hrv_status(days: int = 60) -> dict[str, Any]:
    """HRV-guided training check: 7-day rolling ln(rMSSD) vs your personal normal range
    (baseline ± 0.5 SD), HRV variability (CV), 14-day trend, and resting HR vs baseline."""
    return recovery.hrv_analysis(recovery.recovery_rows(whoop.recoveries(days)))


# ====================================================================== combined

@mcp.tool()
def readiness_check() -> dict[str, Any]:
    """'Should I go hard today?' Combines WHOOP recovery, HRV trend, last night's sleep,
    training form (TSB) and ACWR into a traffic-light recommendation."""
    rec_score = hrv = sl = tsb_rel = acwr_v = None
    detail: dict[str, Any] = {}
    if _whoop_connected():
        rows = recovery.recovery_rows(whoop.recoveries(60))
        if rows:
            rec_score = rows[-1]["recovery"]
            detail["today_whoop"] = rows[-1]
        hrv = recovery.hrv_analysis(rows)
        sl = recovery.sleep_analysis(recovery.sleep_rows(whoop.sleeps(7)))
        detail["hrv"], detail["sleep"] = hrv, sl
    if _strava_connected():
        pk = _load_package(42)
        t = pk["pmc"][-1]
        tsb_rel = (t["tsb"] / t["ctl"] * 100) if t["ctl"] else None
        a = load.acwr(pk["daily"])
        acwr_v = a.get("acwr_ewma")
        detail["form"] = {**t, "interpretation": load.interpret_form(t["tsb"], t["ctl"])}
        detail["acwr"] = a
        last = pk["acts"][0] if pk["acts"] else None
        if last:
            detail["last_activity"] = _summary(last, _units(None))
    return {**recovery.readiness(rec_score, hrv if hrv and "status" in hrv else None, tsb_rel, acwr_v, sl),
            "detail": detail}


@mcp.tool()
def recovery_vs_performance(days: int = 120) -> dict[str, Any]:
    """Do my WHOOP recovery / HRV / sleep predict how my runs go? Correlates morning metrics with
    that day's run efficiency (EF = speed/HR) and pace. Needs both services connected."""
    rec = {x["date"]: x for x in recovery.recovery_rows(whoop.recoveries(days))}
    slp = {x["date"]: x for x in recovery.sleep_rows(whoop.sleeps(days))}
    pairs = []
    for a in strava.iter_activities(days):
        d = a["start_date_local"][:10]
        if d not in rec or not a.get("average_heartrate") or (a.get("moving_time") or 0) < 900:
            continue
        pairs.append({"date": d, "recovery": rec[d]["recovery"], "hrv": rec[d]["hrv_ms"], "rhr": rec[d]["rhr"],
                      "sleep_h": slp.get(d, {}).get("asleep_h"),
                      "ef": a["average_speed"] * 60 / a["average_heartrate"], "avg_hr": a["average_heartrate"],
                      "speed": a["average_speed"]})
    if len(pairs) < 8:
        return {"note": f"Only {len(pairs)} matched run-days; need ~8+ for meaningful correlations.", "pairs": pairs}
    def corr(x, y):
        return r(pearson([p[x] for p in pairs], [p[y] for p in pairs]), 2)
    buckets = {"green (>=67)": [p for p in pairs if p["recovery"] >= 67],
               "yellow (34-66)": [p for p in pairs if 34 <= p["recovery"] < 67],
               "red (<34)": [p for p in pairs if p["recovery"] < 34]}
    return {
        "matched_runs": len(pairs),
        "correlations_with_efficiency_factor": {"recovery": corr("recovery", "ef"), "hrv": corr("hrv", "ef"),
                                                "resting_hr": corr("rhr", "ef"), "sleep_hours": corr("sleep_h", "ef")},
        "correlations_with_speed": {"recovery": corr("recovery", "speed"), "hrv": corr("hrv", "speed")},
        "ef_by_recovery_bucket": {k: {"runs": len(v), "avg_ef": r(mean(p["ef"] for p in v), 3)} for k, v in buckets.items()},
        "notes": "r ≈ ±0.1 weak, ±0.3 moderate, ±0.5 strong. Speed is confounded by workout type (easy vs. intervals); "
                 "EF is the fairer comparison. Correlation ≠ causation.",
    }


@mcp.tool()
def weekly_report(units: str | None = None) -> dict[str, Any]:
    """One-shot weekly summary: this week vs last week volume, runs, load/form, ACWR, WHOOP recovery,
    HRV and sleep averages. Use as the basis for a coach-style weekly review."""
    u = _units(units)
    out: dict[str, Any] = {"profile_goal": _profile().goal_race}
    if _strava_connected():
        pk = _load_package(28)
        runs = [a for a in pk["acts"] if (a.get("sport_type") or a.get("type")) in strava.RUN_TYPES]
        wv = load.weekly_volume(runs, u)
        out["weeks"] = wv[-4:]
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        out["this_week_runs"] = [_summary(a, u) for a in runs if a["start_date_local"][:10] >= monday.isoformat()]
        t = pk["pmc"][-1]
        out["form"] = {**t, "interpretation": load.interpret_form(t["tsb"], t["ctl"])}
        out["acwr"] = load.acwr(pk["daily"])
        out["monotony_strain"] = load.monotony_strain(pk["daily"])
    if _whoop_connected():
        rows = recovery.recovery_rows(whoop.recoveries(60))
        out["whoop_7d"] = {"avg_recovery": r(mean(x["recovery"] for x in rows[-7:]), 0),
                           "avg_hrv_ms": r(mean(x["hrv_ms"] for x in rows[-7:]), 1),
                           "avg_rhr": r(mean(x["rhr"] for x in rows[-7:]), 1)}
        out["hrv"] = recovery.hrv_analysis(rows)
        out["sleep"] = recovery.sleep_analysis(recovery.sleep_rows(whoop.sleeps(14)))
    return out


# ====================================================================== raw access

@mcp.tool()
def strava_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Read-only escape hatch for ANY Strava API v3 GET endpoint, e.g. "/athlete",
    "/athletes/{id}/stats", "/activities/{id}/laps", "/segments/{id}", "/athlete/clubs", "/routes/{id}",
    "/gear/{id}". See https://developers.strava.com/docs/reference/"""
    return strava.raw_get(path, params)


@mcp.tool()
def whoop_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Read-only escape hatch for ANY WHOOP API GET endpoint, e.g. "/v2/user/profile/basic",
    "/v2/user/measurement/body", "/v2/cycle", "/v2/cycle/{id}/recovery", "/v2/activity/sleep/{id}".
    See https://developer.whoop.com/api"""
    return whoop.raw_get(path, params)


# ====================================================================== prompts

@mcp.prompt()
def weekly_review() -> str:
    """Coach-style review of the past week with a plan for next week."""
    return ("Run `weekly_report`, `training_load`, and `intensity_distribution`. Then write a concise coach's "
            "review: what went well, red flags (load spikes, poor recovery, grey-zone training, sleep debt), "
            "and a specific day-by-day plan for next week with target paces from `training_paces` "
            "and HR zones from `heart_rate_zones`, tailored to my goal race if set.")


@mcp.prompt()
def analyze_latest_run() -> str:
    """Break down the most recent run."""
    return ("Run `analyze_run` with activity_id='latest'. Explain pacing (splits, GAP, negative/positive split), "
            "heart rate (zones, drift, decoupling), cadence, and how my WHOOP recovery that morning may have "
            "affected it. Finish with 2-3 concrete takeaways.")


@mcp.prompt()
def race_plan(race: str = "half marathon", race_date: str = "", goal_time: str = "") -> str:
    """Build a race-specific plan from current fitness."""
    return (f"I'm targeting a {race} on {race_date or 'a date TBD'} with goal {goal_time or 'TBD'}. "
            "Use `race_predictor`, `training_load`, `aerobic_fitness_trend`, `personal_bests`, and "
            "`intensity_distribution` to assess whether the goal is realistic, then build a periodized plan "
            "(base/build/peak/taper) with weekly volume targets that respect ~10% ramp, key workouts at "
            "Daniels paces, and a race-day pacing + fueling strategy.")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
