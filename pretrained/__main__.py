"""과제 6: 사전학습 시계열 모델(Chronos-Bolt) 비교. 추가 학습 없음.

  python -m pretrained predict   CO₂ 60분 예측 (검증 기간으로 과거 길이 고르기 → 시험 기간 전부)
  python -m pretrained bench     보드에서 1회 추론 시간·메모리 (배치 1·4)
  python -m pretrained report    기준선 · 직접 학습 모델 · 사전학습 모델 비교표 → docs/reports/006-comparison.md

같은 평가 시점(artifacts/eval_points.csv), 같은 정답(관측값)으로 비교한다.
"""

import argparse
import json
import os
import platform
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.baselines import (build_eval_points, conditions_block, horizon_column, load_points, mae_table, masks,
                                points_path, regular_series)
from pipeline.preprocess import clean_path, load_clean
from pipeline.settings import load_settings
from pipeline.split import load_split, split_path

QUANTILES = [0.1, 0.5, 0.9]
STEPS = 12
DEFAULT_MODELS = ["amazon/chronos-bolt-tiny", "amazon/chronos-bolt-small"]
DEFAULT_CONTEXTS = [48, 288, 576]          # 4시간, 1일, 2일 (5분 칸)


def short(model_id):
    return model_id.split("/")[-1]


def co2_series(clean, settings):
    """공간별 CO₂ 격자: 입력값(보간 허용, 고장·결측 NaN)과 정답값(관측만)."""
    out = {}
    for spec in settings.spaces:
        if "co2" not in spec.metrics:
            continue
        frame = regular_series(clean, spec.space, "co2", settings.interval_minutes)
        if frame.empty:
            continue
        observed, usable = masks(frame)
        out[spec.space] = (frame["value"].where(usable), frame["value"].where(observed))
    return out


def load_pipeline(model_id, device="cpu"):
    import torch
    from chronos import BaseChronosPipeline

    return BaseChronosPipeline.from_pretrained(model_id, device_map=device, torch_dtype=torch.float32)


def predict_points(pipeline, series, points, context, horizons, name, batch_size=256):
    """평가 시점마다 Chronos 예측 → 과제 2 형식 행 + q10·q90."""
    import torch

    rows = []
    co2_points = points[points["metric"] == "co2"]
    for space, group in co2_series_groups(co2_points):
        values, truth = series[space]
        index = values.index
        loc = index.get_indexer(pd.DatetimeIndex(group["time"]))
        keep = loc >= 0
        group, loc = group[keep], loc[keep]
        arr = values.to_numpy(dtype=np.float32)
        tru = truth.to_numpy(dtype=np.float64)
        for start in range(0, len(loc), batch_size):
            part = loc[start:start + batch_size]
            ctx = np.full((len(part), context), np.nan, np.float32)
            for i, p in enumerate(part):
                window = arr[max(0, p - context + 1):p + 1]
                ctx[i, context - len(window):] = window
            quantiles, _ = pipeline.predict_quantiles(torch.from_numpy(ctx), prediction_length=STEPS, quantile_levels=QUANTILES)
            q = quantiles.numpy()
            sub = group.iloc[start:start + batch_size]
            for horizon in horizons:
                flag = sub[horizon_column(horizon)].to_numpy()
                if not flag.any():
                    continue
                k = horizon // 5 - 1
                target = np.clip(part + k + 1, 0, len(tru) - 1)
                rows.append(pd.DataFrame({
                    "space": space, "metric": "co2", "time": sub["time"].to_numpy()[flag], "horizon_min": horizon,
                    "baseline": name, "y_true": tru[target][flag], "y_pred": q[flag, k, 1],
                    "q10": q[flag, k, 0], "q90": q[flag, k, 2],
                    "weekday_class_hours": sub["weekday_class_hours"].to_numpy()[flag],
                }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def co2_series_groups(points):
    return [(space, group.sort_values("time").reset_index(drop=True)) for space, group in points.groupby("space", sort=True)]


def val_points(clean, split, settings, every=3):
    """검증 기간 평가 시점 (과거 길이 고르기용, 시험 기간을 보지 않으려고). 계산량 때문에 3칸마다."""
    fake = dict(split)
    fake["test"] = split["val"]
    points = build_eval_points(clean, fake, settings)
    return points[points["metric"] == "co2"].iloc[::every].reset_index(drop=True)


def cmd_predict(args, root):
    settings = load_settings(root / "config" / "preprocess.toml", root=root)
    clean = load_clean(clean_path(settings))
    split = load_split(split_path(settings))
    series = co2_series(clean, settings)
    out = root / "data" / "pretrained"
    out.mkdir(parents=True, exist_ok=True)
    vpoints = val_points(clean, split, settings)
    tpoints = load_points(points_path(settings))
    selection = {"models": {}, "decided_at": None}
    for model_id in args.models:
        pipeline = load_pipeline(model_id, args.device)
        val_scores = {}
        for context in args.contexts:
            t0 = time.monotonic()
            preds = predict_points(pipeline, series, vpoints, context, [60], f"{short(model_id)}-L{context}")
            mae = float((preds["y_pred"] - preds["y_true"]).abs().mean())
            val_scores[context] = mae
            print(f"[검증] {short(model_id)} 과거 {context}칸: 60분 CO₂ MAE {mae:.2f} ppm ({len(preds):,}개, {time.monotonic() - t0:.0f}초)")
        best = min(val_scores, key=val_scores.get)
        selection["models"][model_id] = {"val_mae_60": val_scores, "chosen_context": best}
        print(f"  → {short(model_id)}: 과거 {best}칸 선택 (검증 기준)")
        for context in args.contexts:
            t0 = time.monotonic()
            preds = predict_points(pipeline, series, tpoints, context, settings.horizons, f"{short(model_id)}-L{context}")
            path = out / f"{short(model_id)}-L{context}.csv.gz"
            saved = preds.copy()
            saved["time"] = pd.DatetimeIndex(saved["time"]).strftime("%Y-%m-%dT%H:%M:%SZ")
            saved.to_csv(path, index=False, float_format="%.3f", compression={"method": "gzip", "compresslevel": 1})
            print(f"[시험] {short(model_id)} 과거 {context}칸 → {path.name} ({len(preds):,}행, {time.monotonic() - t0:.0f}초)")
    selection["decided_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (out / "selection.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def rss_mb():
    with open("/proc/self/status") as fp:
        for line in fp:
            if line.startswith("VmRSS"):
                return int(line.split()[1]) / 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def timeit(fn, warmup=5, repeat=50):
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return {"mean_ms": float(np.mean(times)), "p95_ms": float(np.percentile(times, 95)), "repeat": repeat, "warmup": warmup}


def cmd_bench(args, root):
    import torch

    torch.set_num_threads(os.cpu_count() or 4)
    out = root / "data" / "pretrained"
    out.mkdir(parents=True, exist_ok=True)
    selection_path = out / "selection.json"
    chosen = json.loads(selection_path.read_text(encoding="utf-8"))["models"] if selection_path.exists() else {}
    results = {"machine": f"{platform.node()} {platform.system()} {platform.machine()}", "torch": torch.__version__,
               "threads": torch.get_num_threads(), "device": args.device, "models": {},
               "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        results["power_mode"] = os.popen("nvpmodel -q 2>/dev/null | head -1").read().strip() or "확인 불가"
    except OSError:
        results["power_mode"] = "확인 불가"
    for model_id in args.models:
        before = rss_mb()
        t0 = time.monotonic()
        pipeline = load_pipeline(model_id, args.device)
        load_s = time.monotonic() - t0
        context = chosen.get(model_id, {}).get("chosen_context", 288)
        entry = {"context": context, "load_seconds": round(load_s, 1), "rss_after_load_mb": round(rss_mb(), 1),
                 "rss_increase_mb": round(rss_mb() - before, 1),
                 "parameters": int(sum(p.numel() for p in pipeline.model.parameters()))}
        for batch in (1, 4):
            ctx = torch.full((batch, context), 600.0) + torch.randn(batch, context) * 20
            entry[f"batch{batch}"] = timeit(lambda: pipeline.predict_quantiles(ctx, prediction_length=STEPS, quantile_levels=QUANTILES))
        entry["rss_peak_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        results["models"][model_id] = entry
        print(f"{short(model_id)}: 파라미터 {entry['parameters']:,}, 메모리 +{entry['rss_increase_mb']} MB, "
              f"배치1 {entry['batch1']['mean_ms']:.1f} ms (p95 {entry['batch1']['p95_ms']:.1f}), 배치4 {entry['batch4']['mean_ms']:.1f} ms")
        del pipeline
    # 과제 3 GRU (같은 장비, CPU) 비교용
    try:
        from forecast.model import build_model

        model = build_model("gru", 24, 12, 6, 4).eval()
        with torch.no_grad():
            for batch in (1, 4):
                inputs = (torch.zeros(batch, 24, 12), torch.zeros(batch, 12, 6), torch.zeros(batch, 4))
                results.setdefault("gru", {})[f"batch{batch}"] = timeit(lambda: model(*inputs))
        print(f"GRU(과제 3): 배치1 {results['gru']['batch1']['mean_ms']:.2f} ms")
    except Exception as exc:  # 비교용이라 실패해도 계속
        results["gru_error"] = str(exc)
    (out / "bench.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_report(args, root):
    settings = load_settings(root / "config" / "preprocess.toml", root=root)
    clean = load_clean(clean_path(settings))
    split = load_split(split_path(settings))
    out = root / "data" / "pretrained"
    selection = json.loads((out / "selection.json").read_text(encoding="utf-8"))
    base = pd.read_csv(settings.processed_dir / "baseline_predictions.csv.gz", dtype={"space": str, "metric": str, "baseline": str})
    base = base[base["metric"] == "co2"]
    frames = [base]
    trained_name = None
    forecast_sel = root / "data" / "forecast" / "selection.json"
    if forecast_sel.exists():
        trained_name = json.loads(forecast_sel.read_text(encoding="utf-8"))["chosen"]
        path = root / "data" / "forecast" / "predictions" / f"{trained_name}.csv.gz"
        if path.exists():
            trained = pd.read_csv(path, dtype={"space": str, "metric": str, "baseline": str})
            frames.append(trained[trained["metric"] == "co2"])
    coverage = {}
    names = []
    for path in sorted(out.glob("*-L*.csv.gz")):
        pred = pd.read_csv(path, dtype={"space": str, "metric": str, "baseline": str})
        frames.append(pred.drop(columns=["q10", "q90"]))
        name = pred["baseline"].iloc[0]
        names.append(name)
        inside = (pred["y_true"] >= pred["q10"]) & (pred["y_true"] <= pred["q90"])
        coverage[name] = pred.assign(inside=inside).groupby("horizon_min")["inside"].mean().round(3).to_dict()
    combined = pd.concat(frames, ignore_index=True)
    combined["time"] = pd.to_datetime(combined["time"], utc=True, format="ISO8601")
    combined["weekday_class_hours"] = combined["weekday_class_hours"].astype(str).str.lower().isin(["true", "1"])
    table = mae_table(combined)
    overall = table[table["space"] == "전체"].set_index(["baseline", "horizon_min"])
    bench_path = out / "bench.json"
    bench = json.loads(bench_path.read_text(encoding="utf-8")) if bench_path.exists() else {"models": {}}
    chosen = {f"{short(m)}-L{v['chosen_context']}": m for m, v in selection["models"].items()}
    methods = ["persistence", "mean_1h", "yesterday"] + ([trained_name] if trained_name else []) + names
    labels = {"persistence": "현재값 유지", "mean_1h": "최근 1시간 평균", "yesterday": "어제 같은 시각"}
    lines = [
        "# 과제 6 사전학습 모델 비교표",
        "",
        f"`python -m pretrained report`가 만든 표. 만든 시각 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. 해석·채택 의견은 [006-pretrained.md](006-pretrained.md).",
        "",
        "## 측정 조건",
        "",
        conditions_block(settings, clean, split, {
            "사전학습 모델": ", ".join(selection["models"]) + " (추가 학습 없음, CO₂ 과거값만 입력)",
            "과거 길이 선택": "검증 기간 60분 MAE로 선택: " + ", ".join(f"{short(m)} → {v['chosen_context']}칸" for m, v in selection["models"].items()),
            "추론 측정 장비": f"{bench.get('machine', '-')}, torch {bench.get('torch', '-')}, {bench.get('device', '-')}, 전력 모드 {bench.get('power_mode', '-')}",
        }),
        "",
        "## 정확도 (시험 기간 CO₂ MAE, ppm, 같은 평가 시점)",
        "",
        "| 방법 | " + " | ".join(f"{h}분" for h in settings.horizons) + " | 60분 평가 시점 수 |",
        "|---|" + "---:|" * len(settings.horizons) + "---:|",
    ]
    for method in methods:
        cells = []
        for h in settings.horizons:
            cells.append(f"{overall.loc[(method, h), 'mae']:.1f}" if (method, h) in overall.index else "-")
        n = int(overall.loc[(method, 60), "n"]) if (method, 60) in overall.index else 0
        mark = " (검증 선택)" if method in chosen else ""
        label = labels.get(method, f"직접 학습 `{method}`" if method == trained_name else f"`{method}`{mark}")
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {n:,} |")
    lines += ["", "## 예측 구간 적중률 (10~90% 구간 안에 실제값이 든 비율, 이상적 80%)", "",
              "| 모델 | " + " | ".join(f"{h}분" for h in settings.horizons) + " |", "|---|" + "---:|" * len(settings.horizons)]
    for name, cov in coverage.items():
        lines.append(f"| `{name}` | " + " | ".join(f"{cov.get(h, cov.get(str(h), float('nan'))):.0%}" for h in settings.horizons) + " |")
    lines += ["", "## 비용 (보드 B, 1회 추론)", "", "| 모델 | 파라미터 | 과거 길이 | 메모리 증가 | 배치 1 평균 / p95 | 배치 4 평균 / p95 | 불러오기 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for model_id, e in bench.get("models", {}).items():
        lines.append(f"| {short(model_id)} | {e['parameters']:,} | {e['context']} | {e['rss_increase_mb']:.0f} MB | "
                     f"{e['batch1']['mean_ms']:.1f} / {e['batch1']['p95_ms']:.1f} ms | {e['batch4']['mean_ms']:.1f} / {e['batch4']['p95_ms']:.1f} ms | {e['load_seconds']} 초 |")
    if "gru" in bench:
        g = bench["gru"]
        lines.append(f"| GRU (과제 3) | 약 6만 | 24 | - | {g['batch1']['mean_ms']:.2f} / {g['batch1']['p95_ms']:.2f} ms | {g['batch4']['mean_ms']:.2f} / {g['batch4']['p95_ms']:.2f} ms | - |")
    lines += ["", "## 과거 길이별 검증 60분 MAE (ppm)", "", "| 모델 | " + " | ".join(f"{c}칸" for c in DEFAULT_CONTEXTS) + " |",
              "|---|" + "---:|" * len(DEFAULT_CONTEXTS)]
    for model_id, v in selection["models"].items():
        lines.append(f"| {short(model_id)} | " + " | ".join(f"{v['val_mae_60'].get(str(c), v['val_mae_60'].get(c, float('nan'))):.1f}" for c in DEFAULT_CONTEXTS) + " |")
    (settings.reports_dir / "006-comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = {"overall": table[table["space"] == "전체"].to_dict(orient="records"), "coverage": coverage, "bench": bench}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"비교표 → {settings.reports_dir / '006-comparison.md'}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m pretrained", description="과제 6 사전학습 모델 비교")
    parser.add_argument("command", choices=["predict", "bench", "report", "all"])
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--contexts", nargs="+", type=int, default=DEFAULT_CONTEXTS)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    steps = {"predict": [cmd_predict], "bench": [cmd_bench], "report": [cmd_report],
             "all": [cmd_predict, cmd_bench, cmd_report]}[args.command]
    for step in steps:
        code = step(args, root)
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
