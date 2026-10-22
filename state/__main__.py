"""CLI: python -m state run | observe  (./state.sh run)

  observe --db <collector.db>  직접 관찰 기록(docs/observations/occupancy.csv)과 그 시간 실시간 센서로 모델·규칙 채점
                               → docs/reports/005-observation.md (학습 안 함, 보드에서도 가벼움)

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


def observe(config_dir, db, models_path, log=print):
    """관찰한 시간대의 수집기 데이터를 학습 때와 같은 전처리로 만들고, 저장된 모델·규칙으로 판정해 관찰과 비교."""
    from anomaly.data import METRICS
    from pipeline.loader import from_collector_db
    from pipeline.preprocess import preprocess

    config_dir = Path(config_dir)
    settings = load_settings(config_dir / "preprocess.toml", root=config_dir.resolve().parent)
    schedule = load_schedule(config_dir / "schedule.toml")
    root = settings.root
    observations = load_observations(root / "docs" / "observations" / "occupancy.csv")
    if observations is None or observations.empty:
        log("관찰 기록이 없다: docs/observations/occupancy.csv")
        return 1
    tz = settings.timezone
    start = pd.Timestamp(observations["start_kst"].min(), tz=tz).tz_convert("UTC") - pd.Timedelta(hours=2)
    end = pd.Timestamp(observations["end_kst"].max(), tz=tz).tz_convert("UTC") + pd.Timedelta(minutes=5)
    raw = from_collector_db(db, start, end, list(METRICS))
    clean, _, _ = preprocess({m: raw[raw["metric"].astype(str) == m] for m in METRICS}, settings)
    frames = space_frames(clean, settings)
    with Path(models_path).open("rb") as fp:
        models = pickle.load(fp)
    timelines = []
    for space in sorted(observations["space"].unique()):
        frame = frames.get(space)
        if frame is None:
            log(f"{space}: 그 시간 센서 데이터 없음")
            continue
        table = sensor_features(frame)
        table["observed"] = observation_labels(frame.index, space, observations, tz).to_numpy()
        table["schedule"] = occupancy_labels(frame.index, space, schedule, tz, transition_minutes=0).to_numpy()
        table = table[table["co2"].notna()]
        if table.empty:
            continue
        prob = models["occupancy"].predict_proba(table[models["occupancy_features"]])[:, 1]
        table = table.assign(space=space, time=table.index, prob=prob,
                             model=(prob >= models["occupancy_threshold"]).astype(int),
                             rule=(table["co2_d15"] >= 30).astype(int))
        timelines.append(table.reset_index(drop=True))
    timeline = pd.concat(timelines, ignore_index=True) if timelines else pd.DataFrame()
    table = timeline[timeline["observed"].notna()].reset_index(drop=True) if len(timeline) else timeline
    if table.empty:
        log("관찰 시간대에 쓸 수 있는 센서 칸이 없다")
        return 1
    out = root / "data" / "state"
    out.mkdir(parents=True, exist_ok=True)
    timeline.to_csv(out / "observation_timeline.csv", index=False)       # 그림 다시 그리기용 (커밋 안 함)
    threshold = models["occupancy_threshold"]
    figure = observation_figure(settings, timeline, observations, threshold)
    m_model, m_rule = metrics(table["observed"], table["model"]), metrics(table["observed"], table["rule"])
    sched = table[table["schedule"].notna()]
    m_sched = metrics(sched["observed"], sched["schedule"]) if len(sched) else None
    n_empty = int((table["observed"] == 0).sum())
    first, last = timeline["time"].min().tz_convert(tz), timeline["time"].max().tz_convert(tz)
    header = "| 방법 | 표본 | 정확도 | 정밀도 | 재현율 | F1 | macro-F1 | 비어있음→사용중 |\n|---|---:|---:|---:|---:|---:|---:|---:|"
    lines = [
        "# 과제 5 직접 관찰 채점", "",
        f"`python -m state observe`가 만든 표. 만든 시각 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. 해석은 [005-state.md](005-state.md).", "",
        "## 측정 조건", "",
        "| 항목 | 값 |", "|---|---|",
        "| 데이터 | 실데이터, 수집기가 MQTT로 실시간 저장한 값 (합성 아님) |",
        f"| 정답 | 사람이 직접 본 인원 (`docs/observations/occupancy.csv`, {len(observations)}구간) → 0명이면 비어있음, 1명 이상이면 사용중 |",
        f"| 입력 | 그 시간 실시간 수집기 DB(`{Path(db).name}`), 학습 때와 같은 전처리(`pipeline.preprocess`) |",
        f"| 그림 구간 | {first:%Y-%m-%d %H:%M} ~ {last:%H:%M} KST (관찰 2시간 전부터, 15·60분 변화 계산용) |",
        f"| 모델 | 과제 5 사용 여부 분류기(센서만, 임계값 {threshold:.2f}, 검증 기간으로 정함). 다시 학습하지 않음 |",
        "| 규칙 | 15분 CO₂ 변화 ≥ +30 ppm |",
        "| 일정 | `config/schedule.toml` 실습실 평일 09:00~11:30·13:00~18:00 (점심 11:30~13:00은 0.5로 보고 비교에서 뺌) |",
        f"| 칸 | 5분, 관찰 구간 안의 칸 {len(table)}개 (사용중 {len(table) - n_empty}, 비어있음 {n_empty}) |", "",
    ]
    if n_empty == 0:
        lines += ["> **비어있음 관찰이 아직 없다.** 그래서 '비어있음→사용중' 오류율과 정밀도는 계산할 수 없고(표의 `-`),",
                  "> 지금 숫자는 '사람이 있을 때 알아보는가'(재현율)만 말해 준다. 퇴실 뒤나 점심의 빈 방을 관찰해 추가해야 한다.", ""]
    lines += ["## 결과", "", header, metric_row("모델", m_model), metric_row("규칙: 15분 CO₂ +30 ppm 이상", m_rule)]
    if m_sched:
        lines.append(metric_row("일정표 자체 (정답과 비교)", m_sched))
    lines += ["", "모델 혼동 행렬:", "", cm_text(m_model), ""]
    if figure:
        lines += [f"![{figure.stem}](figures/{figure.name})", ""]
    lines += ["## 구간별", "", "| 공간 | 구간 (KST) | 관찰 인원 | 칸 | 일정 | 모델 사용중 | 규칙 사용중 | 평균 CO₂ | 평균 모델 확률 |",
              "|---|---|---:|---:|---|---:|---:|---:|---:|"]
    for obs in observations.itertuples():
        span = observation_labels(pd.DatetimeIndex(table["time"]), obs.space, observations.loc[[obs.Index]], tz).notna().to_numpy()
        part = table[span & (table["space"] == obs.space).to_numpy()]
        if part.empty:
            lines.append(f"| {obs.space} | {obs.start_kst[5:]} ~ {obs.end_kst[11:]} | {int(obs.people)} | 0 | - | - | - | - | - |")
            continue
        sch = part["schedule"].dropna()
        sch_text = "-" if sch.empty else ("수업" if sch.mean() >= 0.5 else "수업 없음")
        lines.append(f"| {obs.space} | {obs.start_kst[5:]} ~ {obs.end_kst[11:]} | {int(obs.people)} | {len(part)} | {sch_text} | "
                     f"{int(part['model'].sum())}/{len(part)} | {int(part['rule'].sum())}/{len(part)} | {part['co2'].mean():.0f} | {part['prob'].mean():.2f} |")
    lines += ["", "## 칸별", "",
              "| 공간 | 시각 (KST) | 관찰 | 모델 확률 | 모델 | 규칙 | CO₂ | 15분 변화 |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in table.itertuples():
        local = pd.Timestamp(r.time).tz_convert(tz).strftime("%m-%d %H:%M")
        d15 = "-" if pd.isna(r.co2_d15) else f"{r.co2_d15:+.0f}"
        lines.append(f"| {r.space} | {local} | {int(r.observed)} | {r.prob:.2f} | {r.model} | {r.rule} | {r.co2:.0f} | {d15} |")
    (settings.reports_dir / "005-observation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    fo = m_model["false_occupied_rate"]
    log(f"관찰 {len(table)}칸: 모델 정확도 {m_model['accuracy']:.1%}, F1 {m_model['f1']:.3f}, "
        f"비어있음→사용중 {'-' if fo is None else f'{fo:.1%}'} | 규칙 F1 {m_rule['f1']:.3f}")
    return 0


def observation_figure(settings, timeline, observations, threshold):
    """관찰 시간대 한 장: CO₂ 흐름 / 15분 변화와 규칙 선 / 모델 확률과 임계값 / 관찰·일정·모델·규칙 판정 띠."""
    try:
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
    except ImportError:
        return None
    from pipeline.diagnose import GRID, SERIES, SURFACE, TEXT, TEXT_SECONDARY, setup_font, style_axes

    korean = setup_font()
    tz = settings.timezone
    t = lambda ko, en: ko if korean else en
    spaces = list(timeline["space"].unique())
    space = spaces[0]                                   # 관찰은 지금 실습실뿐. 공간이 늘면 공간마다 한 장씩 늘린다
    part = timeline[timeline["space"] == space].sort_values("time")
    x = pd.DatetimeIndex(part["time"]).tz_convert(tz).tz_localize(None) - pd.Timedelta(minutes=2.5)   # 칸 가운데
    occupied_c, empty_c, obs_band = SERIES[0], "#d9d8d3", "#eef4fc"
    fig, axes = plt.subplots(4, 1, figsize=(9, 8.2), sharex=True,
                             gridspec_kw={"height_ratios": [3, 2, 2, 1.6], "hspace": 0.32})
    fig.patch.set_facecolor(SURFACE)
    for ax in axes:
        style_axes(ax)
    spans = []
    for obs in observations[observations["space"] == space].itertuples():
        a, b = pd.Timestamp(obs.start_kst), pd.Timestamp(obs.end_kst)
        spans.append((a, b, int(obs.people)))
        for ax in axes[:3]:
            ax.axvspan(a, b, color=obs_band, zorder=0, linewidth=0)

    ax = axes[0]
    ax.plot(x, part["co2"], color=occupied_c, linewidth=2)
    ax.set_ylabel("CO$_2$ (ppm)", color=TEXT_SECONDARY, fontsize=9)
    top = part["co2"].max()
    for a, b, people in spans:
        ax.text(a + (b - a) / 2, top * 1.02, t(f"{people}명", f"{people}p"), ha="center", va="bottom",
                color=TEXT, fontsize=9, fontweight="bold")
    ax.set_ylim(min(part["co2"].min() * 0.95, 400), top * 1.12)
    ax.set_title(t(f"{space} 직접 관찰 시간대: 센서·모델·규칙 판정 비교", "direct observation window"),
                 color=TEXT, fontsize=12, loc="left", pad=10)

    ax = axes[1]
    d15 = part["co2_d15"]
    ax.bar(x, d15, width=pd.Timedelta(minutes=4), color=[occupied_c if v >= 30 else empty_c for v in d15.fillna(0)])
    ax.axhline(30, color=TEXT_SECONDARY, linewidth=1, linestyle="--")
    ax.axhline(0, color=GRID, linewidth=1)
    ax.text(x[-1], 30, t("규칙 기준 +30 ", "rule +30 "), color=TEXT_SECONDARY, fontsize=8, ha="right", va="bottom",
            bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
    ax.set_ylabel(t("15분 변화 (ppm)", "d15 (ppm)"), color=TEXT_SECONDARY, fontsize=9)

    ax = axes[2]
    ax.plot(x, part["prob"], color=SERIES[1], linewidth=2)
    ax.axhline(threshold, color=TEXT_SECONDARY, linewidth=1, linestyle="--")
    ax.text(x[-1], threshold, t(f"임계값 {threshold:.2f} ", f"threshold {threshold:.2f} "), color=TEXT_SECONDARY,
            fontsize=8, ha="right", va="bottom", bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1})
    ax.set_ylim(0, 1.08)
    ax.set_ylabel(t("모델 사용중 확률", "P(occupied)"), color=TEXT_SECONDARY, fontsize=9)

    ax = axes[3]
    ax.grid(False)
    rows = [(t("직접 관찰", "observed"), part["observed"]), (t("일정표", "schedule"), part["schedule"]),
            (t("모델", "model"), part["model"]), (t("규칙", "rule"), part["rule"])]
    width = pd.Timedelta(minutes=5)
    for i, (_, values) in enumerate(rows):
        y = len(rows) - 1 - i
        for xi, v in zip(x, values):
            if pd.isna(v):
                if i != 1:                               # 일정표의 빈칸 = 점심(0.5, 비교에서 뺌)
                    continue
                v = 0.5
            color = occupied_c if v >= 1 else (empty_c if v <= 0 else "#a9c9ef")
            ax.barh(y, width, left=xi - width / 2, height=0.72, color=color, edgecolor=SURFACE, linewidth=1)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([name for name, _ in rows][::-1], fontsize=9, color=TEXT_SECONDARY)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.legend(handles=[Patch(color=occupied_c, label=t("사용중", "occupied")), Patch(color="#a9c9ef", label=t("점심(일정 0.5)", "lunch")),
                       Patch(color=empty_c, label=t("비어있음", "empty")), Patch(color=obs_band, label=t("관찰 구간", "observed span"))],
              loc="upper center", bbox_to_anchor=(0.5, -0.38), ncol=4, frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.set_xlim(x[0] - width, x[-1] + width)
    day = x[0].strftime("%Y-%m-%d")
    ax.set_xlabel(t(f"{day} 시각 (KST, 5분 칸)  ·  실데이터(수집기 DB)  ·  모델 다시 학습 안 함",
                    f"{day} KST, 5-min cells, real data"), color=TEXT_SECONDARY, fontsize=8.5)
    figure = settings.reports_dir / "figures" / "005-observation.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure, dpi=140, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return figure


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m state", description="과제 5 상태 추정")
    parser.add_argument("command", choices=["run", "observe"])
    parser.add_argument("--config-dir", default="config")
    parser.add_argument("--db", help="observe: 수집기 DB (보드 A에서 복사한 collector.db)")
    parser.add_argument("--models", default="data/state/models.pkl", help="observe: 과제 5 모델 (data/state/models.pkl 또는 data/models/state.pkl)")
    args = parser.parse_args(argv)
    if args.command == "observe":
        if not args.db:
            parser.error("observe에는 --db가 필요하다")
        return observe(Path(args.config_dir), args.db, args.models)
    from pipeline.guard import refuse_training_on_board

    if refuse_training_on_board("과제 5 분류기"):
        return 2
    return run(Path(args.config_dir))


if __name__ == "__main__":
    sys.exit(main())
