"""Training load: fitness/fatigue/form (PMC), ACWR, monotony & strain, weekly volume and ramp."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from .util import KM, MILE, mean, r, stdev


def daily_series(items: Iterable[tuple[date, float]], start: date, end: date) -> list[tuple[date, float]]:
    agg: dict[date, float] = defaultdict(float)
    for d, v in items:
        if v:
            agg[d] += v
    out, d = [], start
    while d <= end:
        out.append((d, agg.get(d, 0.0)))
        d += timedelta(days=1)
    return out


def pmc(daily: list[tuple[date, float]], ctl_days: int = 42, atl_days: int = 7) -> list[dict[str, Any]]:
    """Performance Management Chart (Banister impulse-response, EWMA form).
    CTL = 'fitness' (42d), ATL = 'fatigue' (7d), TSB = 'form' = yesterday's CTL - ATL."""
    kc, ka = 1 - math.exp(-1 / ctl_days), 1 - math.exp(-1 / atl_days)
    ctl = atl = 0.0
    out = []
    for d, load in daily:
        tsb = ctl - atl
        ctl += (load - ctl) * kc
        atl += (load - atl) * ka
        out.append({"date": d.isoformat(), "load": r(load, 1), "ctl": r(ctl, 1),
                    "atl": r(atl, 1), "tsb": r(tsb, 1)})
    return out


def acwr(daily: list[tuple[date, float]]) -> dict[str, Any]:
    """Acute:Chronic Workload Ratio. Rolling (7d / 28d avg-week) and EWMA (Williams et al. 2017)."""
    loads = [v for _, v in daily]
    if len(loads) < 28:
        return {"note": "Need >= 28 days of history."}
    acute = sum(loads[-7:])
    chronic = sum(loads[-28:]) / 4
    la, lc = 2 / (7 + 1), 2 / (28 + 1)
    ea = ec = loads[0]
    for x in loads[1:]:
        ea = x * la + (1 - la) * ea
        ec = x * lc + (1 - lc) * ec
    ratio = acute / chronic if chronic else None
    ewma = ea / ec if ec else None
    ref = ewma or ratio
    zone = ("no chronic base" if ref is None else
            "undertraining / detraining (<0.8)" if ref < 0.8 else
            "sweet spot (0.8-1.3)" if ref <= 1.3 else
            "caution (1.3-1.5) — elevated injury risk" if ref <= 1.5 else
            "danger (>1.5) — spike in load, high injury risk")
    return {"acute_7d": r(acute, 0), "chronic_weekly_avg_28d": r(chronic, 0),
            "acwr_rolling": r(ratio, 2), "acwr_ewma": r(ewma, 2), "zone": zone,
            "caveat": "ACWR is a population heuristic (Gabbett 2016) with debated validity; use as one signal, not a rule."}


def monotony_strain(daily: list[tuple[date, float]]) -> dict[str, Any]:
    """Foster (1998): monotony = mean/sd of daily load over 7 days; strain = weekly load × monotony."""
    last = [v for _, v in daily[-7:]]
    if len(last) < 7:
        return {}
    m, sd = mean(last), stdev(last)
    mono = m / sd if sd else None
    return {"weekly_load": r(sum(last), 0), "monotony": r(mono, 2),
            "strain": r(sum(last) * mono, 0) if mono else None,
            "note": "Monotony > 2.0 means too little hard/easy variation — associated with illness & overtraining."}


def weekly_volume(activities: list[dict], units: str = "km") -> list[dict[str, Any]]:
    """Group Strava activity summaries by ISO week (Mon start)."""
    um = MILE if units == "mi" else KM
    weeks: dict[date, dict[str, Any]] = {}
    for a in activities:
        d = date.fromisoformat(a["start_date_local"][:10])
        wk = d - timedelta(days=d.weekday())
        w = weeks.setdefault(wk, {"dist": 0.0, "time": 0.0, "runs": 0, "elev": 0.0, "long": 0.0})
        w["dist"] += a.get("distance", 0)
        w["time"] += a.get("moving_time", 0)
        w["elev"] += a.get("total_elevation_gain", 0) or 0
        w["runs"] += 1
        w["long"] = max(w["long"], a.get("distance", 0))
    out, prev = [], None
    for wk in sorted(weeks):
        w = weeks[wk]
        dist = w["dist"] / um
        ramp = (dist - prev) / prev * 100 if prev else None
        out.append({"week_of": wk.isoformat(), f"distance_{units}": r(dist, 1),
                    "hours": r(w["time"] / 3600, 2), "runs": w["runs"], "elev_gain_m": r(w["elev"], 0),
                    f"long_run_{units}": r(w["long"] / um, 1),
                    "long_run_pct_of_week": r(w["long"] / w["dist"] * 100, 0) if w["dist"] else None,
                    "ramp_pct_vs_prev_week": r(ramp, 0)})
        prev = dist
    return out


def interpret_form(tsb: float | None, ctl: float | None) -> str:
    if tsb is None or not ctl:
        return "not enough history"
    rel = tsb / ctl * 100
    if rel > 25:
        return "very fresh — possibly losing fitness if sustained (good for race day)"
    if rel > 5:
        return "fresh — good window for a race or key workout"
    if rel > -10:
        return "neutral — normal training"
    if rel > -30:
        return "productive fatigue — building fitness, keep easy days easy"
    return "high fatigue / overreaching risk — consider recovery days"
