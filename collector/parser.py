"""설정 기반 메시지 해석: MQTT 메시지 1건 → 측정값 행 목록 (research.md R3·R4·R7).

정제는 하지 않는다. 받은 값을 그대로 행으로 만들고, 해석할 수 없는 경우만 실패 사유를 돌려준다.
"""

import json
from dataclasses import dataclass, field

from .timeutil import parse_time, to_iso


@dataclass
class MeasurementRow:
    message_key: str
    device_id: str
    device_profile: str | None
    space: str
    spot: str | None
    metric: str
    value: float
    unit: str | None
    measured_at: str
    time_source: str
    gw_time: str | None
    received_at: str
    fcnt: int | None
    source: str
    topic: str


@dataclass
class ParseResult:
    rows: list = field(default_factory=list)
    rejects: list = field(default_factory=list)   # 실패 사유 문자열 목록

    @property
    def ok(self):
        return bool(self.rows)


def resolve(obj, path):
    """점 경로로 값을 찾는다. 'a.b.c'는 중첩 키, 'a[].b'는 배열의 각 원소에서 b를 모은다.

    항상 리스트를 돌려준다(없으면 빈 리스트).
    """
    current = [obj]
    for token in path.split("."):
        is_array = token.endswith("[]")
        key = token[:-2] if is_array else token
        found = []
        for item in current:
            if not isinstance(item, dict) or key not in item:
                continue
            value = item[key]
            if is_array:
                if isinstance(value, list):
                    found.extend(value)
            else:
                found.append(value)
        current = found
    return [value for value in current if value is not None]


def _first(obj, path):
    if not path:
        return None
    values = resolve(obj, path)
    return values[0] if values else None


def _earliest_time(obj, path):
    if not path:
        return None
    times = [t for t in (parse_time(v) for v in resolve(obj, path)) if t is not None]
    return min(times) if times else None


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_message(topic, payload, source, received_at):
    """payload(bytes 또는 str)를 해석한다. received_at은 aware datetime."""
    result = ParseResult()
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        result.rejects.append("json_error")
        return result
    if not isinstance(data, dict):
        result.rejects.append("json_error")
        return result

    fields = source.fields
    device_id = _first(data, fields["device_id"])
    if device_id is None:
        result.rejects.append(f"missing_field:{fields['device_id']}")
        return result
    values = _first(data, fields["values"])
    if not isinstance(values, dict):
        result.rejects.append(f"missing_field:{fields['values']}")
        return result

    space = _first(data, fields["space"]) or "unknown"
    spot = _first(data, fields.get("spot"))
    device_profile = _first(data, fields.get("device_profile"))
    fcnt = _first(data, fields.get("fcnt"))
    fcnt = int(fcnt) if _is_number(fcnt) else None

    received_iso = to_iso(received_at)
    ns_time = _earliest_time(data, fields.get("ns_time"))
    if ns_time is not None:
        measured_at, time_source = to_iso(ns_time), "ns_time"
    else:
        measured_at, time_source = received_iso, "received_at"
    gw_time = _earliest_time(data, fields.get("gw_time"))
    gw_iso = to_iso(gw_time) if gw_time is not None else None

    message_key = _first(data, fields.get("dedup_key"))
    if not message_key:
        message_key = f"{device_id}:{fcnt}:{measured_at[:16]}"

    include = source.include
    for raw_name, value in values.items():
        metric = source.rename.get(raw_name, raw_name)
        if include and metric not in include:
            continue
        if not _is_number(value):
            if include:
                result.rejects.append(f"non_numeric:{metric}")
            continue
        result.rows.append(MeasurementRow(
            message_key=str(message_key),
            device_id=str(device_id),
            device_profile=str(device_profile) if device_profile is not None else None,
            space=str(space),
            spot=str(spot) if spot is not None else None,
            metric=metric,
            value=float(value),
            unit=source.units.get(metric),
            measured_at=measured_at,
            time_source=time_source,
            gw_time=gw_iso,
            received_at=received_iso,
            fcnt=fcnt,
            source=source.name,
            topic=topic,
        ))
    return result


def topic_matches(pattern, topic):
    """MQTT 토픽 필터 일치 여부 (+는 한 단계, #는 나머지 전부)."""
    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")
    for i, part in enumerate(pattern_parts):
        if part == "#":
            return True
        if i >= len(topic_parts):
            return False
        if part != "+" and part != topic_parts[i]:
            return False
    return len(pattern_parts) == len(topic_parts)


def find_source(sources, topic):
    """토픽에 맞는 첫 번째 소스. 없으면 None."""
    for source in sources:
        if topic_matches(source.topic, topic):
            return source
    return None
