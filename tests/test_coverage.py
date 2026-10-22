from datetime import datetime, timedelta, timezone

import pytest

from collector.config import IntervalConfig
from collector.coverage import (
    attribute_cause, collector_down_periods, compute_coverage, find_gaps, slot_coverage,
)
from collector.parser import MeasurementRow
from collector.store import Store
from collector.timeutil import to_iso

T0 = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


def row(device, metric, t, fcnt, value=1.0, profile="EM300-TH", space="실습실", gw_shift=0):
    return MeasurementRow(
        message_key=f"{device}:{fcnt}", device_id=device, device_profile=profile, space=space, spot="중앙",
        metric=metric, value=value, unit=None, measured_at=to_iso(t), time_source="ns_time",
        gw_time=to_iso(t + timedelta(seconds=gw_shift)), received_at=to_iso(t + timedelta(seconds=2)),
        fcnt=fcnt, source="chirpstack", topic="t",
    )


def test_slot_coverage_by_interval():
    times = [T0 + i * MIN for i in range(0, 60, 2)]           # 2분마다 30건
    assert slot_coverage(times, T0, T0 + 60 * MIN, 2 * MIN) == 1.0
    assert slot_coverage(times, T0, T0 + 60 * MIN, MIN) == 0.5
    ten = [T0 + i * MIN for i in range(0, 60, 10)]
    assert slot_coverage(ten, T0, T0 + 60 * MIN, MIN) == pytest.approx(6 / 60)


def test_excluded_window_removed_from_denominator():
    times = [T0 + i * MIN for i in range(60) if not 10 <= i < 20]
    excluded = [(T0 + 10 * MIN, T0 + 20 * MIN)]
    assert slot_coverage(times, T0, T0 + 60 * MIN, MIN, excluded) == 1.0
    assert find_gaps(times, T0, T0 + 60 * MIN, MIN, excluded) == []


def test_find_gaps_needs_two_empty_slots():
    times = [T0 + i * MIN for i in range(30) if i not in (5, 12, 13, 14)]
    gaps = find_gaps(times, T0, T0 + 30 * MIN, MIN)
    assert gaps == [(T0 + 12 * MIN, T0 + 15 * MIN)]           # 한 칸(5분)짜리는 결측 구간이 아니다


def test_collector_down_periods():
    events = [
        {"at": to_iso(T0 + 5 * MIN), "kind": "disconnect"},
        {"at": to_iso(T0 + 6 * MIN), "kind": "reconnect_attempt"},
        {"at": to_iso(T0 + 9 * MIN), "kind": "connect"},
        {"at": to_iso(T0 + 20 * MIN), "kind": "stop"},
    ]
    down = collector_down_periods(events, T0, T0 + 30 * MIN, initial_up=True)
    assert down == [(T0 + 5 * MIN, T0 + 9 * MIN), (T0 + 20 * MIN, T0 + 30 * MIN)]
    assert collector_down_periods([], T0, T0 + 30 * MIN, initial_up=False) == [(T0, T0 + 30 * MIN)]


@pytest.mark.parametrize("down, before, after, battery, cause", [
    ([(T0, T0 + 5 * MIN)], 10, 15, 80, "collector"),           # 수집기 사건이 가장 먼저
    ([], 10, 15, 80, "upstream"),                              # 번호가 건너뜀 → 전달 중 손실
    ([], 10, 11, 80, "sensor"),                                # 번호가 이어짐 → 센서가 안 보냄
    ([], 10, 11, 1, "sensor_battery"),
    ([], 10, None, 1, "sensor_battery"),
    ([], 10, None, 80, "sensor"),
    ([], 500, 3, 80, "sensor_reset"),
])
def test_attribute_cause(down, before, after, battery, cause):
    assert attribute_cause(T0 + MIN, T0 + 4 * MIN, down, before, after, battery)[0] == cause


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "c.db")


def test_compute_coverage_end_to_end(store):
    start, end = T0, T0 + 60 * MIN
    store.add_event("connect", at=to_iso(T0 - MIN))
    # 센서 A(1분): 20~29분 빠짐, fCnt가 10 건너뜀 → 전달 중 손실
    fcnt = 100
    for i in range(60):
        if 20 <= i < 30:
            fcnt += 1
            continue
        t = T0 + i * MIN + timedelta(seconds=12)
        store.insert_message([row("A", "temperature", t, fcnt, gw_shift=300), row("A", "humidity", t, fcnt)])
        fcnt += 1
    # 센서 B(10분 간격 설정): 빠짐 없음
    for i in range(0, 60, 10):
        store.insert_message([row("B", "co2", T0 + i * MIN, i, profile="AM103", space="회의실")])
    # 센서 C(1분): 40분 이후 수신 없음, 배터리 1%
    for i in range(40):
        t = T0 + i * MIN
        store.insert_message([row("C", "co2", t, i, profile="AM103", space="사무실"),
                              row("C", "battery", t, i, value=1, profile="AM103", space="사무실")])

    intervals = IntervalConfig(default=1, by_device={"B": 10})
    result = compute_coverage(store, intervals, start, end, now=end)
    cov = {(s.device_id, s.metric): s for s in result.series}

    assert set(cov) == {("A", "temperature"), ("A", "humidity"), ("B", "co2"), ("C", "co2")}   # battery는 표에서 제외
    assert cov[("A", "temperature")].coverage == pytest.approx(50 / 60)
    assert cov[("B", "co2")].coverage == 1.0 and cov[("B", "co2")].coverage_1min == pytest.approx(0.1)
    assert cov[("C", "co2")].stale and not cov[("B", "co2")].stale
    assert result.battery["C"] == 1

    gaps = {g.device_id: g for g in result.gaps}
    assert set(gaps) == {"A", "C"}                            # 센서 단위라 A의 두 항목은 한 구간
    assert gaps["A"].cause == "upstream" and gaps["A"].start == T0 + 20 * MIN
    assert gaps["C"].cause == "sensor_battery" and gaps["C"].end == end
    assert result.skewed_gw_count == 50                       # A의 게이트웨이 시각이 5분 어긋남
    assert result.delay_median_seconds == pytest.approx(2.0)


def test_report_after_period_end_is_not_stale(store):
    for i in range(60):
        store.insert_message([row("A", "temperature", T0 + i * MIN, i)])
    result = compute_coverage(store, IntervalConfig(default=1), T0, T0 + 60 * MIN, now=T0 + 66 * MIN)
    series = result.series[0]
    assert series.age == MIN and not series.stale            # 기간 끝(60분) 기준 1분 전 수신


def test_gap_during_collector_outage_is_collector(store):
    store.add_event("connect", at=to_iso(T0 - MIN))
    store.add_event("disconnect", at=to_iso(T0 + 10 * MIN))
    store.add_event("connect", at=to_iso(T0 + 15 * MIN))
    for i in range(30):
        if 10 <= i < 15:
            continue
        store.insert_message([row("A", "co2", T0 + i * MIN, i)])
    result = compute_coverage(store, IntervalConfig(), T0, T0 + 30 * MIN, now=T0 + 30 * MIN)
    assert [g.cause for g in result.gaps] == ["collector"]
