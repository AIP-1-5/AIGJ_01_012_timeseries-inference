#!/usr/bin/env bash
# python -m anomaly 줄임. 사용: ./anomaly.sh inject | train | calibrate | evaluate | report | all
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python -m anomaly "$@"
