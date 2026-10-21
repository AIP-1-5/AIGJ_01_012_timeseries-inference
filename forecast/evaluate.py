"""평가 (research.md R6·R8). 시험 기간은 최종 모델을 고른 뒤(selection.json이 있을 때)에만 평가한다.

시험 평가는 과제 2의 평가 시점(artifacts/eval_points.csv)의 (공간, 시각, h)만 쓰고,
정답·현재값은 과제 2와 같은 관측값(원래 단위)으로 계산한다. 결과 열은 과제 2 baseline_predictions와 같다.
"""

import hashlib
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from pipeline.baselines import horizon_column, load_points, points_path

from .settings import TARGET_METRICS
from .train import delta_metrics, load_model, pick_device, predict_delta

LOG_HEADER = ("# 과제 3 시험 기간 평가 기록\n\n"
              "최종 모델을 검증 기간으로 고른 뒤(`data/forecast/selection.json`)에만 시험 기간을 평가한다. "
              "같은 모델(같은 해시)을 다시 평가하면 새 줄을 쓰지 않는다.\n\n"
              "| 평가 시각 (UTC) | 실행 | 모델 sha256 앞 12자 | 60분 CO₂ MAE (ppm) | 60분 온도 MAE (℃) | 선택 시각 (UTC) |\n"
              "|---|---|---|---:|---:|---|\n")


class SelectionMissing(Exception):
    """selection.json 없이 시험 평가를 시도함 (종료 코드 2)."""


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_selection(fsettings):
    if not fsettings.selection_path.exists():
        raise SelectionMissing("시험 평가 전에 `select`로 검증 기간 기준 최종 모델을 먼저 골라야 한다 (data/forecast/selection.json 없음)")
    return json.loads(fsettings.selection_path.read_text(encoding="utf-8"))


def test_predictions(samples, delta, points, horizons, run_name):
    """시험 표본 예측 → 평가 시점별 행 (space, metric, time, horizon_min, baseline, y_true, y_pred, weekday_class_hours)."""
    positions = samples.positions["test"]
    key = pd.DataFrame({
        "space": np.asarray(samples.spaces)[samples.space_id[positions]],
        "time": pd.to_datetime(samples.times[positions]).tz_localize("UTC"),
        "row": np.arange(len(positions)),
    })
    merged = points.merge(key, on=["space", "time"], how="inner")
    parts = []
    for horizon in horizons:
        k = horizon // 5 - 1
        rows = merged[merged[horizon_column(horizon)]]
        if rows.empty:
            continue
        for j, metric in enumerate(TARGET_METRICS):
            part = rows[rows["metric"] == metric]
            if part.empty:
                continue
            r = part["row"].to_numpy()
            pos = positions[r]
            sid = samples.space_id[pos]
            y_now = samples.y[pos, j]
            parts.append(pd.DataFrame({
                "space": part["space"].to_numpy(), "metric": metric, "time": part["time"].to_numpy(),
                "horizon_min": horizon, "baseline": run_name,
                "y_true": samples.y[pos + k + 1, j],
                "y_pred": y_now + delta[r, k, j] * samples.std[sid, j],
                "weekday_class_hours": part["weekday_class_hours"].to_numpy(),
            }))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def append_test_log(fsettings, run_name, digest, table, selection):
    path = fsettings.reports_dir / "003-test-log.md"
    text = path.read_text(encoding="utf-8") if path.exists() else LOG_HEADER
    if digest[:12] in text:
        return False
    overall = table[table["space"] == "전체"].set_index(["metric", "horizon_min"])["mae"]
    co2 = overall.get(("co2", 60), float("nan"))
    temp = overall.get(("temperature", 60), float("nan"))
    text += (f"| {datetime.now(timezone.utc):%Y-%m-%d %H:%M} | `{run_name}` | `{digest[:12]}` | {co2:.2f} | {temp:.3f} | "
             f"{selection['decided_at']} |\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def evaluate_run(fsettings, samples, run_name, split="val", points=None, log=print):
    """한 실행 평가. val이면 검증 지표(dict), test면 예측 DataFrame을 돌려준다."""
    from pipeline.baselines import mae_table

    run_dir = fsettings.runs_dir / run_name
    device = pick_device(fsettings.device)
    model, meta = load_model(run_dir, device)
    positions = samples.positions[split]
    delta = predict_delta(model, samples, positions, device=device)
    if split == "val":
        metrics = delta_metrics(samples, positions, delta)
        co2, temp = metrics["overall"]["co2"]["60"], metrics["overall"]["temperature"]["60"]
        log(f"[{run_name}] 검증 60분: CO₂ {co2['mae']:.2f} vs 현재값 유지 {co2['persistence']:.2f} ppm, "
            f"온도 {temp['mae']:.3f} vs {temp['persistence']:.3f} ℃ (점수 {metrics['score']:.3f})")
        return metrics
    selection = load_selection(fsettings)
    points = points if points is not None else load_points(points_path(fsettings.pipeline))
    predictions = test_predictions(samples, delta, points, fsettings.pipeline.horizons, run_name)
    fsettings.predictions_dir.mkdir(parents=True, exist_ok=True)
    out = predictions.copy()
    out["time"] = pd.DatetimeIndex(out["time"]).strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(fsettings.predictions_dir / f"{run_name}.csv.gz", index=False, float_format="%.4f",
               compression={"method": "gzip", "compresslevel": 1})
    table = mae_table(predictions)
    written = append_test_log(fsettings, run_name, sha256(run_dir / "model.pt"), table, selection)
    overall = table[table["space"] == "전체"].set_index(["metric", "horizon_min"])
    log(f"[{run_name}] 시험 60분: CO₂ {overall.loc[('co2', 60), 'mae']:.2f} ppm, 온도 {overall.loc[('temperature', 60), 'mae']:.3f} ℃ "
        f"({'기록함' if written else '이미 기록된 모델, 새 줄 없음'})")
    return predictions


def load_predictions(fsettings, run_name):
    path = fsettings.predictions_dir / f"{run_name}.csv.gz"
    frame = pd.read_csv(path, dtype={"space": str, "metric": str, "baseline": str})
    frame["time"] = pd.to_datetime(frame["time"], utc=True, format="ISO8601")
    frame["weekday_class_hours"] = frame["weekday_class_hours"].astype(str).str.lower().isin(["true", "1"])
    return frame
