"""합성 정제 데이터(clean_5min 형식) 만들기. 시험에서 손으로 계산할 수 있는 값을 넣으려고 쓴다."""

import numpy as np
import pandas as pd

from pipeline.preprocess import CLEAN_COLUMNS


def clean_frame(start, end, spaces_metrics, value_fn, freq="5min"):
    """start~end(UTC) 5분 격자, (공간, 항목)마다 value_fn(times, space, metric) 값."""
    times = pd.date_range(start, end, freq=freq, tz="UTC")
    parts = []
    for space, metric in spaces_metrics:
        values = np.asarray(value_fn(times, space, metric), dtype=float)
        parts.append(pd.DataFrame({
            "time": times, "space": space, "metric": metric, "value": values,
            "n_raw": 5, "n_sensors": 1, "interpolated": False, "fault": "",
        }))
    frame = pd.concat(parts, ignore_index=True)[CLEAN_COLUMNS]
    frame.loc[frame["value"].isna(), ["n_raw", "n_sensors"]] = 0
    return frame
