"""Race prediction & training paces: Riegel, Daniels/Gilbert VDOT."""
from __future__ import annotations

import math
from typing import Any

from .util import KM, MILE, fmt_duration, fmt_pace, r, unit_m

RACES = {"1 mile": MILE, "5k": 5000, "10k": 10000, "half marathon": 21097.5, "marathon": 42195}


def riegel(d1: float, t1: float, d2: float, exponent: float = 1.06) -> float:
    return t1 * (d2 / d1) ** exponent


def _vo2(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min**2


def _pct_max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(distance_m: float, time_s: float) -> float:
    t = time_s / 60
    return _vo2(distance_m / t) / _pct_max(t)


def velocity_at_vo2(vo2: float) -> float:
    """Inverse of the Daniels oxygen-cost equation → m/min."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def predict_time(v: float, distance_m: float) -> float:
    lo, hi = 60.0, 60 * 60 * 24.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if vdot(distance_m, mid) > v:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def predictions(distance_m: float, time_s: float, units: str = "km") -> dict[str, Any]:
    v = vdot(distance_m, time_s)
    out = []
    for name, d in RACES.items():
        tv = predict_time(v, d)
        tr = riegel(distance_m, time_s, d)
        out.append({"race": name, "vdot_time": fmt_duration(tv), "riegel_time": fmt_duration(tr),
                    "vdot_pace": fmt_pace(tv / d * unit_m(units), units)})
    return {"vdot": r(v, 1), "predictions": out,
            "notes": "VDOT (Daniels & Gilbert) and Riegel (T2=T1·(D2/D1)^1.06) both assume training appropriate to "
                     "the target distance. Marathon predictions from short races are optimistic unless weekly volume "
                     "is high (≈ 70+ km/wk) and long runs are in place."}


def training_paces(v: float, units: str = "km") -> dict[str, Any]:
    um = unit_m(units)

    def pace_at(pct: float) -> str | None:
        vel = velocity_at_vo2(v * pct)
        return fmt_pace(um / vel * 60, units)

    m_pace = predict_time(v, 42195) / 42195 * um
    t_pace_s = um / velocity_at_vo2(v * 0.88) * 60
    i_pace_s = um / velocity_at_vo2(v * 0.975) * 60
    r_pace_s = predict_time(v, MILE) / MILE * um
    return {
        "vdot": r(v, 1),
        "easy": f"{pace_at(0.70)} – {pace_at(0.62)}",
        "marathon": fmt_pace(m_pace, units),
        "threshold": fmt_pace(t_pace_s, units),
        "interval": fmt_pace(i_pace_s, units),
        "repetition": fmt_pace(r_pace_s, units),
        "per_400m": {"threshold": fmt_duration(t_pace_s * 400 / um),
                     "interval": fmt_duration(i_pace_s * 400 / um),
                     "repetition": fmt_duration(r_pace_s * 400 / um)},
        "notes": "E = easy/long runs, M = marathon pace, T = 20-40 min cumulative 'comfortably hard', "
                 "I = 3-5 min reps (VO2max), R = 200-400m reps for speed/economy. (Daniels' Running Formula)",
    }
