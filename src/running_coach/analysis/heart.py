"""Heart-rate analysis: zones, time-in-zone, decoupling, efficiency, drift, TRIMP / hrTSS."""
from __future__ import annotations

import math
from typing import Any

from .pace import minetti_factor
from .util import fmt_duration, linreg, mean, r


# ---------------------------------------------------------------- zones

def hr_zones(max_hr: int | None = None, resting_hr: int | None = None,
             lthr: int | None = None) -> dict[str, Any]:
    """Five HR zones. Priority: LTHR (Friel) > Karvonen (HR reserve) > %HRmax."""
    if lthr:
        pct = [(0, .85), (.85, .90), (.90, .95), (.95, 1.0), (1.0, 1.15)]
        z = [(round(lthr * a), round(lthr * b)) for a, b in pct]
        method = f"Friel %LTHR (LTHR={lthr})"
    elif max_hr and resting_hr:
        hrr = max_hr - resting_hr
        pct = [(.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.0)]
        z = [(round(resting_hr + hrr * a), round(resting_hr + hrr * b)) for a, b in pct]
        z[0] = (0, z[0][1])
        method = f"Karvonen %HRR (max={max_hr}, rest={resting_hr})"
    elif max_hr:
        pct = [(0, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.0)]
        z = [(round(max_hr * a), round(max_hr * b)) for a, b in pct]
        method = f"%HRmax (max={max_hr})"
    else:
        raise ValueError("Need at least max_hr (or lthr) to build zones.")
    names = ["Z1 recovery", "Z2 aerobic/easy", "Z3 tempo", "Z4 threshold", "Z5 VO2max/anaerobic"]
    zones = [{"zone": n, "low": lo, "high": hi} for n, (lo, hi) in zip(names, z)]
    zones[-1]["high"] = 250
    return {"method": method, "zones": zones}


def _zone_index(hr: float, zones: list[dict]) -> int:
    for k, z in enumerate(zones):
        if hr < z["high"]:
            return k
    return len(zones) - 1


def time_in_zones(s: dict[str, list], zones: list[dict]) -> list[dict[str, Any]]:
    t, hr = s.get("time"), s.get("heartrate")
    if not t or not hr:
        return []
    secs = [0.0] * len(zones)
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        if dt <= 0 or dt > 30 or not hr[i]:  # ignore pauses
            continue
        secs[_zone_index(hr[i], zones)] += dt
    tot = sum(secs) or 1
    return [{"zone": z["zone"], "range": f"{z['low']}-{z['high'] if k < len(zones)-1 else 'max'}",
             "time": fmt_duration(sec), "seconds": round(sec), "pct": r(sec / tot * 100, 1)}
            for k, (z, sec) in enumerate(zip(zones, secs))]


def three_zone_from_five(tiz: list[dict]) -> dict[str, float]:
    """Seiler 3-zone model: low (Z1-2), moderate (Z3), high (Z4-5)."""
    p = [z["pct"] or 0 for z in tiz]
    if len(p) < 5:
        return {}
    return {"low_pct": r(p[0] + p[1], 1), "moderate_pct": r(p[2], 1), "high_pct": r(p[3] + p[4], 1)}


# ---------------------------------------------------------------- efficiency & decoupling

def _steady_samples(s, skip_s: float = 600):
    """Moving samples with HR & speed after warm-up. Yields (t, speed_gap_mps, hr)."""
    t, hr, v = s.get("time"), s.get("heartrate"), s.get("velocity_smooth")
    if not (t and hr and v):
        return
    moving, grade = s.get("moving"), s.get("grade_smooth")
    for i in range(len(t)):
        if t[i] < skip_s or not hr[i] or not v[i] or v[i] < 1.0:
            continue
        if moving and not moving[i]:
            continue
        g = grade[i] / 100 if grade and grade[i] is not None else 0
        yield t[i], v[i] * minetti_factor(g), hr[i]


def efficiency_factor(s: dict[str, list], skip_s: float = 600) -> float | None:
    """EF = grade-adjusted speed (m/min) / HR. Higher = fitter. Compare across similar easy runs."""
    pts = list(_steady_samples(s, skip_s))
    if len(pts) < 60:
        return None
    return mean(p[1] * 60 for p in pts) / mean(p[2] for p in pts)


def aerobic_decoupling(s: dict[str, list], skip_s: float = 600) -> dict[str, Any]:
    """Pa:HR decoupling (Friel). EF of 1st half vs 2nd half of steady portion.
    < 5% = good aerobic endurance for that duration; > 5-8% = aerobic base needs work / fatigue / heat."""
    pts = list(_steady_samples(s, skip_s))
    if len(pts) < 600:
        return {"note": "Need >= ~20 min of steady running after warm-up for decoupling."}
    half = len(pts) // 2

    def ef(seg):
        return mean(p[1] * 60 for p in seg) / mean(p[2] for p in seg)

    ef1, ef2 = ef(pts[:half]), ef(pts[half:])
    dec = (ef1 - ef2) / ef1 * 100
    verdict = ("excellent (<3%)" if dec < 3 else "good (<5%)" if dec < 5
               else "moderate (5-8%) — aerobic durability limiter" if dec < 8
               else "high (>8%) — went too hard, heat/dehydration, or aerobic base needs work")
    return {"ef_first_half": r(ef1, 3), "ef_second_half": r(ef2, 3),
            "decoupling_pct": r(dec, 1), "verdict": verdict,
            "caveat": "Only meaningful for steady efforts, not intervals or hilly fartleks."}


def cardiac_drift(s: dict[str, list], skip_s: float = 600) -> dict[str, Any]:
    pts = list(_steady_samples(s, skip_s))
    if len(pts) < 300:
        return {}
    fit_hr = linreg([p[0] for p in pts], [p[2] for p in pts])
    fit_v = linreg([p[0] for p in pts], [p[1] for p in pts])
    return {
        "hr_drift_bpm_per_hour": r(fit_hr[0] * 3600, 1) if fit_hr else None,
        "gap_speed_change_pct_per_hour": r(fit_v[0] * 3600 / mean(p[1] for p in pts) * 100, 1) if fit_v else None,
        "note": "Rising HR at constant/declining pace = cardiovascular drift (heat, dehydration, glycogen depletion).",
    }


# ---------------------------------------------------------------- training impulse

def _trimp_weight(sex: str | None) -> tuple[float, float]:
    return (0.86, 1.67) if (sex or "").lower().startswith("f") else (0.64, 1.92)


def trimp_stream(s: dict[str, list], max_hr: int, resting_hr: int, sex: str | None = None) -> float | None:
    """Banister TRIMP from second-by-second HR."""
    t, hr = s.get("time"), s.get("heartrate")
    if not t or not hr:
        return None
    a, b = _trimp_weight(sex)
    total = 0.0
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        if dt <= 0 or dt > 30 or not hr[i]:
            continue
        x = max(0.0, min(1.0, (hr[i] - resting_hr) / (max_hr - resting_hr)))
        total += dt / 60 * x * a * math.exp(b * x)
    return total


def trimp_summary(duration_s: float, avg_hr: float, max_hr: int, resting_hr: int,
                  sex: str | None = None) -> float | None:
    """Banister TRIMP from average HR (cheap; no streams needed). Slightly underestimates intervals."""
    if not avg_hr or not duration_s or max_hr <= resting_hr:
        return None
    a, b = _trimp_weight(sex)
    x = max(0.0, min(1.0, (avg_hr - resting_hr) / (max_hr - resting_hr)))
    return duration_s / 60 * x * a * math.exp(b * x)


def hrtss(trimp: float | None, lthr: int, max_hr: int, resting_hr: int, sex: str | None = None) -> float | None:
    """Scale TRIMP so 1 hour at LTHR = 100 (TrainingPeaks-style hrTSS)."""
    if trimp is None:
        return None
    ref = trimp_summary(3600, lthr, max_hr, resting_hr, sex)
    return trimp / ref * 100 if ref else None


def estimate_max_hr(birth_year: int | None, observed_max: float | None, this_year: int) -> tuple[int | None, str]:
    est = round(208 - 0.7 * (this_year - birth_year)) if birth_year else None
    if observed_max and est:
        return (max(int(observed_max), est), "max(observed, Tanaka 208-0.7×age)")
    if observed_max:
        return int(observed_max), "highest HR observed in recent activities"
    if est:
        return est, "Tanaka formula 208-0.7×age"
    return None, "unknown"
