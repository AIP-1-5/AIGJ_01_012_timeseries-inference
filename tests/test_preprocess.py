"""정제·칸·공간 중앙값·보간·고장 표시 (research.md R4·R5·R6·R9)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.loader import normalize_raw
from pipeline.preprocess import bin_label, fill_short_gaps, load_clean, mark_stuck, preprocess, save_clean
from pipeline.settings import load_settings

ROOT = Path(__file__).resolve().parent.parent
T0 = pd.Timestamp("2026-03-02T00:00:00Z")   # 월요일 09:00 KST


@pytest.fixture
def settings():
    return load_settings(ROOT / "config" / "preprocess.toml")


def raw(rows):
    return normalize_raw(pd.DataFrame(rows, columns=["time", "device_id", "space", "spot", "metric", "value"]))


def minutes(m):
    return (T0 + pd.Timedelta(minutes=m)).isoformat()


def series(clean, space, metric):
    return clean[(clean["space"] == space) & (clean["metric"] == metric)].set_index("time")


def test_bin_label_right_edge():
    times = pd.Series(pd.to_datetime(["2026-03-02T00:04:59Z", "2026-03-02T00:05:00Z", "2026-03-02T00:00:00Z"]))
    labels = bin_label(times, 5)
    # [00:00, 00:05) → 00:05. 정각 00:05:00은 다음 칸 00:10
    assert [t.strftime("%H:%M") for t in labels] == ["00:05", "00:10", "00:05"]


def test_range_filter_counts_and_median(settings):
    rows = []
    for m in range(0, 30):
        rows.append([minutes(m), "a", "실습실", "", "co2", 600])
        rows.append([minutes(m), "b", "실습실", "", "co2", 700])
        rows.append([minutes(m), "c", "실습실", "", "co2", 1000 + m])   # 값이 움직여 고정으로 잡히지 않게
    rows.append([minutes(3), "a", "실습실", "", "co2", 0])      # 깨진 값 (같은 시각 중복이기도 함)
    rows.append([minutes(4.5), "b", "실습실", "", "co2", 9999])
    clean, stats, _ = preprocess({"co2": raw(rows)}, settings)
    lab = series(clean, "실습실", "co2")
    first = lab.loc[T0 + pd.Timedelta(minutes=5)]
    assert first["value"] == 700            # 600, 700, 1000~1004 평균 → 중앙값 700
    assert first["n_sensors"] == 3 and first["n_raw"] == 15
    row = stats.set_index(["space", "metric"]).loc[("실습실", "co2")]
    assert row["removed_out_of_range"] == 2     # 0과 9999. 깨진 값을 먼저 지워서 같은 시각의 정상 값 600이 남는다
    assert row["duplicates"] == 0
    assert row["sensors"] == 3


def test_excluded_device_not_used(settings):
    rows = [[minutes(m), "24e124136d151523", "실습실", "복도", "temperature", 30.0 + m * 0.01] for m in range(10)]
    rows += [[minutes(m), "in-room", "실습실", "", "temperature", 22.0 + m * 0.01] for m in range(10)]
    clean, stats, _ = preprocess({"temperature": raw(rows)}, settings)
    lab = series(clean, "실습실", "temperature")
    assert lab["value"].max() < 23
    assert stats.set_index(["space", "metric"]).loc[("실습실", "temperature"), "excluded_device_rows"] == 10


def test_short_gaps_interpolated_long_gaps_left(settings):
    # 1분 센서: 0~19분 있음, 20~34분 없음(15분 = 칸 3개 빔), 35~59분 있음, 60~84분 없음(칸 5개 빔), 85~99분 있음
    present = [m for m in range(100) if not (20 <= m < 35 or 60 <= m < 85)]
    rows = [[minutes(m), "a", "회의실", "", "co2", 500 + m] for m in present]
    clean, _, _ = preprocess({"co2": raw(rows)}, settings)
    room = series(clean, "회의실", "co2")
    short = room.loc[T0 + pd.Timedelta(minutes=25):T0 + pd.Timedelta(minutes=35)]
    assert short["interpolated"].all() and short["value"].notna().all()
    # 직선 보간: 20분 칸(15~19분 평균 517)과 40분 칸(35~39분 평균 537) 사이를 고르게
    assert room.loc[T0 + pd.Timedelta(minutes=30), "value"] == pytest.approx(527)
    long = room.loc[T0 + pd.Timedelta(minutes=65):T0 + pd.Timedelta(minutes=85)]
    assert long["value"].isna().all() and not long["interpolated"].any()


def test_ten_minute_sensor_every_other_slot_filled(settings):
    rows = [[minutes(m), "slow", "회의실", "", "co2", 500 + m] for m in range(0, 120, 10)]
    clean, stats, _ = preprocess({"co2": raw(rows)}, settings)
    room = series(clean, "회의실", "co2")
    assert room["value"].notna().all()
    assert room["interpolated"].sum() == len(room) // 2
    row = stats.set_index(["space", "metric"]).loc[("회의실", "co2")]
    assert row["slots_interpolated"] == room["interpolated"].sum()


def test_stuck_value_marked(settings):
    settings.stuck_minutes["co2"] = 60          # 시험용으로 짧게 (실제 설정은 6시간)
    rows = [[minutes(m), "a", "사무실", "", "co2", 650] for m in range(0, 75)]          # 74분 동안 같은 값
    rows += [[minutes(m), "a", "사무실", "", "co2", 650 + (m % 7)] for m in range(75, 120)]
    clean, stats, _ = preprocess({"co2": raw(rows)}, settings)
    office = series(clean, "사무실", "co2")
    assert (office.loc[T0 + pd.Timedelta(minutes=5):T0 + pd.Timedelta(minutes=75), "fault"] == "stuck").all()
    assert (office.loc[T0 + pd.Timedelta(minutes=85):, "fault"] == "").all()
    assert stats.set_index(["space", "metric"]).loc[("사무실", "co2"), "slots_fault"] > 0


def test_stuck_sensor_dropped_from_median_when_others_ok(settings):
    settings.stuck_minutes["co2"] = 60          # 시험용으로 짧게 (실제 설정은 6시간)
    rows = []
    for m in range(0, 90):
        rows.append([minutes(m), "stuck", "실습실", "", "co2", 450])
        rows.append([minutes(m), "b", "실습실", "", "co2", 800 + m % 5])
        rows.append([minutes(m), "c", "실습실", "", "co2", 900 + m % 5])
    clean, _, _ = preprocess({"co2": raw(rows)}, settings)
    lab = series(clean, "실습실", "co2")
    assert (lab["fault"] == "").all()
    assert (lab["n_sensors"] == 2).all()
    assert lab["value"].min() > 800


def test_mark_stuck_breaks_on_gap():
    times = [T0 + pd.Timedelta(minutes=m) for m in list(range(0, 40)) + list(range(80, 120))]
    frame = pd.DataFrame({"device_id": "a", "time": times, "value": 500.0})
    assert not mark_stuck(frame, 60, 15).any()      # 40분 + 40분, 사이 40분 끊김 → 이어 보지 않음


def test_fill_short_gaps_not_next_to_fault():
    values = pd.Series([1.0, np.nan, 3.0, np.nan, 5.0])
    fault = pd.Series(["", "", "stuck", "", ""])
    filled, mask = fill_short_gaps(values, fault, 3)
    assert not mask.any() and filled.isna().sum() == 2


def test_same_input_same_output(settings, tmp_path):
    rows = [[minutes(m), d, "실습실", "", "co2", 600 + (m * 7 + i * 13) % 50] for m in range(200) for i, d in enumerate("abc")]
    rows += [[minutes(m), "t", "실습실", "", "temperature", 22 + (m % 9) * 0.1] for m in range(200)]
    first = preprocess({"co2": raw(rows), "temperature": raw(rows)}, settings)
    second = preprocess({"co2": raw(list(reversed(rows))), "temperature": raw(rows)}, settings)
    pd.testing.assert_frame_equal(first[0], second[0])
    pd.testing.assert_frame_equal(first[1], second[1])
    save_clean(first[0], tmp_path / "c.csv.gz")
    loaded = load_clean(tmp_path / "c.csv.gz")
    pd.testing.assert_series_equal(loaded["interpolated"], first[0]["interpolated"])
    assert (loaded["time"] == first[0]["time"]).all()
    assert np.allclose(loaded["value"].fillna(-1), first[0]["value"].fillna(-1), atol=1e-4)


def test_near_duplicates_removed(settings):
    # 같은 메시지가 0.3초 차이로 두 번 저장됨 (InfluxDB 실제 사례). 정상 간격(60초)은 그대로
    rows = []
    for m in range(0, 20):
        rows.append([minutes(m), "a", "사무실", "", "co2", 600 + m])
        if m % 2 == 0:
            rows.append([(T0 + pd.Timedelta(minutes=m, seconds=0.3)).isoformat(), "a", "사무실", "", "co2", 600 + m])
    clean, stats, _ = preprocess({"co2": raw(rows)}, settings)
    row = stats.set_index(["space", "metric"]).loc[("사무실", "co2")]
    assert row["duplicates"] == 10
    office = series(clean, "사무실", "co2")
    assert office.loc[T0 + pd.Timedelta(minutes=5), "n_raw"] == 5     # 중복이 칸 개수를 부풀리지 않음


def test_untagged_rows_excluded(settings):
    rows = [[minutes(m), "", "실습실", "", "co2", 2000] for m in range(10)]          # 센서 ID 없는 초기 데이터
    rows += [[minutes(m), "a", "실습실", "", "co2", 600 + m] for m in range(10)]
    clean, stats, _ = preprocess({"co2": raw(rows)}, settings)
    row = stats.set_index(["space", "metric"]).loc[("실습실", "co2")]
    assert row["untagged_rows"] == 10 and row["raw_rows"] == 20 and row["sensors"] == 1
    assert series(clean, "실습실", "co2")["value"].max() < 700


def test_ten_minute_sensor_does_not_make_median_jump(settings):
    # 1분 센서 600, 10분 센서 800. 이어 쓰기가 없으면 칸마다 700 / 600이 번갈아 나온다
    rows = [[minutes(m), "fast", "실습실", "", "co2", 600 + (m % 3)] for m in range(0, 120)]
    rows += [[minutes(m), "slow", "실습실", "", "co2", 800 + (m % 7)] for m in range(0, 120, 10)]
    clean, _, _ = preprocess({"co2": raw(rows)}, settings)
    lab = series(clean, "실습실", "co2")
    assert lab["value"].between(695, 710).all()
    assert (lab["n_sensors"] == 2).all()
    assert not lab["interpolated"].any()


def test_held_value_alone_does_not_make_slot(settings):
    # 10분 센서 하나뿐이면 이어 쓴 값만 있는 칸은 결측 → 보간 표시 (정답으로 쓰지 않음)
    rows = [[minutes(m), "slow", "회의실", "", "co2", 500 + m] for m in range(0, 60, 10)]
    clean, _, _ = preprocess({"co2": raw(rows)}, settings)
    room = series(clean, "회의실", "co2")
    assert room["interpolated"].sum() == len(room) // 2
