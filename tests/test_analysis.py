import math
from datetime import date, timedelta

import pytest

from running_coach.analysis import heart, load, pace, race, recovery


def make_run(km=10.0, pace_s_per_km=300.0, hr_start=140, hr_end=150, fade=0.0, cadence=88, grade=0.0):
    """1 Hz synthetic run. fade: fractional slowdown by the end (0.1 = 10% slower)."""
    t, d, hr, v, cad, g, alt, mv = [], [], [], [], [], [], [], []
    dist, sec = 0.0, 0
    total = km * 1000
    while dist < total:
        frac = dist / total
        speed = 1000 / (pace_s_per_km * (1 + fade * frac))
        t.append(sec); d.append(dist); v.append(speed)
        hr.append(hr_start + (hr_end - hr_start) * frac)
        cad.append(cadence); g.append(grade * 100); alt.append(100 + dist * grade); mv.append(True)
        dist += speed
        sec += 1
    return {"time": t, "distance": d, "heartrate": hr, "velocity_smooth": v,
            "cadence": cad, "grade_smooth": g, "altitude": alt, "moving": mv}


def test_splits_even_pace():
    s = make_run(km=5, pace_s_per_km=300)
    sp = pace.splits(s, "km")
    assert len(sp) == 5
    for x in sp:
        assert abs(x["pace_s"] - 300) <= 2
        assert x["cadence_spm"] == 176
    ps = pace.pacing_strategy(s, sp, "km")
    assert ps["verdict"] == "even pacing"


def test_positive_split_detected():
    s = make_run(km=10, pace_s_per_km=300, fade=0.15)
    sp = pace.splits(s, "km")
    ps = pace.pacing_strategy(s, sp, "km")
    assert ps["half_split_pct"] > 5
    assert "positive" in ps["verdict"]
    assert ps["slowest_split"] == 10


def test_mile_splits():
    s = make_run(km=5, pace_s_per_km=300)
    sp = pace.splits(s, "mi")
    assert len(sp) == 4  # 3 full + partial
    assert sp[-1]["partial"]
    assert abs(sp[0]["pace_s"] - 300 * 1.609344) < 3


def test_minetti():
    assert pace.minetti_factor(0) == pytest.approx(1.0)
    assert pace.minetti_factor(0.1) > 1.4
    assert pace.minetti_factor(-0.05) < 1.0


def test_gap_faster_than_pace_uphill():
    s = make_run(km=3, pace_s_per_km=360, grade=0.06)
    sp = pace.splits(s, "km")
    assert sp[0]["gap_s"] < sp[0]["pace_s"]


def test_best_efforts():
    s = make_run(km=6, pace_s_per_km=300)
    be = {e["distance"]: e for e in pace.best_efforts(s)}
    assert abs(be["5k"]["time_s"] - 1500) <= 3
    assert abs(be["1k"]["time_s"] - 300) <= 2
    assert "10k" not in be


def test_zones_methods():
    z = heart.hr_zones(max_hr=190)
    assert z["zones"][1]["low"] == 114
    z = heart.hr_zones(max_hr=190, resting_hr=50)
    assert z["zones"][1]["low"] == 134  # 50 + 0.6*140
    z = heart.hr_zones(lthr=170)
    assert z["zones"][3]["low"] == round(170 * .95)
    with pytest.raises(ValueError):
        heart.hr_zones()


def test_time_in_zones_sum():
    s = make_run(km=5)
    tiz = heart.time_in_zones(s, heart.hr_zones(max_hr=190)["zones"])
    assert sum(z["pct"] for z in tiz) == pytest.approx(100, abs=0.5)


def test_decoupling():
    steady = make_run(km=15, pace_s_per_km=330, hr_start=145, hr_end=146)
    drift = make_run(km=15, pace_s_per_km=330, hr_start=140, hr_end=165)
    assert heart.aerobic_decoupling(steady)["decoupling_pct"] < 2
    assert heart.aerobic_decoupling(drift)["decoupling_pct"] > 5
    assert heart.cardiac_drift(drift)["hr_drift_bpm_per_hour"] > 10


def test_trimp_and_hrtss():
    # 60 minutes at LTHR → hrTSS 100
    tr = heart.trimp_summary(3600, 170, 195, 50)
    assert heart.hrtss(tr, 170, 195, 50) == pytest.approx(100)
    assert heart.trimp_summary(3600, 120, 195, 50) < tr
    s = make_run(km=10, hr_start=150, hr_end=150)
    ts = heart.trimp_stream(s, 195, 50)
    assert ts == pytest.approx(heart.trimp_summary(s["time"][-1], 150, 195, 50), rel=0.01)


def test_pmc_and_acwr():
    start = date(2026, 1, 1)
    daily = [(start + timedelta(days=i), 50.0) for i in range(120)]
    p = load.pmc(daily)
    assert p[-1]["ctl"] == pytest.approx(50, rel=0.1)
    assert abs(p[-1]["tsb"]) < 5
    a = load.acwr(daily)
    assert a["acwr_rolling"] == pytest.approx(1.0)
    assert "sweet" in a["zone"]
    spike = daily[:-7] + [(d, 150.0) for d, _ in daily[-7:]]
    assert load.acwr(spike)["acwr_ewma"] > 1.5


def test_monotony():
    start = date(2026, 1, 1)
    flat = [(start + timedelta(days=i), 50.0 + (i % 2)) for i in range(7)]
    assert load.monotony_strain(flat)["monotony"] > 2


def test_weekly_volume_ramp():
    acts = [{"start_date_local": "2026-01-05T08:00:00Z", "distance": 10000, "moving_time": 3000},
            {"start_date_local": "2026-01-12T08:00:00Z", "distance": 15000, "moving_time": 4500}]
    wv = load.weekly_volume(acts, "km")
    assert wv[1]["ramp_pct_vs_prev_week"] == 50


def test_vdot_known_values():
    # Daniels' tables: 20:00 5k ≈ VDOT 49.8; 40:00 10k ≈ 51.9 (±0.5)
    assert race.vdot(5000, 20 * 60) == pytest.approx(49.8, abs=0.5)
    v = race.vdot(5000, 20 * 60)
    t10 = race.predict_time(v, 10000)
    assert 41 * 60 < t10 < 42.5 * 60  # tables: ~41:35
    assert race.riegel(5000, 1200, 10000) == pytest.approx(1200 * 2 ** 1.06)
    tp = race.training_paces(50)
    assert tp["threshold"].startswith("4:")  # VDOT 50 T ≈ 4:15/km


def make_recovery(days=40, hrv=60, drop_last=0):
    out = []
    for i in range(days):
        d = date(2026, 1, 1) + timedelta(days=i)
        h = hrv * (1 + 0.05 * math.sin(i)) * (0.6 if i >= days - drop_last else 1)
        out.append({"score_state": "SCORED", "created_at": d.isoformat() + "T11:00:00.000Z",
                    "score": {"recovery_score": 70, "hrv_rmssd_milli": h, "resting_heart_rate": 50,
                              "spo2_percentage": 97, "skin_temp_celsius": 33.5}})
    return out


def test_hrv_status():
    ok = recovery.hrv_analysis(recovery.recovery_rows(make_recovery()))
    assert ok["status"].startswith("within")
    bad = recovery.hrv_analysis(recovery.recovery_rows(make_recovery(drop_last=7)))
    assert bad["status"].startswith("BELOW")


def test_sleep_and_readiness():
    recs = []
    for i in range(7):
        d = date(2026, 1, 1) + timedelta(days=i)
        recs.append({"score_state": "SCORED", "nap": False, "timezone_offset": "-05:00",
                     "start": f"{d.isoformat()}T04:00:00.000Z", "end": f"{d.isoformat()}T11:30:00.000Z",
                     "score": {"stage_summary": {"total_in_bed_time_milli": 27e6, "total_light_sleep_time_milli": 12e6,
                                                 "total_slow_wave_sleep_time_milli": 5e6,
                                                 "total_rem_sleep_time_milli": 6e6, "disturbance_count": 3},
                               "sleep_needed": {"baseline_milli": 28.8e6},
                               "respiratory_rate": 15, "sleep_performance_percentage": 80,
                               "sleep_efficiency_percentage": 90, "sleep_consistency_percentage": 85}})
    rows = recovery.sleep_rows(recs)
    assert rows[0]["bedtime"] == "23:00"
    sa = recovery.sleep_analysis(rows)
    assert sa["avg_asleep_h_7d"] == pytest.approx(6.39, abs=0.01)
    assert sa["sleep_debt_h_7d"] > 10
    rd = recovery.readiness(20, {"status": "BELOW normal"}, -40, 1.6, sa)
    assert rd["recommendation"].startswith("REST")
    rd = recovery.readiness(85, {"status": "within normal range"}, 0, 1.0, None)
    assert rd["recommendation"].startswith("GO")
