"""공간별 넓은 표: 칸 시각 × (co2, temperature, humidity). 과제 2 정제 결과에서 만든다.

값은 쓸 수 있는 칸(관측·보간, 고장 아님)만, 아니면 NaN. 관측 여부(보간 아님)는 따로 들고 있는다.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.baselines import masks, regular_series

METRICS = ("co2", "temperature", "humidity")


@dataclass
class AnomalySettings:
    path: Path
    inject: dict
    model: dict
    alarm: dict


def load_anomaly_settings(path="config/anomaly.toml"):
    with open(path, "rb") as fp:
        data = tomllib.load(fp)
    return AnomalySettings(Path(path), data.get("inject", {}), data.get("model", {}), data.get("alarm", {}))


def space_frames(clean, settings):
    """{공간: DataFrame(index=칸 시각 UTC, columns=co2·temperature·humidity + obs_<항목>)}"""
    frames = {}
    step = settings.interval_minutes
    for spec in settings.spaces:
        parts = {m: regular_series(clean, spec.space, m, step) for m in METRICS if m in spec.metrics}
        parts = {m: f for m, f in parts.items() if not f.empty}
        if not parts:
            continue
        grid = pd.date_range(min(f.index.min() for f in parts.values()), max(f.index.max() for f in parts.values()),
                             freq=f"{step}min")
        frame = pd.DataFrame(index=grid)
        for metric in METRICS:
            if metric not in parts:
                frame[metric] = np.nan
                frame[f"obs_{metric}"] = False
                continue
            part = parts[metric].reindex(grid)
            part["interpolated"] = part["interpolated"].fillna(False).astype(bool)
            part["fault"] = part["fault"].fillna("")
            observed, usable = masks(part)
            frame[metric] = part["value"].where(usable)
            frame[f"obs_{metric}"] = observed.to_numpy()
        frames[spec.space] = frame
    return frames


def period_slice(frames, start, end):
    return {space: frame[(frame.index >= start) & (frame.index < end)] for space, frame in frames.items()}
