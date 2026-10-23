#!/bin/bash
# 같은 보드에서 다른 실습(EmbeddingGemma 2 등)을 센서 서비스와 안전하게 같이 돌리는 실행기.
# 각자 보드 계정에 ~/practice.sh로 복사해 쓴다: cp deploy/practice.sh ~/practice.sh && chmod +x ~/practice.sh
# 사용: ~/practice.sh 이름 실행할명령...      (예: ~/practice.sh 재웅 python app.py --port 8001)
#
# - 자물쇠: 한 보드에서 한 번에 한 사람만 (계정이 달라도 /tmp/practice.lock을 같이 본다)
# - 메모리 상한 2500MB, CPU·디스크는 센서 서비스(CPUWeight·IOWeight 400)보다 뒤 순서(50)
# - 메모리가 모자라 커널이 프로세스를 죽여야 하면 실습 앱이 먼저 죽게 OOM 점수 +500
#   (센서 서비스는 -800/-500, deploy/*.service). GPU에 올린 메모리는 상한에 다 잡히지 않을 수 있어 이것이 마지막 안전장치다
# - 8080은 센서 대시보드 포트라 실습 앱이 쓰지 않는다
# - 보드 A에서는 끝난 뒤 센서 서비스가 살아 있는지 확인한다
WHO="$1"; shift
if [ -z "$WHO" ] || [ $# -eq 0 ]; then
  echo "사용: ~/practice.sh 이름 실행할명령..."
  exit 2
fi
case " $* " in
  *" 8080 "*|*":8080"*|*"=8080"*)
    echo "8080은 센서 대시보드 포트예요. 다른 포트(예: 8001)를 써 주세요."
    exit 2 ;;
esac
LOCK=/tmp/practice.lock
[ -e "$LOCK" ] || { touch "$LOCK" && chmod 644 "$LOCK"; }
exec 9<"$LOCK"
if ! flock -n 9; then
  echo "지금 $(cat /tmp/practice.who.* 2>/dev/null) 사용 중이에요. 끝나면 다시 해 주세요."
  exit 1
fi
ME=/tmp/practice.who.$USER
echo "$WHO ($(date +%H:%M)부터)" > "$ME"
trap 'rm -f "$ME"' EXIT
free -h | head -2
systemd-run --user --scope -p MemoryMax=2500M -p CPUWeight=50 -p IOWeight=50 \
  choom -n 500 -- nice -n 10 "$@"
STATUS=$?
if [ "$(hostname)" = team5-a ] && ! curl -sf --max-time 3 localhost:8080/health > /dev/null; then
  echo "경고: 센서 서비스(8080)가 응답하지 않아요. 재웅에게 알려 주세요."
fi
exit $STATUS
