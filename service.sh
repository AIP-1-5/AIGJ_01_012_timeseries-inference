#!/usr/bin/env bash
# python -m service 줄임. .env(MQTT 계정)를 불러오고 .venv의 Python으로 실행한다.
# 사용: ./service.sh export (보드 B) | once | run | summary --hours 12
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
mkdir -p data/service
exec .venv/bin/python -m service "$@"
