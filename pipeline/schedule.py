"""일정 정보: 일정 있는 날 판정, 일정 변화 시각, CO₂ 반응 시간, 어긋난 날 (research.md R11).

일정은 사용자가 알려 준 추정이다. 어긋난 날 목록이 곧 추정의 한계(방학, 휴강, 과정 시작 전)를 보여 준다.
반응 시간은 5분 칸 위에서 재므로 5분 단위로 나온다.
"""

from datetime import datetime, timedelta

import holidays as holiday_lib
import pandas as pd


def holiday_dates(country, years):
    return set(holiday_lib.country_holidays(country, years=sorted(years)).keys())


def class_day_flags(days, space_schedule, schedule):
    """날짜(date 목록)마다 일정 있는 날이면 True: 일정 요일이고 공휴일이 아님."""
    days = list(days)
    off = holiday_dates(schedule.holidays, {d.year for d in days}) if days else set()
    return {d: d.weekday() in space_schedule.weekdays and d not in off for d in days}


def event_times(days, space_schedule, schedule, tz):
    """일정 있는 날의 일정 변화 시각 표: time(UTC), day, kind, label."""
    flags = class_day_flags(days, space_schedule, schedule)
    rows = []
    for day, is_class in sorted(flags.items()):
        if not is_class:
            continue
        for event in space_schedule.events:
            at = pd.Timestamp(datetime.combine(day, event.time), tz=tz).tz_convert("UTC")
            rows.append({"time": at, "day": day, "kind": event.kind, "label": event.label, "clock": event.time})
    return pd.DataFrame(rows, columns=["time", "day", "kind", "label", "clock"])


def response_times(series, events, schedule, interval_minutes=5):
    """일정 변화마다 CO₂ 반응 시간(분).

    기준 = 변화 직전 baseline_minutes 동안의 평균 (칸 시각 t−30분 초과 ~ t 이하 → t 이전 측정값).
    start는 기준보다 threshold 이상 오른 첫 칸, partial·end는 threshold 이상 내린 첫 칸.
    window 안에 없으면 반응 없음(NaN). 변화 폭 = 창 안의 최댓값−기준 (start) 또는 기준−최솟값 (partial·end).
    """
    series = series.dropna().sort_index()
    base_window = pd.Timedelta(minutes=schedule.baseline_minutes)
    window = pd.Timedelta(minutes=schedule.window_minutes)
    min_before = max(1, schedule.baseline_minutes // interval_minutes // 2)   # 기준 칸의 절반 이상
    rows = []
    for event in events.itertuples(index=False):
        before = series[(series.index > event.time - base_window) & (series.index <= event.time)]
        after = series[(series.index > event.time) & (series.index <= event.time + window)]
        row = {"time": event.time, "day": event.day, "kind": event.kind, "label": event.label,
               "baseline": float("nan"), "response_min": float("nan"), "change": float("nan")}
        if len(before) >= min_before and len(after) >= 2:
            baseline = before.mean()
            delta = (after - baseline) if event.kind == "start" else (baseline - after)
            crossed = delta[delta >= schedule.threshold_ppm]
            row["baseline"] = float(baseline)
            row["change"] = float(delta.max())
            if not crossed.empty:
                row["response_min"] = (crossed.index[0] - event.time).total_seconds() / 60
        rows.append(row)
    return pd.DataFrame(rows, columns=["time", "day", "kind", "label", "baseline", "response_min", "change"])


def day_rises(series, days, space_schedule, schedule, tz, interval_minutes=5):
    """날마다 활동 시간 CO₂ 상승 폭 = 활동 시간 최댓값 − 아침 기준(활동 전 평균)."""
    flags = class_day_flags(days, space_schedule, schedule)
    local = series.dropna()
    local.index = local.index.tz_convert(tz)
    (m0, m1), (a0, a1) = schedule.morning_baseline, schedule.active_hours
    expected = (datetime.combine(datetime.min, a1) - datetime.combine(datetime.min, a0)) / timedelta(minutes=interval_minutes)
    rows = []
    for day, is_class in sorted(flags.items()):
        start = pd.Timestamp(datetime.combine(day, m0), tz=tz)
        morning = local[(local.index > start) & (local.index <= pd.Timestamp(datetime.combine(day, m1), tz=tz))]
        active = local[(local.index > pd.Timestamp(datetime.combine(day, a0), tz=tz))
                       & (local.index <= pd.Timestamp(datetime.combine(day, a1), tz=tz))]
        coverage = len(active) / expected
        rise = float(active.max() - morning.mean()) if len(active) and len(morning) else float("nan")
        rows.append({"day": day, "class_day": is_class, "weekday": day.weekday(), "coverage": coverage,
                     "morning": float(morning.mean()) if len(morning) else float("nan"), "rise": rise})
    return pd.DataFrame(rows, columns=["day", "class_day", "weekday", "coverage", "morning", "rise"])


def mismatched_days(rises, schedule):
    """일정과 어긋난 날 후보.

    no_people: 일정 있는 날인데 상승 < min_rise_ppm (휴강, 방학, 과정 시작 전 등)
    unscheduled_use: 일정 없는 날(주말·공휴일)인데 상승 ≥ min_rise_ppm (보강, 자습 등)
    데이터가 min_coverage 미만인 날은 판정하지 않는다.
    """
    enough = rises["coverage"] >= schedule.min_coverage
    no_people = rises[enough & rises["class_day"] & (rises["rise"] < schedule.min_rise_ppm)]
    unscheduled = rises[enough & ~rises["class_day"] & (rises["rise"] >= schedule.min_rise_ppm)]
    return pd.concat([no_people.assign(reason="no_people"), unscheduled.assign(reason="unscheduled_use")]) \
        .sort_values("day").reset_index(drop=True)
