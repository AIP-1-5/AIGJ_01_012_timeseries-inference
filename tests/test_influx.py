"""InfluxDB 내보내기: 가짜 HTTP 서버로 월 나누기, 표 경계, 이어받기, 토큰 비노출 확인."""

import gzip
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd
import pytest

from pipeline.influx import InfluxError, export, iter_records, months, parse_month
from pipeline.settings import InfluxEnv
from pipeline.timecheck import match_times, summarize
from pipeline.loader import normalize_raw

TOKEN = "secret-token-should-not-leak"

# 표 0: spot 있음, 표 1: spot 열 없음 (spot 태그 없는 센서) → 빈 줄 뒤 머리줄이 다시 나온다
CSV_BODY = (
    ",result,table,_time,_value,device_id,location,spot\r\n"
    ",_result,0,2025-08-08T00:00:12Z,612,dev-a,실습실,전방\r\n"
    ",_result,0,2025-08-08T00:01:12Z,615,dev-a,실습실,전방\r\n"
    "\r\n"
    ",result,table,_time,_value,device_id,location\r\n"
    ",_result,1,2025-08-08T00:00:30Z,23.5,dev-b,실습실\r\n"
    "\r\n"
)


class FakeInflux(BaseHTTPRequestHandler):
    requests = []
    fail_months = set()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeInflux.requests.append({"auth": self.headers.get("Authorization"), "query": body["query"], "path": self.path})
        if any(month in body["query"] for month in FakeInflux.fail_months):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"message":"boom"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.end_headers()
        self.wfile.write(CSV_BODY.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def influx():
    FakeInflux.requests = []
    FakeInflux.fail_months = set()
    server = HTTPServer(("127.0.0.1", 0), FakeInflux)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield InfluxEnv(url=f"http://127.0.0.1:{server.server_port}", token=TOKEN, org="org123", bucket="iot-bucket")
    server.shutdown()


def test_months_and_parse():
    first, last = parse_month("2025-11-08"), parse_month("2026-02")
    names = [f"{s:%Y-%m}" for s, _ in months(first, last)]
    assert names == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert months(first, last)[1][1] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(InfluxError):
        parse_month("작년")


def test_iter_records_handles_table_boundaries():
    records = list(iter_records(CSV_BODY.splitlines()))
    assert len(records) == 3
    assert records[0]["spot"] == "전방"
    assert "spot" not in records[2] and records[2]["device_id"] == "dev-b"


def test_iter_records_raises_on_error_table():
    body = ",error,reference\r\n,query timeout,\r\n"
    with pytest.raises(InfluxError, match="query timeout"):
        list(iter_records(body.splitlines()))


def test_export_writes_months_and_skips_existing(influx, tmp_path, capsys):
    now = datetime(2025, 9, 15, 12, 30, 45, tzinfo=timezone.utc)
    failures = export(influx, tmp_path, parse_month("2025-08"), parse_month("2025-09"), ["co2"], now=now)
    assert failures == []
    done = tmp_path / "co2" / "2025-08.csv.gz"
    assert done.exists() and (tmp_path / "co2" / "2025-09.csv.gz").exists()
    frame = pd.read_csv(done, keep_default_na=False)
    assert list(frame.columns) == ["time", "device_id", "space", "spot", "metric", "value"]
    assert len(frame) == 3 and frame["spot"].tolist() == ["전방", "전방", ""]
    # 요청: orgID 쿼리, 월 경계, 이번 달은 지금(분 단위)까지
    assert all(r["path"] == "/api/v2/query?orgID=org123" for r in FakeInflux.requests)
    assert "stop: 2025-09-01T00:00:00Z" in FakeInflux.requests[0]["query"]
    assert "stop: 2025-09-15T12:30:00Z" in FakeInflux.requests[1]["query"]
    assert 'r._measurement == "co2"' in FakeInflux.requests[0]["query"]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["co2/2025-08"]["rows"] == 3

    # 다시 실행: 지난달은 건너뛰고 이번 달만 다시 받는다
    FakeInflux.requests = []
    export(influx, tmp_path, parse_month("2025-08"), parse_month("2025-09"), ["co2"], now=now)
    assert len(FakeInflux.requests) == 1 and "2025-09-01T00:00:00Z" in FakeInflux.requests[0]["query"]
    assert TOKEN not in capsys.readouterr().out
    assert all(r["auth"] == f"Token {TOKEN}" for r in FakeInflux.requests)


def test_export_failure_leaves_no_partial_file(influx, tmp_path, capsys):
    FakeInflux.fail_months = {"2025-08-01"}
    now = datetime(2025, 10, 1, tzinfo=timezone.utc)
    failures = export(influx, tmp_path, parse_month("2025-08"), parse_month("2025-09"), ["co2"], now=now)
    assert failures == [("co2", "2025-08")]
    assert not (tmp_path / "co2" / "2025-08.csv.gz").exists()
    assert not list((tmp_path / "co2").glob("*.part"))
    assert (tmp_path / "co2" / "2025-09.csv.gz").exists()
    out = capsys.readouterr().out
    assert "500" in out and TOKEN not in out


def test_written_file_is_gzip(influx, tmp_path):
    export(influx, tmp_path, parse_month("2025-08"), parse_month("2025-08"), ["temperature"],
           now=datetime(2025, 9, 2, tzinfo=timezone.utc))
    with gzip.open(tmp_path / "temperature" / "2025-08.csv.gz", "rt", encoding="utf-8") as fp:
        assert fp.readline().strip() == "time,device_id,space,spot,metric,value"


def test_time_check_finds_offset():
    base = pd.Timestamp("2026-10-21T00:00:00Z")
    rows_c, rows_i = [], []
    for i in range(30):
        t = base + pd.Timedelta(minutes=i)
        rows_c.append([t.isoformat(), "dev-a", "실습실", "", "co2", 600 + i])
        rows_c.append([t.isoformat(), "dev-b", "회의실", "", "co2", 500 + i])
        rows_i.append([t.isoformat(), "dev-a", "실습실", "", "co2", 600 + i])                    # 같은 시각
        rows_i.append([(t + pd.Timedelta(minutes=5)).isoformat(), "dev-b", "회의실", "", "co2", 500 + i])  # 5분 빠른 시계
    columns = ["time", "device_id", "space", "spot", "metric", "value"]
    merged = match_times(normalize_raw(pd.DataFrame(rows_c, columns=columns)),
                         normalize_raw(pd.DataFrame(rows_i, columns=columns)))
    table = summarize(merged)
    assert table.loc["dev-a", "median_s"] == 0
    assert table.loc["dev-b", "median_s"] == 300
    assert table.loc["dev-a", "match_rate"] == 1.0
