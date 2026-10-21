"""InfluxDB 시각 확인 (research.md R2).

과제 1 수집기는 네트워크 서버 시각(nsTime)으로 저장한다. 같은 센서·항목·값을 InfluxDB에서 찾아
시각 차이(InfluxDB − 수집기)를 잰다. 0에 가까우면 InfluxDB 시각을 그대로 써도 된다.
게이트웨이 시계(약 5분 빠름)를 따랐다면 일부 센서에서 +5분 근처가 나온다.
"""

import pandas as pd

from .influx import OUT_COLUMNS, query_rows
from .loader import from_collector_db, normalize_raw

MATCH_TOLERANCE = pd.Timedelta(minutes=10)


def match_times(collector, influx):
    """수집기 각 행에 같은 센서·항목·값을 가진 가장 가까운 InfluxDB 행을 붙인다 (±10분)."""
    keys = ["device_id", "metric", "value_key"]
    frames = []
    for frame in (collector, influx):
        frame = frame.dropna(subset=["value"]).copy()
        for column in ("device_id", "metric"):
            frame[column] = frame[column].astype(str)
        frame["value_key"] = frame["value"].round(3)
        frames.append(frame.sort_values("time"))
    left, right = frames
    right = right[keys + ["time"]].rename(columns={"time": "influx_time"})
    right["_t"] = right["influx_time"]
    merged = pd.merge_asof(left, right, left_on="time", right_on="_t", by=keys, tolerance=MATCH_TOLERANCE,
                           direction="nearest")
    merged["diff_seconds"] = (merged["influx_time"] - merged["time"]).dt.total_seconds()
    return merged.drop(columns=["_t"])


def summarize(merged):
    """센서별 짝지은 비율과 시각 차이 분포."""
    def stats(group):
        diffs = group["diff_seconds"].dropna()
        return pd.Series({
            "space": group["space"].astype(str).iloc[0],
            "rows": len(group),
            "matched": len(diffs),
            "match_rate": len(diffs) / len(group) if len(group) else 0.0,
            "median_s": diffs.median() if len(diffs) else float("nan"),
            "p10_s": diffs.quantile(0.1) if len(diffs) else float("nan"),
            "p90_s": diffs.quantile(0.9) if len(diffs) else float("nan"),
        })

    rows = [stats(group).rename(device) for device, group in merged.groupby("device_id", sort=True)]
    table = pd.DataFrame(rows)
    table.index.name = "device_id"
    return table


def run(env, collector_db, metrics, hours=48, now=None):
    now = pd.Timestamp(now or pd.Timestamp.now(tz="UTC")).floor("min")
    start = now - pd.Timedelta(hours=hours)
    collector = from_collector_db(collector_db, start, now, metrics)
    if collector.empty:
        return None, None
    start = collector["time"].min().floor("min")
    influx = normalize_raw(pd.DataFrame(query_rows(env, metrics, start.to_pydatetime(), now.to_pydatetime()),
                                        columns=OUT_COLUMNS))
    merged = match_times(collector, influx)
    return merged, summarize(merged)
