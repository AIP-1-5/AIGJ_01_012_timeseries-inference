"""모델 비교표 (research.md R8, 완료 조건 1·2). 과제 2 기준선과 같은 평가 시점·같은 함수로 계산한다.

결과: docs/reports/003-model-comparison.md (자동, 다시 만들 때마다 덮어씀), data/forecast/compare.json.
해석은 docs/reports/003-forecast.md에 사람이 쓴다.
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from pipeline.baselines import BASELINE_LABELS, METRIC_LABELS, UNITS, conditions_block, mae_table
from pipeline.preprocess import clean_path, load_clean
from pipeline.split import load_split, split_path

from .evaluate import load_predictions, load_selection
from .select import candidates

TARGET_RATIO = 0.7


def load_baselines(settings):
    frame = pd.read_csv(settings.processed_dir / "baseline_predictions.csv.gz",
                        dtype={"space": str, "metric": str, "baseline": str})
    frame["time"] = pd.to_datetime(frame["time"], utc=True, format="ISO8601")
    frame["weekday_class_hours"] = frame["weekday_class_hours"].astype(str).str.lower().isin(["true", "1"])
    return frame


def method_label(name):
    return BASELINE_LABELS.get(name, name)


def mae_lookup(table, method, metric, horizon, space="전체"):
    row = table[(table["baseline"] == method) & (table["metric"] == metric) & (table["horizon_min"] == horizon)
                & (table["space"] == space)]
    return (float(row["mae"].iloc[0]), int(row["n"].iloc[0])) if len(row) else (float("nan"), 0)


def comparison_table(table, methods, settings, metric):
    horizons = settings.horizons
    lines = [f"#### {METRIC_LABELS.get(metric, metric)} MAE ({UNITS.get(metric, '')})", "",
             "| 공간 | 방법 | " + " | ".join(f"{h}분" for h in horizons) + " | 평가 시점 수 (" + "/".join(map(str, horizons)) + "분) |",
             "|---|---|" + "---:|" * len(horizons) + "---:|"]
    part = table[table["metric"] == metric]
    spaces = [s.space for s in settings.spaces if s.space in set(part["space"])] + ["전체"]
    for space in spaces:
        best = {h: part[(part["space"] == space) & (part["horizon_min"] == h) & part["baseline"].isin(methods)]["mae"].min()
                for h in horizons}
        for method in methods:
            cells, counts = [], []
            for h in horizons:
                mae, n = mae_lookup(table, method, metric, h, space)
                counts.append(f"{n:,}")
                if n == 0:
                    cells.append("-")
                    continue
                text = f"{mae:.1f}" if metric == "co2" else f"{mae:.3f}"
                cells.append(f"**{text}**" if np.isclose(mae, best[h]) else text)
            if all(c == "-" for c in cells):
                continue
            label = f"**{space}**" if space == "전체" else space
            lines.append(f"| {label} | {method_label(method)} | " + " | ".join(cells) + f" | {' / '.join(counts)} |")
    return "\n".join(lines) + "\n"


def variant_summary(table, runs, settings):
    """GRU 변화량 모델의 변형별 씨앗 평균·범위 (일정 정보의 가치)."""
    rows = []
    for variant in ("full", "no_schedule", "sensor_only"):
        names = [r["run_id"] for r in runs if r["kind"] == "gru" and r["target"] == "delta" and r["variant"] == variant]
        if not names:
            continue
        entry = {"variant": variant, "runs": names}
        for metric in ("co2", "temperature"):
            values = [mae_lookup(table, n, metric, 60)[0] for n in names]
            entry[f"{metric}_60_mean"] = float(np.nanmean(values))
            entry[f"{metric}_60_min"] = float(np.nanmin(values))
            entry[f"{metric}_60_max"] = float(np.nanmax(values))
        entry["by_space_co2_60"] = {s.space: float(np.nanmean([mae_lookup(table, n, "co2", 60, s.space)[0] for n in names]))
                                    for s in settings.spaces if "co2" in s.metrics}
        rows.append(entry)
    return rows


def build_report(fsettings, include_value=True):
    settings = fsettings.pipeline
    selection = load_selection(fsettings)
    runs = candidates(fsettings)
    available = [r for r in runs if (fsettings.predictions_dir / f"{r['run_id']}.csv.gz").exists()]
    if not available:
        raise FileNotFoundError("시험 예측 파일이 없다. 먼저 `evaluate --split test`를 실행한다")
    baselines = load_baselines(settings)
    model_preds = pd.concat([load_predictions(fsettings, r["run_id"]) for r in available], ignore_index=True)
    combined = pd.concat([baselines, model_preds], ignore_index=True)
    table = mae_table(combined)
    table_class = mae_table(combined, subset="weekday_class_hours")
    chosen = selection["chosen"]
    linear = next((r["run_id"] for r in available if r["kind"] == "linear"), None)
    methods = ["persistence", "mean_1h", "yesterday"] + ([linear] if linear else []) + [chosen]
    value_run = next((r["run_id"] for r in available if r["target"] == "value"), None) if include_value else None

    co2_model, _ = mae_lookup(table, chosen, "co2", 60)
    co2_base, n_co2 = mae_lookup(table, "persistence", "co2", 60)
    temp_model, _ = mae_lookup(table, chosen, "temperature", 60)
    temp_base, n_temp = mae_lookup(table, "persistence", "temperature", 60)
    criteria = {
        "co2": {"model": co2_model, "persistence": co2_base, "target": co2_base * TARGET_RATIO,
                "ratio": co2_model / co2_base, "pass": bool(co2_model <= co2_base * TARGET_RATIO), "n": n_co2},
        "temperature": {"model": temp_model, "persistence": temp_base, "ratio": temp_model / temp_base,
                        "pass": bool(temp_model < temp_base), "n": n_temp},
    }
    variants = variant_summary(table, available, settings)
    clean = load_clean(clean_path(settings))
    split = load_split(split_path(settings))
    meta = json.loads((fsettings.runs_dir / chosen / "config.json").read_text(encoding="utf-8"))
    lines = [
        "# 과제 3 모델 비교표",
        "",
        f"`python -m forecast compare`가 만든 표. 만든 시각 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. "
        "해석은 [003-forecast.md](003-forecast.md).",
        "",
        "## 측정 조건",
        "",
        conditions_block(settings, clean, split, {
            "평가 시점": "과제 2와 같은 `artifacts/eval_points.csv` (h별 표시). 정답은 관측값, 원래 단위",
            "최종 모델": f"`{chosen}` (검증 기간으로만 선택, {selection['decided_at']})",
            "학습 장비": f"{meta['machine']}, {meta['device']}, torch {meta['torch']}, 파라미터 {meta['parameters']:,}",
            "학습": f"학습 표본 {meta['train_samples']:,}, 검증 {meta['val_samples']:,}, 에폭 {meta['best_epoch']} 선택, {meta['minutes']}분",
        }),
        "",
        "## 완료 조건",
        "",
        "| 조건 | 고른 모델 | 현재값 유지 | 판정 |",
        "|---|---:|---:|---|",
        f"| 1. 60분 CO₂가 현재값 유지보다 30% 이상 작다 | {co2_model:.2f} ppm | {co2_base:.2f} ppm (목표 ≤ {co2_base * TARGET_RATIO:.2f}) | "
        f"{'통과' if criteria['co2']['pass'] else '미통과'} ({(1 - criteria['co2']['ratio']) * 100:.1f}% 감소) |",
        f"| 2. 60분 온도가 현재값 유지보다 좋다 | {temp_model:.3f} ℃ | {temp_base:.3f} ℃ | "
        f"{'통과' if criteria['temperature']['pass'] else '미통과'} ({(1 - criteria['temperature']['ratio']) * 100:.1f}% 감소) |",
        "| 3. 시험 기간을 학습·선택에 쓰지 않았다 | 선택 기록 → 시험 기록 순서 | [003-test-log.md](003-test-log.md) | 기록 확인 |",
        "",
        "## 전체 평가 시점",
        "",
        comparison_table(table, methods, settings, "co2"),
        comparison_table(table, methods, settings, "temperature"),
        "## 평일 수업 시간만 (09:00~18:00, 공휴일 제외)",
        "",
        comparison_table(table_class, methods, settings, "co2"),
        comparison_table(table_class, methods, settings, "temperature"),
    ]
    if variants:
        lines += ["## 일정 정보의 가치 (GRU, 변화량, 씨앗별 시험 60분 MAE)", "",
                  "| 변형 | 입력 | CO₂ 평균 (최소~최대) | 온도 평균 (최소~최대) | " +
                  " | ".join(f"{s.space} CO₂" for s in settings.spaces if "co2" in s.metrics) + " |",
                  "|---|---|---:|---:|" + "---:|" * sum(1 for s in settings.spaces if "co2" in s.metrics)]
        inputs = {"full": "센서 + 시각·요일 + 일정(시간표·공휴일)", "no_schedule": "센서 + 시각·요일", "sensor_only": "센서만"}
        for v in variants:
            lines.append(f"| `{v['variant']}` | {inputs[v['variant']]} | {v['co2_60_mean']:.2f} ({v['co2_60_min']:.2f}~{v['co2_60_max']:.2f}) | "
                         f"{v['temperature_60_mean']:.3f} ({v['temperature_60_min']:.3f}~{v['temperature_60_max']:.3f}) | " +
                         " | ".join(f"{v['by_space_co2_60'][s.space]:.1f}" for s in settings.spaces if "co2" in s.metrics) + " |")
        by_name = {v["variant"]: v for v in variants}
        if "full" in by_name and "no_schedule" in by_name:
            diff = by_name["no_schedule"]["co2_60_mean"] - by_name["full"]["co2_60_mean"]
            lines += ["", f"- 일정 정보(시간표·공휴일)를 넣으면 60분 CO₂ 오차가 평균 **{diff:+.2f} ppm** "
                          f"({'줄어든다' if diff > 0 else '줄지 않는다'}, `no_schedule` → `full`)"]
        if "no_schedule" in by_name and "sensor_only" in by_name:
            diff = by_name["sensor_only"]["co2_60_mean"] - by_name["no_schedule"]["co2_60_mean"]
            lines.append(f"- 시각·요일을 넣으면 60분 CO₂ 오차가 평균 **{diff:+.2f} ppm** ({'줄어든다' if diff > 0 else '줄지 않는다'}, `sensor_only` → `no_schedule`)")
        lines.append("")
    if value_run:
        lines += ["## 값 직접 예측 vs 변화량 예측 (같은 GRU·변형·씨앗)", "",
                  "| h | 변화량 (고른 모델) | 값 직접 | 현재값 유지 |", "|---:|---:|---:|---:|"]
        for h in settings.horizons:
            lines.append(f"| {h}분 | {mae_lookup(table, chosen, 'co2', h)[0]:.2f} | {mae_lookup(table, value_run, 'co2', h)[0]:.2f} | "
                         f"{mae_lookup(table, 'persistence', 'co2', h)[0]:.2f} |")
        lines.append("")
    lines += ["## 검증 기간 점수 (선택 근거)", "", "| 실행 | 검증 점수 | 검증 60분 CO₂ | 검증 60분 온도 |", "|---|---:|---:|---:|"]
    for r in sorted(runs, key=lambda r: r["val_score"]):
        mark = " ← 선택" if r["run_id"] == chosen else ""
        lines.append(f"| `{r['run_id']}`{mark} | {r['val_score']:.3f} | {r['val_co2_60']:.2f} | {r['val_temperature_60']:.3f} |")
    report = "\n".join(lines) + "\n"
    summary = {"chosen": chosen, "criteria": criteria, "variants": variants, "value_run": value_run,
               "table": table.to_dict(orient="records")}
    return report, summary, combined


def run(fsettings, log=print):
    report, summary, _ = build_report(fsettings)
    out = fsettings.reports_dir / "003-model-comparison.md"
    out.write_text(report, encoding="utf-8")
    fsettings.out_dir.mkdir(parents=True, exist_ok=True)
    (fsettings.out_dir / "compare.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    c = summary["criteria"]
    log(f"비교표 → {out}")
    log(f"완료 조건 1 (60분 CO₂ ≤ 현재값 유지 × 0.7): {c['co2']['model']:.2f} vs {c['co2']['persistence']:.2f} ppm → "
        f"{'통과' if c['co2']['pass'] else '미통과'}")
    log(f"완료 조건 2 (60분 온도 < 현재값 유지): {c['temperature']['model']:.3f} vs {c['temperature']['persistence']:.3f} ℃ → "
        f"{'통과' if c['temperature']['pass'] else '미통과'}")
    for v in summary["variants"]:
        log(f"  {v['variant']:<12} CO₂60 {v['co2_60_mean']:.2f} ({v['co2_60_min']:.2f}~{v['co2_60_max']:.2f}), 온도60 {v['temperature_60_mean']:.3f}")
    return 0
