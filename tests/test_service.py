"""과제 8: 모델 묶음(export) → ONNX 서비스 한 주기, 데이터 끊김 경보와 복구, 조회 API, 주기 기록 요약."""

import json
import pickle
import shutil
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from anomaly import rules as rule_module
from anomaly.data import space_frames
from anomaly.model import AutoEncoder
from collector.store import Store
from forecast.model import build_model
from pipeline.settings import load_schedule, load_settings
from pipeline.split import compute_normalization, make_split, normalization_to_json
from service.__main__ import AlertGate, State, cmd_summary, load_service_config, make_handler, run_one
from tests.test_forecast import occupancy, synthetic_clean

ROOT = Path(__file__).resolve().parent.parent
NOW = pd.Timestamp("2026-04-14T05:03:00Z")          # 화요일 14:03 KST (수업 중)
DEVICES = [("lab-1", "AM103", "실습실", ("co2", "temperature", "humidity")),
           ("off-1", "AM103", "사무실", ("co2", "temperature", "humidity")),
           ("out-1", "EM300-TH", "사무실 밖", ("temperature", "humidity"))]     # 회의실 센서는 없음 → 데이터 끊김


def raw_value(times, metric):
    busy = occupancy(pd.DatetimeIndex(times))
    if metric == "co2":
        return 450 + 400 * busy
    if metric == "temperature":
        return 21 + 2 * busy
    return np.full(len(times), 45.0)


def insert_rows(db, start, end):
    """[start, end) 1분마다 센서 값 (수집기 measurements 표 형식)."""
    times = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    rows = []
    for device, profile, space, metrics in DEVICES:
        for metric in metrics:
            for t, v in zip(times, raw_value(times, metric)):
                iso = t.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                rows.append((f"{device}:{iso}", device, profile, space, "s", metric, float(v), None, iso, "time", None,
                             iso, None, "test", "t"))
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT OR IGNORE INTO measurements (message_key, device_id, device_profile, space, spot, metric, value, "
                         "unit, measured_at, time_source, gw_time, received_at, fcnt, source, topic) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    """과제 3·4·5 결과물(가짜, 작게 학습)과 수집기 DB가 있는 저장소 → export까지."""
    root = tmp_path_factory.mktemp("svc")
    (root / "config").mkdir()
    for name in ("preprocess.toml", "schedule.toml", "forecast.toml", "collector.toml", "service.toml"):
        shutil.copy(ROOT / "config" / name, root / "config" / name)
    settings = load_settings(root / "config" / "preprocess.toml", root=root)
    schedule = load_schedule(root / "config" / "schedule.toml")
    spaces = [s.space for s in settings.spaces]

    clean = synthetic_clean()
    split = make_split(clean, settings)
    norm = normalization_to_json(compute_normalization(clean, split), split)
    (root / "artifacts").mkdir()
    (root / "artifacts" / "normalization.json").write_text(json.dumps(norm, default=str), encoding="utf-8")

    # 과제 3: GRU (가중치는 0 초기화 그대로 = 현재값 유지)
    torch.manual_seed(0)
    model = build_model("gru", 24, 12, 6, len(spaces))
    meta = {"run_id": "gru-full-L120-s1", "kind": "gru", "variant": "full", "lookback_steps": 24, "n_past": 12, "n_future": 6,
            "n_spaces": len(spaces), "spaces": spaces, "hidden": 64, "layers": 2, "dropout": 0.1}
    run_dir = root / "data" / "forecast" / "runs" / meta["run_id"]
    run_dir.mkdir(parents=True)
    torch.save({"state_dict": model.state_dict(), "meta": meta}, run_dir / "model.pt")
    (root / "data" / "forecast" / "selection.json").write_text(json.dumps({"chosen": meta["run_id"]}), encoding="utf-8")

    # 과제 4: 오토인코더(학습 안 함) + 규칙 기준값
    frames = space_frames(clean, settings)
    ae = AutoEncoder(12, 3 + len(spaces), 16, 4)
    (root / "data" / "anomaly").mkdir(parents=True)
    torch.save({"state_dict": ae.state_dict(), "window": 12, "n_cond": 3 + len(spaces), "hidden": 16, "bottleneck": 4,
                "spaces": spaces}, root / "data" / "anomaly" / "autoencoder.pt")
    (root / "data" / "anomaly" / "threshold.json").write_text(json.dumps({"threshold": 1e6, "decided_at": "val"}), encoding="utf-8")
    params = rule_module.fit(frames, schedule, settings.timezone)
    (root / "data" / "anomaly" / "rules.json").write_text(json.dumps(params), encoding="utf-8")

    # 과제 5: 작은 분류기 두 개
    from sklearn.ensemble import HistGradientBoostingClassifier
    from state.features import CALENDAR_FEATURES, SENSOR_FEATURES, calendar_frame, sensor_features

    lab = frames["실습실"]
    table = pd.concat([sensor_features(lab), calendar_frame(lab.index, "실습실", schedule, settings.timezone)], axis=1)
    occ = HistGradientBoostingClassifier(max_iter=20).fit(table[SENSOR_FEATURES], occupancy(lab.index).astype(int))
    vent = HistGradientBoostingClassifier(max_iter=20).fit(table[SENSOR_FEATURES + CALENDAR_FEATURES],
                                                           (lab["co2"].shift(-6) >= 900).astype(int))
    (root / "data" / "state").mkdir(parents=True)
    with (root / "data" / "state" / "models.pkl").open("wb") as fp:
        pickle.dump({"occupancy": occ, "occupancy_threshold": 0.5, "ventilation": vent, "ventilation_threshold": 0.5,
                     "occupancy_features": SENSOR_FEATURES, "ventilation_features": SENSOR_FEATURES + CALENDAR_FEATURES}, fp)

    from service.export import export

    manifest = export(root, root / "data" / "models")
    assert {"forecast.onnx", "autoencoder.onnx", "state.pkl", "normalization.json"} <= set(manifest["files"])
    assert manifest["forecast_run"] == "gru-full-L120-s1"

    Store(root / "data" / "collector.db").close()
    insert_rows(root / "data" / "collector.db", NOW - pd.Timedelta(hours=30), NOW)
    return root


@pytest.fixture(scope="module")
def engine(root):
    from service.__main__ import build_engine

    cfg = load_service_config(root / "config" / "service.toml")
    return build_engine(root, cfg)[0]


def test_cycle_gives_forecast_anomaly_state(engine):
    result = engine.run_cycle(NOW)
    assert result["slot"] == "2026-04-14T05:00:00Z"
    lab = result["spaces"]["lab"]
    assert [p["minutes"] for p in lab["forecast"]["co2"]["points"]] == list(range(5, 65, 5))
    # 0 초기화 모델 = 현재값 유지: 예측값이 지금 값과 같다 (전처리·역정규화가 학습 때와 같다는 확인)
    assert lab["forecast"]["co2"]["now"] == pytest.approx(850, abs=1)
    assert all(p["value"] == lab["forecast"]["co2"]["now"] for p in lab["forecast"]["co2"]["points"])
    assert isinstance(lab["anomaly"]["score"], float) and lab["anomaly"]["is_anomaly"] is False
    assert 0 <= lab["state"]["occupied_probability"] <= 1 and "ventilation_alert" in lab["state"]
    assert not [a for a in lab["alerts"] if a["type"] in ("data_gap", "sensor_gap")]
    assert "co2" not in result["spaces"]["office-out"]["forecast"]          # CO₂ 센서 없는 공간
    assert [a["type"] for a in result["spaces"]["meeting"]["alerts"]] == ["data_gap"]


def test_collector_stop_raises_data_gap_then_recovers(root, engine):
    """수집기를 20분 멈춘 상황 → '데이터 끊김' 경보, 수집 재개 후 해제."""
    gate = AlertGate(30)
    later = NOW + pd.Timedelta(minutes=20)
    first = gate.step(engine.run_cycle(NOW), 0)
    assert ("lab", "data_gap") not in {(a["slug"], a["type"]) for a in first}
    stopped = engine.run_cycle(later)
    assert "data_gap" in [a["type"] for a in stopped["spaces"]["lab"]["alerts"]]
    sent = gate.step(stopped, 60)
    assert {"slug": "lab", "type": "data_gap", "status": "active"}.items() <= next(
        a for a in sent if a["slug"] == "lab" and a["type"] == "data_gap").items()
    assert not [a for a in gate.step(stopped, 120) if a["slug"] == "lab"]      # 같은 경보 반복 안 함
    insert_rows(root / "data" / "collector.db", NOW, later)
    recovered = engine.run_cycle(later)
    assert "data_gap" not in [a["type"] for a in recovered["spaces"]["lab"]["alerts"]]
    assert recovered["spaces"]["lab"]["forecast"]["co2"]["points"]
    cleared = gate.step(recovered, 180)
    assert any(a["slug"] == "lab" and a["type"] == "data_gap" and a["status"] == "cleared" for a in cleared)


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_http_api(engine, tmp_path):
    state = State()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, stale_after=180))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        assert get(port, "/v1/spaces")[0] == 503                                # 첫 계산 전
        assert get(port, "/health")[0] == 503
        run_one(engine, state, AlertGate(30), None, tmp_path / "cycles.csv", now=NOW)
        code, health = get(port, "/health")
        assert code == 200 and health["cycles"] == 1
        code, body = get(port, "/v1/spaces")
        assert code == 200 and {s["slug"] for s in body["spaces"]} == {"lab", "office", "meeting", "office-out"}
        code, fc = get(port, "/v1/spaces/lab/forecast?minutes=60")
        assert code == 200 and fc["minutes"] == 60 and "co2" in fc and "temperature" in fc
        assert get(port, "/v1/spaces/lab/forecast?minutes=7")[0] == 400
        assert get(port, "/v1/spaces/nowhere")[0] == 404
        assert get(port, "/v1/spaces/lab/state")[1]["state"]["occupied_probability"] is not None
        code, alerts = get(port, "/v1/alerts")
        assert code == 200 and any(a["slug"] == "meeting" and a["type"] == "data_gap" for a in alerts["active"])
    finally:
        server.shutdown()


class Broken:
    def run_cycle(self, now=None):
        raise OSError("database is locked")


def test_failed_cycle_is_recorded_and_service_continues(engine, tmp_path):
    state, gate, path = State(), AlertGate(30), tmp_path / "cycles.csv"
    assert run_one(Broken(), state, gate, None, path) is None
    assert state.failures == 1 and "database is locked" in state.last_error
    assert run_one(engine, state, gate, None, path, now=NOW) is not None
    assert state.last_error is None
    rows = pd.read_csv(path)
    assert rows["ok"].tolist() == [0, 1]


def test_summary_counts_downtime(tmp_path):
    root = tmp_path
    (root / "data" / "service").mkdir(parents=True)
    times = list(pd.date_range("2026-10-22T06:00Z", periods=60, freq="60s")) + \
        list(pd.date_range("2026-10-22T07:10Z", periods=60, freq="60s"))       # 10분 멈춤
    frame = pd.DataFrame({"time": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times], "ok": 1, "ms": 120.0, "raw_rows": 100,
                          "n_spaces": 4, "alerts": "", "data_gaps": 0, "error": ""})
    frame.loc[5, "alerts"] = "lab:data_gap;meeting:data_gap"
    frame.to_csv(root / "data" / "service" / "cycles.csv", index=False)
    cfg = {"cycle": {"seconds": 60}}
    args = type("A", (), {"hours": None, "since": None})()
    assert cmd_summary(root, cfg, args) == 0
    text = (root / "docs" / "reports" / "008-service-log.md").read_text(encoding="utf-8")
    assert "| 멈춘 구간 (간격 > 180초) | 1회 |" in text and "| lab:data_gap | 1 |" in text


def test_out_of_range_rule_alert(root, engine):
    start, end = NOW + pd.Timedelta(minutes=20), NOW + pd.Timedelta(minutes=25)
    insert_rows(root / "data" / "collector.db", start, end)
    iso = (end.floor("5min") - pd.Timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with sqlite3.connect(root / "data" / "collector.db") as conn:
        conn.execute("INSERT INTO measurements (message_key, device_id, device_profile, space, spot, metric, value, measured_at, "
                     "time_source, received_at, source, topic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("bad", "lab-2", "AM103", "실습실", "s", "co2", 65535.0, iso, "time", iso, "test", "t"))
    result = engine.run_cycle(end)
    alert = next(a for a in result["spaces"]["lab"]["alerts"] if a["type"] == "rule_out_of_range")
    assert alert["cause"] == "co2" and alert["devices"] == ["lab-2"]
    assert result["spaces"]["lab"]["forecast"]["co2"]["now"] < 5000          # 범위 밖 값은 계산에서 빠짐
