"""과제 3: 특성, 표본 누수, 마스크, 모델, 평가 정합성, 시험 잠금, CLI 전 과정."""

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from forecast.dataset import build_samples
from forecast.features import calendar_features, feature_names
from forecast.model import build_model, count_parameters, masked_l1
from forecast.settings import load_forecast_settings
from pipeline import baselines as bl
from pipeline.preprocess import save_clean
from pipeline.settings import load_schedule, load_settings
from pipeline.split import compute_normalization, make_split, normalization_to_json, split_to_json
from tests.synth import clean_frame

ROOT = Path(__file__).resolve().parent.parent
TZ = "Asia/Seoul"


@pytest.fixture(scope="module")
def schedule():
    return load_schedule(ROOT / "config" / "schedule.toml")


@pytest.fixture(scope="module")
def settings():
    return load_settings(ROOT / "config" / "preprocess.toml")


def occupancy(times):
    local = times.tz_convert(TZ)
    clock = local.hour + local.minute / 60
    busy = (local.weekday < 5) & (((clock >= 9.2) & (clock < 11.5)) | ((clock >= 13.2) & (clock < 18)))
    return busy.astype(float)


def synthetic_clean(days=100, end="2026-04-10T15:00Z"):
    start = pd.Timestamp(end) - pd.Timedelta(days=days)

    def value(t, space, metric):
        busy = occupancy(t)
        level = pd.Series(busy).rolling(6, min_periods=1).mean().to_numpy()     # 30분에 걸쳐 오르내림
        noise = (np.arange(len(t)) * 7919 % 13) / 13
        if metric == "co2":
            return 450 + (600 if space == "실습실" else 300) * level + 5 * noise
        if metric == "temperature":
            return 21 + 2 * level + 0.05 * noise
        return 45 + 3 * noise

    spaces = [("실습실", "co2"), ("실습실", "temperature"), ("실습실", "humidity"),
              ("사무실", "co2"), ("사무실", "temperature"), ("사무실 밖", "temperature")]
    return clean_frame(start, end, spaces, value)


@pytest.fixture(scope="module")
def prepared(settings):
    clean = synthetic_clean()
    split = make_split(clean, settings)
    norm = normalization_to_json(compute_normalization(clean, split), split)
    points = bl.build_eval_points(clean, split, settings)
    return clean, split, norm, points


def test_calendar_features(schedule):
    times = pd.DatetimeIndex(pd.to_datetime([
        "2026-03-09 09:10", "2026-03-09 12:05", "2026-03-09 19:05", "2026-05-05 10:05", "2026-03-02 10:05",
    ]).tz_localize(TZ).tz_convert("UTC"))
    full = calendar_features(times, "실습실", "full", schedule)
    names = feature_names("full")
    assert full.shape == (5, 6)
    in_class = full[:, names.index("in_class")]
    class_day = full[:, names.index("class_day")]
    assert in_class.tolist() == [1.0, 0.5, 0.0, 0.0, 0.0]          # 수업, 점심, 퇴실 뒤, 어린이날, 대체공휴일
    assert class_day.tolist() == [1, 1, 1, 0, 0]
    assert calendar_features(times, "사무실", "full", schedule)[:, names.index("in_class")].sum() == 0
    assert calendar_features(times, "실습실", "no_schedule", schedule).shape == (5, 4)
    assert calendar_features(times, "실습실", "sensor_only", schedule).shape == (5, 0)


def test_samples_leakage_free(prepared, settings, schedule):
    clean, split, norm, points = prepared
    s = build_samples(clean, split, norm, settings, schedule, "full", 24, eval_points=points)
    step = pd.Timedelta(minutes=5)
    train_times = pd.to_datetime(s.times[s.positions["train"]]).tz_localize("UTC")
    assert len(train_times) > 1000
    assert (train_times + 12 * step < split["train"]["end"]).all()       # 정답 시각까지 학습 기간 안
    assert (train_times >= split["train"]["start"]).all()
    val_times = pd.to_datetime(s.times[s.positions["val"]]).tz_localize("UTC")
    assert (val_times >= split["val"]["start"]).all() and (val_times + 12 * step < split["val"]["end"]).all()
    # 검증·시험 값을 크게 바꿔도 학습 표본은 같다
    changed = clean.copy()
    later = changed["time"] >= split["val"]["start"]
    changed.loc[later, "value"] = changed.loc[later, "value"] * 3 + 999
    s2 = build_samples(changed, split, norm, settings, schedule, "full", 24, eval_points=points)
    rows = s.positions["train"][:5000]
    a, b = s.batch(rows), s2.batch(s2.positions["train"][:5000])
    for k in ("past", "future", "target", "mask", "base"):
        np.testing.assert_array_equal(a[k], b[k])
    # 시험 표본은 평가 시점과 같은 시각
    test_times = set(pd.to_datetime(s.times[s.positions["test"]]).tz_localize("UTC"))
    assert test_times == set(points["time"])


def test_batch_contents(prepared, settings, schedule):
    clean, split, norm, points = prepared
    s = build_samples(clean, split, norm, settings, schedule, "full", 24, eval_points=points)
    pos = s.positions["train"][100:110]
    b = s.batch(pos)
    assert b["past"].shape == (10, 24, 6 + 6) and b["future"].shape == (10, 12, 6) and b["target"].shape == (10, 12, 2)
    # 미래 쪽에는 센서값이 없다 (달력·일정 6개뿐), 과거 마지막 칸이 시각 t
    np.testing.assert_array_equal(b["past"][:, -1, :3], s.feats[pos, :3])
    # 정답 = y(t+h) − y(t)
    np.testing.assert_allclose(b["target"][:, 0, 0], np.nan_to_num(s.z[pos + 1, 0] - s.z[pos, 0]), atol=1e-6)
    # 사무실 밖은 CO₂가 없으므로 CO₂ 정답 마스크가 모두 0
    out_id = s.spaces.index("사무실 밖")
    rows = s.positions["train"][s.space_id[s.positions["train"]] == out_id][:50]
    assert s.batch(rows)["mask"][:, :, 0].sum() == 0


def test_interpolated_target_masked(prepared, settings, schedule):
    clean, split, norm, points = prepared
    dirty = clean.copy()
    t_bad = split["train"]["start"] + pd.Timedelta(days=10, hours=3)
    dirty.loc[(dirty["time"] == t_bad) & (dirty["space"] == "실습실") & (dirty["metric"] == "co2"), "interpolated"] = True
    s = build_samples(dirty, split, norm, settings, schedule, "full", 24)
    lab = s.spaces.index("실습실")
    times = pd.to_datetime(s.times).tz_localize("UTC")
    pos_bad = np.flatnonzero((times == t_bad) & (s.space_id == lab))[0]
    b = s.batch(np.array([pos_bad - 1]))           # t+5분이 보간 칸
    assert b["mask"][0, 0, 0] == 0 and b["mask"][0, 1, 0] == 1


def test_model_shapes_and_onnx(tmp_path):
    for kind in ("linear", "gru"):
        model = build_model(kind, 24, 12, 6, 4)
        out = model(torch.zeros(3, 24, 12), torch.zeros(3, 12, 6), torch.zeros(3, 4))
        assert out.shape == (3, 12, 2)
        assert torch.all(out == 0)                  # 처음에는 현재값 유지
        assert count_parameters(model) <= 100_000
    model = build_model("gru", 24, 12, 6, 4)
    torch.onnx.export(model, (torch.zeros(1, 24, 12), torch.zeros(1, 12, 6), torch.zeros(1, 4)), tmp_path / "m.onnx",
                      input_names=["past", "future", "space"], output_names=["delta"], dynamo=False)
    assert (tmp_path / "m.onnx").stat().st_size > 0


def test_masked_l1_ignores_masked_cells():
    pred = torch.zeros(2, 12, 2, requires_grad=True)
    target = torch.ones(2, 12, 2)
    mask = torch.zeros(2, 12, 2)
    mask[:, :, 1] = 1
    target[:, :, 0] = 1000                          # 가려진 칸은 아무리 커도 영향 없음
    loss = masked_l1(pred, target, mask)
    loss.backward()
    assert float(loss) == pytest.approx(1.0)
    assert torch.all(pred.grad[:, :, 0] == 0)


def test_zero_delta_reproduces_persistence(prepared, settings, schedule):
    from forecast.evaluate import test_predictions

    clean, split, norm, points = prepared
    s = build_samples(clean, split, norm, settings, schedule, "full", 24, eval_points=points)
    delta = np.zeros((len(s.positions["test"]), 12, 2), np.float32)
    model_table = bl.mae_table(test_predictions(s, delta, points, settings.horizons, "zero"))
    base_table = bl.mae_table(bl.predict(clean, points, settings))
    m = model_table.set_index(["metric", "space", "horizon_min"])
    p = base_table[base_table["baseline"] == "persistence"].set_index(["metric", "space", "horizon_min"])
    joined = m.join(p, lsuffix="_model", rsuffix="_base", how="inner")
    assert len(joined) == len(p)
    np.testing.assert_allclose(joined["mae_model"], joined["mae_base"], atol=1e-6)
    np.testing.assert_array_equal(joined["n_model"], joined["n_base"])


def make_root(tmp_path, settings):
    """과제 2 산출물이 있는 가짜 저장소 (설정 파일 + data/processed + artifacts)."""
    (tmp_path / "config").mkdir()
    for name in ("preprocess.toml", "schedule.toml", "forecast.toml"):
        shutil.copy(ROOT / "config" / name, tmp_path / "config" / name)
    text = (tmp_path / "config" / "forecast.toml").read_text(encoding="utf-8")
    text = text.replace("max_epochs = 30", "max_epochs = 2").replace("seeds = [1, 2, 3]", "seeds = [1]")
    text = text.replace('names = ["full", "no_schedule", "sensor_only"]', 'names = ["full", "no_schedule"]')
    (tmp_path / "config" / "forecast.toml").write_text(text, encoding="utf-8")
    clean = synthetic_clean()
    (tmp_path / "data" / "processed").mkdir(parents=True)
    save_clean(clean, tmp_path / "data" / "processed" / "clean_5min.csv.gz")
    s = load_settings(tmp_path / "config" / "preprocess.toml", root=tmp_path)
    from pipeline import split as split_module

    assert split_module.run(s) == 0
    assert bl.run(s) == 0
    return tmp_path


def test_cli_end_to_end_and_test_lock(tmp_path, settings, capsys):
    from forecast.__main__ import main

    root = make_root(tmp_path, settings)
    config = ["--config", str(root / "config" / "forecast.toml")]
    assert main(["samples", *config]) == 0
    assert main(["train", "--kind", "linear", *config]) == 0
    assert main(["train", "--kind", "gru", "--variant", "full", *config]) == 0
    assert main(["train", "--kind", "gru", "--variant", "no_schedule", *config]) == 0
    assert main(["evaluate", "--split", "test", "--all", *config]) == 2      # 고르기 전에는 시험 평가 거부
    assert main(["evaluate", "--split", "val", "--run", "gru-full-L120-s1", *config]) == 0
    assert main(["select", *config]) == 0
    selection = json.loads((root / "data" / "forecast" / "selection.json").read_text(encoding="utf-8"))
    assert selection["chosen"] in {"linear-full-L120-s1", "gru-full-L120-s1", "gru-no_schedule-L120-s1"}
    assert main(["evaluate", "--split", "test", "--all", *config]) == 0
    def log_rows():
        text = (root / "docs" / "reports" / "003-test-log.md").read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line.startswith("| 20")]

    assert len(log_rows()) == 3
    assert main(["evaluate", "--split", "test", "--all", *config]) == 0      # 같은 모델 다시 → 새 줄 없음
    assert len(log_rows()) == 3
    assert main(["compare", *config]) == 0
    report = (root / "docs" / "reports" / "003-model-comparison.md").read_text(encoding="utf-8")
    assert "완료 조건" in report and "현재값 유지" in report and "일정 정보의 가치" in report
    assert main(["analyze", *config]) == 0
    assert (root / "docs" / "reports" / "figures" / "003-error-by-time.png").exists()
