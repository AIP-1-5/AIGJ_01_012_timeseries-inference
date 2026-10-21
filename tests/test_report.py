import csv
from datetime import datetime, timedelta, timezone

import pytest

from collector.config import load_config
from collector.report import EXIT_OK, EXIT_STALE, build_report, display_width, export, parse_kst, status
from collector.store import Store
from collector.timeutil import to_iso
from tests.test_coverage import row

NOW = datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


@pytest.fixture
def config(root, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(root / "config" / "collector.toml")
    cfg.storage_path = tmp_path / "c.db"
    store = Store(cfg.storage_path)
    store.add_event("connect", at=to_iso(NOW - 120 * MIN))
    for i in range(60):
        t = NOW - 60 * MIN + i * MIN
        store.insert_message([row("A", "co2", t, i, profile="AM103"), row("A", "temperature", t, i, profile="AM103")])
    store.close()
    return cfg


def test_status_prints_table(config, capsys):
    assert status(config, hours=1, now=NOW) == EXIT_OK
    out = capsys.readouterr().out
    assert "실습실" in out and "co2" in out and "100.0%" in out and "실행 중" in out


def test_status_flags_stale(config, capsys):
    assert status(config, hours=1, now=NOW + 30 * MIN) == EXIT_STALE
    assert "← 끊김" in capsys.readouterr().out


def test_report_contains_required_sections(config):
    store = Store(config.storage_path, readonly=True)
    text, result = build_report(config, store, NOW - 60 * MIN, NOW, NOW)
    for heading in ("## 측정 조건", "## 확인 기준 판정", "## 센서·항목별 커버율", "## 결측 구간",
                    "## 수집기 사건", "## 중복·해석 실패", "## 시계 점검", "## 해석", "### 직접 작성"):
        assert heading in text
    assert "실데이터" in text and "통과" in text
    assert len(result.series) == 2


def test_export_writes_csv(config, tmp_path):
    out = tmp_path / "data" / "export.csv"
    assert export(config, "2026-10-21T09:00", "2026-10-21T10:00", metrics=["co2"], out=str(out)) == EXIT_OK
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0][:3] == ["measured_at", "device_id", "device_profile"]
    assert len(rows) == 61 and {r[5] for r in rows[1:]} == {"co2"}


def test_helpers():
    assert display_width("실습실") == 6 and display_width("co2") == 3
    assert parse_kst("2026-10-21T09:00").utcoffset() == timedelta(hours=9)


def test_interpretation_suggests_why_and_action(config):
    from tests.test_coverage import row as make_row

    store = Store(config.storage_path)
    # 배터리 1% 센서가 30분 전부터 끊김
    for i in range(30):
        t = NOW - 60 * MIN + i * MIN
        store.insert_message([make_row("C", "co2", t, i, profile="AM103", space="사무실"),
                              make_row("C", "battery", t, i, value=1, profile="AM103", space="사무실")])
    store.close()
    reader = Store(config.storage_path, readonly=True)
    text, _ = build_report(config, reader, NOW - 60 * MIN, NOW, NOW)
    section = text.split("## 해석", 1)[1]
    assert "센서 쪽 (배터리 낮음)" in section and "**왜**" in section and "**그래서**" in section
    assert "배터리 20% 이하 센서 1대" in section
    assert "95% 미만" in section


def test_report_warns_when_end_is_in_future(config, capsys, tmp_path):
    from collector.report import write_report

    out = tmp_path / "r.md"
    write_report(config, start="2026-10-21T09:00", end="2026-10-22T09:00", out=str(out), now=NOW)
    assert "아직 지나지 않았다" in capsys.readouterr().err
    assert "아직 끝나지 않은 기간" in out.read_text(encoding="utf-8")
