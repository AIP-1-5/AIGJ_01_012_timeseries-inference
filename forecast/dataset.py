"""표본 만들기 (data-model.md, contracts/model-io.md). 과제 8 서비스가 같은 특성 함수를 쓴다.

표본 = (공간, 예측 시각 t).
  past    L × (6 + C)  칸마다 co2·temperature·humidity (정규화, 못 쓰면 0) + 쓸 수 있는지 3 + 달력·일정 C
  future  12 × C       t+5 ~ t+60분의 달력·일정 (미리 알 수 있는 정보만)
  space   4            공간 원-핫
  target  12 × 2       co2·temperature의 y(t+h) − y(t) (target="value"면 y(t+h)), 정규화 단위
  mask    12 × 2       정답이 관측값(보간·고장 아님)이고 y(t)도 관측값이면 1
  base    2            y(t) 정규화 값 (없으면 0)

공간마다 빈틈없는 5분 격자로 펼친 뒤 한 배열로 이어 붙이고, 표본은 그 배열의 위치로만 들고 있다.
배치를 만들 때 위치로 창을 잘라 쓰므로 표본 수가 많아도 메모리를 적게 쓴다.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from pipeline.baselines import load_points, masks, regular_series
from pipeline.split import PERIODS, assign_period

from .features import calendar_features
from .settings import HORIZON_STEPS, INPUT_METRICS, TARGET_METRICS

DROP_REASONS = ("no_target_base", "short_window", "edge")


@dataclass
class SampleSet:
    spaces: list                 # 공간 이름 (원-핫 순서)
    times: np.ndarray            # (N,) 칸 시각 datetime64[ns, UTC] — 모든 공간을 이어 붙인 배열
    space_id: np.ndarray         # (N,) 공간 번호
    feats: np.ndarray            # (N, 6 + C) float32
    future_feats: np.ndarray     # (N, C) float32 — 달력·일정만
    z: np.ndarray                # (N, 2) 정답 항목 정규화 값 (관측 칸만, 아니면 NaN)
    y: np.ndarray                # (N, 2) 정답 항목 원래 단위 값 (관측 칸만, 아니면 NaN) — 평가용
    observed: np.ndarray         # (N, 2) 정답으로 쓸 수 있는 칸
    std: np.ndarray              # (S, 2) 공간·항목별 표준편차 (원래 단위로 되돌릴 때)
    mean: np.ndarray             # (S, 2)
    positions: dict              # 기간 → 표본 위치 배열
    dropped: dict                # 기간 → {이유: 개수}
    lookback: int
    target_kind: str
    feature_names: list

    def batch(self, rows):
        """위치 배열 → 모델 입력·정답 (numpy)."""
        rows = np.asarray(rows)
        past_idx = rows[:, None] + np.arange(-self.lookback + 1, 1)[None, :]
        future_idx = rows[:, None] + np.arange(1, HORIZON_STEPS + 1)[None, :]
        base = np.nan_to_num(self.z[rows]).astype(np.float32)
        future_z = self.z[future_idx]                                        # (B, 12, 2)
        target = future_z if self.target_kind == "value" else future_z - base[:, None, :]
        mask = self.observed[future_idx] & self.observed[rows][:, None, :]
        space = np.eye(len(self.spaces), dtype=np.float32)[self.space_id[rows]]
        return {
            "past": self.feats[past_idx],
            "future": self.future_feats[future_idx],
            "space": space,
            "target": np.nan_to_num(target).astype(np.float32),
            "mask": mask.astype(np.float32),
            "base": base,
        }


def space_arrays(clean, space, normalization, settings, variant, schedule):
    """한 공간을 빈틈없는 격자로: (시각, 특성, 미래 특성, 정답 z, 관측, 창 사용 비율용 usable)."""
    step = settings.interval_minutes
    frames = {m: regular_series(clean, space, m, step) for m in INPUT_METRICS}
    frames = {m: f for m, f in frames.items() if not f.empty}
    if not frames:
        return None
    start = min(f.index.min() for f in frames.values())
    # 끝에 12칸(60분)을 비워 덧붙인다: 데이터 끝 근처 시각도 표본이 되도록 (정답이 없는 칸은 마스크 0)
    end = max(f.index.max() for f in frames.values()) + pd.Timedelta(minutes=step * HORIZON_STEPS)
    grid = pd.date_range(start, end, freq=f"{step}min")
    n = len(grid)
    values, ok, observed = np.zeros((n, 3), np.float32), np.zeros((n, 3), bool), np.zeros((n, 3), bool)
    raw = np.full((n, 3), np.nan)
    stats = normalization["values"].get(space, {})
    for j, metric in enumerate(INPUT_METRICS):
        if metric not in frames or metric not in stats:
            continue
        frame = frames[metric].reindex(grid)
        frame["interpolated"] = frame["interpolated"].fillna(False).astype(bool)
        frame["fault"] = frame["fault"].fillna("")
        obs, usable = masks(frame)
        z = ((frame["value"] - stats[metric]["mean"]) / stats[metric]["std"]).to_numpy()
        values[:, j] = np.where(usable, z, 0.0)
        raw[:, j] = np.where(obs, frame["value"].to_numpy(), np.nan)
        ok[:, j], observed[:, j] = usable.to_numpy(), obs.to_numpy()
    cal = calendar_features(grid, space, variant, schedule, settings.timezone, step)
    feats = np.concatenate([values, ok.astype(np.float32), cal], axis=1).astype(np.float32)
    target_cols = [INPUT_METRICS.index(m) for m in TARGET_METRICS]
    z_target = np.where(observed[:, target_cols], values[:, target_cols], np.nan)
    mean = [stats.get(m, {}).get("mean", 0.0) for m in TARGET_METRICS]
    std = [stats.get(m, {}).get("std", 1.0) for m in TARGET_METRICS]
    return (grid, feats, cal, z_target, raw[:, target_cols], observed[:, target_cols], ok[:, target_cols],
            np.array(mean), np.array(std))


def build_samples(clean, split, normalization, settings, schedule, variant, lookback, target_kind="delta",
                  min_window_fraction=0.75, eval_points=None):
    """모든 공간의 표본. 학습·검증 표본은 t와 t+60분이 그 기간 안, 시험 표본은 eval_points의 시각."""
    spaces = [spec.space for spec in settings.spaces]
    parts, offset = [], 0
    positions = {p: [] for p in PERIODS}
    dropped = {p: {r: 0 for r in DROP_REASONS} for p in PERIODS}
    means, stds = [], []
    need = int(np.ceil(lookback * min_window_fraction))
    for sid, space in enumerate(spaces):
        arrays = space_arrays(clean, space, normalization, settings, variant, schedule)
        if arrays is None:
            means.append(np.zeros(2)), stds.append(np.ones(2))
            continue
        grid, feats, cal, z, y, observed, usable, mean, std = arrays
        means.append(mean), stds.append(std)
        n = len(grid)
        parts.append((grid, np.full(n, sid), feats, cal, z, observed, y))
        index = np.arange(n)
        inside = (index >= lookback - 1) & (index + HORIZON_STEPS < n)
        window_ok = pd.DataFrame(usable).rolling(lookback, min_periods=lookback).sum().to_numpy() >= need
        has_base = observed.any(axis=1)
        good = has_base & (window_ok & observed).any(axis=1)
        period = assign_period(pd.Series(grid), split).to_numpy()
        horizon_end = grid + pd.Timedelta(minutes=settings.interval_minutes * HORIZON_STEPS)
        same_period = assign_period(pd.Series(horizon_end), split).to_numpy() == period
        for name in ("train", "val"):
            in_period = period == name
            keep = in_period & inside & same_period & good
            positions[name].append(index[keep] + offset)
            candidates = in_period & same_period
            dropped[name]["edge"] += int((candidates & ~inside).sum())
            dropped[name]["no_target_base"] += int((candidates & inside & ~has_base).sum())
            dropped[name]["short_window"] += int((candidates & inside & has_base & ~good).sum())
        if eval_points is not None:
            times = pd.DatetimeIndex(eval_points.loc[eval_points["space"] == space, "time"].unique())
            loc = grid.get_indexer(times)
            usable_loc = loc[(loc >= lookback - 1) & (loc + HORIZON_STEPS < n)]
            dropped["test"]["edge"] += int(len(loc) - len(usable_loc))
            positions["test"].append(np.sort(usable_loc) + offset)
        offset += n
    if not parts:
        raise ValueError("표본을 만들 데이터가 없다")
    grid_all = np.concatenate([np.asarray(p[0].tz_convert("UTC").tz_localize(None)) for p in parts])
    return SampleSet(
        spaces=spaces,
        times=grid_all,
        space_id=np.concatenate([p[1] for p in parts]),
        feats=np.concatenate([p[2] for p in parts]),
        future_feats=np.concatenate([p[3] for p in parts]),
        z=np.concatenate([p[4] for p in parts]),
        observed=np.concatenate([p[5] for p in parts]),
        y=np.concatenate([p[6] for p in parts]),
        std=np.vstack(stds),
        mean=np.vstack(means),
        positions={k: (np.concatenate(v) if v else np.array([], dtype=int)) for k, v in positions.items()},
        dropped=dropped,
        lookback=lookback,
        target_kind=target_kind,
        feature_names=[f"{m}_z" for m in INPUT_METRICS] + [f"{m}_ok" for m in INPUT_METRICS],
    )


def samples_for(fsettings, variant, target_kind=None, with_test=True, lookback=None):
    """설정 파일 기준으로 과제 2 산출물을 읽어 표본을 만든다."""
    from pipeline.preprocess import clean_path, load_clean
    from pipeline.split import load_normalization, load_split, normalization_path, split_path
    from pipeline.baselines import points_path

    settings = fsettings.pipeline
    clean = load_clean(clean_path(settings))
    split = load_split(split_path(settings))
    normalization = load_normalization(normalization_path(settings))
    points = load_points(points_path(settings)) if with_test else None
    return build_samples(clean, split, normalization, settings, fsettings.schedule, variant,
                         lookback or fsettings.lookback_steps, target_kind or fsettings.target,
                         fsettings.min_window_fraction, points)
