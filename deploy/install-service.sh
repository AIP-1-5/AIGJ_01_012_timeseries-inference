#!/usr/bin/env bash
# 수집기를 systemd 서비스로 설치하고 시작한다. 다시 실행해도 안전하다.
# 사용: sudo deploy/install-service.sh            (sudo로 실행한 계정이 서비스 실행 계정이 된다)
#       sudo deploy/install-service.sh <계정>
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "sudo로 실행해야 한다: sudo $0" >&2
    exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${1:-${SUDO_USER:-}}"
UNIT=/etc/systemd/system/collector.service

if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
    echo "실행 계정을 정할 수 없다. 개인 계정에서 sudo로 실행하거나 계정을 인자로 준다" >&2
    exit 1
fi
for path in "$REPO/.env" "$REPO/.venv/bin/python" "$REPO/config/collector.toml"; do
    if [[ ! -e "$path" ]]; then
        echo "없다: $path (quickstart.md §0 준비를 먼저 한다)" >&2
        exit 1
    fi
done

if [[ "$(hostname)" == "team5-a" ]]; then
    echo "주의: 보드 A다. 재부팅 시험은 보드 B에서만 한다. 서비스 등록만 진행한다."
fi

# 설정 점검 (실패하면 설치하지 않는다)
sudo -u "$RUN_USER" bash -c "cd '$REPO' && set -a && source .env && set +a && .venv/bin/python -m collector check-config >/dev/null"

sed -e "s|@USER@|$RUN_USER|g" -e "s|@REPO@|$REPO|g" "$REPO/deploy/collector.service" > "$UNIT"
systemctl daemon-reload
systemctl enable collector.service
systemctl restart collector.service

echo "설치 완료: $UNIT (계정 $RUN_USER, 저장소 $REPO)"
systemctl --no-pager --lines=5 status collector.service || true
