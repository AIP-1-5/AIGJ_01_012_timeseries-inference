"""진단서 초안 docs/reports/002-diagnosis.md (US5).

숫자·표·그래프 링크와 숫자에서 뽑을 수 있는 관찰(자동 제안)을 채우고,
"왜 / 그래서" 해석과 "생각해 볼 점" 답은 사람이 쓰는 칸으로 남긴다.
손으로 쓴 해석이 지워지지 않게, 진단서가 이미 있으면 --force 없이는 덮어쓰지 않는다.
"""

import json

import pandas as pd

from .baselines import BASELINE_LABELS, METRIC_LABELS, UNITS, conditions_block, strongest
from .preprocess import clean_path, load_clean
from .split import PERIOD_LABELS, PERIODS, load_normalization, load_split, normalization_path, split_path

TODO = "> ✍️ **해석 (직접 쓴다)**: 왜 이렇게 나왔고, 그래서 무엇을 해야 하는가."


def pct(value):
    return f"{value:.1%}"


def missing_table(diagnosis, settings):
    stats = pd.DataFrame(diagnosis["quality"]["stats"])
    by_period = pd.DataFrame(diagnosis["quality"]["by_period"])
    lines = ["| 공간 | 항목 | 센서 | 센서 ID 없음 | 범위 밖 제거 | 중복 | 관측 | 보간 | 결측 | 고장 의심 | 시험 기간 관측 |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in stats.itertuples():
        total = max(row.slots_total, 1)
        test = by_period[(by_period["period"] == "test") & (by_period["space"] == row.space) & (by_period["metric"] == row.metric)]
        test_obs = pct(test["observed"].iloc[0]) if len(test) else "-"
        lines.append(f"| {row.space} | {row.metric} | {row.sensors} | {row.untagged_rows:,} | {row.removed_out_of_range:,} | {row.duplicates:,} | "
                     f"{pct(row.slots_observed / total)} | {pct(row.slots_interpolated / total)} | "
                     f"{pct(row.slots_missing / total)} | {pct(row.slots_fault / total)} | {test_obs} |")
    return "\n".join(lines)


def gap_lines(diagnosis, top=3):
    lines = []
    for key, gaps in diagnosis["quality"]["longest_gaps"].items():
        if not gaps or gaps[0]["minutes"] < 24 * 60:
            continue
        space, metric = key.split("|")
        listed = ", ".join(f"{g['start']} ~ {g['end']} ({g['minutes'] / 60 / 24:.1f}일)" for g in gaps[:top])
        lines.append(f"- {space} {metric}: {listed}")
    return lines or ["- 하루 넘게 이어진 빈칸 없음"]


def fault_lines(diagnosis):
    lines = []
    for key, episodes in diagnosis["quality"]["stuck_episodes"].items():
        space, metric = key.split("|")
        longest = episodes[0]
        lines.append(f"- {space} {metric}: 가장 긴 값 고정 {longest['start']} ~ {longest['end']} ({longest['minutes'] / 60:.1f}시간), "
                     f"상위 {len(episodes)}개 구간 기록")
    for space, drift in diagnosis["night_drift"].items():
        lines.append(f"- {space} 밤(02~05시) CO₂ 바닥값: 중앙값 {drift['median_floor']} ppm, "
                     f"{drift['limit']:.0f} ppm 초과 {drift['flagged']}/{drift['nights']}밤 (가장 길게 {drift['longest_streak']}밤 연속)")
    return lines or ["- 고장 의심 없음"]


def response_table(data):
    lines = ["| 일정 | 종류 | 측정한 날 | 반응한 날 | 반응 시간 중앙값 (25~75%) | 변화 폭 중앙값 |",
             "|---|---|---:|---:|---|---:|"]
    kinds = {"start": "들어옴", "partial": "일부 남음", "end": "모두 나감"}
    for row in data:
        span = (f"{row['median_min']:.0f}분 ({row['p25_min']:.0f}~{row['p75_min']:.0f})"
                if row["median_min"] is not None else "반응 없음")
        change = f"{row['median_change']} ppm" if row["median_change"] is not None else "-"
        lines.append(f"| {row['time']} {row['label']} | {kinds.get(row['kind'], row['kind'])} | {row['measured']} | "
                     f"{row['responded']} | {span} | {change} |")
    return "\n".join(lines)


def month_table(data):
    lines = ["| 달 | 일정 있는 날 | 사람 없던 날 | 주말·공휴일 | 일정 없이 쓴 날 | 일정일 상승 폭 중앙값 |",
             "|---|---:|---:|---:|---:|---:|"]
    for row in data:
        rise = f"{row['median_rise_class']} ppm" if row["median_rise_class"] is not None else "-"
        lines.append(f"| {row['month']} | {row['class_days']} | {row['no_people']} | {row['off_days']} | "
                     f"{row['unscheduled_use']} | {rise} |")
    return "\n".join(lines)


def auto_observations(diagnosis, baseline_table, settings):
    """숫자에서 바로 나오는 관찰. 해석이 아니라 해석의 재료다."""
    notes = []
    stats = pd.DataFrame(diagnosis["quality"]["stats"])
    for row in stats.itertuples():
        missing = row.slots_missing / max(row.slots_total, 1)
        if missing >= 0.1:
            notes.append(f"{row.space} {row.metric} 결측 {pct(missing)}: 긴 빈칸 목록에서 언제 끊겼는지 보고 원인(배터리, 설치 전, 게이트웨이)을 확인한다")
        interp = row.slots_interpolated / max(row.slots_total, 1)
        if interp >= 0.3:
            notes.append(f"{row.space} {row.metric} 보간 {pct(interp)}: 10분 간격 센서라 칸 절반이 보간이다. 정답에서 빠져 평가 시점이 적다")
        fault = row.slots_fault / max(row.slots_total, 1)
        if fault >= 0.01:
            notes.append(f"{row.space} {row.metric} 값 고정 의심 {pct(fault)}: 실제 고장인지, 해상도(0.1℃ 등)가 낮아 밤에 값이 안 변하는 것인지 그래프로 확인한다")
    for space, drift in diagnosis["night_drift"].items():
        if drift["flagged_rate"] >= 0.2:
            notes.append(f"{space}: 밤 CO₂ 바닥값이 외기보다 높은 밤이 {pct(drift['flagged_rate'])}다. 환기 부족이거나 센서 영점이 틀어졌을 수 있다")
    for fact in diagnosis["profile_facts"]:
        if fact["metric"] == "co2" and fact["off_day_range"] is not None and fact["day_range"] > 2 * max(fact["off_day_range"], 1):
            hour, minute = int(fact["peak_clock"]), int(round(fact["peak_clock"] % 1 * 60))
            notes.append(f"{fact['space']} CO₂: 일정 있는 날(평일) 하루 변동 {fact['day_range']:.0f} ppm, 없는 날 {fact['off_day_range']:.0f} ppm. "
                         f"정점 {hour:02d}:{minute:02d}경 {fact['peak']:.0f} ppm → 사람 수가 CO₂를 움직인다")
    for space, data in diagnosis["schedule"]["spaces"].items():
        lunch = next((r for r in data["response_matched"] if r["kind"] == "partial"), None)
        end = next((r for r in data["response_matched"] if r["kind"] == "end"), None)
        if lunch and end and lunch["median_change"] is not None and end["median_change"]:
            notes.append(f"{space} 점심 감소 폭 {lunch['median_change']} ppm vs 퇴실 감소 폭 {end['median_change']} ppm "
                         f"(비율 {lunch['median_change'] / end['median_change']:.2f}) → 점심에 남는 인원의 흔적인지 본다")
        no_people = sum(1 for m in data["mismatched"] if m["reason"] == "no_people")
        if no_people:
            notes.append(f"{space}: 일정이 있는데 CO₂가 거의 안 오른 날 {no_people}일 → 달별 표에서 몰린 달(방학, 과정 시작 전)을 찾는다")
    if baseline_table is not None:
        for row in strongest(baseline_table).itertuples():
            notes.append(f"{METRIC_LABELS.get(row.metric, row.metric)} {row.horizon_min}분 뒤: 가장 강한 기준선은 "
                         f"{BASELINE_LABELS[row.baseline]} (MAE {row.mae:.2f} {UNITS.get(row.metric, '')})")
    return notes


def build(settings, schedule, clean, split, normalization, diagnosis, baseline_table, figures):
    space_names = [s.space for s in settings.spaces]
    lines = [
        "# 과제 2 진단서: 데이터 이해와 기준선",
        "",
        "`python -m pipeline report`가 만든 초안이다. 숫자·표·그래프는 자동, ✍️ 칸은 직접 쓴다.",
        "",
        "## 1. 측정 조건",
        "",
        conditions_block(settings, clean, split, {
            "공간": ", ".join(space_names),
            "일정": f"{schedule.source} (실습실만, 회의실·사무실은 일정이 유동적이라 제외)",
            "패턴·일정 분석 기간": "학습+검증 (시험 기간은 보지 않는다)",
        }),
        "",
        "## 2. 정제 규칙과 이유",
        "",
        "| 규칙 | 값 | 이유 |",
        "|---|---|---|",
    ]
    reasons = {"co2": "외기가 약 420ppm이라 300 밑은 불가능, 0은 깨진 값, 5,000 초과는 센서 범위 밖",
               "temperature": "실내 공간, 60℃ 같은 값은 깨진 값", "humidity": "0·100은 센서 오류값으로 자주 나온다"}
    for metric, (lo, hi) in settings.ranges.items():
        lines.append(f"| {metric} 허용 범위 | {lo:g} ~ {hi:g} | {reasons.get(metric, '')} |")
    lines += [
        "| 센서 ID 없는 행 | 뺀다 | 2025-08 초기 데이터는 `device_id` 태그가 없어 센서를 구분할 수 없다 |",
        f"| 중복 | 같은 센서가 {settings.duplicate_seconds:g}초 안에 다시 보낸 기록은 첫 번째만 | InfluxDB가 받은 순간 시각으로 저장해 두 번 도착한 메시지가 1초 미만 차이로 둘 다 남는다. 전송 간격은 최소 1분 |",
        f"| 칸 | {settings.interval_minutes}분, [t−{settings.interval_minutes}분, t) → t | 과제 3이 5분 간격 예측. 시각 t에는 t 이전 값만 들어가 미래가 섞이지 않는다 |",
        "| 공간 값 | 센서들의 중앙값 | 한 대가 고장 나도 크게 흔들리지 않는다 |",
        f"| 짧은 결측 | {settings.max_interp_minutes}분 이하만 선형 보간 | 10분 센서의 메시지 1건 손실까지만 채운다 |",
        "| 보간 값 | 입력에는 쓰되 정답으로는 안 쓴다 | 직선으로 채운 값을 맞히면 성적이 부푼다 |",
        "| 값 고정 | 같은 값이 " + ", ".join(f"{m} {v / 60:g}시간" for m, v in settings.stuck_minutes.items())
        + " 이상 → 고장 의심 | 과제 4의 '값 고정' 고장. 학습·평가 정답에서 뺀다. 기준은 정상인 빈방의 가장 긴 정체(CO₂ 3.3시간, 온도·습도 약 15시간)보다 길게 잡았다. 60분이면 온도·습도 해상도 때문에 정상인 밤이 고장으로 잡힌다 |",
        "",
        "## 3. 결측률과 제거 건수",
        "",
        missing_table(diagnosis, settings),
        "",
        "하루 넘게 이어진 빈칸:",
        "",
        *gap_lines(diagnosis),
        "",
        TODO,
        "",
        "## 4. 기간 분할",
        "",
        "| 기간 | 시작 | 끝 (미포함) | 일수 |",
        "|---|---|---|---:|",
    ]
    for period in PERIODS:
        start, end = split[period]["start"], split[period]["end"]
        lines.append(f"| {PERIOD_LABELS[period]} | {start:%Y-%m-%d} | {end:%Y-%m-%d} | {(end - start).days} |")
    lines += [
        "",
        f"정규화 값은 학습 기간 관측값으로만 계산했다 (`artifacts/normalization.json`, 기간 {normalization['period']['start'][:10]} ~ {normalization['period']['end'][:10]}).",
        "",
        "**왜 무작위로 섞으면 안 되나**: 5분 칸은 이웃끼리 거의 같은 값이다. 무작위로 섞으면 시험 칸의 바로 앞뒤 칸이 학습에 들어가 "
        "모델이 '옆 칸 값을 외워서' 맞히게 되고, 시험 성적이 실제 운영보다 좋게 나온다. 또 미래 기간의 패턴(계절, 일정 변화)이 학습에 섞인다. "
        "운영에서는 항상 과거로 미래를 예측하므로 평가도 시간 순서로 나눈다.",
        "",
        "## 5. 하루 패턴",
        "",
    ]
    for figure in figures:
        if "daily-" in figure and "rise" not in figure:
            lines.append(f"![{figure}]({figure})")
            lines.append("")
    lines += [TODO, "", "## 6. 실습실 일정과 CO₂ 반응", ""]
    for space, data in diagnosis["schedule"]["spaces"].items():
        lines += [
            f"### {space} (일정 있는 날 {data['class_days']}일, 판정 {data['judged_days']}일)",
            "",
            "일정과 맞은 날만 (사람 없던 일정일 제외):",
            "",
            response_table(data["response_matched"]),
            "",
            "모든 일정일:",
            "",
            response_table(data["response_all"]),
            "",
            "![002-response](figures/002-response.png)",
            "",
            "달별 일정 일치:",
            "",
            month_table(data["by_month"]),
            "",
            "![002-daily-rise](figures/002-daily-rise.png)",
            "",
        ]
        mismatched = data["mismatched"]
        if mismatched:
            weekdays = "월화수목금토일"
            listed = ", ".join(f"{m['day']}({weekdays[m['weekday']]}) {'사람 없음' if m['reason'] == 'no_people' else '일정 없이 씀'}"
                               for m in mismatched[:20])
            lines += [f"어긋난 날 {len(mismatched)}일 (처음 20일): {listed}", ""]
    lines += [
        "점심(11:30~13:00)에는 1~3명이 남을 수 있다(사용자 제보). 그래서 점심 감소 폭은 퇴실(18:00)보다 작고, "
        "점심 동안 외기 수준(약 420ppm)까지 내려가지 않는 것이 정상일 수 있다.",
        "",
        TODO,
        "",
        "## 7. 고장 의심 구간과 처리",
        "",
        *fault_lines(diagnosis),
        "",
        "처리: 값 고정 구간은 학습과 평가 정답에서 뺀다(`fault=stuck`). 표시는 남겨 과제 4(이상 탐지)가 쓴다. 밤 바닥값 의심은 표시만 한다.",
        "",
        TODO,
        "",
        "## 8. 기준선",
        "",
        "표 전체는 [002-baselines.md](002-baselines.md). 같은 평가 시점(`artifacts/eval_points.csv`)으로 과제 3·6 모델도 평가한다.",
        "",
    ]
    if baseline_table is not None:
        best = strongest(baseline_table)
        lines += ["| 항목 | h | 가장 강한 기준선 | MAE |", "|---|---:|---|---:|"]
        for row in best.itertuples():
            lines.append(f"| {METRIC_LABELS.get(row.metric, row.metric)} | {row.horizon_min}분 | {BASELINE_LABELS[row.baseline]} | "
                         f"{row.mae:.2f} {UNITS.get(row.metric, '')} |")
        lines.append("")
    lines += [
        "> ✍️ **가장 강한 기준선과 그 이유 (완료 조건 3)**: 어느 기준선이 왜 강한지 숫자로 설명한다.",
        "",
        "## 9. 자동 관찰 (숫자에서 뽑은 해석 재료)",
        "",
        *[f"- {note}" for note in auto_observations(diagnosis, baseline_table, settings)],
        "",
        "## 10. 생각해 볼 점",
        "",
        "> ✍️ **'어제 같은 시각'이 약한/강한 이유와, 강할 만한 공간**:",
        "",
        "> ✍️ **고장 구간을 학습·평가에 넣을지**:",
        "",
        "> ✍️ **점심 1~3명 잔류가 패턴에 보이는지**:",
        "",
    ]
    return "\n".join(lines)


def run(settings, schedule, force=False):
    out = settings.reports_dir / "002-diagnosis.md"
    if out.exists() and not force:
        print(f"{out}가 이미 있다. 손으로 쓴 해석을 지우지 않으려고 멈춘다 (덮어쓰려면 --force)")
        return 1
    clean = load_clean(clean_path(settings))
    split = load_split(split_path(settings))
    normalization = load_normalization(normalization_path(settings))
    diagnosis_path = settings.processed_dir / "diagnosis.json"
    if not diagnosis_path.exists():
        print("diagnosis.json이 없다. 먼저 diagnose를 실행한다")
        return 1
    diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))
    mae_path = settings.processed_dir / "baseline_mae.csv"
    baseline_table = pd.read_csv(mae_path, dtype={"space": str, "metric": str, "baseline": str}) if mae_path.exists() else None
    text = build(settings, schedule, clean, split, normalization, diagnosis, baseline_table, diagnosis.get("figures", []))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"진단서 초안 → {out} (✍️ 칸을 채운다)")
    return 0
