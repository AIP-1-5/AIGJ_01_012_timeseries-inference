from datetime import datetime, timezone

import pytest

from collector.config import load_config
from collector.mqtt_client import Collector, should_reconnect
from collector.store import Store

TOPIC = "application/app/device/a0a0a0a0a0000001/event/up"
RECEIVED = datetime(2026, 10, 21, 0, 31, 30, tzinfo=timezone.utc)


class FakeReason:
    def __init__(self, failure):
        self.is_failure = failure

    def __str__(self):
        return "Not authorized" if self.is_failure else "Success"


class FakeClient:
    def __init__(self):
        self.subscribed = []

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))


@pytest.fixture
def collector(root, tmp_path, monkeypatch):
    monkeypatch.setenv("MQTT_USERNAME", "user")
    monkeypatch.setenv("MQTT_PASSWORD", "secret")
    config = load_config(root / "config" / "collector.toml")
    store = Store(tmp_path / "c.db")
    clock = {"now": 1000.0}
    c = Collector(config, store, clock=lambda: clock["now"])
    c.fake_clock = clock
    return c


def test_should_reconnect():
    assert not should_reconnect(False, 0, 1000, 300)       # 끊긴 상태는 자동 재시도에 맡김
    assert not should_reconnect(True, None, 1000, 300)
    assert not should_reconnect(True, 800, 1000, 300)
    assert should_reconnect(True, 700, 1000, 300)


def test_client_settings(collector):
    client = collector.build_client()
    assert client._reconnect_min_delay == 1
    assert client._reconnect_max_delay == 20
    import getpass, socket
    assert client._client_id == f"{socket.gethostname()}-{getpass.getuser()}-collector".encode()
    assert client._clean_session is False


def test_failed_attempts_are_merged(collector):
    for _ in range(5):
        collector.on_connect_fail(None, None)
    client = FakeClient()
    collector.on_connect(client, None, None, FakeReason(False), None)
    kinds = [(r["kind"], r["detail"]) for r in collector.store.conn.execute("SELECT kind, detail FROM events ORDER BY id")]
    assert kinds[0] == ("reconnect_attempt", "connect failed")
    assert kinds[1] == ("connect", "failed_attempts=5")
    assert ("subscribe", "chirpstack: application/+/device/+/event/up") in kinds
    assert client.subscribed == [("application/+/device/+/event/up", 1)]
    assert collector.connected


def test_disconnect_recorded_once(collector):
    collector.on_connect(FakeClient(), None, None, FakeReason(False), None)
    collector.on_disconnect(None, None, None, "Connection lost", None)
    collector.on_disconnect(None, None, None, "Connection lost", None)
    count = collector.store.conn.execute("SELECT COUNT(*) FROM events WHERE kind='disconnect'").fetchone()[0]
    assert count == 1 and not collector.connected


def test_handle_message_stores_and_dedupes(collector, fixture_bytes):
    payload = fixture_bytes("chirpstack_up.json")
    collector.handle_message(TOPIC, payload, RECEIVED)
    collector.handle_message(TOPIC, payload, RECEIVED)
    assert collector.store.row_count() == 4
    assert collector.stats["messages"] == 2 and collector.stats["duplicates"] == 1
    assert collector.last_message_at == 1000.0


def test_unknown_topic_and_bad_payload_are_rejected(collector):
    collector.handle_message("other/topic", b"{}", RECEIVED)
    collector.handle_message(TOPIC, b"not json", RECEIVED)
    reasons = [r[0] for r in collector.store.conn.execute("SELECT reason FROM rejects ORDER BY id")]
    assert reasons == ["no_matching_source", "json_error"]


def test_store_errors_stop_after_limit(collector, fixture_bytes):
    import sqlite3

    def broken(rows):
        raise sqlite3.OperationalError("disk I/O error")

    collector.store.insert_message = broken
    for _ in range(10):
        collector.handle_message(TOPIC, fixture_bytes("chirpstack_up.json"), RECEIVED)
    assert collector.exit_code == 1 and collector._stop.is_set()


def test_missing_credentials_is_config_error(root, monkeypatch):
    from collector.config import ConfigError

    monkeypatch.delenv("MQTT_USERNAME", raising=False)
    monkeypatch.delenv("MQTT_PASSWORD", raising=False)
    config = load_config(root / "config" / "collector.toml")
    with pytest.raises(ConfigError) as err:
        config.broker.credentials()
    assert "MQTT_USERNAME" in str(err.value) and "secret" not in str(err.value)


def test_config_option_before_or_after_command():
    from collector.__main__ import build_parser

    parser = build_parser()
    assert parser.parse_args(["--config", "a.toml", "status"]).config == "a.toml"
    assert parser.parse_args(["status", "--config", "b.toml"]).config == "b.toml"
    assert parser.parse_args(["status"]).config == "config/collector.toml"


def test_wait_for_clock_sync(collector, tmp_path, monkeypatch):
    import collector.mqtt_client as mc

    sync_dir = tmp_path / "timesync"
    monkeypatch.setattr(mc, "TIMESYNC_DIR", sync_dir)
    assert collector.wait_for_clock_sync(timeout=3) == 0          # timesyncd가 없는 환경: 기다리지 않음
    sync_dir.mkdir()
    collector._stop.wait = lambda seconds: False                    # 실제로 자지 않게
    assert collector.wait_for_clock_sync(timeout=3) == 3            # 끝까지 안 맞춰지면 timeout 후 진행
    (sync_dir / "synchronized").touch()
    assert collector.wait_for_clock_sync(timeout=3) == 0
