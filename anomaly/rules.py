"""규칙 기반 탐지 (spec US3). 기준값은 학습 기간 정상 데이터에서만 정한다.

  spike        |Δ| > 학습 기간 99.99% × 1.5 이고 다음 칸에서 반대로 되돌아옴 (튄 값)
  stuck        CO₂·습도가 N칸 동안 완전히 같은 값 (N = 학습 기간 정상 최장 × 1.5, 최소 6칸)
  frozen       습도(또는 온도)가 9칸 넘게 같은 값인데 같은 공간의 온도(또는 습도)는 그동안 움직임
               (상대습도는 온도를 따라 바뀌므로 한쪽만 멈추면 고장). 움직임 기준 = 학습 기간 정상 분위 × 1.2
  night_level  사람 없는 밤(00~06시) 최근 1시간 최솟값 CO₂ > 학습 기간 밤 99.5% (영점 틀어짐)
  hot_class    수업 중 온도 − 최근 일정일 같은 시각 중앙값 > 학습 기간 99.9% (냉방 정지)
  high_class   수업 중 CO₂ > 학습 기간 같은 30분 시각대 99.9% (환기 정지)
"""

import json

import numpy as np
import pandas as pd

from forecast.features import calendar_features

RULES = ("spike", "stuck", "frozen", "night_level", "hot_class", "high_class")
FROZEN_PAIRS = {"humidity": "temperature", "temperature": "humidity"}   # 멈춘 항목 → 움직임을 볼 짝
FROZEN_BINS = 9                                                          # 45분 (CO₂ 값 고정 기준과 같은 길이)
FROZEN_FLOOR = {"temperature": 0.3, "humidity": 1.5}                     # 짝 움직임 기준의 최솟값
RULE_METRIC = {"night_level": "co2", "hot_class": "temperature", "high_class": "co2"}


def max_run(series):
    """같은 값이 연속한 최장 칸 수 (NaN에서 끊김)."""
    values = series.to_numpy()
    best, run, prev = 0, 0, None
    for v in values:
        if np.isnan(v):
            run, prev = 0, None
            continue
        run = run + 1 if prev is not None and v == prev else 1
        prev = v
        best = max(best, run)
    return best


def run_length(series):
    """칸마다 지금까지 같은 값이 이어진 칸 수."""
    values = series.to_numpy()
    change = np.r_[True, values[1:] != values[:-1]] | np.isnan(values)
    group = np.cumsum(change)
    return pd.Series(values, index=series.index).groupby(group).cumcount().to_numpy() + 1


def partner_range(metric_values, partner_values):
    """칸마다 (지금 같은 값이 이어진 칸 수, 그 구간 동안 짝 항목의 최대−최소)."""
    values = metric_values.to_numpy()
    change = np.r_[True, values[1:] != values[:-1]] | np.isnan(values)
    group = np.cumsum(change)
    length = pd.Series(values).groupby(group).cumcount().to_numpy() + 1
    partner = pd.Series(partner_values.to_numpy())
    span = (partner.groupby(group).cummax() - partner.groupby(group).cummin()).to_numpy()
    return length, span


def observed(frame, metric):
    flag = frame.get(f"obs_{metric}")
    return frame[metric] if flag is None else frame[metric].where(flag.astype(bool))


def local_info(index, space, schedule, tz):
    cal = calendar_features(index, space, "full", schedule, tz)
    local = index.tz_convert(tz) - pd.Timedelta(minutes=5)
    return local, cal[:, 5] >= 1.0, local.hour


def class_reference(temp, in_class, local):
    """같은 시각, 최근 7개 일정일(오늘 제외)의 중앙값 (과거만 씀)."""
    frame = pd.DataFrame({"v": temp.where(in_class).to_numpy(), "day": local.date, "clock": local.hour * 60 + local.minute},
                         index=temp.index)
    wide = frame.pivot_table(index="day", columns="clock", values="v", aggfunc="mean")
    ref = wide.rolling(7, min_periods=3).median().shift(1)
    lookup = ref.stack().rename("ref")
    keys = pd.MultiIndex.from_arrays([frame["day"], frame["clock"]])
    return pd.Series(lookup.reindex(keys).to_numpy(), index=temp.index)


def fit(train_frames, schedule, tz):
    """학습 기간에서 기준값 계산."""
    params = {"spike": {}, "stuck": {}, "frozen": {}, "night_level": {}, "hot_class": {}, "high_class": {}}
    runs = {"co2": 0, "humidity": 0}
    for space, frame in train_frames.items():
        local, in_class, hour = local_info(frame.index, space, schedule, tz)
        for metric in ("co2", "temperature", "humidity"):
            diff = frame[metric].diff().abs().dropna()
            if len(diff) > 100:
                params["spike"][f"{space}|{metric}"] = float(diff.quantile(0.9999) * 1.5)
        for metric in runs:
            if frame[metric].notna().mean() > 0.5:
                runs[metric] = max(runs[metric], max_run(frame[metric].where(frame[f"obs_{metric}"])))
        for metric, partner in FROZEN_PAIRS.items():
            if frame[metric].notna().mean() < 0.5 or frame[partner].notna().mean() < 0.5:
                continue
            length, span = partner_range(observed(frame, metric), observed(frame, partner))
            normal = span[(length >= FROZEN_BINS) & ~np.isnan(span)]
            limit = float(np.quantile(normal, 0.9999)) * 1.2 if len(normal) else 0.0
            params["frozen"][f"{space}|{metric}"] = max(limit, FROZEN_FLOOR[partner])
        co2 = frame["co2"]
        if co2.notna().mean() > 0.5:
            night = co2.rolling(12, min_periods=6).min()[(hour >= 0) & (hour < 6)].dropna()
            if len(night) > 100:
                params["night_level"][space] = float(night.quantile(0.995))
        if in_class.any():
            temp = frame["temperature"]
            resid = (temp - class_reference(temp, in_class, local))[in_class].dropna()
            if len(resid) > 100:
                params["hot_class"][space] = float(resid.quantile(0.999))
            bucket = (local.hour * 2 + local.minute // 30)
            high = co2[in_class].groupby(np.asarray(bucket)[in_class]).quantile(0.999)
            params["high_class"][space] = {str(int(k)): float(v) for k, v in high.items()}
    params["stuck"] = {m: int(max(6, np.ceil(r * 1.5))) for m, r in runs.items()}
    params["stuck_normal_max"] = runs
    return params


def apply(frames, params, schedule, tz):
    """규칙 경보 칸: DataFrame(space, time, rule, cause)."""
    rows = []
    for space, frame in frames.items():
        local, in_class, hour = local_info(frame.index, space, schedule, tz)
        for metric in ("co2", "temperature", "humidity"):
            limit = params["spike"].get(f"{space}|{metric}")
            if limit is None:
                continue
            v = frame[metric]
            d1, d2 = v.diff(), v.diff().shift(-1)
            hit = (d1.abs() > limit) & (d2.abs() > 0.5 * limit) & (np.sign(d1) != np.sign(d2))
            # 다음 칸을 봐야 알 수 있으므로 경보는 다음 칸 시각에 울린다
            for t in frame.index[hit.fillna(False).to_numpy()]:
                rows.append((space, t + pd.Timedelta(minutes=5), "spike", metric))
        for metric, n in params["stuck"].items():
            v = frame[metric]
            if v.notna().mean() < 0.5:
                continue
            length = run_length(v)
            hit = (length >= n) & v.notna().to_numpy()
            rows += [(space, t, "stuck", metric) for t in frame.index[hit]]
        for metric, partner in FROZEN_PAIRS.items():
            limit = params.get("frozen", {}).get(f"{space}|{metric}")
            if limit is None:
                continue
            length, span = partner_range(observed(frame, metric), observed(frame, partner))
            hit = (length >= FROZEN_BINS) & (np.nan_to_num(span) > limit) & frame[metric].notna().to_numpy()
            rows += [(space, t, "frozen", metric) for t in frame.index[hit]]
        if space in params["night_level"]:
            level = frame["co2"].rolling(12, min_periods=6).min()
            hit = (level > params["night_level"][space]) & (hour >= 0) & (hour < 6)
            rows += [(space, t, "night_level", "co2") for t in frame.index[hit.fillna(False).to_numpy()]]
        if space in params["hot_class"] and in_class.any():
            temp = frame["temperature"]
            resid = temp - class_reference(temp, in_class, local)
            hit = (resid > params["hot_class"][space]) & in_class
            rows += [(space, t, "hot_class", "temperature") for t in frame.index[hit.fillna(False).to_numpy()]]
        if space in params["high_class"] and in_class.any():
            bucket = np.asarray(local.hour * 2 + local.minute // 30).astype(str)
            limit = pd.Series(bucket).map(params["high_class"][space]).to_numpy(dtype=float)
            hit = (frame["co2"].to_numpy() > limit) & in_class
            rows += [(space, t, "high_class", "co2") for t in frame.index[np.nan_to_num(hit).astype(bool)]]
    return pd.DataFrame(rows, columns=["space", "time", "rule", "cause"]).sort_values(["space", "time"]).reset_index(drop=True)


def save(params, path):
    path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))
