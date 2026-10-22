"""대시보드 센서 실측값 (/v1/sensors)."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

from collector.config import IntervalConfig
from collector.parser import MeasurementRow
from collector.store import Store
from collector.timeutil import to_iso
from service.__main__ import State, make_handler
from service.sensors import SensorReader, read_sensors

NOW = datetime(2026, 10, 22, 6, 0, 30, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


def row(device, profile, space, metric, value, t, spot="중앙", key=None):
    return MeasurementRow(
        message_key=key or f"{device}:{to_iso(t)}", device_id=device, device_profile=profile, space=space, spot=spot,
        metric=metric, value=value, unit=None, measured_at=to_iso(t), time_source="ns_time", gw_time=None,
        received_at=to_iso(t), fcnt=None, source="chirpstack", topic="t",
    )


def make_db(tmp_path):
    store = Store(tmp_path / "collector.db")
    for i in range(180):                                   # 실습실 AM107: 3시간 동안 1분마다, TVOC·기압도
        t = NOW - (180 - i) * MIN
        store.insert_message([row("lab-1", "AM107", "실습실", "co2", 500.0 + i, t),
                              row("lab-1", "AM107", "실습실", "temperature", 22.0, t),
                              row("lab-1", "AM107", "실습실", "tvoc", 120.0, t),
                              row("lab-1", "AM107", "실습실", "pressure", 1008.5, t)])
    for i in range(150):                                   # 사무실 AM103: 30분 전에 끊김, 배터리 1%
        t = NOW - (180 - i) * MIN
        store.insert_message([row("off-1", "AM103", "사무실", "co2", 600.0, t),
                              row("off-1", "AM103", "사무실", "battery", 1.0, t)])
    door = NOW - timedelta(hours=5)                        # 문 열림 센서: 사건형, 5시간 전 한 번
    store.insert_message([row("door-1", "WS301", "사무실", "battery", 90.0, door)])
    store.insert_message([row("hall-1", "EM300-TH", "복도", "humidity", 40.0, NOW - 2 * MIN)])
    store.close()
    return tmp_path / "collector.db"


def test_read_sensors_groups_latest_and_history(tmp_path):
    db = make_db(tmp_path)
    data = read_sensors(db, NOW, IntervalConfig(default=1), [("실습실", "lab"), ("사무실", "office")],
                        history_minutes=60, event_profiles=("WS301",))
    assert [g["space"] for g in data["spaces"]] == ["실습실", "사무실", "복도"]      # 설정 순서, 나머지는 뒤에
    lab = data["spaces"][0]
    assert lab["slug"] == "lab" and lab["total"] == 1 and lab["stale"] == 0
    s = lab["sensors"][0]
    assert s["metrics"]["co2"]["value"] == 679.0 and s["metrics"]["tvoc"]["value"] == 120.0
    assert "pressure" in s["metrics"] and s["stale"] is False and s["kind"] == "periodic"
    co2 = s["history"]["co2"]
    filled = [v for v in co2 if v is not None]                  # 지금 막 시작한 5분 칸은 아직 비어 있을 수 있다
    assert len(co2) == data["history"]["buckets"] and len(filled) >= 12 and filled[0] < filled[-1]   # 오르는 중
    office = {d["device_id"]: d for d in data["spaces"][1]["sensors"]}
    assert office["off-1"]["stale"] is True and office["off-1"]["metrics"]["battery"]["value"] == 1.0
    assert office["door-1"]["kind"] == "event" and office["door-1"]["stale"] is None      # 사건형은 끊김 판정 안 함
    assert data["spaces"][1]["stale"] == 1


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_sensors_route_works_before_first_cycle(tmp_path):
    db = make_db(tmp_path)
    reader = SensorReader(db, IntervalConfig(default=1), [("실습실", "lab")], {"history_minutes": 60})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(State(), stale_after=180, sensors=reader))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        code, body = get(port, "/v1/sensors")                 # 모델 계산 전이어도 실측값은 보인다
        assert code == 200 and {g["space"] for g in body["spaces"]} == {"실습실", "사무실", "복도"}
        code, body = get(port, "/v1/sensors?space=lab")
        assert code == 200 and [g["space"] for g in body["spaces"]] == ["실습실"]
        assert get(port, "/v1/spaces")[0] == 503
    finally:
        server.shutdown()


def test_sensors_route_without_reader():
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(State(), stale_after=180))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert get(server.server_address[1], "/v1/sensors")[0] == 503
    finally:
        server.shutdown()
