#!/usr/bin/env bash
# 추론 서비스를 systemd 서비스(inference-<계정>)로 설치하고 시작한다. 재부팅하면 자동으로 시작된다. 다시 실행해도 안전하다.
# 사용: sudo deploy/install-inference.sh            (sudo로 실행한 계정이 서비스 실행 계정이 된다)
#       sudo deploy/install-inference.sh <계정>
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "sudo로 실행해야 한다: sudo $0" >&2
    exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${1:-${SUDO_USER:-}}"
if [[ -z "$RUN_USER" || "$RUN_USER" == "root" ]]; then
    echo "실행 계정을 정할 수 없다. 개인 계정에서 sudo로 실행하거나 계정을 인자로 준다" >&2
    exit 1
fi
NAME="inference-$RUN_USER"
UNIT="/etc/systemd/system/$NAME.service"

for path in "$REPO/.env" "$REPO/.venv/bin/python" "$REPO/config/service.toml" "$REPO/data/models/manifest.json"; do
    if [[ ! -e "$path" ]]; then
        echo "없다: $path (모델은 보드 B에서 ./service.sh export 후 data/models/를 복사한다)" >&2
        exit 1
    fi
done

# 한 주기를 미리 계산해 본다 (모델·DB·설정 문제면 설치하지 않는다)
if ! sudo -u "$RUN_USER" bash -c "cd '$REPO' && ./service.sh once > /dev/null"; then
    echo "한 주기 계산에 실패해 설치를 멈춘다 (./service.sh once로 오류 확인)" >&2
    exit 1
fi

# tmux 등으로 직접 띄운 서비스가 같은 포트를 쓰고 있으면 먼저 끈다
PORT="$(grep -E '^port' "$REPO/config/service.toml" | head -1 | tr -dc '0-9')"
if ss -ltn "sport = :$PORT" | grep -q LISTEN && ! systemctl is-active --quiet "$NAME.service"; then
    echo "포트 $PORT를 다른 프로세스가 쓰고 있다. tmux에서 띄운 ./service.sh run을 먼저 멈춘다 (Ctrl+C)" >&2
    exit 1
fi

sed -e "s|@USER@|$RUN_USER|g" -e "s|@REPO@|$REPO|g" "$REPO/deploy/inference.service" > "$UNIT"
systemctl daemon-reload
systemctl enable "$NAME.service"
systemctl restart "$NAME.service"

echo "설치 완료: $UNIT (계정 $RUN_USER, 저장소 $REPO)"
echo "확인: systemctl status $NAME  /  journalctl -u $NAME -f  /  curl -s localhost:$PORT/health"
sleep 5
systemctl --no-pager --lines=5 status "$NAME.service" || true
