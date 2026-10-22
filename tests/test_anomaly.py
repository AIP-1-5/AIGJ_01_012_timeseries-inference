"""과제 4: 고장 주입, 규칙, 경보 묶기, 이벤트 매칭, 임계값 선택."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from anomaly.data import space_frames
from anomaly.evaluate import alarm_events, match, pick_threshold, summarize
from anomaly.inject import inject
from anomaly import rules
from pipeline.settings import load_schedule, load_settings
from tests.test_forecast import synthetic_clean

ROOT = Path(__file__).resolve().parent.parent
CFG = {"per_type": 4, "spike_co2": [400, 1200], "spike_temperature": [4, 8], "stuck_hours": [1, 4],
       "drift_ppm": [200, 500], "cooling_rise": [3, 5], "ventilation_factor": 1.8}


@pytest.fixture(scope="module")
def env():
    settings = load_settings(ROOT / "config" / "preprocess.toml")
    schedule = load_schedule(ROOT / "config" / "schedule.toml")
    frames = space_frames(synthetic_clean(), settings)
    start = pd.Timestamp("2026-03-09", tz="Asia/Seoul").tz_convert("UTC")
    end = start + pd.Timedelta(days=21)
    return settings, schedule, frames, start, end


def test_inject_reproducible_and_original_untouched(env):
    settings, schedule, frames, start, end = env
    before = {s: f.copy() for s, f in frames.items()}
    a_frames, a = inject(frames, start, end, CFG, 5, settings, schedule)
    _, b = inject(frames, start, end, CFG, 5, settings, schedule)
    pd.testing.assert_frame_equal(a, b)
    for space in frames:
        pd.testing.assert_frame_equal(frames[space], before[space])
    assert set(a["kind"]) == {"spike", "stuck", "drift", "cooling", "ventilation"}
    # 같은 공간의 고장끼리 겹치지 않는다
    for space, group in a.groupby("space"):
        group = group.sort_values("start")
        assert (group["start"].iloc[1:].to_numpy() > group["end"].iloc[:-1].to_numpy()).all()


def test_inject_shapes(env):
    settings, schedule, frames, start, end = env
    out, faults = inject(frames, start, end, CFG, 7, settings, schedule)
    for f in faults.itertuples():
        orig = frames[f.space][f.metric].reindex(out[f.space].index)
        new = out[f.space][f.metric]
        window = (new.index >= f.start) & (new.index <= f.end)
        if f.kind == "spike":
            assert new[f.start] - orig[f.start] == pytest.approx(f.size)
        if f.kind == "stuck":
            assert new[window].dropna().nunique() == 1
        if f.kind == "drift":
            assert (new[window] - orig[window]).max() == pytest.approx(f.size, rel=1e-6)
        if f.kind == "cooling":
            assert (new[window] - orig[window]).max() == pytest.approx(f.size, rel=1e-6)
        if f.kind == "ventilation":
            assert (new[window] - orig[window]).max() > 100


def test_rules_catch_spike_and_stuck(env):
    settings, schedule, frames, start, end = env
    train = {s: f[f.index < start] for s, f in frames.items()}
    params = rules.fit(train, schedule, settings.timezone)
    test = {s: f[(f.index >= start) & (f.index < end)].copy() for s, f in frames.items()}
    lab = test["실습실"]
    t_spike = lab.index[500]
    lab.loc[t_spike, "co2"] += 1500
    stuck_from = lab.index[1500]
    lab.loc[stuck_from:stuck_from + pd.Timedelta(hours=2), "co2"] = lab.loc[stuck_from - pd.Timedelta(minutes=5), "co2"]
    rows = rules.apply(test, params, schedule, settings.timezone)
    spikes = rows[(rows["rule"] == "spike") & (rows["space"] == "실습실")]
    assert (spikes["time"] == t_spike + pd.Timedelta(minutes=5)).any()       # 다음 칸에서 확정
    stuck = rows[(rows["rule"] == "stuck") & (rows["space"] == "실습실")]
    assert stuck["time"].min() <= stuck_from + pd.Timedelta(minutes=5 * params["stuck"]["co2"])
    assert params["stuck"]["co2"] >= 6


def test_alarm_events_and_match():
    t0 = pd.Timestamp("2026-03-10T00:00Z")
    alarms = pd.DataFrame({
        "space": ["실습실"] * 4 + ["사무실"],
        "time": [t0, t0 + pd.Timedelta(minutes=30), t0 + pd.Timedelta(hours=5), t0 + pd.Timedelta(hours=5, minutes=5), t0],
        "detector": ["model", "rule:stuck", "model", "model", "model"],
        "cause": ["co2", "co2", "temperature", "temperature", "co2"],
    })
    events = alarm_events(alarms, merge_minutes=60)
    assert len(events) == 3                                       # 실습실 2개(5시간 떨어짐), 사무실 1개
    faults = pd.DataFrame({"event_id": [1, 2], "space": ["실습실", "회의실"], "metric": ["co2", "co2"],
                           "kind": ["stuck", "spike"], "start": [t0 - pd.Timedelta(minutes=20), t0], "end": [t0 + pd.Timedelta(hours=1), t0]})
    per_fault, false_events = match(faults, events, grace_minutes=60)
    assert per_fault.loc[0, "detected"] and per_fault.loc[0, "delay_min"] == 20 and per_fault.loc[0, "cause_ok"]
    assert not per_fault.loc[1, "detected"]
    assert len(false_events) == 2                                 # 실습실 5시간 뒤 경보, 사무실 경보
    _, _, overall, _ = summarize(faults, alarms, days=2, merge_minutes=60, grace_minutes=60)
    assert overall["rate"] == 0.5 and overall["false_per_day"] == 1.0


def test_pick_threshold_respects_false_alarm_cap():
    table = {0.1: {"rate": 0.95, "false_per_day": 3.0}, 0.2: {"rate": 0.9, "false_per_day": 0.8},
             0.3: {"rate": 0.9, "false_per_day": 0.4}, 0.4: {"rate": 0.7, "false_per_day": 0.1}}
    chosen, _ = pick_threshold(list(table), lambda t: table[t], target_false_per_day=0.9)
    assert chosen == 0.3                                          # 헛경보 상한 안에서 탐지율 최대, 같으면 높은 임계값


def test_frozen_rule_catches_humidity_stuck_while_temperature_moves(env):
    settings, schedule, frames, start, end = env
    train = {s: f[f.index < start] for s, f in frames.items()}
    params = rules.fit(train, schedule, settings.timezone)
    assert params["frozen"]
    test = {s: f[(f.index >= start) & (f.index < end)].copy() for s, f in frames.items()}
    space = next(k.split("|")[0] for k in params["frozen"] if k.endswith("|humidity"))
    frame = test[space]
    b = frame.index[1500]
    window = (frame.index >= b) & (frame.index <= b + pd.Timedelta(hours=3))
    frame.loc[window, "humidity"] = frame.loc[b - pd.Timedelta(minutes=5), "humidity"]
    frame.loc[window, "temperature"] = frame.loc[window, "temperature"] + np.linspace(0, 3, window.sum())
    rows = rules.apply(test, params, schedule, settings.timezone)
    frozen = rows[(rows["rule"] == "frozen") & (rows["space"] == space) & (rows["cause"] == "humidity")]
    assert not frozen.empty and frozen["time"].min() <= b + pd.Timedelta(hours=3)
    clean_rows = rules.apply({s: f[(f.index >= start) & (f.index < end)] for s, f in frames.items()}, params, schedule, settings.timezone)
    assert (clean_rows["rule"] == "frozen").sum() == 0                      # 고장 없는 복사본에서는 안 울림


def test_frozen_rule_old_params_still_work(env):
    settings, schedule, frames, start, end = env
    params = rules.fit({s: f[f.index < start] for s, f in frames.items()}, schedule, settings.timezone)
    params.pop("frozen")                                                    # 보드 A에 있는 옛 rules.json
    rows = rules.apply({s: f[(f.index >= start) & (f.index < end)] for s, f in frames.items()}, params, schedule, settings.timezone)
    assert "frozen" not in set(rows["rule"])
