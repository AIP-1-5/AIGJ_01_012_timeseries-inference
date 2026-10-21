"""원시 데이터 읽기: InfluxDB 내보내기 파일과 과제 1 수집기 기록을 같은 열로 (data-model.md)."""

import sqlite3
from pathlib import Path

import pandas as pd

RAW_COLUMNS = ["time", "device_id", "space", "spot", "metric", "value"]
CATEGORY_COLUMNS = ["device_id", "space", "spot", "metric"]


def normalize_raw(frame):
    """열 이름·형식 맞추기: time은 UTC, value는 실수, 글자 열은 category (메모리 절약)."""
    frame = frame.reindex(columns=RAW_COLUMNS)
    frame["time"] = pd.to_datetime(frame["time"], utc=True, format="ISO8601")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    for column in CATEGORY_COLUMNS:
        frame[column] = frame[column].fillna("").astype(str).astype("category")
    return frame


def raw_files(raw_dir, metric):
    return sorted((Path(raw_dir) / metric).glob("*.csv.gz"))


def read_raw_files(files):
    files = list(files)
    if not files:
        return normalize_raw(pd.DataFrame(columns=RAW_COLUMNS))
    # 파일마다 category로 바꾼 뒤 합친다 (1년치 글자 열을 한꺼번에 들고 있지 않게)
    parts = [normalize_raw(pd.read_csv(f, dtype={"device_id": str, "space": str, "spot": str, "metric": str},
                                       keep_default_na=False, na_values={"value": [""]})) for f in files]
    for column in CATEGORY_COLUMNS:
        categories = sorted(set().union(*(part[column].cat.categories for part in parts)))
        for part in parts:
            part[column] = part[column].cat.set_categories(categories)
    return pd.concat(parts, ignore_index=True)


def load_metric(raw_dir, metric):
    """한 항목의 월 파일 전부."""
    return read_raw_files(raw_files(raw_dir, metric))


def from_collector_export(path):
    """과제 1 `collector export` CSV (measured_at → time)."""
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return normalize_raw(frame.rename(columns={"measured_at": "time"}))


def from_collector_db(db_path, start, end, metrics=None):
    """과제 1 수집기 SQLite에서 [start, end) 기간을 읽기 (읽기 전용, 수집 중에도 안전)."""
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    query = "SELECT measured_at AS time, device_id, space, spot, metric, value FROM measurements WHERE measured_at >= ? AND measured_at < ?"
    params = [_iso(start), _iso(end)]
    if metrics:
        query += f" AND metric IN ({','.join('?' * len(metrics))})"
        params += list(metrics)
    with sqlite3.connect(uri, uri=True) as conn:
        frame = pd.read_sql_query(query, conn, params=params)
    return normalize_raw(frame)


def _iso(ts):
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S")
