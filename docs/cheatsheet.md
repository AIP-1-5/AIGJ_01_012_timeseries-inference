# 명령어 모음 (수집기·분석)

보드에서 쓰는 명령을 상황별로 모았다. 모두 저장소 폴더에서 실행한다.

```bash
cd ~/work/AIGJ_01_012_timeseries-inference
cat docs/cheatsheet.md        # 이 파일 보기
```

`./collector.sh <명령>`은 `.env`를 불러온 뒤 `.venv/bin/python -m collector <명령>`을 실행하는 줄임이다.

## 매일 확인

| 하고 싶은 것 | 명령 |
|---|---|
| 지금 잘 쌓이고 있나 | `./collector.sh status` |
| 최근 1시간만 | `./collector.sh status --hours 1` |
| 한 공간만 | `./collector.sh status --space 실습실` |
| 서비스가 살아 있나 | `systemctl status collector-$USER --no-pager` |
| 최근 로그 20줄 | `journalctl -u collector-$USER -n 20 --no-pager` |
| 로그 계속 보기 (끝내기 Ctrl+C) | `journalctl -u collector-$USER -f` |
| 지금 들어오는 센서 값 보기 (끝내기 Ctrl+C) | `mosquitto_sub -h data.withio.net -p 1883 -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD" -t 'application/+/device/+/event/up' \| python3 ~/mqttwatch.py` (먼저 `set -a; source .env; set +a`) |

`status`에서 `← 끊김`이 붙은 줄이 있으면 그 센서가 한동안 안 들어온 것이다.

## 서비스 관리 (sudo)

| 하고 싶은 것 | 명령 |
|---|---|
| 처음 등록 / 다시 등록 | `sudo deploy/install-service.sh` |
| 다시 시작 (설정을 고친 뒤) | `sudo systemctl restart collector-$USER` |
| 멈추기 | `sudo systemctl stop collector-$USER` |
| 시작 | `sudo systemctl start collector-$USER` |
| 부팅 시 자동 시작 끄기 | `sudo systemctl disable collector-$USER` |

## 코드나 설정을 바꿨을 때

```bash
git pull
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # 의존성이 바뀌었을 때만
.venv/bin/pytest -q
./collector.sh check-config                                         # 설정 점검
sudo systemctl restart collector-$USER
./collector.sh status --hours 1
```

센서 간격이 다르면 `config/collector.toml`의 `[intervals]`만 고치고 재시작한다.

## 보고서와 데이터

| 하고 싶은 것 | 명령 |
|---|---|
| 24시간 보고서 | `./collector.sh report` → `docs/reports/001-collection-status.md` |
| 기간을 정해서 (한국 시간) | `./collector.sh report --from 2026-10-22T09:00 --to 2026-10-23T09:00` |
| 시험 시간대 빼고 | `./collector.sh report --exclude "2026-10-22T14:00~2026-10-22T14:30"` |
| CSV 내보내기 | `./collector.sh export --from 2026-10-21 --to 2026-10-22 --out data/export.csv` |
| 맥으로 가져오기 (맥에서) | `scp board-b:~/work/AIGJ_01_012_timeseries-inference/data/export.csv ~/Downloads/` |

## 장애 시험 (보드 B만, 자세한 순서는 specs/001-data-collection/quickstart.md §4)

| 시험 | 명령 |
|---|---|
| 브로커 차단 | `sudo iptables -I OUTPUT -p tcp --dport 1883 -j REJECT` |
| 차단 풀기 (이 시각을 기록) | `sudo iptables -D OUTPUT -p tcp --dport 1883 -j REJECT` |
| 차단이 남아 있는지 확인 | `sudo iptables -L OUTPUT -n \| grep 1883` (아무것도 안 나와야 정상) |
| 재부팅 | `sudo reboot` |
| 부팅 시각 / 서비스 시작 시각 | `uptime -s` / `systemctl show collector-$USER -p ActiveEnterTimestamp` |

## 문제가 생겼을 때

| 증상 | 확인 |
|---|---|
| `status`가 전부 끊김 | `systemctl status collector-$USER`, `journalctl -u collector-$USER -n 50` |
| 로그에 "설정 오류" | `./collector.sh check-config`로 원인 확인. 서비스는 설정 오류면 다시 시작하지 않는다 |
| 로그에 "환경변수가 비어 있다" | `.env`에 `MQTT_USERNAME`, `MQTT_PASSWORD`가 있는지 (`./collector.sh check-config --credentials`). 고친 뒤 `sudo systemctl restart collector-$USER` (설정 오류로 멈춘 서비스는 자동으로 다시 시작하지 않는다) |
| `nano: command not found` | `sudo apt install -y nano` (apt가 권해도 `autoremove`는 하지 않는다) |
| 브로커에 안 붙음 | `nc -zv -w 5 data.withio.net 1883`, 차단 규칙이 남아 있는지 (위 iptables 확인) |
| `git pull`이 10.250.250.10에서 막힘 | 유선의 DNS를 쓰고 있다. `sudo nmcli connection modify "Wired connection 1" ipv4.ignore-auto-dns yes ipv4.never-default yes` 후 `sudo nmcli connection up "Wired connection 1"` |

## 과제 2: 데이터 진단·기준선

`./pipeline.sh <명령>`은 `.env`를 불러온 뒤 `.venv/bin/python -m pipeline <명령>`을 실행하는 줄임이다. 순서는 `specs/002-data-baseline/quickstart.md`.

| 하고 싶은 것 | 어디서 | 명령 |
|---|---|---|
| InfluxDB 접속 확인 | 보드 B | `curl -sI --max-time 5 http://10.116.64.13:8086/ping \| head -1` → `204 No Content` |
| 과거 데이터 내려받기 (끊기면 같은 명령 다시) | 보드 B (tmux 안) | `./pipeline.sh export-influx --from 2025-08` |
| 한 항목·한 달만 다시 받기 | 보드 B | `./pipeline.sh export-influx --from 2026-03 --to 2026-03 --metric co2 --overwrite` |
| InfluxDB 시각 확인 (pandas 필요) | 보드 B | `.venv/bin/pip install pandas && ./pipeline.sh check-time` |
| 맥으로 가져오기 | 맥 | `mkdir -p data/raw && scp -r board-b:~/work/AIGJ_01_012_timeseries-inference/data/raw/influx data/raw/` |
| 정제 → 분할 → 진단 → 기준선 → 진단서 | 맥 | `./pipeline.sh preprocess && ./pipeline.sh split && ./pipeline.sh diagnose && ./pipeline.sh baselines && ./pipeline.sh report` |
| 분할을 다시 정하기 (이유를 진단서에 적는다) | 맥 | `./pipeline.sh split --force` |
| 평가 시점을 다시 만들기 (이유를 적는다) | 맥 | `./pipeline.sh baselines --rebuild-points` |
| InfluxDB 화면을 맥에서 보기 | 맥 | `ssh -L 8086:10.116.64.13:8086 board-b` 켜 두고 브라우저 `http://localhost:8086` (화면 로그인은 토큰이 아니라 계정이 필요) |
