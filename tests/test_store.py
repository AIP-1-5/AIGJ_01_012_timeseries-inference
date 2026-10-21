from collector.parser import MeasurementRow
from collector.store import REJECT_PAYLOAD_LIMIT, Store


def make_row(message_key="m1", metric="co2", value=900.0, measured_at="2026-10-21T00:00:10.000Z"):
    return MeasurementRow(
        message_key=message_key, device_id="dev1", device_profile="AM103", space="실습실", spot="강단",
        metric=metric, value=value, unit="ppm", measured_at=measured_at, time_source="ns_time",
        gw_time=None, received_at="2026-10-21T00:00:11.000Z", fcnt=1, source="chirpstack", topic="t",
    )


def test_schema_and_wal(tmp_path):
    store = Store(tmp_path / "c.db")
    assert store.journal_mode() == "wal"
    tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"measurements", "events", "rejects", "duplicates"} <= tables


def test_duplicate_message_is_ignored_and_recorded(tmp_path):
    store = Store(tmp_path / "c.db")
    rows = [make_row(metric="co2"), make_row(metric="temperature", value=22.5)]
    assert store.insert_message(rows) == (2, False)
    assert store.insert_message(rows) == (0, True)
    assert store.row_count() == 2
    assert store.conn.execute("SELECT COUNT(*) FROM duplicates").fetchone()[0] == 1


def test_reject_payload_is_truncated(tmp_path):
    store = Store(tmp_path / "c.db")
    store.add_reject("t", "json_error", "x" * (REJECT_PAYLOAD_LIMIT + 100))
    payload = store.conn.execute("SELECT payload FROM rejects").fetchone()[0]
    assert len(payload) == REJECT_PAYLOAD_LIMIT


def test_readonly_reader_sees_writer_data(tmp_path):
    path = tmp_path / "c.db"
    writer = Store(path)
    writer.insert_message([make_row()])
    reader = Store(path, readonly=True)
    rows = reader.measurements_between("2026-10-21T00:00:00.000Z", "2026-10-21T01:00:00.000Z").fetchall()
    assert len(rows) == 1
    # 읽는 동안에도 쓸 수 있다
    writer.insert_message([make_row(message_key="m2", measured_at="2026-10-21T00:01:10.000Z")])
    assert reader.row_count() == 2


def test_event_order_follows_insertion_not_clock(tmp_path):
    """부팅 직후 시계가 늦게 맞춰지면 새 사건의 at이 이전 사건보다 이를 수 있다."""
    store = Store(tmp_path / "c.db")
    store.add_event("stop", at="2026-10-21T04:12:13.000Z")
    store.add_event("start", at="2026-10-21T04:12:05.000Z")     # 시계가 아직 안 맞은 채 기록됨
    store.add_event("connect", at="2026-10-21T04:12:06.000Z")
    assert store.last_event(["start", "connect", "stop"])["kind"] == "connect"
    kinds = [e["kind"] for e in store.events_between("2026-10-21T04:00:00.000Z", "2026-10-21T05:00:00.000Z")]
    assert kinds == ["stop", "start", "connect"]
