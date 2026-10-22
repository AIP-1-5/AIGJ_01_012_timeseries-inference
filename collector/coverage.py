"""커버율, 결측 구간, 결측 원인 추정 (research.md R8, data-model.md "파생 값").

- 커버율: 기간을 센서의 기대 간격으로 나눈 칸 중 값이 1건 이상 있는 칸의 비율 (1분 기준도 함께)
- 결측 구간: 연속으로 빈 칸이 기대 간격 × 2 이상. 센서 단위로 찾는다(같은 메시지의 항목들은 함께 오므로)
- 원인 추정 순서: 수집기 사건 → fCnt 건너뜀(전달 중 손실) → fCnt 이어짐(센서가 안 보냄, 배터리)
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from .timeutil import from_iso, to_iso

# 커버율은 과제의 측정 항목만 본다. 배터리는 값만 보여 주고, 그 밖의 항목(TVOC·기압·조도·소음 등)은 대시보드용으로만 저장한다
COVERAGE_METRICS = {"co2", "temperature", "humidity"}
LOW_BATTERY_PERCENT = 10
STALE_MIN = timedelta(minutes=5)
STALE_INTERVAL_FACTOR = 3
GAP_MIN_SLOTS = 2
RECENT_SERIES_DAYS = 7

UP_KINDS = {"connect", "subscribe"}
DOWN_KINDS = {"start", "disconnect", "stop", "reconnect_attempt"}

CAUSE_LABELS = {
    "collector": "수집기 쪽",
    "upstream": "전달 중 손실 (무선·게이트웨이·서버)",
    "sensor": "센서 쪽 (센서가 보내지 않음)",
    "sensor_battery": "센서 쪽 (배터리 낮음)",
    "sensor_reset": "센서 재시작 (fCnt 초기화)",
    "excluded": "시험 시간대 (제외)",
}


@dataclass
class Gap:
    device_id: str
    start: object
    end: object
    cause: str = "sensor"
    evidence: str = ""

    @property
    def duration(self):
        return self.end - self.start


@dataclass
class SeriesCoverage:
    device_id: str
    metric: str
    device_profile: str | None
    space: str
    spot: str | None
    interval_min: float
    count: int = 0
    last_measured: object = None
    coverage: float = 0.0
    coverage_1min: float = 0.0
    stale: bool = False
    age: object = None


@dataclass
class CoverageResult:
    start: object
    end: object
    series: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    battery: dict = field(default_factory=dict)       # device_id → 최근 배터리 값
    delay_median_seconds: float | None = None         # received_at - measured_at 중앙값
    skewed_gw_count: int = 0                          # |gw_time - measured_at| > 60초인 메시지 수
    message_count: int = 0

    def passed(self, threshold=0.95):
        return [s for s in self.series if s.coverage >= threshold]


def _slot_index(t, start, interval):
    return int((t - start) / interval)


def valid_slots(start, end, interval, excluded=()):
    """제외 시간대와 겹치지 않는 칸 번호 목록."""
    n = math.ceil((end - start) / interval)
    slots = []
    for i in range(n):
        slot_start = start + i * interval
        slot_end = min(slot_start + interval, end)
        if any(ex_start < slot_end and slot_start < ex_end for ex_start, ex_end in excluded):
            continue
        slots.append(i)
    return slots


def slot_coverage(times, start, end, interval, excluded=()):
    slots = valid_slots(start, end, interval, excluded)
    if not slots:
        return 0.0
    filled = {_slot_index(t, start, interval) for t in times if start <= t < end}
    return len(filled.intersection(slots)) / len(slots)


def find_gaps(times, start, end, interval, excluded=(), min_slots=GAP_MIN_SLOTS):
    """빈 칸이 min_slots개 이상 이어진 구간 [(시작, 끝)]. 제외 시간대는 구간을 끊는다."""
    valid = set(valid_slots(start, end, interval, excluded))
    filled = {_slot_index(t, start, interval) for t in times if start <= t < end}
    n = math.ceil((end - start) / interval)
    gaps, run = [], []
    for i in range(n + 1):
        empty = i < n and i in valid and i not in filled
        if empty:
            run.append(i)
            continue
        if len(run) >= min_slots:
            gaps.append((start + run[0] * interval, min(start + (run[-1] + 1) * interval, end)))
        run = []
    return gaps


def collector_down_periods(events, start, end, initial_up):
    """사건 기록으로 수집기가 브로커에 붙어 있지 않던 구간 [(시작, 끝)]."""
    periods, up, down_since = [], initial_up, None if initial_up else start
    for event in events:
        at = from_iso(event["at"])
        if event["kind"] in UP_KINDS and not up:
            periods.append((down_since, at))
            up, down_since = True, None
        elif event["kind"] in DOWN_KINDS and up:
            up, down_since = False, at
    if not up:
        periods.append((down_since, end))
    return [(s, e) for s, e in periods if e > s]


def attribute_cause(gap_start, gap_end, down_periods, fcnt_before, fcnt_after, battery):
    for down_start, down_end in down_periods:
        if down_start < gap_end and gap_start < down_end:
            return "collector", "그 시간에 수집기가 중단·끊김 상태 (사건 기록)"
    if fcnt_before is not None and fcnt_after is not None:
        diff = fcnt_after - fcnt_before
        if diff >= 2:
            return "upstream", f"fCnt {fcnt_before}→{fcnt_after}: 센서가 보낸 {diff - 1}건이 도착하지 않음"
        if diff < 0:
            return "sensor_reset", f"fCnt {fcnt_before}→{fcnt_after}: 번호가 줄어듦"
    if battery is not None and battery <= LOW_BATTERY_PERCENT:
        return "sensor_battery", f"배터리 {battery:g}%, fCnt {fcnt_before}→{fcnt_after if fcnt_after is not None else '수신 없음'}"
    if fcnt_after is None:
        return "sensor", "이후 수신 없음 (센서가 보내지 않거나 아직 복구되지 않음)"
    return "sensor", f"fCnt {fcnt_before}→{fcnt_after}: 번호가 이어짐 (센서가 보내지 않음)"


def _parse_rows(rows):
    """측정값 행을 센서·항목별 시각 목록과 센서별 (시각, fCnt) 목록으로 묶는다."""
    by_series = defaultdict(list)
    by_device = defaultdict(list)
    meta = {}
    battery = {}
    delays, skewed, keys = [], 0, set()
    for row in rows:
        measured = from_iso(row["measured_at"])
        device = row["device_id"]
        if row["metric"] == "battery":
            battery[device] = row["value"]
            continue
        if row["metric"] not in COVERAGE_METRICS:
            continue
        by_series[(device, row["metric"])].append(measured)
        by_device[device].append((measured, row["fcnt"]))
        meta[(device, row["metric"])] = (row["device_profile"], row["space"], row["spot"])
        if row["message_key"] not in keys:
            keys.add(row["message_key"])
            received = from_iso(row["received_at"])
            delays.append((received - measured).total_seconds())
            if row["gw_time"]:
                if abs((from_iso(row["gw_time"]) - measured).total_seconds()) > 60:
                    skewed += 1
    return by_series, by_device, meta, battery, delays, skewed, len(keys)


def compute_coverage(store, intervals, start, end, now, excluded=()):
    rows = store.measurements_between(to_iso(start), to_iso(end)).fetchall()
    by_series, by_device, meta, battery, delays, skewed, message_count = _parse_rows(rows)

    # 기간 안에 값이 없어도 최근에 보였던 센서·항목은 0%로 보여 준다 (끊김을 숨기지 않기)
    recent = to_iso(end - timedelta(days=RECENT_SERIES_DAYS))
    last_seen = {}
    for key in store.series_keys():
        if key["metric"] not in COVERAGE_METRICS or key["last_measured"] < recent:
            continue
        series_key = (key["device_id"], key["metric"])
        last_seen[series_key] = from_iso(key["last_measured"])
        meta.setdefault(series_key, (key["device_profile"], key["space"], key["spot"]))
        by_series.setdefault(series_key, [])

    result = CoverageResult(start=start, end=end, battery=battery, skewed_gw_count=skewed,
                            message_count=message_count)
    result.delay_median_seconds = statistics.median(delays) if delays else None
    one_min = timedelta(minutes=1)

    for (device, metric), times in sorted(by_series.items(), key=lambda kv: (meta[kv[0]][1], kv[0])):
        profile, space, spot = meta[(device, metric)]
        interval_min = intervals.minutes_for(device, profile)
        interval = timedelta(minutes=interval_min)
        last = max(times) if times else last_seen.get((device, metric))
        series = SeriesCoverage(
            device_id=device, metric=metric, device_profile=profile, space=space, spot=spot,
            interval_min=interval_min, count=len(times), last_measured=last,
            coverage=slot_coverage(times, start, end, interval, excluded),
            coverage_1min=slot_coverage(times, start, end, one_min, excluded),
        )
        if last is not None:
            # 기간이 끝난 뒤 보고서를 만들어도 기간 끝 기준으로 판정한다 (끝난 뒤 흐른 시간은 끊김이 아니다)
            series.age = min(now, end) - last
            series.stale = series.age >= max(STALE_MIN, STALE_INTERVAL_FACTOR * interval)
        else:
            series.stale = True
        result.series.append(series)

    # 결측 구간은 센서 단위로 (같은 메시지의 co2·온도·습도가 같이 빠지므로)
    initial = store.last_event_before(to_iso(start), sorted(UP_KINDS | DOWN_KINDS))
    initial_up = initial is not None and initial["kind"] in UP_KINDS
    events = store.events_between(to_iso(start), to_iso(end))
    down = collector_down_periods(events, start, end, initial_up)
    devices = {device for device, _ in by_series}
    for device in sorted(devices):
        points = sorted(by_device.get(device, []), key=lambda p: p[0])
        profile = next(meta[k][0] for k in meta if k[0] == device)
        interval = timedelta(minutes=intervals.minutes_for(device, profile))
        times = [t for t, _ in points]
        for gap_start, gap_end in find_gaps(times, start, end, interval, excluded):
            before = [f for t, f in points if t < gap_start]
            after = [f for t, f in points if t >= gap_end]
            fcnt_before = before[-1] if before else _fcnt_before(store, device, gap_start)
            fcnt_after = after[0] if after else None
            cause, evidence = attribute_cause(gap_start, gap_end, down, fcnt_before, fcnt_after,
                                              battery.get(device))
            result.gaps.append(Gap(device, gap_start, gap_end, cause, evidence))
    return result


def _fcnt_before(store, device_id, before):
    row = store.conn.execute(
        "SELECT fcnt FROM measurements WHERE device_id = ? AND measured_at < ? AND metric != 'battery' "
        "ORDER BY measured_at DESC LIMIT 1",
        (device_id, to_iso(before)),
    ).fetchone()
    return row["fcnt"] if row else None
