"""Pacing analysis from Strava streams: splits, GAP, split strategy, best efforts, cadence."""
from __future__ import annotations

from typing import Any

from .util import (KM, MILE, fmt_duration, fmt_pace, linreg, mean, pearson, r, stdev,
                   unit_m, wmean)

STANDARD_DISTANCES = {
    "400m": 400, "800m": 800, "1k": 1000, "1 mile": MILE, "2 mile": 2 * MILE,
    "5k": 5000, "10k": 10000, "15k": 15000, "10 mile": 10 * MILE,
    "half marathon": 21097.5, "30k": 30000, "marathon": 42195,
}


def minetti_factor(grade: float) -> float:
    """Metabolic cost multiplier vs flat (Minetti et al. 2002). grade is a fraction (0.05 = 5%)."""
    i = max(-0.45, min(0.45, grade))
    c = 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6
    return c / 3.6


def _samples(s: dict[str, list]):
    """Yield per-sample increments: (i, dt, dd, moving, grade_frac)."""
    t, d = s["time"], s["distance"]
    moving = s.get("moving")
    grade = s.get("grade_smooth")
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        dd = max(0.0, d[i] - d[i - 1])
        if dt <= 0:
            continue
        mv = moving[i] if moving else (dd / dt > 0.5)
        g = (grade[i] / 100.0) if grade and grade[i] is not None else 0.0
        yield i, dt, dd, bool(mv), g


def gap_distance_stream(s: dict[str, list]) -> list[float]:
    """Cumulative grade-adjusted ('equivalent flat') distance."""
    out = [0.0]
    for i, dt, dd, mv, g in _samples(s):
        while len(out) < i:
            out.append(out[-1])
        out.append(out[-1] + dd * minetti_factor(g))
    while len(out) < len(s["time"]):
        out.append(out[-1])
    return out


def splits(s: dict[str, list], units: str = "km", split_m: float | None = None) -> list[dict[str, Any]]:
    """Per-km / per-mile (or custom) splits with pace, GAP, HR, cadence and elevation."""
    if "time" not in s or "distance" not in s:
        return []
    size = split_m or unit_m(units)
    hr, cad, alt = s.get("heartrate"), s.get("cadence"), s.get("altitude")
    buckets: list[dict[str, Any]] = []

    def new_bucket():
        return {"dist": 0.0, "gap_dist": 0.0, "elapsed": 0.0, "moving": 0.0, "hr": [], "hr_w": [],
                "cad": [], "cad_w": [], "gain": 0.0, "loss": 0.0, "max_hr": None}

    cur = new_bucket()
    boundary = size
    d = s["distance"]
    for i, dt, dd, mv, g in _samples(s):
        cur["dist"] += dd
        cur["elapsed"] += dt
        if mv:
            cur["moving"] += dt
            cur["gap_dist"] += dd * minetti_factor(g)
            if hr and hr[i]:
                cur["hr"].append(hr[i]); cur["hr_w"].append(dt)
                cur["max_hr"] = max(cur["max_hr"] or 0, hr[i])
            if cad and cad[i]:
                cur["cad"].append(cad[i] * 2); cur["cad_w"].append(dt)
        if alt and alt[i] is not None and alt[i - 1] is not None:
            da = alt[i] - alt[i - 1]
            cur["gain" if da > 0 else "loss"] += abs(da)
        if d[i] >= boundary:
            buckets.append(cur)
            cur = new_bucket()
            boundary += size
    if cur["dist"] > size * 0.05:
        buckets.append(cur)

    out = []
    for n, b in enumerate(buckets, 1):
        frac = b["dist"] / size if size else 1
        pace = b["moving"] / frac if frac > 0 and b["moving"] else None
        gap = b["moving"] / (b["gap_dist"] / size) if b["gap_dist"] > 0 else None
        out.append({
            "split": n,
            "distance_m": r(b["dist"], 0),
            "moving_time": fmt_duration(b["moving"]),
            "moving_s": r(b["moving"], 0),
            "pace": fmt_pace(pace, units),
            "pace_s": r(pace, 0),
            "gap": fmt_pace(gap, units),
            "gap_s": r(gap, 0),
            "avg_hr": r(wmean(b["hr"], b["hr_w"]), 0),
            "max_hr": b["max_hr"],
            "cadence_spm": r(wmean(b["cad"], b["cad_w"]), 0),
            "elev_gain_m": r(b["gain"], 0),
            "elev_loss_m": r(b["loss"], 0),
            "partial": frac < 0.98,
        })
    return out


def _moving_time_between(s, d_lo: float, d_hi: float) -> tuple[float, float]:
    """(moving_time, gap_distance) for samples whose distance lies in (d_lo, d_hi]."""
    d = s["distance"]
    t_mv = gap = 0.0
    for i, dt, dd, mv, g in _samples(s):
        if d_lo < d[i] <= d_hi and mv:
            t_mv += dt
            gap += dd * minetti_factor(g)
    return t_mv, gap


def pacing_strategy(s: dict[str, list], split_list: list[dict], units: str = "km") -> dict[str, Any]:
    """Half-vs-half split, fade, pace variability, and a plain-English verdict."""
    if not split_list or "distance" not in s:
        return {}
    total = s["distance"][-1]
    t1, g1 = _moving_time_between(s, 0, total / 2)
    t2, g2 = _moving_time_between(s, total / 2, total)
    first = t1 / (total / 2) * unit_m(units) if t1 else None
    second = t2 / (total / 2) * unit_m(units) if t2 else None
    gap_first = t1 / g1 * unit_m(units) if g1 else None
    gap_second = t2 / g2 * unit_m(units) if g2 else None
    split_pct = (second - first) / first * 100 if first and second else None
    gap_split_pct = (gap_second - gap_first) / gap_first * 100 if gap_first and gap_second else None

    full = [x for x in split_list if not x["partial"] and x["pace_s"]]
    paces = [x["pace_s"] for x in full]
    gaps = [x["gap_s"] for x in full if x["gap_s"]]
    cv = (stdev(paces) / mean(paces) * 100) if len(paces) >= 3 else None
    gap_cv = (stdev(gaps) / mean(gaps) * 100) if len(gaps) >= 3 else None
    trend = linreg(list(range(len(gaps))), gaps) if len(gaps) >= 3 else None

    fade = None
    if len(paces) >= 4:
        k = max(1, len(paces) // 4)
        fade = (mean(paces[-k:]) - mean(paces[:-k])) / mean(paces[:-k]) * 100

    ref = gap_split_pct if gap_split_pct is not None else split_pct
    if ref is None:
        verdict = "not enough data"
    elif ref < -2:
        verdict = "negative split (finished faster) — generally the most efficient race strategy"
    elif ref <= 2:
        verdict = "even pacing"
    elif ref <= 5:
        verdict = "mild positive split (slowed a bit in the second half)"
    else:
        verdict = "significant positive split / fade — likely started too fast or under-fuelled"

    return {
        "first_half_pace": fmt_pace(first, units),
        "second_half_pace": fmt_pace(second, units),
        "half_split_pct": r(split_pct, 1),
        "first_half_gap": fmt_pace(gap_first, units),
        "second_half_gap": fmt_pace(gap_second, units),
        "gap_half_split_pct": r(gap_split_pct, 1),
        "last_quarter_fade_pct": r(fade, 1),
        "pace_cv_pct": r(cv, 1),
        "gap_cv_pct": r(gap_cv, 1),
        "gap_trend_s_per_split": r(trend[0], 1) if trend else None,
        "fastest_split": min(full, key=lambda x: x["pace_s"])["split"] if full else None,
        "slowest_split": max(full, key=lambda x: x["pace_s"])["split"] if full else None,
        "verdict": verdict,
        "notes": "Positive % = slower in 2nd half. GAP (grade-adjusted pace, Minetti 2002) removes hill effects. "
                 "Pace CV < 3% is very even; > 6% is erratic (fine for intervals/trails).",
    }


def best_efforts(s: dict[str, list], targets: dict[str, float] | None = None,
                 units: str = "km") -> list[dict[str, Any]]:
    """Fastest elapsed time over each standard distance within the activity (sliding window)."""
    if "time" not in s or "distance" not in s:
        return []
    t, d = s["time"], s["distance"]
    total = d[-1]
    targets = targets or STANDARD_DISTANCES
    out = []
    for name, D in targets.items():
        if D > total:
            continue
        best = None
        best_start = None
        i = 0
        for j in range(len(d)):
            while i < j and d[j] - d[i + 1] >= D:
                i += 1
            if d[j] - d[i] >= D:
                # interpolate exact start so window is exactly D
                over = (d[j] - d[i]) - D
                seg_d = d[i + 1] - d[i] if i + 1 < len(d) else 0
                seg_t = t[i + 1] - t[i] if i + 1 < len(t) else 0
                adj = (over / seg_d * seg_t) if seg_d > 0 else 0
                el = t[j] - t[i] - adj
                if best is None or el < best:
                    best, best_start = el, d[i]
        if best:
            out.append({
                "distance": name, "time": fmt_duration(best), "time_s": r(best, 0),
                "pace": fmt_pace(best / D * unit_m(units), units),
                "starts_at_km": r(best_start / KM, 2),
            })
    return out


def cadence_analysis(s: dict[str, list]) -> dict[str, Any]:
    cad, v = s.get("cadence"), s.get("velocity_smooth")
    moving = s.get("moving")
    if not cad:
        return {}
    spm, speeds = [], []
    for i, c in enumerate(cad):
        if not c or (moving and not moving[i]):
            continue
        spm.append(c * 2)
        speeds.append(v[i] if v else None)
    if not spm:
        return {}
    avg = mean(spm)
    sp = mean([x for x in speeds if x])
    stride = (sp * 60 / avg) if sp and avg else None
    return {
        "avg_cadence_spm": r(avg, 0),
        "cadence_sd": r(stdev(spm), 1),
        "avg_stride_length_m": r(stride, 2),
        "cadence_speed_correlation": r(pearson(spm, speeds), 2) if v else None,
        "notes": "Most trained runners sit ~165-185 spm at easy-moderate pace; cadence naturally rises with speed. "
                 "A sudden drop late in a run alongside slowing pace often signals fatigue.",
    }


def elevation_summary(s: dict[str, list]) -> dict[str, Any]:
    alt = s.get("altitude")
    if not alt:
        return {}
    gain = sum(max(0, alt[i] - alt[i - 1]) for i in range(1, len(alt)))
    loss = sum(max(0, alt[i - 1] - alt[i]) for i in range(1, len(alt)))
    km = s["distance"][-1] / KM if s.get("distance") else None
    return {"gain_m": r(gain, 0), "loss_m": r(loss, 0), "min_alt_m": r(min(alt), 0),
            "max_alt_m": r(max(alt), 0), "gain_per_km_m": r(gain / km, 1) if km else None}
