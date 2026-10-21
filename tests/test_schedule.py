"""일정 판정, 반응 시간, 어긋난 날 (research.md R11)."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.schedule import class_day_flags, day_rises, event_times, mismatched_days, response_times
from pipeline.settings import load_schedule

ROOT = Path(__file__).resolve().parent.parent
TZ = "Asia/Seoul"


@pytest.fixture
def schedule():
    return load_schedule(ROOT / "config" / "schedule.toml")


def lab_co2(days, empty_days=(), weekend_use=(), delay_min=10):
    """5분 칸 CO₂: 수업 중 900, 점심 650(1~3명 남음), 밤 450. 시작 후 delay_min 뒤부터 오른다."""
    times = pd.date_range(pd.Timestamp(days[0], tz=TZ), pd.Timestamp(days[-1], tz=TZ) + pd.Timedelta(days=1),
                          freq="5min", inclusive="left")
    values = []
    for t in times:
        minute = t.hour * 60 + t.minute
        working = t.weekday() < 5 and t.date() not in empty_days or t.date() in weekend_use
        if not working:
            values.append(450.0)
        elif 9 * 60 + delay_min <= minute <= 11 * 60 + 30 or 13 * 60 + delay_min <= minute <= 18 * 60:
            values.append(900.0)
        elif 11 * 60 + 30 < minute < 13 * 60 + delay_min:
            values.append(650.0)
        else:
            values.append(450.0)
    return pd.Series(values, index=times.tz_convert("UTC"))


def test_class_days_weekend_and_holiday(schedule):
    lab = schedule.for_space("실습실")
    flags = class_day_flags([date(2026, 3, 9), date(2026, 3, 14), date(2026, 5, 5), date(2026, 3, 2)], lab, schedule)
    assert flags[date(2026, 3, 9)] is True          # 월요일
    assert flags[date(2026, 3, 14)] is False        # 토요일
    assert flags[date(2026, 5, 5)] is False         # 어린이날 (화요일)
    assert flags[date(2026, 3, 2)] is False         # 삼일절 대체공휴일 (월요일)


def test_event_times_in_utc(schedule):
    events = event_times([date(2026, 3, 9), date(2026, 3, 14)], schedule.for_space("실습실"), schedule, TZ)
    assert list(events["kind"]) == ["start", "partial", "start", "end"]       # 토요일은 없음
    assert events["time"].iloc[0] == pd.Timestamp("2026-03-09T00:00Z")         # 09:00 KST


def test_response_time_and_kinds(schedule):
    days = [date(2026, 3, 9) + pd.Timedelta(days=i) for i in range(7)]
    series = lab_co2(days, delay_min=10)
    events = event_times(days, schedule.for_space("실습실"), schedule, TZ)
    result = response_times(series, events, schedule)
    starts = result[result["kind"] == "start"]
    # 칸 시각 09:10(측정 09:05~09:10)에 처음 900 → 10분
    assert (starts["response_min"] == 10).all()
    lunch = result[result["kind"] == "partial"]
    end = result[result["kind"] == "end"]
    assert (lunch["response_min"] == 5).all() and (end["response_min"] == 5).all()
    # 점심(1~3명 남음)은 퇴실보다 덜 내려간다
    assert lunch["change"].mean() == pytest.approx(250) and end["change"].mean() == pytest.approx(450)


def test_no_response_within_window(schedule):
    days = [date(2026, 3, 9)]
    series = lab_co2(days, empty_days={date(2026, 3, 9)})
    events = event_times(days, schedule.for_space("실습실"), schedule, TZ)
    result = response_times(series, events, schedule)
    assert result["response_min"].isna().all()


def test_mismatched_days(schedule):
    days = [date(2026, 3, 9) + pd.Timedelta(days=i) for i in range(14)]
    empty = date(2026, 3, 11)          # 수요일인데 사람이 없음
    saturday_use = date(2026, 3, 21)   # 토요일인데 씀
    series = lab_co2(days, empty_days={empty}, weekend_use={saturday_use})
    rises = day_rises(series, days, schedule.for_space("실습실"), schedule, TZ)
    found = mismatched_days(rises, schedule)
    assert list(zip(found["day"], found["reason"])) == [(empty, "no_people"), (saturday_use, "unscheduled_use")]


def test_low_coverage_day_not_judged(schedule):
    days = [date(2026, 3, 11)]
    series = lab_co2(days, empty_days={date(2026, 3, 11)})
    series[(series.index.hour >= 1)] = np.nan       # 10시(KST) 이후 데이터 없음
    rises = day_rises(series, days, schedule.for_space("실습실"), schedule, TZ)
    assert rises["coverage"].iloc[0] < 0.5
    assert mismatched_days(rises, schedule).empty
