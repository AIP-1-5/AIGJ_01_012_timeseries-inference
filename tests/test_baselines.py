"""기준선 3종과 평가 시점 (완료 조건 2·3, SC-002·SC-007)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline import baselines as bl
from pipeline.preprocess import save_clean
from pipeline.settings import load_settings
from pipeline.split import make_split, split_to_json
from tests.synth import clean_frame

ROOT = Path(__file__).resolve().parent.parent
TZ = "Asia/Seoul"


@pytest.fixture
def settings():
    return load_settings(ROOT / "config" / "preprocess.toml")


def ramp_clean():
    """칸마다 1씩 오르는 값: 손으로 MAE를 계산할 수 있다."""
    return clean_frame("2026-03-08T15:00Z", "2026-03-12T15:00Z", [("실습실", "co2")], lambda t, s, m: 500 + np.arange(len(t)))


def manual_split(test_start, test_end):
    ts = lambda text: pd.Timestamp(text, tz=TZ)
    return {"timezone": TZ, "train": {"start": ts("2026-03-01"), "end": ts("2026-03-05")},
            "val": {"start": ts("2026-03-05"), "end": ts(test_start)},
            "test": {"start": ts(test_start), "end": ts(test_end)}, "data_end": ts(test_end)}


def test_baseline_predictions_on_ramp(settings):
    clean = ramp_clean()
    split = manual_split("2026-03-11", "2026-03-12")
    points = bl.build_eval_points(clean, split, settings)
    # 시험 하루(288칸) 중 마지막 60분은 정답이 시험 기간 밖이라 빠진다 → 288 − 12 = 276
    assert len(points) == 276
    table = bl.mae_table(bl.predict(clean, points, settings)).set_index(["space", "baseline", "horizon_min"])
    for h in (5, 15, 30, 60):
        k = h // 5
        assert table.loc[("실습실", "persistence", h), "mae"] == pytest.approx(k)
        assert table.loc[("실습실", "mean_1h", h), "mae"] == pytest.approx(k + 5.5)      # 12칸 평균은 5.5칸 뒤처짐
        assert table.loc[("실습실", "yesterday", h), "mae"] == pytest.approx(288)        # 하루 = 288칸
        assert table.loc[("전체", "persistence", h), "n"] == 276
    best = bl.strongest(bl.mae_table(bl.predict(clean, points, settings)))
    assert set(best["baseline"]) == {"persistence"}


def test_eval_point_rules(settings):
    clean = ramp_clean()
    split = manual_split("2026-03-11", "2026-03-12")
    base = set(bl.build_eval_points(clean, split, settings)["time"])
    t_interp = pd.Timestamp("2026-03-11T03:00Z")
    t_fault = pd.Timestamp("2026-03-11T06:00Z")
    t_missing_yesterday = pd.Timestamp("2026-03-10T09:00Z")
    t_window = pd.Timestamp("2026-03-11T12:00Z")
    dirty = clean.set_index("time")
    dirty.loc[t_interp, "interpolated"] = True                        # 보간 칸은 정답·현재값으로 못 씀
    dirty.loc[t_fault, "fault"] = "stuck"                             # 고장 칸도
    dirty.loc[t_missing_yesterday, "value"] = np.nan                  # 하루 전 값이 비면 어제 기준선을 못 만듦
    window_holes = pd.date_range(t_window - pd.Timedelta(minutes=55), periods=4, freq="5min")
    dirty.loc[window_holes, "value"] = np.nan                          # 1시간 창 12칸 중 4칸 빔 → 8칸 < 9칸
    dirty = dirty.reset_index()
    points = set(bl.build_eval_points(dirty, split, settings)["time"])
    removed = base - points
    step = pd.Timedelta(minutes=5)
    for h in (0, 5, 15, 30, 60):                                      # 그 칸을 정답(t+h)으로 쓰는 t는 모두 빠진다
        assert t_interp - pd.Timedelta(minutes=h) in removed
        assert t_fault - pd.Timedelta(minutes=h) in removed
    for h in (5, 15, 30, 60):                                         # y(t+h−24h)가 그 칸인 t
        assert t_missing_yesterday + pd.Timedelta(days=1) - pd.Timedelta(minutes=h) in removed
    assert t_window in removed
    assert t_window + 4 * step not in removed                         # 창이 구멍을 벗어나면 다시 평가


def test_class_hours_flag(settings):
    clean = clean_frame("2026-05-03T15:00Z", "2026-05-06T15:00Z", [("실습실", "co2")], lambda t, s, m: 500 + np.arange(len(t)))
    periods = [("train", "2026-05-01", "2026-05-02"), ("val", "2026-05-02", "2026-05-05"), ("test", "2026-05-05", "2026-05-07")]
    split = {"timezone": TZ, **{p: {"start": pd.Timestamp(a, tz=TZ), "end": pd.Timestamp(b, tz=TZ)} for p, a, b in periods}}
    points = bl.build_eval_points(clean, split, settings).set_index("time")
    kst = lambda text: pd.Timestamp(text, tz=TZ).tz_convert("UTC")
    assert not points.loc[kst("2026-05-05 10:00"), "weekday_class_hours"]     # 어린이날
    assert points.loc[kst("2026-05-06 10:00"), "weekday_class_hours"]          # 수요일 오전
    assert not points.loc[kst("2026-05-06 18:00"), "weekday_class_hours"]      # 18시는 끝(미포함)
    assert not points.loc[kst("2026-05-06 08:55"), "weekday_class_hours"]


def setup_root(tmp_path, settings):
    clean = clean_frame("2025-12-31T15:00Z", "2026-04-10T15:00Z", [("실습실", "co2"), ("실습실", "temperature"),
                                                                   ("회의실", "co2")],
                        lambda t, s, m: (22 if m == "temperature" else 600) + 50 * np.sin(np.arange(len(t)) / 30)
                        + (np.arange(len(t)) * 7919 % 13))
    (tmp_path / "data" / "processed").mkdir(parents=True)
    save_clean(clean, tmp_path / "data" / "processed" / "clean_5min.csv.gz")
    s = load_settings(ROOT / "config" / "preprocess.toml", root=tmp_path)
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "split.json").write_text(json.dumps(split_to_json(make_split(clean, s), s)), encoding="utf-8")
    return s


def test_same_input_same_points_and_mae(tmp_path, settings):
    s = setup_root(tmp_path, settings)
    assert bl.run(s) == 0
    points_first = (tmp_path / "artifacts" / "eval_points.csv").read_bytes()
    mae_first = (tmp_path / "data" / "processed" / "baseline_mae.csv").read_bytes()
    assert bl.run(s, rebuild_points=True) == 0                       # 다시 만들어도 같은 파일
    assert (tmp_path / "artifacts" / "eval_points.csv").read_bytes() == points_first
    assert (tmp_path / "data" / "processed" / "baseline_mae.csv").read_bytes() == mae_first
    report = (tmp_path / "docs" / "reports" / "002-baselines.md").read_text(encoding="utf-8")
    assert "측정 조건" in report and "현재값 유지" in report and "평일 실습 시간" in report


def test_existing_points_are_reused(tmp_path, settings):
    s = setup_root(tmp_path, settings)
    bl.run(s)
    points = bl.load_points(tmp_path / "artifacts" / "eval_points.csv")
    fewer = points.iloc[::2]
    bl.save_points(fewer, tmp_path / "artifacts" / "eval_points.csv")
    bl.run(s)                                                         # --rebuild-points 없으면 고정 목록 그대로
    mae = pd.read_csv(tmp_path / "data" / "processed" / "baseline_mae.csv")
    total = mae[(mae["space"] == "전체") & (mae["baseline"] == "persistence") & (mae["horizon_min"] == 5)]["n"].sum()
    assert total == len(fewer)
    assert bl.load_points(tmp_path / "artifacts" / "eval_points.csv").equals(fewer.reset_index(drop=True))
