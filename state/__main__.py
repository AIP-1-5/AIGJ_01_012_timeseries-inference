"""CLI: python -m state run  (./state.sh run)

사용 여부·환기 경보 분류기를 학습(학습 기간) → 임계값(검증 기간) → 시험 기간 한 번 평가 → 성적표.
결과: docs/reports/005-classification.md, figures/005-*.png, data/state/ (모델·예측, 커밋 안 함)
"""

import argparse
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

from anomaly.data import space_frames
from pipeline.baselines import conditions_block
from pipeline.preprocess import clean_path, load_clean
from pipeline.settings import load_schedule, load_settings
from pipeline.split import assign_period, load_split, split_path

from .features import (CALENDAR_FEATURES, SENSOR_FEATURES, calendar_frame, load_observations, observation_labels,
                       occupancy_labels, sensor_features, ventilation_labels)

OCC_FEATURES = SENSOR_FEATURES                                   # 일정·시각 없음 (spec)
VENT_FEATURES = SENSOR_FEATURES + CALENDAR_FEATURES
RECALL_TARGET = 0.85


def build_table(clean, settings, schedule, split, observations):
    frames = space_frames(clean, settings)
    parts = []
    for space, frame in frames.items():
        feats = sensor_features(frame)
        cal = calendar_frame(frame.index, space, schedule, settings.timezone)
        table = pd.concat([feats, cal], axis=1)
        table["space"] = space
        table["time"] = frame.index
        table["period"] = assign_period(pd.Series(frame.index, index=frame.index), split).to_numpy()
        table["occupied"] = occupancy_labels(frame.index, space, schedule, settings.timezone).to_numpy()
        table["observed_occupied"] = observation_labels(frame.index, space, observations, settings.timezone).to_numpy()
        table["vent"] = ventilation_labels(frame["co2"]).to_numpy()
        parts.append(table.reset_index(drop=True))
    return pd.concat(parts, ignore_index=True)


def metrics(y_true, y_pred):
    y_true, y_pred = np.asarray(y_true).astype(int), np.asarray(y_pred).astype(int)
    if len(y_true) == 0:
        return None
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = cm.ravel()
    return {
        "n": int(len(y_true)), "positive_rate": float(y_true.mean()),
        "accuracy": float((tp + tn) / len(y_true)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "false_occupied_rate": float(fp / (fp + tn)) if (fp + tn) else None,
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "always_negative_accuracy": float(1 - y_true.mean()),
    }


def fit(table, features, label, seed=1):
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=31, l2_regularization=1.0,
                                           class_weight="balanced", random_state=seed, early_stopping=False)
    model.fit(table[features], table[label].astype(int))
    return model


def choose_threshold(prob, y, rule):
    """rule='f1': F1 최대. rule='recall': 재현율 ≥ 0.85 중 macro-F1 최대(없으면 재현율 최대)."""
    rows = []
    for threshold in np.round(np.arange(0.05, 0.96, 0.01), 2):
        pred = prob >= threshold
        rows.append({"threshold": float(threshold), "recall": recall_score(y, pred, zero_division=0),
                     "f1": f1_score(y, pred, zero_division=0), "macro_f1": f1_score(y, pred, average="macro", zero_division=0)})
    table = pd.DataFrame(rows)
    if rule == "f1":
        best = table.sort_values(["f1", "threshold"], ascending=[False, False]).iloc[0]
    else:
        ok = table[table["recall"] >= RECALL_TARGET]
        best = (ok.sort_values(["macro_f1", "threshold"], ascending=[False, False]).iloc[0] if len(ok)
                else table.sort_values(["recall", "threshold"], ascending=[False, True]).iloc[0])
    return float(best["threshold"]), table


def hour_errors(rows, pred, label, tz):
    local = pd.DatetimeIndex(rows["time"]).tz_convert(tz) - pd.Timedelta(minutes=5)
    frame = pd.DataFrame({"hour": local.hour, "fp": (pred == 1) & (rows[label] == 0), "fn": (pred == 0) & (rows[label] == 1)})
    return frame.groupby("hour")[["fp", "fn"]].sum().astype(int)


def cm_text(m):
    c = m["confusion"]
    return (f"| | 예측 0 | 예측 1 |\n|---|---:|---:|\n| 실제 0 | {c['tn']:,} | {c['fp']:,} |\n| 실제 1 | {c['fn']:,} | {c['tp']:,} |")


def metric_row(name, m):
    if m is None:
        return f"| {name} | - | - | - | - | - | - | - |"
    fo = f"{m['false_occupied_rate']:.1%}" if m["false_occupied_rate"] is not None else "-"
    return (f"| {name} | {m['n']:,} | {m['accuracy']:.1%} | {m['precision']:.1%} | {m['recall']:.1%} | {m['f1']:.3f} | "
            f"{m['macro_f1']:.3f} | {fo} |")


def run(config_dir, log=print):
    root = config_dir.resolve().parent
    settings = load_settings(config_dir / "preprocess.toml", root=root)
    schedule = load_schedule(config_dir / "schedule.toml")
    clean = load_clean(clean_path(settings))
    split = load_split(split_path(settings))
    observations = load_observations(root / "docs" / "observations" / "occupancy.csv")
    out = root / "data" / "state"
    out.mkdir(parents=True, exist_ok=True)
    table = build_table(clean, settings, schedule, split, observations)
    tz = settings.timezone
    result = {"decided_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    # 사용 여부 (실습실 일정 기반 정답, 센서 특성만)
    occ = table[table["occupied"].notna()]
    train, val, test = (occ[occ["period"] == p] for p in ("train", "val", "test"))
    occ_model = fit(train, OCC_FEATURES, "occupied")
    occ_thr, occ_sweep = choose_threshold(occ_model.predict_proba(val[OCC_FEATURES])[:, 1], val["occupied"].astype(int), "f1")
    occ_pred = (occ_model.predict_proba(test[OCC_FEATURES])[:, 1] >= occ_thr).astype(int)
    occ_rule = (test["co2_d15"] >= 30).astype(int).to_numpy()
    result["occupancy"] = {
        "threshold": occ_thr, "train_rows": int(len(train)), "val_rows": int(len(val)),
        "test_model": metrics(test["occupied"], occ_pred), "test_rule": metrics(test["occupied"], occ_rule),
        "hour_errors_model": hour_errors(test, occ_pred, "occupied", tz).to_dict(orient="index"),
    }
    observed = table[table["observed_occupied"].notna()]
    if len(observed):
        prob = occ_model.predict_proba(observed[OCC_FEATURES].fillna(np.nan))[:, 1]
        result["occupancy"]["observed_model"] = metrics(observed["observed_occupied"], (prob >= occ_thr).astype(int))
        result["occupancy"]["observed_rule"] = metrics(observed["observed_occupied"], (observed["co2_d15"] >= 30).astype(int))
        sched = observed["occupied"]
        both = sched.notna()
        result["occupancy"]["schedule_vs_observed"] = metrics(observed.loc[both, "observed_occupied"], sched[both]) if both.any() else None
    # 공간별 추정 사용률 (정답 없는 공간에도 적용, 시험 기간 평일 시각별)
    test_all = table[(table["period"] == "test") & table["co2"].notna()]
    usage = test_all.assign(p=occ_model.predict_proba(test_all[OCC_FEATURES])[:, 1] >= occ_thr)
    local = pd.DatetimeIndex(usage["time"]).tz_convert(tz) - pd.Timedelta(minutes=5)
    usage = usage.assign(hour=local.hour, weekday=local.weekday < 5)
    result["occupancy"]["estimated_usage_by_hour"] = (usage[usage["weekday"]].groupby(["space", "hour"])["p"].mean()
                                                      .unstack(0).round(3).to_dict())

    # 환기 경보 (CO₂ 있는 공간, 센서 + 달력·일정)
    vent = table[table["vent"].notna()]
    train, val, test = (vent[vent["period"] == p] for p in ("train", "val", "test"))
    vent_model = fit(train, VENT_FEATURES, "vent")
    vent_thr, vent_sweep = choose_threshold(vent_model.predict_proba(val[VENT_FEATURES])[:, 1], val["vent"].astype(int), "recall")
    vent_prob = vent_model.predict_proba(test[VENT_FEATURES])[:, 1]
    vent_pred = (vent_prob >= vent_thr).astype(int)
    vent_rule = (test["co2"] >= 850).astype(int).to_numpy()
    result["ventilation"] = {
        "threshold": vent_thr, "train_rows": int(len(train)), "train_positive_rate": float(train["vent"].mean()),
        "test_model": metrics(test["vent"], vent_pred), "test_rule": metrics(test["vent"], vent_rule),
        "by_space": {space: {"model": metrics(test.loc[test["space"] == space, "vent"], vent_pred[(test["space"] == space).to_numpy()]),
                             "rule": metrics(test.loc[test["space"] == space, "vent"], vent_rule[(test["space"] == space).to_numpy()])}
                     for space in sorted(test["space"].unique())},
        "hour_errors_model": hour_errors(test, vent_pred, "vent", tz).to_dict(orient="index"),
        "val_sweep": vent_sweep.round(4).to_dict(orient="records"),
    }
    misses = test[(vent_pred == 0) & (test["vent"] == 1).to_numpy()]
    result["ventilation"]["missed_examples"] = [
        {"space": r.space, "time_kst": pd.Timestamp(r.time).tz_convert(tz).strftime("%m-%d(%a) %H:%M"),
         "co2": round(float(r.co2), 0), "co2_d15": round(float(r.co2_d15), 0) if not np.isnan(r.co2_d15) else None}
        for r in misses.head(15).itertuples()]
    with (out / "models.pkl").open("wb") as fp:
        pickle.dump({"occupancy": occ_model, "occupancy_threshold": occ_thr, "ventilation": vent_model,
                     "ventilation_threshold": vent_thr, "occupancy_features": OCC_FEATURES, "ventilation_features": VENT_FEATURES}, fp)
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(root, settings, clean, split, result, observations, occ_sweep, vent_sweep)
    o, v = result["occupancy"]["test_model"], result["ventilation"]["test_model"]
    log(f"사용 여부 (시험, 일정 기준): 모델 F1 {o['f1']:.3f} vs 규칙 {result['occupancy']['test_rule']['f1']:.3f}, "
        f"비어있음→사용중 {o['false_occupied_rate']:.1%}")
    log(f"환기 경보 (시험): 재현율 {v['recall']:.1%}, macro-F1 {v['macro_f1']:.3f} vs 규칙 {result['ventilation']['test_rule']['macro_f1']:.3f}")
    return 0


def write_report(root, settings, clean, split, result, observations, occ_sweep, vent_sweep):
    import matplotlib.pyplot as plt

    from pipeline.diagnose import SERIES, SURFACE, TEXT, TEXT_SECONDARY, setup_font, style_axes

    korean = setup_font()
    usage = pd.DataFrame(result["occupancy"]["estimated_usage_by_hour"])
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    for color, space in zip(SERIES, usage.columns):
        ax.plot(usage.index.astype(int), usage[space] * 100, color=color, linewidth=2,
                label=space if korean else settings.slug(space))
    ax.set_xlim(0, 23)
    ax.set_xlabel("시각 (KST)" if korean else "hour", color=TEXT_SECONDARY, fontsize=9)
    ax.set_ylabel("사용중 판정 비율 (%)" if korean else "occupied (%)", color=TEXT_SECONDARY, fontsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)
    ax.set_title("센서만으로 추정한 평일 사용률 (시험 기간, 실습실로 학습)" if korean else "estimated weekday usage",
                 color=TEXT, fontsize=11, loc="left")
    fig.tight_layout()
    figure = settings.reports_dir / "figures" / "005-usage-by-hour.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure, dpi=130, facecolor=SURFACE)
    plt.close(fig)

    occ, vent = result["occupancy"], result["ventilation"]
    o, orule, v, vrule = occ["test_model"], occ["test_rule"], vent["test_model"], vent["test_rule"]
    obs = occ.get("observed_model")
    header = "| 방법 | 표본 | 정확도 | 정밀도 | 재현율 | F1 | macro-F1 | 비어있음→사용중 |\n|---|---:|---:|---:|---:|---:|---:|---:|"
    lines = [
        "# 과제 5 분류 성적표",
        "",
        f"`python -m state run`이 만든 표. 만든 시각 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. 해석은 [005-state.md](005-state.md).",
        "",
        "## 측정 조건",
        "",
        conditions_block(settings, clean, split, {
            "사용 여부 정답": "실습실 일정(평일 09:00~11:30·13:00~18:00, 공휴일 제외). 점심·일정 변화 직후 15분은 제외",
            "사용 여부 입력": "센서만 (CO₂·온도·습도 수준과 변화). 시각·요일·일정 없음",
            "환기 경보 정답": "지금 < 1,000 ppm이고 30분 안 ≥ 1,000 ppm. 이미 1,000 이상인 시각 제외",
            "모델": f"HistGradientBoosting(균형 가중치). 임계값은 검증 기간 (사용 여부 {occ['threshold']:.2f}: F1 최대, 환기 {vent['threshold']:.2f}: 재현율 ≥ 85% 중 macro-F1 최대)",
            "직접 관찰": f"{len(observations)}구간" if observations is not None else "아직 없음 (`docs/observations/occupancy.csv`)",
        }),
        "",
        "## 완료 조건",
        "",
        "| 조건 | 결과 | 판정 |",
        "|---|---|---|",
        (f"| 1. 사용 여부: 관찰 시간대 정확도·F1, 비어있음→사용중 오류율, 규칙보다 좋음 | "
         + (f"관찰: 정확도 {obs['accuracy']:.1%}, F1 {obs['f1']:.3f}, 오류율 {obs['false_occupied_rate']:.1%}" if obs else
            f"관찰 기록 없음 → 시험 기간 일정 기준: F1 {o['f1']:.3f} (규칙 {orule['f1']:.3f}), 비어있음→사용중 {o['false_occupied_rate']:.1%}")
         + f" | {'규칙보다 좋음' if o['f1'] > orule['f1'] else '규칙보다 못함'}{'' if obs else ' (관찰 기록 필요)'} |"),
        f"| 2. 환기 경보 재현율 ≥ 85%, macro-F1 > 규칙 | 재현율 {v['recall']:.1%}, macro-F1 {v['macro_f1']:.3f} (규칙 {vrule['macro_f1']:.3f}) | "
        f"{'통과' if v['recall'] >= RECALL_TARGET and v['macro_f1'] > vrule['macro_f1'] else '미통과'} |",
        "| 3. 혼동 행렬과 가장 많이 헷갈리는 경우 | 아래 표, 해석은 005-state.md | - |",
        "",
        "## 사용 여부 (실습실, 시험 기간, 일정 기준 정답)",
        "",
        header,
        metric_row("모델", o),
        metric_row("규칙: 15분 CO₂ +30 ppm 이상", orule),
        "",
        "모델 혼동 행렬:",
        "",
        cm_text(o),
        "",
    ]
    if obs:
        lines += ["### 직접 관찰 시간대", "", header, metric_row("모델", obs), metric_row("규칙", occ["observed_rule"])]
        if occ.get("schedule_vs_observed"):
            lines.append(metric_row("일정 기반 정답 자체", occ["schedule_vs_observed"]))
        lines.append("")
    lines += ["시각별 오류 (모델, 시험 기간): 비어있음→사용중(FP) / 사용중→비어있음(FN)", "",
              "| 시각 | FP | FN |", "|---:|---:|---:|"]
    for hour, row in sorted(occ["hour_errors_model"].items(), key=lambda kv: int(kv[0])):
        if row["fp"] or row["fn"]:
            lines.append(f"| {int(hour):02d}시 | {row['fp']} | {row['fn']} |")
    lines += ["", "![005-usage-by-hour](figures/005-usage-by-hour.png)", "",
              "## 환기 선제 경보 (CO₂ 공간 3곳, 시험 기간)", "",
              f"양성 비율 {v['positive_rate']:.1%} — 늘 '아니오'라고만 해도 정확도가 {v['always_negative_accuracy']:.1%}다. 그래서 정확도 대신 재현율·macro-F1로 본다.", "",
              header, metric_row("모델", v), metric_row("규칙: 현재 CO₂ ≥ 850 ppm", vrule), "", "모델 혼동 행렬:", "", cm_text(v), "",
              "| 공간 | 모델 재현율 | 모델 macro-F1 | 규칙 재현율 | 규칙 macro-F1 | 양성 수 |", "|---|---:|---:|---:|---:|---:|"]
    for space, row in vent["by_space"].items():
        m, r = row["model"], row["rule"]
        if m:
            lines.append(f"| {space} | {m['recall']:.1%} | {m['macro_f1']:.3f} | {r['recall']:.1%} | {r['macro_f1']:.3f} | {m['confusion']['tp'] + m['confusion']['fn']} |")
    lines += ["", "놓친 경보 예 (모델):", "", "| 공간 | 시각 | CO₂ | 15분 변화 |", "|---|---|---:|---:|"]
    for r in vent["missed_examples"]:
        lines.append(f"| {r['space']} | {r['time_kst']} | {r['co2']:.0f} | {r['co2_d15'] if r['co2_d15'] is not None else '-'} |")
    (settings.reports_dir / "005-classification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m state", description="과제 5 상태 추정")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--config-dir", default="config")
    args = parser.parse_args(argv)
    return run(Path(args.config_dir))


if __name__ == "__main__":
    sys.exit(main())
