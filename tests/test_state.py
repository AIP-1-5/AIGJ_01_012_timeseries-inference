"""과제 5: 정답 정의와 입력 규칙."""

from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.settings import load_schedule
from state.__main__ import OCC_FEATURES, VENT_FEATURES, choose_threshold
from state.features import CALENDAR_FEATURES, observation_labels, occupancy_labels, ventilation_labels

ROOT = Path(__file__).resolve().parent.parent
TZ = "Asia/Seoul"


def kst(text):
    return pd.Timestamp(text, tz=TZ).tz_convert("UTC")


def test_occupancy_input_has_no_schedule_or_clock():
    assert not set(OCC_FEATURES) & set(CALENDAR_FEATURES)
    assert set(CALENDAR_FEATURES) <= set(VENT_FEATURES)


def test_occupancy_labels_exclude_lunch_and_transitions():
    schedule = load_schedule(ROOT / "config" / "schedule.toml")
    index = pd.date_range(kst("2026-03-09 00:05"), kst("2026-03-10 00:00"), freq="5min")
    labels = occupancy_labels(index, "실습실", schedule, TZ)
    at = lambda hhmm: labels[kst(f"2026-03-09 {hhmm}")]              # 칸 끝 시각
    assert at("10:05") == 1 and at("15:00") == 1 and at("20:00") == 0 and at("03:00") == 0
    assert np.isnan(at("12:00"))                                       # 점심
    assert np.isnan(at("09:05")) and np.isnan(at("13:10"))             # 수업 시작 직후 15분
    assert labels.loc[kst("2026-03-09 18:30")] == 0 and np.isnan(labels.loc[kst("2026-03-09 18:10")])
    assert occupancy_labels(index, "사무실", schedule, TZ).isna().all()


def test_ventilation_labels_exclude_already_high():
    index = pd.date_range("2026-03-09T00:00Z", periods=12, freq="5min")
    co2 = pd.Series([800, 850, 900, 950, 990, 1010, 1100, 990, 900, 800, 700, 600], index=index, dtype=float)
    labels = ventilation_labels(co2)
    assert labels.iloc[0] == 1                       # 25분 뒤 1,010 → 양성
    assert np.isnan(labels.iloc[5]) and np.isnan(labels.iloc[6])      # 이미 1,000 이상 → 제외
    assert labels.iloc[8] == 0                       # 앞으로 안 넘음
    assert np.isnan(labels.iloc[-1])                 # 미래 값 부족


def test_observation_labels():
    index = pd.date_range(kst("2026-10-22 09:05"), periods=12, freq="5min")
    obs = pd.DataFrame({"space": ["실습실", "실습실"], "start_kst": ["2026-10-22 09:00", "2026-10-22 09:30"],
                        "end_kst": ["2026-10-22 09:30", "2026-10-22 10:00"], "people": [20, 0]})
    labels = observation_labels(index, "실습실", obs, TZ)
    assert (labels.iloc[:6] == 1).all() and (labels.iloc[6:] == 0).all()


def test_threshold_recall_rule():
    y = np.array([0] * 90 + [1] * 10)
    prob = np.r_[np.linspace(0, 0.5, 90), np.linspace(0.3, 0.9, 10)]
    threshold, _ = choose_threshold(prob, y, "recall")
    assert ((prob >= threshold)[y == 1]).mean() >= 0.85
