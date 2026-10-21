#!/usr/bin/env bash
# python -m state 줄임. 사용: ./state.sh run
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python -m state "$@"
