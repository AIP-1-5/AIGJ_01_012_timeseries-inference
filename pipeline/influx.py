"""InfluxDB 월 단위 내보내기 (research.md R1). 표준 라이브러리 urllib만 쓴다 (보드에 패키지 추가 없음).

한 달 × 한 항목씩 조회해서 data/raw/influx/<항목>/<YYYY-MM>.csv.gz로 바로 gzip 저장한다.
다 받은 뒤에만 이름을 바꿔 붙이므로, 중간에 끊긴 달은 다음 실행에서 다시 받는다.
토큰은 요청 헤더에만 쓰고 화면·파일에 남기지 않는다.
"""

import csv
import gzip
import io
import json
import os
import time as clock
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT_COLUMNS = ["time", "device_id", "space", "spot", "metric", "value"]
TAG_COLUMNS = {"device_id": "device_id", "location": "space", "spot": "spot"}
TIMEOUT_SECONDS = 600


class InfluxError(Exception):
    pass


def parse_month(text):
    """"2025-08" 또는 "2025-08-08" → 그 달 1일 0시 UTC."""
    try:
        year, month = (int(part) for part in str(text).split("-")[:2])
        return datetime(year, month, 1, tzinfo=timezone.utc)
    except ValueError:
        raise InfluxError(f"달은 YYYY-MM 형식이어야 한다 (받은 값: {text!r})") from None


def next_month(start):
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def months(first, last):
    """[first, last] 달들의 (시작, 끝) 목록. 끝은 포함하지 않는다."""
    result = []
    current = first
    while current <= last:
        result.append((current, next_month(current)))
        current = next_month(current)
    return result


def rfc3339(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def flux_query(bucket, metric, start, stop):
    return (
        f'from(bucket: "{bucket}")\n'
        f"  |> range(start: {rfc3339(start)}, stop: {rfc3339(stop)})\n"
        f'  |> filter(fn: (r) => r._measurement == "{metric}" and r._field == "value")\n'
        '  |> keep(columns: ["_time", "_value", "device_id", "location", "spot"])\n'
    )


def open_query(env, flux, timeout=TIMEOUT_SECONDS):
    """Flux 조회 요청. 응답은 헤더 있는 CSV(주석 줄 없음). 실패하면 InfluxError (토큰은 메시지에 없음)."""
    body = json.dumps({
        "query": flux,
        "type": "flux",
        "dialect": {"header": True, "annotations": [], "delimiter": ","},
    }).encode()
    request = urllib.request.Request(
        f"{env.url}/api/v2/query?orgID={env.org}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Token {env.token}",
            "Content-Type": "application/json",
            "Accept": "application/csv",
            "Accept-Encoding": "identity",
        },
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", "replace").strip()
        raise InfluxError(f"InfluxDB 응답 {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InfluxError(f"InfluxDB 접속 실패: {exc} (보드 B 유선 경로·{env.url} 확인)") from None


def iter_records(lines):
    """Flux CSV 줄들 → dict. 표(table)마다 열 구성이 바뀌면 머리줄이 다시 나온다."""
    header = None
    for row in csv.reader(lines):
        if not any(cell.strip() for cell in row):
            header = None                 # 빈 줄 = 표 경계
            continue
        if row[0].startswith("#"):
            continue
        if header is None or ("_time" in row and "_value" in row):
            header = row
            continue
        record = dict(zip(header, row))
        if "error" in header and "_value" not in header:
            raise InfluxError(f"InfluxDB 조회 오류: {record.get('error', '')}")
        yield record


def to_row(record, metric):
    return [
        record.get("_time", ""),
        record.get("device_id", ""),
        record.get("location", ""),
        record.get("spot", ""),
        metric,
        record.get("_value", ""),
    ]


def export_month(env, metric, start, stop, out_path):
    """한 달 × 한 항목을 받아 저장. 저장한 행 수와 센서 수."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    rows, devices = 0, set()
    with open_query(env, flux_query(env.bucket, metric, start, stop)) as response, \
            gzip.open(part, "wt", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(OUT_COLUMNS)
        for record in iter_records(io.TextIOWrapper(response, encoding="utf-8", newline="")):
            writer.writerow(to_row(record, metric))
            rows += 1
            devices.add(record.get("device_id", ""))
    os.replace(part, out_path)
    return rows, len(devices)


def export(env, raw_dir, first_month, last_month, metrics, overwrite=False, now=None, log=print):
    """여러 달·여러 항목 내보내기. 실패한 (항목, 달) 목록을 돌려준다."""
    now = now or datetime.now(timezone.utc)
    raw_dir = Path(raw_dir)
    manifest_path = raw_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": {}}
    failures = []
    for metric in metrics:
        for start, stop in months(first_month, last_month):
            if start >= now:
                break
            name = f"{start:%Y-%m}"
            out_path = raw_dir / metric / f"{name}.csv.gz"
            current = stop > now
            if out_path.exists() and not overwrite and not current:
                log(f"  {metric} {name}: 이미 있음, 건너뜀")
                continue
            stop = min(stop, now.replace(second=0, microsecond=0))
            began = clock.monotonic()
            try:
                rows, devices = export_month(env, metric, start, stop, out_path)
            except InfluxError as exc:
                log(f"  {metric} {name}: 실패 — {exc}")
                failures.append((metric, name))
                continue
            seconds = clock.monotonic() - began
            log(f"  {metric} {name}: {rows:,}행, 센서 {devices}대, {seconds:.1f}초{' (이번 달, 진행 중)' if current else ''}")
            manifest["files"][f"{metric}/{name}"] = {
                "rows": rows, "devices": devices, "start": rfc3339(start), "stop": rfc3339(stop),
                "exported_at": rfc3339(now),
            }
    manifest["bucket"] = env.bucket
    manifest["updated_at"] = rfc3339(now)
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return failures


def query_rows(env, metrics, start, stop):
    """짧은 기간 조회 (check-time용). OUT_COLUMNS 순서의 행 목록."""
    rows = []
    for metric in metrics:
        with open_query(env, flux_query(env.bucket, metric, start, stop)) as response:
            for record in iter_records(io.TextIOWrapper(response, encoding="utf-8", newline="")):
                rows.append(to_row(record, metric))
    return rows
