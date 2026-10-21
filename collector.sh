#!/usr/bin/env bash
# python -m collector 줄임. .env를 불러오고 .venv의 Python으로 실행한다.
# 사용: ./collector.sh status | report | export ... | run | check-config
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
exec .venv/bin/python -m collector "$@"
