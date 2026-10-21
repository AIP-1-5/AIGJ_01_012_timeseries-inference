#!/usr/bin/env bash
# python -m forecast 줄임. .venv의 Python으로 실행한다.
# 사용: ./forecast.sh samples | train --all | select | evaluate --split test --all | compare | analyze
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python -m forecast "$@"
