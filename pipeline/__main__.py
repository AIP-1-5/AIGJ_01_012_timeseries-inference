"""CLI 진입점: python -m pipeline <명령> (specs/002-data-baseline/contracts/cli.md).

보드 B: export-influx, check-time (InfluxDB가 보드 B 유선으로만 닿는다)
맥: preprocess, split, diagnose, baselines, report
"""

import argparse
import sys
from datetime import datetime, timezone

from .influx import InfluxError
from .settings import DEFAULT_CONFIG, DEFAULT_SCHEDULE, SettingsError, influx_env, load_schedule, load_settings

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2


def cmd_export_influx(args, settings):
    from .influx import export, parse_month

    env = influx_env(settings.bucket)
    first = parse_month(args.start or settings.source_start)
    last = parse_month(args.end) if args.end else datetime.now(timezone.utc)
    last = last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    metrics = args.metric or settings.source_metrics
    print(f"InfluxDB 내보내기: 버킷 {env.bucket}, {first:%Y-%m} ~ {last:%Y-%m}, 항목 {', '.join(metrics)}")
    print(f"저장 위치: {settings.raw_dir}")
    failures = export(env, settings.raw_dir, first, last, metrics, overwrite=args.overwrite)
    if failures:
        print(f"\n실패 {len(failures)}건: {', '.join(f'{m} {n}' for m, n in failures)} — 같은 명령을 다시 실행하면 그 달만 다시 받는다")
        return EXIT_ERROR
    print("\n완료. 다음: 맥에서 scp로 data/raw/influx를 가져온다 (quickstart.md 2단계)")
    return EXIT_OK


def cmd_check_time(args, settings):
    from .timecheck import run

    env = influx_env(settings.bucket)
    metrics = args.metric or settings.analysis_metrics
    merged, table = run(env, settings.root / args.collector_db, metrics, hours=args.hours)
    if table is None:
        print(f"수집기 기록이 없다: {args.collector_db} (최근 {args.hours:g}시간)")
        return EXIT_ERROR
    out = settings.processed_dir / "time_check.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out)
    matched = merged["diff_seconds"].dropna()
    print(f"InfluxDB 시각 − 수집기 시각(nsTime), 최근 {args.hours:g}시간, 같은 센서·항목·값을 ±10분 안에서 짝지음\n")
    print(f"{'센서':<18} {'공간':<10} {'행':>7} {'짝':>6} {'중앙값(초)':>10} {'10%':>8} {'90%':>8}")
    for device, row in table.iterrows():
        print(f"{device:<18} {row['space']:<10} {int(row['rows']):>7} {row['match_rate']:>6.0%} "
              f"{row['median_s']:>10.1f} {row['p10_s']:>8.1f} {row['p90_s']:>8.1f}")
    if len(matched):
        print(f"\n전체: 짝 {len(matched):,}/{len(merged):,}행 ({len(matched) / len(merged):.0%}), "
              f"차이 중앙값 {matched.median():.1f}초, 10%~90% {matched.quantile(0.1):.1f}~{matched.quantile(0.9):.1f}초")
    print(f"저장: {out}")
    print("해석: 중앙값이 0 근처면 InfluxDB 시각을 그대로 쓴다. 약 +300초인 센서가 있으면 게이트웨이 시계를 따른 것이다")
    return EXIT_OK


def cmd_preprocess(args, settings):
    from .preprocess import run

    return run(settings)


def cmd_split(args, settings):
    from .split import run

    return run(settings, force=args.force)


def cmd_diagnose(args, settings):
    from .diagnose import run

    return run(settings, load_schedule(args.schedule))


def cmd_baselines(args, settings):
    from .baselines import run

    return run(settings, rebuild_points=args.rebuild_points, write_report=not args.no_report)


def cmd_report(args, settings):
    from .report import run

    return run(settings, load_schedule(args.schedule), force=args.force)


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m pipeline", description="과제 2 데이터 진단·기준선 파이프라인")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"설정 파일 (기본 {DEFAULT_CONFIG})")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export-influx", parents=[common], help="(보드 B) InfluxDB를 월 단위로 내보내기")
    p.add_argument("--from", dest="start", help="첫 달 YYYY-MM (기본: 설정의 source.start)")
    p.add_argument("--to", dest="end", help="마지막 달 YYYY-MM (기본: 이번 달)")
    p.add_argument("--metric", action="append", help="항목만 고르기 (여러 번 가능)")
    p.add_argument("--overwrite", action="store_true", help="이미 받은 달도 다시 받기")
    p.set_defaults(func=cmd_export_influx)

    p = sub.add_parser("check-time", parents=[common], help="(보드 B) InfluxDB와 수집기 기록의 시각 차이")
    p.add_argument("--collector-db", default="data/collector.db", help="과제 1 수집기 DB (기본 data/collector.db)")
    p.add_argument("--hours", type=float, default=48)
    p.add_argument("--metric", action="append")
    p.set_defaults(func=cmd_check_time)

    sub.add_parser("preprocess", parents=[common], help="정제·5분 칸·공간 합치기·고장 표시").set_defaults(func=cmd_preprocess)

    p = sub.add_parser("split", parents=[common], help="기간 나누기와 정규화 값 (학습 기간만)")
    p.add_argument("--force", action="store_true", help="이미 있는 artifacts/split.json을 다시 만들기")
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("diagnose", parents=[common], help="결측률, 시간대 패턴, 일정 반응, 어긋난 날")
    p.add_argument("--schedule", default=DEFAULT_SCHEDULE)
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("baselines", parents=[common], help="평가 시점 고정과 기준선 3종 평가")
    p.add_argument("--rebuild-points", action="store_true", help="이미 있는 artifacts/eval_points.csv를 다시 만들기")
    p.add_argument("--no-report", action="store_true", help="docs/reports/002-baselines.md를 쓰지 않기 (다른 장비에서 예측 파일만 다시 만들 때)")
    p.set_defaults(func=cmd_baselines)

    p = sub.add_parser("report", parents=[common], help="진단서 초안 docs/reports/002-diagnosis.md")
    p.add_argument("--schedule", default=DEFAULT_SCHEDULE)
    p.add_argument("--force", action="store_true", help="이미 있는 진단서를 덮어쓰기 (손으로 쓴 해석이 지워진다)")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        return args.func(args, settings)
    except SettingsError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except (InfluxError, FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
