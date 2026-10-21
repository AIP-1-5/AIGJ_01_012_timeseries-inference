# Quickstart & 확인 기준 시험: 과제 1

모든 단계는 **보드 B, 개인 계정**에서 한다(`jaeung@team5-b`). 보드 A 배포는 마지막 절.
명령과 설정 형식은 `contracts/cli.md`, `contracts/config.md`를 따른다.

## 0. 준비

```bash
cd ~/work/AIGJ_01_012_timeseries-inference
git pull
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
# .env에 MQTT_USERNAME, MQTT_PASSWORD가 채워져 있어야 한다
timedatectl                          # "System clock synchronized: yes", 시간대 확인
```

## 1. 단위 시험

```bash
.venv/bin/pytest -q
```

기대: 해석(실제 메시지 형식 샘플), 중복 제거, 커버율·결측 구간·원인 추정 시험이 모두 통과.

## 2. 앞에서 돌려 보기

```bash
set -a; source .env; set +a
.venv/bin/python -m collector check-config --sample tests/fixtures/chirpstack_up.json
.venv/bin/python -m collector run          # 2~3분 뒤 Ctrl+C
.venv/bin/python -m collector status --hours 1
```

기대: 공간·센서·항목별 건수가 늘고, 마지막 수신 시각이 최근이다.

## 3. 서비스로 등록 (자동 시작)

```bash
sudo deploy/install-service.sh            # 서비스 파일 설치, enable, start
systemctl status collector --no-pager
journalctl -u collector -n 20 --no-pager
```

## 4. 확인 기준 시험

### SC-001 브로커 꺼짐 → 1분 안에 재수집 (3회)

```bash
sudo iptables -I OUTPUT -p tcp --dport 1883 -j REJECT     # 브로커로 가는 길 막기
sleep 120
sudo iptables -D OUTPUT -p tcp --dport 1883 -j REJECT     # 풀기 → 이 시각을 기록
.venv/bin/python -m collector status --hours 1             # 푼 시각 이후 첫 저장 시각 확인
```

기대: 푼 시각부터 첫 새 저장까지 60초 이내, 3회 모두. 사건 기록에 `disconnect` → `connect`가 보인다.

### SC-004 네트워크 끊김 → 1분 안에 재수집

SC-001과 같은 방식(와이파이 끄기는 SSH가 끊기므로 쓰지 않음). 기대: 60초 이내.

### SC-002 재부팅 → 자동 시작

```bash
sudo reboot
# 다시 접속한 뒤
uptime -s
systemctl show collector -p ActiveEnterTimestamp
.venv/bin/python -m collector status --hours 1
```

기대: 로그인 전에 서비스가 시작되어 있고, 부팅 후 첫 저장 시각이 기록된다(보고서에 부팅 완료→첫 저장 시간을 적는다).

### SC-005 형식 변경을 설정만으로 대응

```bash
sudo apt install -y mosquitto                               # 시험용 로컬 브로커 (보드 B만, localhost에서만 열림)
export TEST_MQTT_USERNAME=test TEST_MQTT_PASSWORD=test      # 로컬 브로커용 아무 값 (운영 계정과 분리)
.venv/bin/python -m collector check-config --config config/examples/format-change.toml \
    --sample tests/fixtures/format_changed.json --topic test/new-format      # 저장 없이 해석 결과 미리 보기
.venv/bin/python -m collector run --config config/examples/format-change.toml &   # 로컬 브로커에 붙는 두 번째 수집기
mosquitto_pub -h localhost -t 'test/new-format' -f tests/fixtures/format_changed.json
.venv/bin/python -m collector status --config config/examples/format-change.toml --hours 1
kill %1                                                     # 시험용 수집기 종료
```

기대: 설정 파일만 고친 상태에서 새 형식 값이 올바른 공간·항목으로 저장된다. 코드 diff는 없다.

### SC-007 동시 읽기가 수집을 방해하지 않음

```bash
.venv/bin/python -m collector export --from 2026-10-01 --to 2026-12-31 --out data/export.csv
.venv/bin/python -m collector status --hours 1
```

기대: 내보내는 동안에도 1분 간격 저장이 빠지지 않는다.

### SC-003 24시간 커버율 95% + 결측 원인 설명

서비스로 24시간 이상 돌린 뒤 (장애 시험을 한 시간대는 `--exclude`로 빼거나, 시험이 없는 24시간을 `--from/--to`로 고른다. 시각은 한국 시간):

```bash
.venv/bin/python -m collector report --hours 24 --out docs/reports/001-collection-status.md
.venv/bin/python -m collector report --from 2026-10-22T09:00 --to 2026-10-23T09:00 \
    --exclude "2026-10-22T14:00~2026-10-22T14:30" --out docs/reports/001-collection-status.md
```

기대: 센서·항목별 간격 기준 커버율 95% 이상. 결측 구간마다 추정 원인과 근거가 있다. 장애 시험을 한 시간대는 "시험으로 인한 결측"으로 따로 표시하고, 시험이 없는 연속 24시간 구간으로 판정한다.

## 5. 보드 A 배포 (최종 운영)

```bash
ssh board-a
nc -zv -w 5 data.withio.net 1883          # MQTT 접속 먼저 확인
# 저장소 clone/pull, .venv, .env 준비 후 config의 client_id와 storage.path를 보드 A용으로 바꾼다
sudo deploy/install-service.sh
```

보드 A는 재부팅하지 않는다. 재부팅 시험 결과는 보드 B 결과로 대신하고 같은 서비스 파일을 쓴다는 점을 보고서에 적는다. 24시간 커버율은 보드 A에서 다시 확인한다.
