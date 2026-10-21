"""python -m pipeline 전 과정: 원시 파일 → preprocess → split → diagnose → baselines → report."""

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.__main__ import main

ROOT = Path(__file__).resolve().parent.parent


def write_raw(root, days=90):
    """실습실 CO₂ 3대(5분), 회의실 1대(10분), 온도. 평일 9~18시에 오른다."""
    t = pd.date_range(pd.Timestamp("2026-10-01", tz="Asia/Seoul") - pd.Timedelta(days=days), periods=days * 288,
                      freq="5min").tz_convert("UTC")
    local = t.tz_convert("Asia/Seoul")
    clock = local.hour + local.minute / 60
    busy = (local.weekday < 5) & (clock >= 9.2) & (clock < 18)
    co2 = np.where(busy, 900, 430) + (np.arange(len(t)) * 37 % 11)
    rows = []
    for i, device in enumerate(("lab1", "lab2", "lab3")):
        rows.append(pd.DataFrame({"time": t, "device_id": device, "space": "실습실", "spot": "", "metric": "co2",
                                  "value": co2 + i * 10}))
        rows.append(pd.DataFrame({"time": t, "device_id": device, "space": "실습실", "spot": "", "metric": "temperature",
                                  "value": 21 + busy * 2 + (np.arange(len(t)) * 13 % 7) / 10}))
    rows.append(pd.DataFrame({"time": t[::2], "device_id": "meet", "space": "회의실", "spot": "", "metric": "co2",
                              "value": co2[::2] - 50}))
    frame = pd.concat(rows)
    frame["time"] = frame["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for metric, group in frame.groupby("metric"):
        out = root / "data" / "raw" / "influx" / metric / "all.csv.gz"
        out.parent.mkdir(parents=True, exist_ok=True)
        group.to_csv(out, index=False)
    # 설정의 항목마다 원시 파일이 있어야 preprocess가 돈다
    temperature = frame[frame["metric"] == "temperature"]
    hum = temperature.assign(metric="humidity", value=40 + temperature["value"].to_numpy() % 1 * 10)
    out = root / "data" / "raw" / "influx" / "humidity" / "all.csv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    hum.to_csv(out, index=False)


def test_full_pipeline(tmp_path, capsys):
    (tmp_path / "config").mkdir()
    for name in ("preprocess.toml", "schedule.toml"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    write_raw(tmp_path)
    config = ["--config", str(tmp_path / "config" / "preprocess.toml")]
    schedule = ["--schedule", str(tmp_path / "config" / "schedule.toml")]
    assert main(["preprocess", *config]) == 0
    assert main(["split", *config]) == 0
    assert main(["diagnose", *config, *schedule]) == 0
    assert main(["baselines", *config]) == 0
    assert main(["report", *config, *schedule]) == 0
    assert main(["report", *config, *schedule]) == 1            # 진단서가 있으면 덮어쓰지 않는다

    split = json.loads((tmp_path / "artifacts" / "split.json").read_text(encoding="utf-8"))
    norm = json.loads((tmp_path / "artifacts" / "normalization.json").read_text(encoding="utf-8"))
    assert norm["computed_from"] == "train" and norm["period"]["end"] == split["train"]["end"]
    points = pd.read_csv(tmp_path / "artifacts" / "eval_points.csv")
    assert len(points) > 0 and set(points["metric"]) <= {"co2", "temperature"}
    assert (pd.to_datetime(points["time"], utc=True) >= pd.Timestamp(split["test"]["start"])).all()
    report = (tmp_path / "docs" / "reports" / "002-diagnosis.md").read_text(encoding="utf-8")
    for section in ("측정 조건", "결측률", "기간 분할", "실습실 일정", "기준선", "생각해 볼 점"):
        assert section in report
    assert (tmp_path / "docs" / "reports" / "figures" / "002-daily-co2.png").exists()
    assert (tmp_path / "docs" / "reports" / "figures" / "002-response.png").exists()
    out = capsys.readouterr().out
    assert "가장 강한 기준선" in out


def test_missing_raw_files(tmp_path, capsys):
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "preprocess.toml", tmp_path / "config" / "preprocess.toml")
    assert main(["preprocess", "--config", str(tmp_path / "config" / "preprocess.toml")]) == 1
    assert "export-influx" in capsys.readouterr().out


def test_bad_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "config" / "preprocess.toml"
    bad.parent.mkdir()
    bad.write_text((ROOT / "config" / "preprocess.toml").read_text(encoding="utf-8").replace("co2 = [300, 5000]", "co2 = [5000, 300]"),
                   encoding="utf-8")
    assert main(["preprocess", "--config", str(bad)]) == 2
    assert "최소 < 최대" in capsys.readouterr().err
