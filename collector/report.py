"""status 표, 수집 현황 보고서(Markdown), 학습용 내보내기 (contracts/cli.md)."""

import csv
import socket
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from .coverage import CAUSE_LABELS, compute_coverage
from .store import EXPORT_COLUMNS, Store
from .timeutil import KST, to_iso, to_kst_str, utcnow

EXIT_OK = 0
EXIT_STALE = 3
PASS_THRESHOLD = 0.95
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---- 공통 ----

def display_width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text, width):
    text = str(text)
    return text + " " * max(0, width - display_width(text))


def table(headers, rows):
    widths = [max(display_width(str(c)) for c in col) for col in zip(headers, *rows)]
    lines = ["  ".join(pad(h, w) for h, w in zip(headers, widths))]
    lines += ["  ".join(pad(c, w) for c, w in zip(row, widths)) for row in rows]
    return "\n".join(line.rstrip() for line in lines)


def fmt_age(delta):
    if delta is None:
        return "-"
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def fmt_pct(value):
    return f"{value * 100:.1f}%"


def fmt_interval(minutes):
    return f"{minutes:g}분"


def parse_kst(text):
    """'2026-10-21' 또는 '2026-10-21T09:00'(한국 시간)을 aware datetime으로."""
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"시각 형식이 틀렸다: {text} (예: 2026-10-21T09:00)") from exc
    return value.replace(tzinfo=KST) if value.tzinfo is None else value


def parse_excluded(items):
    windows = []
    for item in items or []:
        if "~" not in item:
            raise SystemExit(f"--exclude 형식은 '시작~끝'이다: {item}")
        a, b = item.split("~", 1)
        windows.append((parse_kst(a.strip()), parse_kst(b.strip())))
    return windows


def collector_state(store):
    last = store.last_event(["start", "connect", "disconnect", "stop", "reconnect_attempt"])
    if last is None:
        return "기록 없음 (수집기를 실행한 적 없음)"
    labels = {"connect": "실행 중 (연결됨)", "start": "시작 중 (접속 대기)", "disconnect": "끊김 (재접속 시도 중)",
              "reconnect_attempt": "끊김 (재접속 시도 중)", "stop": "정지됨"}
    when = to_kst_str(datetime.fromisoformat(last["at"].replace("Z", "+00:00")), "%m-%d %H:%M:%S")
    return f"{labels[last['kind']]} · 마지막 사건 {last['kind']} {when}"


def board_conditions(config):
    host = socket.gethostname()
    try:
        out = subprocess.run(["nvpmodel", "-q"], capture_output=True, text=True, timeout=5).stdout
        power = next((line.split(":", 1)[1].strip() for line in out.splitlines() if "Power Mode" in line), "확인 불가")
    except (OSError, subprocess.SubprocessError):
        power = "확인 불가"
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                timeout=5, cwd=REPO_ROOT).stdout.strip() or "확인 불가"
    except (OSError, subprocess.SubprocessError):
        commit = "확인 불가"
    return host, power, commit


def _series_row(s, battery):
    flag = "← 끊김" if s.stale else ""
    last = to_kst_str(s.last_measured, "%m-%d %H:%M:%S") if s.last_measured else "-"
    bat = battery.get(s.device_id)
    return [s.space, s.spot or "-", s.device_profile or "-", s.metric, s.count, last, fmt_age(s.age),
            fmt_interval(s.interval_min), fmt_pct(s.coverage), fmt_pct(s.coverage_1min),
            f"{bat:g}%" if bat is not None else "-", flag]


SERIES_HEADERS = ["공간", "위치", "센서", "항목", "건수", "마지막 수신", "경과", "간격", "커버율(간격)", "커버율(1분)", "배터리", ""]


# ---- status ----

def status(config, hours=24, space=None, now=None):
    store = Store(config.storage_path, readonly=True)
    now = now or utcnow()
    start = now - timedelta(hours=hours)
    result = compute_coverage(store, config.intervals, start, now, now)
    series = [s for s in result.series if not space or s.space == space]
    start_iso, end_iso = to_iso(start), to_iso(now)
    today_start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    duplicates = sum(r["n"] for r in store.duplicates_between(to_iso(today_start), end_iso))
    rejects = sum(r["n"] for r in store.rejects_between(start_iso, end_iso))

    print(f"기준: 최근 {hours:g}시간 ({to_kst_str(start, '%Y-%m-%d %H:%M')} ~ {to_kst_str(now, '%Y-%m-%d %H:%M')} KST)"
          f" · 보드 {socket.gethostname()} · 실데이터")
    print(f"수집기: {collector_state(store)} · 오늘 중복 {duplicates}건 · 실패 {rejects}건 (최근 {hours:g}시간)")
    print()
    if not series:
        print("저장된 측정값이 없다.")
        return EXIT_STALE
    print(table(SERIES_HEADERS, [_series_row(s, result.battery) for s in series]))
    stale = [s for s in series if s.stale]
    if stale:
        print(f"\n끊긴 센서·항목 {len(stale)}개")
        return EXIT_STALE
    return EXIT_OK


# ---- report ----

def build_report(config, store, start, end, now, excluded=()):
    result = compute_coverage(store, config.intervals, start, end, now, excluded)
    host, power, commit = board_conditions(config)
    hours = (end - start).total_seconds() / 3600
    excluded_text = ", ".join(
        to_kst_str(a, "%m-%d %H:%M") + "~" + to_kst_str(b, "%H:%M") for a, b in excluded
    ) or "없음"
    lines = ["# 과제 1 수집 현황 보고", ""]

    lines += ["## 측정 조건", "", "| 항목 | 값 |", "|---|---|",
              f"| 기간 | {to_kst_str(start, '%Y-%m-%d %H:%M')} ~ {to_kst_str(end, '%Y-%m-%d %H:%M')} KST ({hours:.1f}시간) |",
              f"| 데이터 | 실데이터 (MQTT `{config.broker.host}` 수집) |",
              f"| 보드 | `{host}`, 전력 모드 {power} |",
              f"| 제외한 시험 시간대 | {excluded_text} |",
              f"| 설정 / 코드 | `{config.path}`, commit `{commit}` |",
              f"| 작성 | {to_kst_str(now)} KST |", ""]

    passed = result.passed(PASS_THRESHOLD)
    worst = min(result.series, key=lambda s: s.coverage, default=None)
    explained = [g for g in result.gaps if g.evidence]
    verdict = "통과" if result.series and len(passed) == len(result.series) else "미통과"
    lines += ["## 확인 기준 판정", "",
              f"- **24시간 커버율 95% 이상**: {verdict} — 센서·항목 {len(result.series)}개 중 {len(passed)}개 통과"
              + (f", 최저 {fmt_pct(worst.coverage)} ({worst.space} {worst.device_profile} {worst.metric})" if worst else ""),
              f"- **빠진 구간 원인 설명**: 결측 구간 {len(result.gaps)}개 중 {len(explained)}개에 추정 원인과 근거 있음", ""]

    by_space = {}
    for s in result.series:
        by_space.setdefault(s.space, []).append(s.coverage)
    lines += ["## 공간별 요약", "", "| 공간 | 센서·항목 수 | 평균 커버율 | 최저 커버율 |", "|---|---|---|---|"]
    lines += [f"| {sp} | {len(v)} | {fmt_pct(sum(v) / len(v))} | {fmt_pct(min(v))} |" for sp, v in by_space.items()]
    lines += [""]

    lines += ["## 센서·항목별 커버율", "", "커버율(간격)은 센서의 기대 전송 간격 기준, 커버율(1분)은 과제 정의(1분 칸) 기준이다.", "",
              "| " + " | ".join(h or "표시" for h in SERIES_HEADERS) + " |", "|" + "---|" * len(SERIES_HEADERS)]
    lines += ["| " + " | ".join(str(c) for c in _series_row(s, result.battery)) + " |" for s in result.series]
    lines += [""]

    lines += ["## 결측 구간", ""]
    if result.gaps:
        device_meta = {s.device_id: s for s in result.series}
        lines += ["| 공간 | 센서 | 장비 ID | 시작 | 끝 | 길이 | 추정 원인 | 근거 |", "|---|---|---|---|---|---|---|---|"]
        for g in result.gaps:
            m = device_meta.get(g.device_id)
            lines.append(f"| {m.space if m else '-'} | {m.device_profile if m else '-'} | `{g.device_id}` | "
                         f"{to_kst_str(g.start, '%m-%d %H:%M')} | {to_kst_str(g.end, '%m-%d %H:%M')} | "
                         f"{fmt_age(g.duration)} | {CAUSE_LABELS[g.cause]} | {g.evidence} |")
    else:
        lines.append("결측 구간 없음.")
    lines += [""]

    events = store.events_between(to_iso(start), to_iso(end))
    counts = {}
    for e in events:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    lines += ["## 수집기 사건", "", ", ".join(f"{k} {v}건" for k, v in sorted(counts.items())) or "사건 없음", ""]
    notable = [e for e in events if e["kind"] in ("disconnect", "stop", "start", "stale_reconnect", "store_error")]
    if notable:
        lines += ["| 시각 | 종류 | 내용 |", "|---|---|---|"]
        lines += [f"| {to_kst_str(datetime.fromisoformat(e['at'].replace('Z', '+00:00')), '%m-%d %H:%M:%S')} | "
                  f"{e['kind']} | {e['detail'] or ''} |" for e in notable[:50]]
        lines += [""]

    dups = store.duplicates_between(to_iso(start), to_iso(end))
    rejects = store.rejects_between(to_iso(start), to_iso(end))
    lines += ["## 중복·해석 실패", "",
              f"- 걸러낸 중복 메시지: {sum(r['n'] for r in dups)}건"
              + (" (많은 순: " + ", ".join(f"`{r['device_id']}` {r['n']}" for r in dups[:5]) + ")" if dups else ""),
              f"- 해석 실패: {sum(r['n'] for r in rejects)}건"
              + (" (" + ", ".join(f"{r['reason']} {r['n']}" for r in rejects) + ")" if rejects else ""), ""]

    delay = result.delay_median_seconds
    lines += ["## 시계 점검", "",
              f"- 보드 수신 시각 − 기준 시각(nsTime) 중앙값: {f'{delay:.1f}초' if delay is not None else '데이터 없음'}",
              f"- 게이트웨이 시각이 기준 시각과 60초 넘게 다른 메시지: {result.skewed_gw_count}건 / {result.message_count}건", ""]

    lines += ["## 해석 (직접 작성)", "",
              "- 왜 이렇게 나왔나: ",
              "- 무엇을 해야 하나: ", ""]
    return "\n".join(lines), result


def write_report(config, hours=24, start=None, end=None, out=None, exclude=None, now=None):
    store = Store(config.storage_path, readonly=True)
    now = now or utcnow()
    end_dt = parse_kst(end) if end else now
    start_dt = parse_kst(start) if start else end_dt - timedelta(hours=hours)
    text, result = build_report(config, store, start_dt, end_dt, now, parse_excluded(exclude))
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    passed = len(result.passed(PASS_THRESHOLD))
    print(f"보고서 작성: {out_path} (커버율 95% 이상 {passed}/{len(result.series)}, 결측 구간 {len(result.gaps)}개)")
    return EXIT_OK


# ---- export ----

def export(config, start, end, metrics=None, out="data/export.csv"):
    store = Store(config.storage_path, readonly=True)
    start_dt, end_dt = parse_kst(start), parse_kst(end)
    out_path = Path(out)
    if "data" not in out_path.resolve().parts:
        print(f"경고: {out_path}는 data/ 밖이다. 데이터 파일을 커밋하지 않게 주의한다", file=sys.stderr)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(EXPORT_COLUMNS)
        for row in store.export_rows(to_iso(start_dt), to_iso(end_dt), metrics):
            writer.writerow(tuple(row))
            count += 1
    print(f"내보내기: {out_path} ({count}행, {to_kst_str(start_dt, '%Y-%m-%d %H:%M')} ~ {to_kst_str(end_dt, '%Y-%m-%d %H:%M')} KST)")
    return EXIT_OK
