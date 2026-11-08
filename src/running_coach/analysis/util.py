from __future__ import annotations

import math
from typing import Iterable, Sequence

KM = 1000.0
MILE = 1609.344


def unit_m(units: str) -> float:
    return MILE if units in ("mi", "mile", "miles") else KM


def mean(xs: Iterable[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def stdev(xs: Iterable[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def wmean(values: Sequence[float], weights: Sequence[float]) -> float | None:
    num = den = 0.0
    for v, w in zip(values, weights):
        if v is None or w is None or w <= 0:
            continue
        num += v * w
        den += w
    return num / den if den else None


def linreg(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float] | None:
    """Return (slope, intercept) of least squares fit, or None."""
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pts)
    if n < 2:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx == 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
    return slope, my - slope * mx


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3:
        return None
    a = [p[0] for p in pts]
    b = [p[1] for p in pts]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def fmt_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(sec_per_unit: float | None, units: str = "km") -> str | None:
    if not sec_per_unit or sec_per_unit <= 0 or math.isinf(sec_per_unit):
        return None
    return f"{fmt_duration(sec_per_unit)}/{'mi' if units in ('mi', 'mile', 'miles') else 'km'}"


def pace_from_speed(mps: float | None, units: str = "km") -> float | None:
    if not mps or mps <= 0:
        return None
    return unit_m(units) / mps


def r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)
