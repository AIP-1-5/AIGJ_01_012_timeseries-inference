#!/usr/bin/env bash
# 랜선(유선)이 끊기거나 IP 문제가 생겨도 보드가 인터넷·MQTT에 붙어 있게 와이파이를 예비로 켠다. 다시 실행해도 안전하다.
# 유선이 되면 유선을 쓰고(경로 우선순위 100), 유선이 안 되면 와이파이(600)로 나간다.
# 유선은 꽂혀 있는데 인터넷이 안 되는 경우(IP 충돌 등)는 NetworkManager 연결 확인이 유선 우선순위를 낮춰 와이파이로 넘어간다.
# 사용: sudo deploy/setup-network-backup.sh            (와이파이 이름 기본값 CHOSUN_Free)
#       sudo deploy/setup-network-backup.sh <와이파이 이름>
# 지금 쓰는 유선 연결은 끊지 않는다 (원격으로 실행해도 접속이 안 끊기게). 바뀐 유선 설정은 다음 연결부터 적용된다.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "sudo로 실행해야 한다: sudo $0" >&2
    exit 1
fi

SSID="${1:-CHOSUN_Free}"
WIFI_METRIC=600

WIFI_DEV="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')"
if [[ -z "$WIFI_DEV" ]]; then
    echo "와이파이 장치가 없다 (nmcli device로 확인)" >&2
    exit 1
fi

# 1) 와이파이 연결 만들기 (이미 있으면 그대로 쓴다). 비밀번호가 있는 망이면 물어본다
if ! nmcli -t -f NAME connection show | grep -qx "$SSID"; then
    echo "와이파이 $SSID 연결을 만든다 ($WIFI_DEV)"
    nmcli device wifi rescan ifname "$WIFI_DEV" || true
    sleep 3
    nmcli --ask device wifi connect "$SSID" ifname "$WIFI_DEV" name "$SSID"
fi

# 2) 와이파이는 예비: 부팅 때 자동으로 붙고, 유선보다 우선순위를 낮게
nmcli connection modify "$SSID" connection.autoconnect yes \
    ipv4.route-metric "$WIFI_METRIC" ipv6.route-metric "$WIFI_METRIC"
nmcli connection up "$SSID" ifname "$WIFI_DEV" > /dev/null

# 3) 유선: 같은 IP를 다른 기기가 쓰고 있으면(IP 충돌) 그 주소를 쓰지 않게 주소 중복 검사를 켠다
WIRED="$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2=="802-3-ethernet"{print $1; exit}')"
if [[ -n "$WIRED" ]]; then
    METHOD="$(nmcli -g ipv4.method connection show "$WIRED")"
    nmcli connection modify "$WIRED" ipv4.dad-timeout 3000 connection.autoconnect yes
    echo "유선 연결: $WIRED (주소 받는 방식: $METHOD)"
    if [[ "$METHOD" == "manual" ]]; then
        echo "  주의: 유선이 고정 IP다. 교육장 망은 DHCP로 주소를 주므로 고정 IP는 다른 기기와 겹칠 수 있다."
        echo "  DHCP로 바꾸려면 현장에서(원격 접속이 끊길 수 있음): sudo nmcli connection modify \"$WIRED\" ipv4.method auto ipv4.addresses '' ipv4.gateway ''"
    fi
else
    echo "지금 켜진 유선 연결이 없다. 와이파이만으로 나간다"
fi

echo
echo "== 경로 (metric이 작은 쪽으로 나간다) =="
ip route | grep '^default' || true
echo "== MQTT 브로커로 나가는 길 =="
ip route get "$(getent ahostsv4 data.withio.net | awk '{print $1; exit}')" || true
echo "== 인터넷 연결 확인 (full이면 됨) =="
nmcli networking connectivity check || true
