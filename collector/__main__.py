"""CLI 진입점: python -m collector <명령> (contracts/cli.md)."""

import argparse
import logging
import sys
from pathlib import Path

from .config import ConfigError, load_config

DEFAULT_CONFIG = "config/collector.toml"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def cmd_run(args, config):
    from .mqtt_client import Collector
    from .store import Store

    config.broker.credentials()   # 비어 있으면 ConfigError → 종료 코드 2
    store = Store(config.storage_path)
    try:
        return Collector(config, store).run()
    finally:
        store.close()


def cmd_check_config(args, config):
    from datetime import datetime, timezone

    from .parser import find_source, parse_message

    print(f"설정 OK: {config.path}")
    print(f"  브로커: {config.broker.host}:{config.broker.port}, client_id={config.broker.client_id}")
    print(f"  저장소: {config.storage_path}")
    try:
        config.broker.credentials()
        print(f"  계정: {config.broker.username_env}, {config.broker.password_env} 설정됨")
    except ConfigError as exc:
        if args.credentials:
            raise
        print(f"  계정: 비어 있음 ({exc})")
    for source in config.sources:
        print(f"  소스 {source.name}: {source.topic} (항목: {', '.join(source.include) or '숫자 전부'})")
    if not args.sample:
        return EXIT_OK
    source = find_source(config.sources, args.topic) if args.topic else config.sources[0]
    if source is None:
        print(f"토픽 {args.topic}에 맞는 소스가 없다")
        return EXIT_ERROR
    payload = Path(args.sample).read_bytes()
    result = parse_message(args.topic or source.topic, payload, source, datetime.now(timezone.utc))
    print(f"\n샘플 해석 ({source.name}): {len(result.rows)}행, 실패 {result.rejects or '없음'}  (저장하지 않음)")
    for row in result.rows:
        print(f"  {row.measured_at}  {row.space} / {row.spot or '-'}  {row.device_id} ({row.device_profile})  "
              f"{row.metric}={row.value:g}{row.unit or ''}  fcnt={row.fcnt}  key={row.message_key}")
    return EXIT_OK if result.rows else EXIT_ERROR


def cmd_status(args, config):
    from .report import status

    return status(config, hours=args.hours, space=args.space)


def cmd_report(args, config):
    from .report import write_report

    return write_report(config, hours=args.hours, start=args.start, end=args.end,
                        out=args.out, exclude=args.exclude)


def cmd_export(args, config):
    from .report import export

    return export(config, start=args.start, end=args.end, metrics=args.metric, out=args.out)


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m collector", description="MQTT 센서 데이터 수집기")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help=f"설정 파일 (기본 {DEFAULT_CONFIG})")
    # --config는 명령 앞뒤 어디에 써도 된다 (python -m collector status --config ...)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", parents=[common], help="수집기 실행").set_defaults(func=cmd_run)

    p = sub.add_parser("status", parents=[common], help="지금 잘 쌓이고 있나")
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--space", help="이 공간만 보기")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("report", parents=[common], help="수집 현황 보고서(Markdown)")
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--from", dest="start", help="시작 (예: 2026-10-21T09:00, 한국 시간)")
    p.add_argument("--to", dest="end", help="끝 (예: 2026-10-22T09:00, 한국 시간)")
    p.add_argument("--exclude", action="append", default=[],
                   help="커버율에서 뺄 시험 시간대 '시작~끝' (한국 시간, 여러 번 가능)")
    p.add_argument("--out", default="docs/reports/001-collection-status.md")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("export", parents=[common], help="학습용 CSV 내보내기")
    p.add_argument("--from", dest="start", required=True, help="시작 날짜·시각 (한국 시간)")
    p.add_argument("--to", dest="end", required=True, help="끝 날짜·시각 (한국 시간, 포함 안 함)")
    p.add_argument("--metric", action="append", help="항목만 고르기 (여러 번 가능)")
    p.add_argument("--out", default="data/export.csv")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("check-config", parents=[common], help="설정 점검, 샘플 메시지 해석")
    p.add_argument("--sample", help="해석해 볼 메시지 파일(JSON)")
    p.add_argument("--topic", help="샘플의 토픽 (생략하면 첫 번째 소스)")
    p.add_argument("--credentials", action="store_true", help="브로커 계정 환경변수가 비어 있으면 실패 (서비스 설치 전 점검용)")
    p.set_defaults(func=cmd_check_config)
    return parser


def main(argv=None):
    setup_logging()
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        return args.func(args, config)
    except ConfigError as exc:
        logging.error("설정 오류: %s", exc)
        return EXIT_CONFIG
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
