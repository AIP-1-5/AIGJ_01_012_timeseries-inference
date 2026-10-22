"""오토인코더 입력 특성 (numpy·pandas만). 학습(anomaly.model)과 과제 8 서비스(torch 없음)가 같이 쓴다.

창 = 최근 1시간(12칸)의 co2·temperature·humidity 정규화 값(z)과 5분 변화량(Δz).
조건 = 하루 시각 sin·cos, 일정 있는 날, 공간 원-핫 (재구성하지 않고 입력으로만).
점수 = 마지막 TAIL칸(15분)의 z·Δz 재구성 절대 오차 평균.
"""

import numpy as np
import pandas as pd

from forecast.features import calendar_features

from .data import METRICS

TAIL = 3


def window_features(frame, space, spaces, normalization, schedule, window, tz="Asia/Seoul"):
    """한 공간 표 → (X 창 (n, window, 6), 마스크 (n, window, 6), 조건 (n, 3 + 공간 수), 쓸 수 있는 시각 위치)."""
    stats = normalization["values"].get(space, {})
    n = len(frame)
    z = np.zeros((n, 3), np.float32)
    ok = np.zeros((n, 3), bool)
    for j, metric in enumerate(METRICS):
        if metric in stats:
            values = ((frame[metric] - stats[metric]["mean"]) / stats[metric]["std"]).to_numpy()
            ok[:, j] = ~np.isnan(values)
            z[:, j] = np.nan_to_num(values)
    dz = np.zeros_like(z)
    dz[1:] = z[1:] - z[:-1]
    dz_ok = np.zeros_like(ok)
    dz_ok[1:] = ok[1:] & ok[:-1]
    dz = np.where(dz_ok, dz, 0.0)
    values = np.concatenate([z, dz], axis=1)
    mask = np.concatenate([ok, dz_ok], axis=1)
    cal = calendar_features(frame.index, space, "full", schedule, tz)[:, [0, 1, 4]]
    onehot = np.zeros((n, len(spaces)), np.float32)
    onehot[:, spaces.index(space)] = 1
    cond = np.concatenate([cal, onehot], axis=1).astype(np.float32)
    index = np.arange(window - 1, n)
    present = ok.any(axis=1)
    usable = pd.Series(present).rolling(window, min_periods=window).mean().to_numpy() >= 0.75
    valid = index[present[index] & usable[index]]
    return values.astype(np.float32), mask, cond, valid
