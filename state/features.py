"""과제 5 특성과 정답. 과제 8 서비스가 같은 특성 함수를 쓴다.

센서 특성(시각 t까지의 값만): 수준과 5·15·30·60분 변화, 30분 표준편차.
달력·일정 특성(환기 경보에만): 하루 시각·요일, 일정 있는 날, 지금·30분 안 수업 여부.
사용 여부 입력에는 달력·일정을 넣지 않는다 (정답을 일정에서 만들었기 때문, spec 참고).
"""

import numpy as np
import pandas as pd

from forecast.features import calendar_features

SENSOR_FEATURES = [
    "co2", "co2_d5", "co2_d15", "co2_d30", "co2_d60", "co2_std30",
    "temperature", "temp_d15", "temp_d60", "humidity", "hum_d15", "hum_d60",
]
CALENDAR_FEATURES = ["tod_sin", "tod_cos", "dow_sin", "dow_cos", "class_day", "in_class", "in_class_next30"]
CO2_LIMIT = 1000.0
HORIZON_SLOTS = 6                    # 30분 = 6칸


def sensor_features(frame):
    """frame: anomaly.data.space_frames의 한 공간 표 (값은 쓸 수 있는 칸만, 아니면 NaN)."""
    co2, temp, hum = frame["co2"], frame["temperature"], frame["humidity"]
    return pd.DataFrame({
        "co2": co2, "co2_d5": co2.diff(1), "co2_d15": co2.diff(3), "co2_d30": co2.diff(6), "co2_d60": co2.diff(12),
        "co2_std30": co2.rolling(6, min_periods=4).std(),
        "temperature": temp, "temp_d15": temp.diff(3), "temp_d60": temp.diff(12),
        "humidity": hum, "hum_d15": hum.diff(3), "hum_d60": hum.diff(12),
    }, index=frame.index)


def calendar_frame(index, space, schedule, tz):
    cal = calendar_features(index, space, "full", schedule, tz)
    frame = pd.DataFrame(cal, index=index, columns=["tod_sin", "tod_cos", "dow_sin", "dow_cos", "class_day", "in_class"])
    frame["in_class"] = (frame["in_class"] >= 1.0).astype(float)
    frame["in_class_next30"] = frame["in_class"][::-1].rolling(HORIZON_SLOTS + 1, min_periods=1).max()[::-1]
    return frame


def occupancy_labels(index, space, schedule, tz, transition_minutes=15):
    """일정 기반 사용 여부: 수업 1, 그 밖 0. 점심과 일정 변화 직후는 NaN(학습·일정 기준 평가에서 뺌).

    일정이 없는 공간은 모두 NaN.
    """
    labels = pd.Series(np.nan, index=index)
    lab = schedule.for_space(space)
    if lab is None:
        return labels
    cal = calendar_features(index, space, "full", schedule, tz)
    in_class, class_day = cal[:, 5], cal[:, 4]
    labels[:] = np.where(in_class >= 1.0, 1.0, 0.0)
    labels[(in_class > 0) & (in_class < 1)] = np.nan                  # 점심 (1~3명이 남을 수 있음)
    local = index.tz_convert(tz) - pd.Timedelta(minutes=5)
    minutes = np.asarray(local.hour * 60 + local.minute)
    for event in lab.events:
        at = event.time.hour * 60 + event.time.minute
        near = (class_day > 0) & (minutes >= at) & (minutes < at + transition_minutes)
        labels[near] = np.nan
    return labels


def ventilation_labels(co2):
    """양성 = 지금 < 1,000이고 30분 안(t+5~t+30)에 1,000 이상. 이미 1,000 이상이거나 값이 없으면 NaN."""
    future = pd.concat([co2.shift(-k) for k in range(1, HORIZON_SLOTS + 1)], axis=1)
    future_max = future.max(axis=1, skipna=True)
    has_future = future.notna().sum(axis=1) >= HORIZON_SLOTS // 2
    label = (future_max >= CO2_LIMIT).astype(float)
    label[(co2 >= CO2_LIMIT) | co2.isna() | ~has_future] = np.nan
    return label


def observation_labels(index, space, observations, tz):
    """직접 관찰 기록 → 사용중 1 / 비어있음 0 (기록 없는 칸은 NaN)."""
    labels = pd.Series(np.nan, index=index)
    if observations is None or observations.empty:
        return labels
    local_end = index.tz_convert(tz)
    for row in observations[observations["space"] == space].itertuples():
        start = pd.Timestamp(row.start_kst, tz=tz)
        end = pd.Timestamp(row.end_kst, tz=tz)
        inside = (local_end > start) & (local_end <= end)
        labels[inside] = 1.0 if float(row.people) > 0 else 0.0
    return labels


def load_observations(path):
    if not path.exists():
        return None
    frame = pd.read_csv(path, dtype={"space": str})
    return frame.dropna(subset=["space", "start_kst", "end_kst", "people"])
