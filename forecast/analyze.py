"""어디서·언제 틀리는지 (research.md R9, US4). 고른 모델과 현재값 유지를 같은 시점에서 비교한다.

결과: docs/reports/figures/003-error-by-space.png, 003-error-by-time.png, data/forecast/analysis.json
"""

import json

import numpy as np
import pandas as pd

from pipeline.diagnose import SERIES, SURFACE, TEXT, TEXT_SECONDARY, setup_font, style_axes
from pipeline.schedule import holiday_dates

from .compare import load_baselines
from .evaluate import load_predictions, load_selection

EVENT_LABELS = {"09:00": "수업 시작", "11:30": "점심", "13:00": "오후 시작", "18:00": "퇴실"}


def classify(rows, settings, schedule, window_minutes=60):
    """오차 큰 순간의 상황: 일정 변화 ±60분(실습실, 일정 있는 날), 주말·공휴일, 회의실, 기타."""
    local_target = (rows["time"] + pd.to_timedelta(rows["horizon_min"], unit="m")).dt.tz_convert(settings.timezone)
    off = holiday_dates(schedule.holidays, set(local_target.dt.year))
    class_day = (local_target.dt.weekday < 5) & ~local_target.dt.date.isin(off)
    minutes = local_target.dt.hour * 60 + local_target.dt.minute
    labels = np.where(class_day, "평일 기타", "주말·공휴일").astype(object)
    labels = np.where(rows["space"] == "회의실", "회의실", labels)
    lab = schedule.for_space("실습실")
    if lab is not None:
        for event in lab.events:
            at = event.time.hour * 60 + event.time.minute
            near = (rows["space"] == "실습실") & class_day & ((minutes - at).abs() <= window_minutes)
            labels = np.where(near, EVENT_LABELS.get(event.time.strftime("%H:%M"), event.label), labels)
    return labels


def run(fsettings, log=print):
    settings, schedule = fsettings.pipeline, fsettings.schedule
    chosen = load_selection(fsettings)["chosen"]
    model = load_predictions(fsettings, chosen)
    base = load_baselines(settings)
    persistence = base[base["baseline"] == "persistence"]
    key = ["space", "metric", "time", "horizon_min"]
    both = model.merge(persistence[key + ["y_pred"]].rename(columns={"y_pred": "y_persist"}), on=key, how="inner")
    both["err_model"] = (both["y_pred"] - both["y_true"]).abs()
    both["err_persist"] = (both["y_persist"] - both["y_true"]).abs()
    co2_60 = both[(both["metric"] == "co2") & (both["horizon_min"] == 60)].copy()
    co2_60["situation"] = classify(co2_60, settings, schedule)

    by_space = (both[both["horizon_min"] == 60].groupby(["metric", "space"])[["err_model", "err_persist"]].mean()
                .assign(ratio=lambda d: d["err_model"] / d["err_persist"]).reset_index())
    by_situation = (co2_60.groupby("situation")[["err_model", "err_persist"]].agg(["mean", "size"]))
    by_situation.columns = ["model", "n", "persistence", "n2"]
    by_situation = by_situation.drop(columns="n2").assign(ratio=lambda d: d["model"] / d["persistence"]).reset_index()
    top = co2_60.sort_values("err_model", ascending=False).head(50)
    top_rows = [{
        "space": r.space, "time_kst": r.time.tz_convert(settings.timezone).strftime("%Y-%m-%d(%a) %H:%M"),
        "target_kst": (r.time + pd.Timedelta(minutes=60)).tz_convert(settings.timezone).strftime("%H:%M"),
        "y_true": round(float(r.y_true), 1), "y_pred": round(float(r.y_pred), 1), "y_persist": round(float(r.y_persist), 1),
        "err_model": round(float(r.err_model), 1), "situation": r.situation,
    } for r in top.itertuples()]
    top_counts = top["situation"].value_counts().to_dict()

    korean = setup_font()
    import matplotlib.pyplot as plt

    out_dir = settings.reports_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 공간별 60분 MAE
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.4))
    fig.patch.set_facecolor(SURFACE)
    for ax, metric, unit in ((axes[0], "co2", "ppm"), (axes[1], "temperature", "℃")):
        style_axes(ax)
        part = by_space[by_space["metric"] == metric]
        x = np.arange(len(part))
        ax.bar(x - 0.2, part["err_persist"], width=0.38, color=SERIES[1], label="현재값 유지" if korean else "persistence")
        ax.bar(x + 0.2, part["err_model"], width=0.38, color=SERIES[0], label="모델" if korean else "model")
        ax.set_xticks(x, [s if korean else settings.slug(s) for s in part["space"]])
        ax.set_ylabel(f"60분 MAE ({unit})", color=TEXT_SECONDARY, fontsize=9)
        for xi, (m, p) in enumerate(zip(part["err_model"], part["err_persist"])):
            ax.annotate(f"{m / p:.0%}", (xi + 0.2, m), xytext=(0, 3), textcoords="offset points", ha="center",
                        fontsize=8, color=TEXT_SECONDARY)
    axes[0].legend(frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)
    fig.suptitle("공간별 60분 예측 오차 (시험 기간, 막대 위 = 현재값 유지 대비)" if korean else "60-min MAE by space",
                 color=TEXT, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(out_dir / "003-error-by-space.png", dpi=130, facecolor=SURFACE)
    plt.close(fig)

    # 시각별 60분 CO₂ 오차 (평일, 예측 대상 시각 기준)
    co2_60["target_clock"] = ((co2_60["time"] + pd.Timedelta(minutes=60)).dt.tz_convert(settings.timezone)
                              .pipe(lambda s: s.dt.hour + s.dt.minute / 60))
    weekday = co2_60[co2_60["situation"] != "주말·공휴일"]
    spaces = [s.space for s in settings.spaces if "co2" in s.metrics]
    fig, axes = plt.subplots(1, len(spaces), figsize=(4.2 * len(spaces), 3.3), sharey=True, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    for ax, space in zip(axes[0], spaces):
        style_axes(ax)
        part = weekday[weekday["space"] == space].groupby("target_clock")[["err_model", "err_persist"]].mean()
        ax.plot(part.index, part["err_persist"], color=SERIES[1], linewidth=2, label="현재값 유지" if korean else "persistence")
        ax.plot(part.index, part["err_model"], color=SERIES[0], linewidth=2, label="모델" if korean else "model")
        lab = schedule.for_space(space)
        if lab is not None:
            for event in lab.events:
                ax.axvline(event.time.hour + event.time.minute / 60, color=TEXT_SECONDARY, linewidth=0.8, linestyle=(0, (3, 3)))
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 6))
        ax.set_title(space if korean else settings.slug(space), color=TEXT, fontsize=11, loc="left")
        ax.set_xlabel("예측 대상 시각 (KST)" if korean else "target hour", color=TEXT_SECONDARY, fontsize=9)
    axes[0][0].set_ylabel("60분 CO₂ MAE (ppm)", color=TEXT_SECONDARY, fontsize=9)
    axes[0][0].legend(frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY, loc="upper left")
    fig.suptitle("평일 시각별 60분 CO₂ 오차 (시험 기간, 점선 = 실습실 일정)" if korean else "60-min CO₂ error by hour",
                 color=TEXT, fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(out_dir / "003-error-by-time.png", dpi=130, facecolor=SURFACE)
    plt.close(fig)

    result = {
        "chosen": chosen,
        "by_space_60": by_space.round(4).to_dict(orient="records"),
        "co2_60_by_situation": by_situation.round(3).to_dict(orient="records"),
        "top50_co2_60": top_rows,
        "top50_situation_counts": top_counts,
    }
    (fsettings.out_dir / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"분석 → {fsettings.out_dir / 'analysis.json'}, 그래프 003-error-by-space.png, 003-error-by-time.png")
    log("60분 CO₂ 상황별 (모델 / 현재값 유지):")
    for r in by_situation.itertuples():
        log(f"  {r.situation:<8} {r.model:7.1f} / {r.persistence:7.1f} ppm ({r.ratio:.0%}, {int(r.n):,}개)")
    log(f"오차 상위 50개 상황: {top_counts}")
    return 0
