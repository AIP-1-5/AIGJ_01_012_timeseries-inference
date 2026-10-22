"""CLI: python -m forecast <명령> (specs/003-forecast-60min/contracts/cli.md).

samples → train → select → evaluate --split test → compare → analyze
"""

import argparse
import sys

from pipeline.settings import SettingsError

from .settings import DEFAULT_CONFIG, load_forecast_settings

EXIT_OK, EXIT_ERROR, EXIT_CONFIG = 0, 1, 2


def cmd_samples(args, fs):
    from .dataset import samples_for

    for variant in args.variant or fs.variants[:1]:
        s = samples_for(fs, variant)
        print(f"[{variant}] 과거 {fs.lookback_minutes}분({s.lookback}칸), 입력 특성 {s.feats.shape[1]}개, 미래 특성 {s.future_feats.shape[1]}개")
        for period in ("train", "val", "test"):
            pos = s.positions[period]
            counts = {sp: int((s.space_id[pos] == i).sum()) for i, sp in enumerate(s.spaces)}
            dropped = ", ".join(f"{k} {v:,}" for k, v in s.dropped[period].items() if v)
            print(f"  {period:<5} 표본 {len(pos):>7,}  " + "  ".join(f"{k} {v:,}" for k, v in counts.items())
                  + (f"  (제외: {dropped})" if dropped else ""))
    return EXIT_OK


def cmd_train(args, fs):
    from pipeline.guard import refuse_training_on_board

    if refuse_training_on_board("과제 3 예측"):
        return 2
    from .dataset import samples_for
    from .train import train_run

    if args.device:
        fs.device = args.device
    if args.all:
        jobs = [("linear", fs.variants[0], fs.seeds[0], "delta")]
        jobs += [("gru", v, seed, "delta") for v in fs.variants for seed in fs.seeds]
        jobs += [("gru", fs.variants[0], fs.seeds[0], "value")]
    else:
        jobs = [(args.kind or fs.kind, args.variant or fs.variants[0], args.seed or fs.seeds[0], args.target or fs.target)]
    cache = {}
    for kind, variant, seed, target in jobs:
        from .train import run_id

        rid = run_id(kind, variant, fs.lookback_minutes, seed, target)
        if args.skip_done and (fs.runs_dir / rid / "val_metrics.json").exists():
            print(f"[{rid}] 이미 있음, 건너뜀")
            continue
        key = (variant, target)
        if key not in cache:
            cache.clear()
            cache[key] = samples_for(fs, variant, target, with_test=False)
        train_run(fs, cache[key], kind, variant, seed, target)
    return EXIT_OK


def cmd_select(args, fs):
    from .select import select

    return select(fs, force=args.force)


def cmd_evaluate(args, fs):
    import json

    from .dataset import samples_for
    from .evaluate import SelectionMissing, evaluate_run, load_selection

    if args.split == "test":
        try:
            load_selection(fs)
        except SelectionMissing as exc:
            print(f"거부: {exc}", file=sys.stderr)
            return EXIT_CONFIG
    names = sorted(p.name for p in fs.runs_dir.glob("*/") if (p / "model.pt").exists()) if args.all else [args.run]
    if not names or names == [None]:
        print("--run <실행 이름> 또는 --all", file=sys.stderr)
        return EXIT_ERROR
    cache = {}
    for name in names:
        meta = json.loads((fs.runs_dir / name / "config.json").read_text(encoding="utf-8"))
        key = (meta["variant"], meta["target"], meta["lookback_steps"])
        if key not in cache:
            cache.clear()
            cache[key] = samples_for(fs, meta["variant"], meta["target"], with_test=(args.split == "test"),
                                     lookback=meta["lookback_steps"])
        evaluate_run(fs, cache[key], name, args.split)
    return EXIT_OK


def cmd_ensemble(args, fs):
    from .ensemble import run

    return run(fs)


def cmd_compare(args, fs):
    from .compare import run

    return run(fs)


def cmd_analyze(args, fs):
    from .analyze import run

    return run(fs)


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m forecast", description="과제 3 60분 예측")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("samples", parents=[common], help="표본 수와 제외 이유 점검")
    p.add_argument("--variant", action="append")
    p.set_defaults(func=cmd_samples)

    p = sub.add_parser("train", parents=[common], help="학습 (검증 기간으로 조기 종료)")
    p.add_argument("--kind", choices=["linear", "gru"])
    p.add_argument("--variant", choices=["full", "no_schedule", "sensor_only"])
    p.add_argument("--seed", type=int)
    p.add_argument("--target", choices=["delta", "value"])
    p.add_argument("--device")
    p.add_argument("--all", action="store_true", help="선형 1 + GRU 변형 × 씨앗 + 값 직접 예측 1")
    p.add_argument("--skip-done", action="store_true", help="이미 학습한 실행은 건너뛰기")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("select", parents=[common], help="검증 기간으로 최종 모델 고르기")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("evaluate", parents=[common], help="평가 (--split test는 select 뒤에만)")
    p.add_argument("--split", choices=["val", "test"], default="val")
    p.add_argument("--run")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_evaluate)

    sub.add_parser("ensemble", parents=[common], help="2차 선택: 씨앗 평균·시점별 섞기를 검증 기간으로 고르기 (시험은 새 기간에서)").set_defaults(func=cmd_ensemble)
    sub.add_parser("compare", parents=[common], help="기준선 + 모델 비교표").set_defaults(func=cmd_compare)
    sub.add_parser("analyze", parents=[common], help="공간별·시각별 오차 분석").set_defaults(func=cmd_analyze)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        fs = load_forecast_settings(args.config)
        return args.func(args, fs)
    except SettingsError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
