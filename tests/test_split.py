"""기간 분할과 정규화 (완료 조건 1, SC-001)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.split import (assign_period, compute_normalization, denormalize, load_split, make_split, normalize,
                            split_to_json)
from pipeline.settings import load_settings
from tests.synth import clean_frame

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings():
    return load_settings(ROOT / "config" / "preprocess.toml")


@pytest.fixture
def clean():
    # 2026-01-01 ~ 2026-04-10 15시 KST. 마지막 날은 반나절이라 분할 끝은 4월 10일 자정
    return clean_frame("2025-12-31T15:00Z", "2026-04-10T06:00Z", [("실습실", "co2"), ("실습실", "temperature")],
                       lambda t, s, m: (600 if m == "co2" else 22) + np.arange(len(t)) % 10)


def test_split_boundaries_are_time_ordered(clean, settings):
    split = make_split(clean, settings)
    kst = lambda text: pd.Timestamp(text, tz="Asia/Seoul")
    assert split["train"]["start"] == kst("2026-01-01")
    assert split["test"]["end"] == kst("2026-04-10")
    assert split["test"]["start"] == kst("2026-03-13")            # 28일 전
    assert split["val"]["start"] == kst("2026-02-13")             # 그 앞 28일
    assert split["train"]["end"] == split["val"]["start"] and split["val"]["end"] == split["test"]["start"]
    periods = assign_period(clean["time"], split)
    order = periods.map({"train": 0, "val": 1, "test": 2, "": 3})
    times = clean["time"]
    # 학습 칸은 모두 검증 칸보다 앞서고, 검증 칸은 모두 시험 칸보다 앞선다 (섞이지 않음)
    assert times[order == 0].max() < times[order == 1].min()
    assert times[order == 1].max() < times[order == 2].min()
    assert (periods[times >= split["data_end"]] == "").all()       # 반나절 남은 마지막 날은 어디에도 안 들어감


def test_normalization_ignores_val_and_test_values(clean, settings):
    split = make_split(clean, settings)
    base = compute_normalization(clean, split)
    changed = clean.copy()
    later = assign_period(changed["time"], split).isin(["val", "test"])
    changed.loc[later, "value"] = changed.loc[later, "value"] * 10 + 5000     # 검증·시험 값을 크게 바꿔도
    assert compute_normalization(changed, split) == base                       # 정규화 값은 같다
    train = clean[assign_period(clean["time"], split) == "train"]
    co2 = train[train["metric"] == "co2"]["value"]
    assert base["실습실"]["co2"]["mean"] == pytest.approx(co2.mean())
    assert base["실습실"]["co2"]["std"] == pytest.approx(co2.std(ddof=0))
    assert base["실습실"]["co2"]["n"] == len(co2)


def test_normalization_excludes_interpolated_and_fault(clean, settings):
    split = make_split(clean, settings)
    base = compute_normalization(clean, split)
    dirty = clean.copy()
    train_rows = dirty.index[(assign_period(dirty["time"], split) == "train") & (dirty["metric"] == "co2")]
    dirty.loc[train_rows[:100], ["value", "interpolated"]] = [99999.0, True]
    dirty.loc[train_rows[100:200], ["value", "fault"]] = [99999.0, "stuck"]
    result = compute_normalization(dirty, split)
    assert result["실습실"]["co2"]["n"] == base["실습실"]["co2"]["n"] - 200
    assert result["실습실"]["co2"]["mean"] < 1000


def test_split_json_round_trip_and_normalize(clean, settings, tmp_path):
    split = make_split(clean, settings)
    path = tmp_path / "split.json"
    path.write_text(json.dumps(split_to_json(split, settings)), encoding="utf-8")
    loaded = load_split(path)
    for period in ("train", "val", "test"):
        assert loaded[period] == split[period]
    norm = {"values": compute_normalization(clean, split)}
    values = pd.Series([600.0, 605.0])
    assert np.allclose(denormalize(normalize(values, "실습실", "co2", norm), "실습실", "co2", norm), values)


def test_split_command_does_not_overwrite(clean, settings, tmp_path, capsys):
    from pipeline import split as split_module
    from pipeline.preprocess import save_clean

    root = tmp_path
    (root / "data" / "processed").mkdir(parents=True)
    save_clean(clean, root / "data" / "processed" / "clean_5min.csv.gz")
    s = load_settings(ROOT / "config" / "preprocess.toml", root=root)
    assert split_module.run(s) == 0
    fixed = (root / "artifacts" / "split.json").read_text(encoding="utf-8")
    # 데이터가 늘어나도 --force 없이는 분할이 그대로
    longer = pd.concat([clean, clean_frame("2026-04-10T06:05Z", "2026-05-20T00:00Z", [("실습실", "co2")],
                                           lambda t, s_, m: 600 + 0 * np.arange(len(t)))])
    save_clean(longer, root / "data" / "processed" / "clean_5min.csv.gz")
    assert split_module.run(s) == 0
    assert (root / "artifacts" / "split.json").read_text(encoding="utf-8") == fixed
    assert "이미 고정됨" in capsys.readouterr().out
    assert split_module.run(s, force=True) == 0
    assert (root / "artifacts" / "split.json").read_text(encoding="utf-8") != fixed
    norm = json.loads((root / "artifacts" / "normalization.json").read_text(encoding="utf-8"))
    assert norm["computed_from"] == "train"
    assert norm["period"]["end"] == json.loads((root / "artifacts" / "split.json").read_text())["train"]["end"]
