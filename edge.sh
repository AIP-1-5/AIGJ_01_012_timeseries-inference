#!/usr/bin/env bash
# python -m edge 줄임. 사용: ./edge.sh bench | report | all   (보드 B에서)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec "${EDGE_PYTHON:-.venv/bin/python}" -m edge "$@"
