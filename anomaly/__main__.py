"""CLI: python -m anomaly <명령>  (./anomaly.sh)

  train      학습 기간: 규칙 기준값 + 오토인코더 (정상만)
  calibrate  검증 기간 복사본에 고장을 심고, 헛경보 상한 안에서 탐지율이 가장 높은 임계값 고르기
  evaluate   시험 기간 복사본(다른 씨앗)에 고장을 심고 한 번 평가 → 성적표
  all        위 세 단계를 차례로
"""

import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import torch

from pipeline.baselines import conditions_block, git_commit
from pipeline.preprocess import clean_path, load_clean
from pipeline.settings import SettingsError, load_schedule, load_settings
from pipeline.split import load_normalization, load_split, normalization_path, split_path

from . import rules as rule_module
from .data import load_anomaly_settings, period_slice, space_frames
from .evaluate import pick_threshold, rule_alarms, score_alarms, summarize
from .inject import KINDS, inject
from .model import AutoEncoder, Dataset, scores, train_autoencoder

KIND_LABELS = {"spike": "튄 값", "stuck": "값 고정", "drift": "영점 틀어짐", "cooling": "냉방 정지", "ventilation": "환기 정지"}
DETECTOR_SETS = {"model": ["model"], "rules": ["rules"], "combined": ["model", "rules"]}


class Context:
    def __init__(self, config):
        self.cfg = load_anomaly_settings(config)
        root = self.cfg.path.resolve().parent.parent
        self.settings = load_settings(self.cfg.path.resolve().parent / "preprocess.toml", root=root)
        self.schedule = load_schedule(self.cfg.path.resolve().parent / "schedule.toml")
        self.out = root / "data" / "anomaly"
        self.out.mkdir(parents=True, exist_ok=True)
        self.clean = load_clean(clean_path(self.settings))
        self.split = load_split(split_path(self.settings))
        self.normalization = load_normalization(normalization_path(self.settings))
        self.frames = space_frames(self.clean, self.settings)
        self.spaces = [s.space for s in self.settings.spaces]
        self.window = int(self.cfg.model.get("window_minutes", 60)) // self.settings.interval_minutes
        self.tz = self.settings.timezone

    def period(self, name):
        return self.split[name]["start"], self.split[name]["end"]

    def dataset(self, frames):
        return Dataset(frames, self.spaces, self.normalization, self.schedule, self.window, self.tz)

    def load_model(self):
        checkpoint = torch.load(self.out / "autoencoder.pt", map_location="cpu", weights_only=False)
        model = AutoEncoder(self.window, checkpoint["n_cond"], checkpoint["hidden"], checkpoint["bottleneck"])
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        return model


def cmd_train(ctx):
    train_frames = period_slice(ctx.frames, *ctx.period("train"))
    params = rule_module.fit(train_frames, ctx.schedule, ctx.tz)
    rule_module.save(params, ctx.out / "rules.json")
    print(f"규칙 기준값 → {ctx.out / 'rules.json'} (값 고정 기준 칸 수: {params['stuck']}, 정상 최장: {params['stuck_normal_max']})")
    train_ds = ctx.dataset(train_frames)
    val_ds = ctx.dataset(period_slice(ctx.frames, *ctx.period("val")))
    print(f"오토인코더 학습: 학습 창 {len(train_ds.valid):,}, 검증(정상) 창 {len(val_ds.valid):,}")
    model = train_autoencoder(train_ds, val_ds, ctx.cfg.model)
    torch.save({"state_dict": model.state_dict(), "n_cond": train_ds.cond.shape[1],
                "hidden": int(ctx.cfg.model.get("hidden", 64)), "bottleneck": int(ctx.cfg.model.get("bottleneck", 16)),
                "window": ctx.window, "spaces": ctx.spaces, "code_commit": git_commit(ctx.settings.root)},
               ctx.out / "autoencoder.pt")
    normal = scores(model, val_ds)
    normal.assign(time=normal["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")).to_csv(ctx.out / "scores_val_normal.csv.gz", index=False)
    print(f"검증(정상) 점수 분위: 99% {normal['score'].quantile(0.99):.3f}, 99.9% {normal['score'].quantile(0.999):.3f}")
    return 0


def detect(ctx, frames, model, params):
    ds = ctx.dataset(frames)
    score_table = scores(model, ds)
    rule_rows = rule_module.apply(frames, params, ctx.schedule, ctx.tz)
    return score_table, rule_rows


def alarms_for(detectors, score_table, rule_rows, threshold):
    parts = []
    if "model" in detectors:
        parts.append(score_alarms(score_table, threshold))
    if "rules" in detectors:
        parts.append(rule_alarms(rule_rows))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["space", "time", "detector", "cause"])


def days_of(ctx, name):
    start, end = ctx.period(name)
    return (end - start).total_seconds() / 86400


def save_faults(faults, path):
    out = faults.copy()
    for column in ("start", "end"):
        out[column] = pd.to_datetime(out[column]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(path, index=False)


def cmd_calibrate(ctx):
    model, params = ctx.load_model(), rule_module.load(ctx.out / "rules.json")
    frames, faults = inject(ctx.frames, *ctx.period("val"), ctx.cfg.inject, int(ctx.cfg.inject.get("val_seed", 11)),
                            ctx.settings, ctx.schedule)
    save_faults(faults, ctx.out / "faults_val.csv")
    score_table, rule_rows = detect(ctx, frames, model, params)
    normal = pd.read_csv(ctx.out / "scores_val_normal.csv.gz")["score"]
    candidates = sorted(set(np.round(normal.quantile([0.99, 0.995, 0.998, 0.999, 0.9995, 0.9998, 0.9999]).to_numpy(), 4))
                        | {round(float(normal.max()) * 1.05, 4)})
    days, alarm = days_of(ctx, "val"), ctx.cfg.alarm
    merge, grace = int(alarm.get("merge_minutes", 60)), int(alarm.get("grace_minutes", 60))

    def evaluate_combined(threshold):
        return summarize(faults, alarms_for(["model", "rules"], score_table, rule_rows, threshold), days, merge, grace)[2]

    threshold, table = pick_threshold(candidates, evaluate_combined, float(alarm.get("target_false_per_day", 0.5)))
    model_only = [summarize(faults, alarms_for(["model"], score_table, rule_rows, t), days, merge, grace)[2] for t in table["threshold"]]
    table["model_rate"] = [m["rate"] for m in model_only]
    table["model_false_per_day"] = [m["false_per_day"] for m in model_only]
    record = {"threshold": threshold, "decided_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "rule": f"검증 복사본(씨앗 {ctx.cfg.inject.get('val_seed', 11)})에서 헛경보 ≤ {alarm.get('target_false_per_day', 0.5)}회/일 안에서 결합 탐지율 최대",
              "sweep": table.to_dict(orient="records")}
    (ctx.out / "threshold.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"검증 고장 {len(faults)}건, 임계값 → {threshold:.4f}")
    print(table[["threshold", "rate", "false_per_day", "model_rate", "model_false_per_day"]].to_string(index=False))
    return 0


def cmd_evaluate(ctx):
    """시험 복사본 평가. --seed로 다른 씨앗을 주면 성적표를 004-detection-seed<N>.md로 따로 쓴다."""
    threshold_path = ctx.out / "threshold.json"
    if not threshold_path.exists():
        print("거부: 먼저 calibrate로 검증 기간에서 임계값을 정한다", file=sys.stderr)
        return 2
    chosen = json.loads(threshold_path.read_text(encoding="utf-8"))
    model, params = ctx.load_model(), rule_module.load(ctx.out / "rules.json")
    seed = ctx.seed if ctx.seed is not None else int(ctx.cfg.inject.get("test_seed", 22))
    suffix = "" if seed == int(ctx.cfg.inject.get("test_seed", 22)) else f"-seed{seed}"
    frames, faults = inject(ctx.frames, *ctx.period("test"), ctx.cfg.inject, seed, ctx.settings, ctx.schedule)
    save_faults(faults, ctx.out / f"faults_test{suffix}.csv")
    score_table, rule_rows = detect(ctx, frames, model, params)
    days, alarm = days_of(ctx, "test"), ctx.cfg.alarm
    merge, grace = int(alarm.get("merge_minutes", 60)), int(alarm.get("grace_minutes", 60))
    results = {}
    for name, detectors in DETECTOR_SETS.items():
        per_fault, by_kind, overall, false_events = summarize(
            faults, alarms_for(detectors, score_table, rule_rows, chosen["threshold"]), days, merge, grace)
        results[name] = {"per_fault": per_fault, "by_kind": by_kind, "overall": overall, "false_events": false_events}
    sweep = []
    for t in [row["threshold"] for row in chosen["sweep"]]:
        sweep.append({"threshold": t, **summarize(faults, alarms_for(["model", "rules"], score_table, rule_rows, t), days, merge, grace)[2],
                      "model_rate": summarize(faults, alarms_for(["model"], score_table, rule_rows, t), days, merge, grace)[2]["rate"]})
    # 규칙별 헛경보 (어떤 규칙이 헛경보를 내는지)
    false_by_rule = results["combined"]["false_events"]["detectors"].str.split(",").explode().value_counts().to_dict()
    write_report(ctx, faults, results, chosen, pd.DataFrame(sweep), false_by_rule, params, seed, suffix)
    summary = {name: r["overall"] for name, r in results.items()}
    summary["false_by_detector"] = false_by_rule
    summary["threshold"] = chosen["threshold"]
    (ctx.out / f"test_results{suffix}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    results["combined"]["per_fault"].to_csv(ctx.out / f"per_fault_test{suffix}.csv", index=False)
    for name, r in results.items():
        o = r["overall"]
        print(f"{name:<9} 탐지 {o['detected']}/{o['total']} ({o['rate']:.0%}), 헛경보 {o['false_per_day']:.2f}회/일, 지연 중앙값 {o['delay_median_min']}분")
    return 0


def kind_table(results, faults):
    lines = ["| 고장 | 건수 | 모델 | 규칙 | 결합 | 결합 지연 중앙값 | 원인 항목 맞음 (결합) |", "|---|---:|---:|---:|---:|---:|---:|"]
    for kind in KINDS:
        total = int((faults["kind"] == kind).sum())
        if total == 0:
            continue
        cells = []
        for name in ("model", "rules", "combined"):
            row = results[name]["by_kind"].set_index("kind").reindex([kind]).iloc[0]
            cells.append(f"{int(row['detected']) if not np.isnan(row['detected']) else 0}/{total}")
        comb = results["combined"]["by_kind"].set_index("kind").reindex([kind]).iloc[0]
        delay = f"{comb['delay_median']:.0f}분" if not np.isnan(comb["delay_median"]) else "-"
        lines.append(f"| {KIND_LABELS[kind]} | {total} | " + " | ".join(cells) + f" | {delay} | {int(comb['cause_ok'] or 0)}/{total} |")
    return "\n".join(lines)


def write_report(ctx, faults, results, chosen, sweep, false_by_rule, params, seed, suffix=""):
    import matplotlib.pyplot as plt

    from pipeline.diagnose import SERIES, SURFACE, TEXT, TEXT_SECONDARY, setup_font, style_axes

    korean = setup_font()
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    fig.patch.set_facecolor(SURFACE)
    style_axes(ax)
    val_sweep = pd.DataFrame(chosen["sweep"])
    ax.plot(val_sweep["false_per_day"], val_sweep["rate"] * 100, marker="o", color=SERIES[0], linewidth=2,
            label="결합, 검증" if korean else "combined, val")
    ax.plot(sweep["false_per_day"], sweep["rate"] * 100, marker="s", color=SERIES[1], linewidth=2,
            label="결합, 시험" if korean else "combined, test")
    ax.axvline(1.0, color=TEXT_SECONDARY, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.axhline(80, color=TEXT_SECONDARY, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.set_xlabel("하루당 헛경보 (전체 공간)" if korean else "false alarms / day", color=TEXT_SECONDARY, fontsize=9)
    ax.set_ylabel("이벤트 탐지율 (%)" if korean else "detection rate (%)", color=TEXT_SECONDARY, fontsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY)
    ax.set_title("임계값을 바꿀 때 탐지율과 헛경보 (점선 = 확인 기준)" if korean else "threshold trade-off",
                 color=TEXT, fontsize=11, loc="left")
    fig.tight_layout()
    figure = ctx.settings.reports_dir / "figures" / f"004-threshold{suffix}.png"
    figure.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure, dpi=130, facecolor=SURFACE)
    plt.close(fig)

    comb = results["combined"]["overall"]
    lines = [
        "# 과제 4 탐지 성적표",
        "",
        f"`python -m anomaly evaluate`가 만든 표. 만든 시각 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. 해석은 [004-anomaly.md](004-anomaly.md).",
        "",
        "## 측정 조건",
        "",
        conditions_block(ctx.settings, ctx.clean, ctx.split, {
            "고장": f"합성 5종을 실데이터 복사본에 주입. 검증 씨앗 {ctx.cfg.inject.get('val_seed')} {len(pd.read_csv(ctx.out / 'faults_val.csv'))}건(임계값 결정), "
                    f"시험 씨앗 {seed} {len(faults)}건(평가)",
            "모델": f"오토인코더 (창 {ctx.window}칸, 학습 기간 정상 데이터만), 임계값 {chosen['threshold']:.4f} ({chosen['decided_at']}, 검증 기준)",
            "규칙 기준값": f"학습 기간. 값 고정 칸 수 {params['stuck']} (정상 최장 {params['stuck_normal_max']})",
            "경보 묶기": f"{ctx.cfg.alarm.get('merge_minutes')}분 안의 경보는 한 이벤트, 고장 끝 뒤 유예 {ctx.cfg.alarm.get('grace_minutes')}분",
        }),
        "",
        "## 완료 조건",
        "",
        "| 조건 | 결과 | 판정 |",
        "|---|---|---|",
        f"| 1. 모델+규칙 이벤트 탐지율 ≥ 80% | {comb['detected']}/{comb['total']} = {comb['rate']:.0%} | {'통과' if comb['rate'] >= 0.8 else '미통과'} |",
        f"| 2. 헛경보 ≤ 하루 1회 (전체 공간) | {comb['false_alarms']}건 / {days_of(ctx, 'test'):.0f}일 = {comb['false_per_day']:.2f}회/일 | {'통과' if comb['false_per_day'] <= 1 else '미통과'} |",
        "| 3. 모델·규칙이 못 잡는 종류와 이유 | 아래 종류별 표, 해석은 004-anomaly.md | - |",
        "",
        "## 고장 종류별 (시험 기간)",
        "",
        kind_table(results, faults),
        "",
        "| 방법 | 탐지 | 탐지율 | 지연 중앙값 | 헛경보 | 하루당 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, label in (("model", "모델만"), ("rules", "규칙만"), ("combined", "결합")):
        o = results[name]["overall"]
        delay = f"{o['delay_median_min']:.0f}분" if o["delay_median_min"] is not None else "-"
        lines.append(f"| {label} | {o['detected']}/{o['total']} | {o['rate']:.0%} | {delay} | {o['false_alarms']} | {o['false_per_day']:.2f} |")
    lines += ["", f"헛경보를 낸 탐지기 (결합): {false_by_rule or '없음'}", "",
              "## 임계값을 바꿀 때", "", f"![004-threshold](figures/004-threshold{suffix}.png)", "",
              "| 임계값 | 검증 결합 탐지율 | 검증 헛경보/일 | 시험 결합 탐지율 | 시험 헛경보/일 | 시험 모델만 탐지율 |", "|---:|---:|---:|---:|---:|---:|"]
    for v, t in zip(chosen["sweep"], sweep.to_dict(orient="records")):
        mark = " ← 선택" if abs(v["threshold"] - chosen["threshold"]) < 1e-9 else ""
        lines.append(f"| {v['threshold']:.4f}{mark} | {v['rate']:.0%} | {v['false_per_day']:.2f} | {t['rate']:.0%} | {t['false_per_day']:.2f} | {t['model_rate']:.0%} |")
    lines += ["", "## 놓친 고장 (결합)", "", "| 종류 | 공간 | 항목 | 시작 (KST) | 크기 |", "|---|---|---|---|---:|"]
    missed = results["combined"]["per_fault"].merge(faults[["event_id", "start", "size"]], on="event_id")
    for r in missed[~missed["detected"]].itertuples():
        lines.append(f"| {KIND_LABELS[r.kind]} | {r.space} | {r.metric} | {pd.Timestamp(r.start).tz_convert(ctx.tz):%m-%d %H:%M} | {r.size:.1f} |")
    report = ctx.settings.reports_dir / f"004-detection{suffix}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"성적표 → {report}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m anomaly", description="과제 4 이상 탐지")
    parser.add_argument("command", choices=["train", "calibrate", "evaluate", "all"])
    parser.add_argument("--config", default="config/anomaly.toml")
    parser.add_argument("--seed", type=int, help="evaluate: 설정의 test_seed 대신 이 씨앗으로 고장을 심는다")
    args = parser.parse_args(argv)
    try:
        ctx = Context(args.config)
        ctx.seed = args.seed
    except (SettingsError, FileNotFoundError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    from pipeline.guard import refuse_training_on_board

    if args.command in ("train", "all") and refuse_training_on_board("과제 4 오토인코더"):
        return 2
    steps = {"train": [cmd_train], "calibrate": [cmd_calibrate], "evaluate": [cmd_evaluate],
             "all": [cmd_train, cmd_calibrate, cmd_evaluate]}[args.command]
    for step in steps:
        code = step(ctx)
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
