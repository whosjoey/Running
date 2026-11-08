"""WHOOP-derived recovery analysis: HRV baseline & trend, RHR, sleep quality, readiness."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from .util import linreg, mean, r, stdev


def _local_dt(ts: str, offset: str | None) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if offset:
        sign = -1 if offset.startswith("-") else 1
        hh, mm = offset.lstrip("+-").split(":")
        dt = dt + sign * timedelta(hours=int(hh), minutes=int(mm))
    return dt.replace(tzinfo=None)


def recovery_rows(records: list[dict]) -> list[dict[str, Any]]:
    """Flatten WHOOP v2 recovery records → one row per day (date = wake-up day)."""
    rows = []
    for rec in records:
        sc = rec.get("score") or {}
        if rec.get("score_state") != "SCORED" or not sc:
            continue
        d = (rec.get("created_at") or rec.get("updated_at"))[:10]
        rows.append({"date": d, "recovery": sc.get("recovery_score"),
                     "hrv_ms": sc.get("hrv_rmssd_milli"), "rhr": sc.get("resting_heart_rate"),
                     "spo2": sc.get("spo2_percentage"), "skin_temp_c": sc.get("skin_temp_celsius"),
                     "calibrating": sc.get("user_calibrating")})
    rows.sort(key=lambda x: x["date"])
    return rows


def hrv_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """HRV-guided training approach (Plews/Kiviniemi): compare 7-day rolling lnRMSSD to a
    normal range of baseline mean ± 0.5 SD. Also tracks HRV CV and resting HR vs baseline."""
    pts = [(x["date"], math.log(x["hrv_ms"]), x["rhr"]) for x in rows if x.get("hrv_ms")]
    if len(pts) < 10:
        return {"note": "Need >= 10 scored WHOOP days for HRV trend analysis."}
    ln = [p[1] for p in pts]
    base = ln[:-7] if len(ln) >= 21 else ln
    bm, bsd = mean(base), stdev(base) or 0.0
    lo, hi = bm - 0.5 * bsd, bm + 0.5 * bsd
    last7 = ln[-7:]
    roll = mean(last7)
    cv7 = stdev(last7) / roll * 100 if len(last7) >= 3 else None
    cv_base = (stdev(base) / bm * 100) if len(base) >= 3 else None
    trend = linreg(list(range(min(14, len(ln)))), ln[-14:])
    rhr = [p[2] for p in pts if p[2]]
    rhr7, rhr_base = mean(rhr[-7:]), mean(rhr[:-7] if len(rhr) >= 21 else rhr)

    if roll < lo:
        status = "BELOW normal range — accumulated fatigue, stress, illness, or poor sleep. Favor easy/rest."
    elif roll > hi:
        status = ("ABOVE normal range — usually well-recovered; but if alongside heavy training and "
                  "poor performance it can signal parasympathetic overreaching.")
    else:
        status = "within normal range — train as planned."
    signals = []
    if cv7 and cv_base and cv7 > cv_base * 1.5:
        signals.append("HRV day-to-day variability is unusually high (rising CV often precedes a dip).")
    if rhr7 and rhr_base and rhr7 - rhr_base >= 3:
        signals.append(f"Resting HR is {rhr7 - rhr_base:.1f} bpm above baseline.")
    return {
        "today_hrv_ms": round(math.exp(ln[-1])),
        "hrv_7d_avg_ms": round(math.exp(roll)),
        "baseline_hrv_ms": round(math.exp(bm)),
        "normal_range_ms": [round(math.exp(lo)), round(math.exp(hi))],
        "status": status,
        "hrv_cv_7d_pct": r(cv7, 1), "hrv_cv_baseline_pct": r(cv_base, 1),
        "ln_hrv_trend_14d_per_day": r(trend[0], 4) if trend else None,
        "rhr_7d_avg": r(rhr7, 1), "rhr_baseline": r(rhr_base, 1),
        "signals": signals, "days_used": len(pts),
    }


def sleep_rows(records: list[dict]) -> list[dict[str, Any]]:
    rows = []
    for rec in records:
        sc = rec.get("score") or {}
        if rec.get("nap") or rec.get("score_state") != "SCORED" or not sc:
            continue
        st = sc.get("stage_summary") or {}
        need = sc.get("sleep_needed") or {}
        asleep = sum(st.get(k, 0) or 0 for k in ("total_light_sleep_time_milli",
                     "total_slow_wave_sleep_time_milli", "total_rem_sleep_time_milli"))
        need_ms = sum(need.get(k, 0) or 0 for k in need)
        off = rec.get("timezone_offset")
        start, end = _local_dt(rec["start"], off), _local_dt(rec["end"], off)
        rows.append({
            "date": end.date().isoformat(),
            "bedtime": start.strftime("%H:%M"), "wake": end.strftime("%H:%M"),
            "_bed_min": ((start.hour * 60 + start.minute) - 720) % 1440,  # minutes after noon
            "_wake_min": end.hour * 60 + end.minute,
            "asleep_h": r(asleep / 3.6e6, 2),
            "in_bed_h": r((st.get("total_in_bed_time_milli") or 0) / 3.6e6, 2),
            "need_h": r(need_ms / 3.6e6, 2) if need_ms else None,
            "deep_h": r((st.get("total_slow_wave_sleep_time_milli") or 0) / 3.6e6, 2),
            "rem_h": r((st.get("total_rem_sleep_time_milli") or 0) / 3.6e6, 2),
            "disturbances": st.get("disturbance_count"),
            "performance_pct": sc.get("sleep_performance_percentage"),
            "efficiency_pct": r(sc.get("sleep_efficiency_percentage"), 0),
            "consistency_pct": sc.get("sleep_consistency_percentage"),
            "resp_rate": r(sc.get("respiratory_rate"), 1),
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def sleep_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"note": "No scored sleep."}
    last7 = rows[-7:]
    asleep = [x["asleep_h"] for x in last7]
    debt = sum(max(0, (x["need_h"] or 0) - x["asleep_h"]) for x in last7 if x["need_h"])
    rr = [x["resp_rate"] for x in rows if x["resp_rate"]]
    rr_flag = (mean(rr[-3:]) - mean(rr[:-3])) if len(rr) > 10 else None
    def pct(key):
        tot = sum(x["asleep_h"] for x in last7)
        return r(sum(x[key] for x in last7) / tot * 100, 0) if tot else None
    notes = []
    if mean(asleep) and mean(asleep) < 7:
        notes.append("Averaging < 7 h asleep — athletes generally benefit from 8+ h; sleep loss impairs "
                     "endurance performance and raises injury risk.")
    bed_sd = stdev([x["_bed_min"] for x in last7])
    if bed_sd and bed_sd > 60:
        notes.append(f"Bedtime varies by ±{bed_sd:.0f} min — more consistent timing improves sleep quality.")
    if rr_flag and rr_flag > 1.0:
        notes.append("Respiratory rate is elevated vs baseline — can be an early sign of illness.")
    return {
        "nights": len(rows),
        "avg_asleep_h_7d": r(mean(asleep), 2),
        "avg_asleep_h_all": r(mean(x["asleep_h"] for x in rows), 2),
        "sleep_debt_h_7d": r(debt, 1),
        "deep_pct_7d": pct("deep_h"), "rem_pct_7d": pct("rem_h"),
        "avg_efficiency_pct_7d": r(mean(x["efficiency_pct"] for x in last7), 0),
        "avg_performance_pct_7d": r(mean(x["performance_pct"] for x in last7), 0),
        "bedtime_sd_min_7d": r(bed_sd, 0),
        "resp_rate_change_vs_baseline": r(rr_flag, 2),
        "last_night": {k: v for k, v in rows[-1].items() if not k.startswith("_")},
        "notes": notes,
    }


def readiness(recovery_score: float | None, hrv: dict | None, tsb_rel: float | None,
              acwr_val: float | None, sleep: dict | None) -> dict[str, Any]:
    """Transparent traffic-light combination of recovery signals."""
    sig: list[tuple[str, str, str]] = []
    if recovery_score is not None:
        c = "green" if recovery_score >= 67 else "yellow" if recovery_score >= 34 else "red"
        sig.append(("WHOOP recovery", c, f"{recovery_score:.0f}%"))
    if hrv and "status" in hrv:
        c = "red" if hrv["status"].startswith("BELOW") else "green"
        sig.append(("HRV 7d vs baseline", c, hrv["status"].split(" — ")[0]))
    if tsb_rel is not None:
        c = "red" if tsb_rel < -30 else "yellow" if tsb_rel < -10 else "green"
        sig.append(("Training form (TSB % of CTL)", c, f"{tsb_rel:.0f}%"))
    if acwr_val is not None:
        c = "red" if acwr_val > 1.5 else "yellow" if acwr_val > 1.3 else "green"
        sig.append(("ACWR", c, f"{acwr_val:.2f}"))
    if sleep and sleep.get("last_night"):
        ln = sleep["last_night"]
        need = ln.get("need_h") or 8
        c = "green" if ln["asleep_h"] >= need * 0.9 else "yellow" if ln["asleep_h"] >= need * 0.75 else "red"
        sig.append(("Last night's sleep", c, f"{ln['asleep_h']} h of {need} h needed"))
    reds = sum(1 for s in sig if s[1] == "red")
    yellows = sum(1 for s in sig if s[1] == "yellow")
    if not sig:
        rec = "No data — connect WHOOP and/or Strava."
    elif reds >= 2:
        rec = "REST or very easy 20-30 min Z1. Multiple systems flag poor recovery."
    elif reds == 1 or yellows >= 2:
        rec = "EASY day: Z1-Z2 aerobic run, keep it conversational. Move hard workouts to a greener day."
    elif yellows == 1:
        rec = "MODERATE: planned training is fine; a steady/tempo session OK but skip max efforts."
    else:
        rec = "GO: green across the board — good day for intervals, threshold work, or a long run."
    return {"recommendation": rec,
            "signals": [{"signal": a, "light": b, "value": c} for a, b, c in sig]}
