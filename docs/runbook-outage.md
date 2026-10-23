# 보드 A가 끊겼을 때 (네트워크·IP 문제, 다른 실습)

2026-10-23 보드 A가 유선 IP 문제로 네트워크가 끊겼다. 같은 일이 생겨도 센서 데이터가 비지 않게, 그리고 같은 보드에서 도는 다른 실습(EmbeddingGemma 2)이 센서 서비스를 죽이지 못하게 아래처럼 대비한다.

## 1. 무엇이 지켜 주나

| 걱정 | 대비 | 어디 |
| --- | --- | --- |
| 랜선이 빠지거나 IP가 겹친다 | 와이파이를 예비로 켜 둔다. 유선이 안 되면 와이파이로 나간다. IP가 겹치면 그 주소를 쓰지 않는다 | `deploy/setup-network-backup.sh` (sudo, 한 번) |
| 네트워크가 끊겼다 돌아온다 | 수집기가 스스로 다시 붙는다(최대 20초 간격, 5분 무응답이면 다시 접속) | `collector/mqtt_client.py` (이미 있음) |
| 보드가 재부팅된다 | 수집기·추론 서비스가 systemd로 자동 시작한다. 재시작을 몇 번 해도 포기하지 않는다 | `deploy/install-service.sh`, `deploy/install-inference.sh` (sudo) |
| 그래도 데이터가 빈다 | 보드 B 수집기가 같은 메시지를 따로 받고 있다. 빈 구간을 보드 B 저장소로 메운다(아래 3) | `collector snapshot`, `collector merge` |
| 실습 앱이 메모리를 다 쓴다 | 실습 앱은 2500MB 상한, 메모리가 모자라면 실습 앱이 먼저 꺼진다. CPU·디스크도 센서 서비스가 먼저 | `deploy/practice.sh`, `deploy/*.service`의 `OOMScoreAdjust`·`CPUWeight` |
| 실습 앱이 8080을 쓴다 | `practice.sh`가 8080이 들어간 명령을 막는다 | `deploy/practice.sh` |

- 메모리 점수: 수집기 −800, 추론 서비스 −500, 실습 앱 +500. 커널은 점수가 높은 프로세스부터 끈다.
- GPU에 올린 모델 메모리는 `MemoryMax`에 다 잡히지 않을 수 있다(Jetson은 CPU·GPU가 메모리를 같이 씀). 그래서 "실습 앱이 먼저 꺼지기"가 마지막 안전장치다.
- 와이파이 우선순위는 600, 유선은 100(기본). 숫자가 작은 쪽으로 나간다. 유선이 꽂혀 있는데 인터넷이 안 되면 NetworkManager 연결 확인이 유선 숫자를 크게 올려(보드 B에서 20100 관찰) 와이파이로 넘어간다.

## 2. 처음 한 번 (보드 A, 사용자가 직접)

```bash
cd ~/work/AIGJ_01_012_timeseries-inference && git pull
sudo deploy/setup-network-backup.sh          # 와이파이 예비 (비밀번호를 물으면 와이파이 비밀번호)
sudo deploy/install-service.sh               # 수집기 다시 설치 (몇 초 끊김)
tmux kill-session -t inference               # tmux로 띄운 추론 서비스를 끄고
sudo deploy/install-inference.sh             # systemd로 다시 켠다
cp deploy/practice.sh ~/practice.sh && chmod +x ~/practice.sh
```

확인:

```bash
ip route | grep default                      # 유선(metric 100)과 와이파이(metric 600) 두 줄
systemctl show collector-$USER inference-$USER -p OOMScoreAdjust -p CPUWeight -p ActiveState
curl -s localhost:8080/health
```

보드 B에서도 수집기를 다시 설치해 같은 설정(메모리 점수, 재시작 포기 안 함)을 받는다: `git pull && sudo deploy/install-service.sh`.

## 3. 끊긴 뒤 빈 구간 메우기

보드 B 수집기가 계속 돌고 있었다면 보드 B 저장소에 그 구간이 있다. 두 보드는 같은 브로커에서 같은 메시지(`deduplicationId`)를 받으므로, 이미 있는 행은 건너뛰고 빈 행만 들어간다. 여러 번 해도 결과가 같다.

```bash
# 1) 보드 B: 돌고 있는 저장소를 한 파일로 복사 (수집기를 멈추지 않아도 된다)
ssh board-b 'cd ~/work/AIGJ_01_012_timeseries-inference && ./collector.sh snapshot --out data/board-b-snapshot.db'
# 2) 맥: 보드 B → 보드 A로 옮기기
scp -3 board-b:work/AIGJ_01_012_timeseries-inference/data/board-b-snapshot.db board-a:work/AIGJ_01_012_timeseries-inference/data/
# 3) 보드 A: 끊긴 구간만 합치기 (한국 시간, 앞뒤 여유를 30분쯤 둔다)
ssh board-a 'cd ~/work/AIGJ_01_012_timeseries-inference && ./collector.sh merge data/board-b-snapshot.db --from 2026-10-23T09:00 --to 2026-10-23T13:00 && ./collector.sh status --hours 6'
```

- 합친 일은 저장소 사건(`merge`)으로 남고 수집 보고서(`report`)의 사건 표에 나온다. 커버율을 말할 때 "보드 B로 메운 구간"이라고 같이 적는다.
- 보드 B는 와이파이로 MQTT를 받으므로 보드 A 유선 문제와 같이 끊기지 않는다. 다만 보드 B도 같은 시간에 끊겼다면 메울 수 없다. 그 구간은 숨기지 않고 공백으로 남긴다.
- 과제 3 2차 시험 기간(2026-10-22 ~ 11-05) 데이터다. 메운 데이터도 학습·모델 선택에 쓰지 않는다.
- 데이터 파일(`data/*.db`)은 커밋하지 않는다.

## 4. 다른 실습을 할 때 (두 보드 공통)

- 실습 앱은 `~/practice.sh 이름 명령`으로만 켠다. 웹 앱은 8080 말고 다른 포트(예: 8001).
- 끝나면 보드 A는 `practice.sh`가 센서 서비스(`/health`)를 자동으로 확인한다. 경고가 나오면 재웅에게 알린다.
- 수집기, 추론 서비스, tmux, 네트워크 설정을 건드리지 않는다. 재부팅하지 않는다.
