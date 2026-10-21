"""달력·일정 특성 (contracts/model-io.md). 학습과 과제 8 서비스가 같은 함수를 쓴다.

칸 시각(칸 끝 t)의 칸 시작(t−5분)을 한국 시간으로 보고 계산한다. 모두 미리 알 수 있는 정보다.
  tod_sin, tod_cos   하루 중 시각
  dow_sin, dow_cos   요일 (월 0 … 일 6)
  class_day          평일이고 공휴일이 아니면 1 (일정 요일)
  in_class           일정이 있는 공간(실습실)에서 수업 1, 점심 0.5, 그 외 0
"""

from datetime import datetime

import numpy as np
import pandas as pd

from pipeline.schedule import holiday_dates

FEATURES = {
    "full": ["tod_sin", "tod_cos", "dow_sin", "dow_cos", "class_day", "in_class"],
    "no_schedule": ["tod_sin", "tod_cos", "dow_sin", "dow_cos"],
    "sensor_only": [],
}
STATE_AFTER = {"start": 1.0, "partial": 0.5, "end": 0.0}


def feature_names(variant):
    return list(FEATURES[variant])


def slot_start_local(times, tz, interval_minutes=5):
    return pd.DatetimeIndex(times).tz_convert(tz) - pd.Timedelta(minutes=interval_minutes)


def in_class_values(local, space_schedule, class_day):
    """일정 변화 뒤 상태: start → 1, partial → 0.5, end → 0. 첫 일정 전과 일정 없는 날은 0."""
    values = np.zeros(len(local), dtype=np.float32)
    if space_schedule is None:
        return values
    minutes = np.asarray(local.hour * 60 + local.minute)
    for event in space_schedule.events:                       # 시각 순으로 정렬되어 있음
        values = np.where(minutes >= event.time.hour * 60 + event.time.minute, STATE_AFTER[event.kind], values)
    return np.where(class_day > 0, values, 0.0).astype(np.float32)


def calendar_features(times, space, variant, schedule, tz="Asia/Seoul", interval_minutes=5):
    """칸 시각들 → (n, C) float32. 순서는 FEATURES[variant]."""
    names = FEATURES[variant]
    if not names:
        return np.zeros((len(times), 0), dtype=np.float32)
    local = slot_start_local(times, tz, interval_minutes)
    tod = np.asarray((local.hour * 60 + local.minute) / 1440.0)
    dow = np.asarray(local.weekday / 7.0)
    off = holiday_dates(schedule.holidays, set(local.year)) if len(local) else set()
    weekday = np.asarray(local.weekday < 5)
    class_day = (weekday & ~pd.Index(local.date).isin(off)).astype(np.float32)
    columns = {
        "tod_sin": np.sin(2 * np.pi * tod), "tod_cos": np.cos(2 * np.pi * tod),
        "dow_sin": np.sin(2 * np.pi * dow), "dow_cos": np.cos(2 * np.pi * dow),
        "class_day": class_day,
        "in_class": in_class_values(local, schedule.for_space(space), class_day),
    }
    return np.stack([np.asarray(columns[name], dtype=np.float32) for name in names], axis=1)
