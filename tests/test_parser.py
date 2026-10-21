from datetime import datetime, timezone

import pytest

from collector.config import SourceConfig, load_config
from collector.parser import find_source, parse_message, resolve, topic_matches

RECEIVED = datetime(2026, 10, 21, 0, 31, 30, tzinfo=timezone.utc)
TOPIC = "application/app/device/a0a0a0a0a0000001/event/up"


@pytest.fixture
def source(root):
    return load_config(root / "config" / "collector.toml").sources[0]


def test_resolve_paths():
    data = {"a": {"b": 1}, "rx": [{"t": "x"}, {"t": "y"}, {"u": 1}]}
    assert resolve(data, "a.b") == [1]
    assert resolve(data, "rx[].t") == ["x", "y"]
    assert resolve(data, "a.missing") == []
    assert resolve(data, "rx.t") == []


def test_am103_message_gives_four_rows(source, fixture_bytes):
    result = parse_message(TOPIC, fixture_bytes("chirpstack_up.json"), source, RECEIVED)
    assert not result.rejects
    by_metric = {row.metric: row for row in result.rows}
    assert set(by_metric) == {"co2", "temperature", "humidity", "battery"}
    co2 = by_metric["co2"]
    assert co2.value == 951 and co2.unit == "ppm"
    assert co2.device_id == "a0a0a0a0a0000001" and co2.device_profile == "AM103"
    assert co2.space == "사무실" and co2.spot == "업무 공간 안쪽"
    assert co2.fcnt == 211699
    assert co2.message_key == "00000000-0000-4000-8000-000000000001"
    assert co2.measured_at == "2026-10-21T00:31:23.553Z" and co2.time_source == "ns_time"
    assert co2.received_at == "2026-10-21T00:31:30.000Z"


def test_missing_spot_is_null(source, fixture_bytes):
    result = parse_message(TOPIC, fixture_bytes("chirpstack_up_no_spot.json"), source, RECEIVED)
    assert result.rows and all(row.spot is None for row in result.rows)
    assert {row.metric for row in result.rows} == {"temperature", "humidity", "battery"}


def test_skewed_gateway_uses_earliest_ns_time(source, fixture_bytes):
    result = parse_message(TOPIC, fixture_bytes("chirpstack_up_skewed_gw.json"), source, RECEIVED)
    row = result.rows[0]
    # time(게이트웨이 시계)은 5분 빠르지만 기준 시각은 가장 이른 nsTime
    assert row.measured_at == "2026-10-21T00:31:25.297Z"
    assert row.gw_time == "2026-10-21T00:36:20.731Z"
    # include에 없는 pressure는 저장하지 않는다
    assert "pressure" not in {r.metric for r in result.rows}


def test_zero_and_negative_values_are_kept(source):
    payload = b'{"deviceInfo":{"devEui":"d1","tags":{"location":"L"}},"fCnt":1,"object":{"co2":0,"temperature":-3.5}}'
    result = parse_message(TOPIC, payload, source, RECEIVED)
    assert {(r.metric, r.value) for r in result.rows} == {("co2", 0.0), ("temperature", -3.5)}


def test_missing_ns_time_falls_back_to_received_and_builds_key(source):
    payload = b'{"deviceInfo":{"devEui":"d1","tags":{"location":"L"}},"fCnt":42,"object":{"co2":500}}'
    row = parse_message(TOPIC, payload, source, RECEIVED).rows[0]
    assert row.measured_at == "2026-10-21T00:31:30.000Z" and row.time_source == "received_at"
    assert row.message_key == "d1:42:2026-10-21T00:31"


def test_non_numeric_included_metric_is_rejected(source):
    payload = b'{"deviceInfo":{"devEui":"d1","tags":{"location":"L"}},"object":{"co2":"high","humidity":40}}'
    result = parse_message(TOPIC, payload, source, RECEIVED)
    assert result.rejects == ["non_numeric:co2"]
    assert [r.metric for r in result.rows] == ["humidity"]


def test_bad_json_and_missing_fields(source):
    assert parse_message(TOPIC, b"not json", source, RECEIVED).rejects == ["json_error"]
    no_device = parse_message(TOPIC, b'{"object":{"co2":1}}', source, RECEIVED)
    assert no_device.rejects == ["missing_field:deviceInfo.devEui"]
    no_values = parse_message(TOPIC, b'{"deviceInfo":{"devEui":"d1"}}', source, RECEIVED)
    assert no_values.rejects == ["missing_field:object"]


def test_missing_space_becomes_unknown(source):
    payload = b'{"deviceInfo":{"devEui":"d1"},"object":{"co2":500}}'
    assert parse_message(TOPIC, payload, source, RECEIVED).rows[0].space == "unknown"


def test_rename_and_empty_include():
    src = SourceConfig(name="s", topic="t", fields={"device_id": "id", "space": "room", "values": "v"},
                       include=[], rename={"temp": "temperature"})
    payload = b'{"id":"d","room":"R","v":{"temp":21.5,"note":"text","lux":3}}'
    rows = parse_message("t", payload, src, RECEIVED).rows
    assert {(r.metric, r.value) for r in rows} == {("temperature", 21.5), ("lux", 3.0)}


def test_format_change_handled_by_config_only(root, fixture_bytes):
    """SC-005: 바뀐 형식을 설정 파일만으로 해석한다 (config/examples/format-change.toml)."""
    config = load_config(root / "config" / "examples" / "format-change.toml")
    source = find_source(config.sources, "test/new-format")
    rows = parse_message("test/new-format", fixture_bytes("format_changed.json"), source, RECEIVED).rows
    assert {(r.metric, r.value, r.unit) for r in rows} == {
        ("co2", 812.0, "ppm"), ("temperature", 23.4, "°C"), ("humidity", 58.0, "%"),
    }
    row = rows[0]
    assert row.space == "실습실" and row.spot == "창가" and row.device_profile == "NEW-CO2"
    assert row.fcnt == 7 and row.message_key == "fmt-0001"
    assert row.measured_at == "2026-10-21T00:40:00.000Z"


def test_topic_matching():
    assert topic_matches("application/+/device/+/event/up", "application/a/device/d/event/up")
    assert not topic_matches("application/+/device/+/event/up", "application/a/device/d/event/join")
    assert topic_matches("xflow/#", "xflow/hvacr/idu/1/status")
    assert not topic_matches("a/+", "a/b/c")
