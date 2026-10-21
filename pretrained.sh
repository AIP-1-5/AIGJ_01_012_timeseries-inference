#!/usr/bin/env bash
# python -m pretrained 줄임. 사용: ./pretrained.sh predict | bench | report | all
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python -m pretrained "$@"
