"""대시보드용 센서 실측값 (/v1/sensors). 모델 계산과 따로, 수집기 DB를 읽기 전용으로 읽는다.

센서마다 항목별 마지막 값(CO₂·온도·습도·배터리, 들어오면 TVOC·기압·조도·소음 등)과
최근 history_minutes의 CO₂·온도·습도를 bucket_minutes 칸 평균으로 돌려준다.
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HISTORY_METRICS = ("co2", "temperature", "humidity")


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_sensors(db, now, intervals, spaces=(), history_minutes=180, bucket_minutes=5, latest_hours=26,
                 stale_factor=3.0, stale_min_minutes=5.0, event_profiles=()):
    """spaces: [(공간 이름, slug)] — 이 순서로 먼저 보여 주고, 나머지 공간은 이름 순으로 뒤에 붙인다."""
    uri = f"file:{Path(db).resolve()}?mode=ro"
    since = _iso(now - timedelta(hours=latest_hours))
    start = now - timedelta(minutes=history_minutes)
    start = start - timedelta(seconds=start.second, microseconds=start.microsecond,
                              minutes=start.minute % bucket_minutes)
    n_buckets = int((now - start) / timedelta(minutes=bucket_minutes)) + 1
    with sqlite3.connect(uri, uri=True) as conn:
        latest = conn.execute(
            "SELECT device_id, device_profile, space, spot, metric, value, unit, measured_at FROM measurements "
            "WHERE id IN (SELECT MAX(id) FROM measurements WHERE measured_at >= ? GROUP BY device_id, metric)",
            (since,)).fetchall()
        history = conn.execute(
            "SELECT device_id, metric, measured_at, value FROM measurements "
            f"WHERE measured_at >= ? AND metric IN ({','.join('?' * len(HISTORY_METRICS))})",
            (_iso(start), *HISTORY_METRICS)).fetchall()

    devices = {}
    for device, profile, space, spot, metric, value, unit, at in latest:
        d = devices.setdefault(device, {"device_id": device, "profile": profile, "space": space, "spot": spot,
                                        "last_seen": None, "metrics": {}, "history": {}})
        d["metrics"][metric] = {"value": value, "unit": unit, "at": at}
        if d["last_seen"] is None or at > d["last_seen"]:
            d["last_seen"] = at

    sums = {}
    for device, metric, at, value in history:
        i = int((_parse(at) - start) / timedelta(minutes=bucket_minutes))
        if 0 <= i < n_buckets:
            s = sums.setdefault((device, metric), [[0.0, 0] for _ in range(n_buckets)])
            s[i][0] += value
            s[i][1] += 1
    for (device, metric), buckets in sums.items():
        if device in devices:
            devices[device]["history"][metric] = [round(t / n, 2) if n else None for t, n in buckets]

    for d in devices.values():
        age = (now - _parse(d["last_seen"])).total_seconds() / 60
        d["age_minutes"] = round(age, 1)
        if d["profile"] in event_profiles:          # 문 열림처럼 사건이 있을 때만 보내는 센서는 끊김을 판정하지 않는다
            d["kind"], d["stale"] = "event", None
        else:
            limit = max(intervals.minutes_for(d["device_id"], d["profile"]) * stale_factor, stale_min_minutes)
            d["kind"], d["stale"] = "periodic", age > limit

    slugs = dict(spaces)
    order = {name: i for i, (name, _) in enumerate(spaces)}
    grouped = {}
    for d in devices.values():
        grouped.setdefault(d.pop("space"), []).append(d)
    out = []
    for space in sorted(grouped, key=lambda s: (order.get(s, len(order)), s)):
        sensors = sorted(grouped[space], key=lambda d: (d["spot"] or "~", d["profile"] or "", d["device_id"]))
        out.append({"space": space, "slug": slugs.get(space), "total": len(sensors),
                    "stale": sum(bool(d["stale"]) for d in sensors), "sensors": sensors})
    return {"generated_at": _iso(now),
            "history": {"start": _iso(start), "bucket_minutes": bucket_minutes, "buckets": n_buckets, "metrics": list(HISTORY_METRICS)},
            "spaces": out}


class SensorReader:
    """HTTP 요청마다 DB를 읽지 않게 cache_seconds 동안 결과를 재사용한다."""

    def __init__(self, db, intervals, spaces, cfg):
        self.db, self.intervals, self.spaces = db, intervals, list(spaces)
        self.cfg = cfg
        self.cache_seconds = float(cfg.get("cache_seconds", 20))
        self.lock = threading.Lock()
        self.cached, self.cached_at = None, 0.0

    def get(self):
        with self.lock:
            if self.cached is None or time.time() - self.cached_at > self.cache_seconds:
                self.cached = read_sensors(
                    self.db, datetime.now(timezone.utc), self.intervals, self.spaces,
                    history_minutes=int(self.cfg.get("history_minutes", 180)),
                    bucket_minutes=int(self.cfg.get("bucket_minutes", 5)),
                    stale_factor=float(self.cfg.get("stale_factor", 3)),
                    stale_min_minutes=float(self.cfg.get("stale_min_minutes", 5)),
                    event_profiles=tuple(self.cfg.get("event_profiles", ())))
                self.cached_at = time.time()
            return self.cached
