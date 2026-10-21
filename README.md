# AIGJ_01_012_timeseries-inference

교육장 센서(CO₂·온도·습도) 데이터로 60분 예측, 이상 탐지, 사용 여부 추정을 하고 Jetson Orin Nano에서 상시 추론 서비스로 운영하는 과제 저장소.

## 구성

| 경로 | 내용 |
|---|---|
| `docs/reports/` | 과제별 결과 보고 |
| `CLAUDE.md` | Claude Code 작업 규칙, 장비 정보 |
| `.specify/memory/constitution.md` | Spec Kit 원칙 (모든 과제가 지킬 규칙) |
| `.claude/skills/` | Spec Kit 명령 (`/speckit-*`) |
| `specs/` | 과제별 spec·plan·tasks (Spec Kit이 만든다) |

## 시작하기

과제 안내 원문은 팀 Private 저장소 `AIP-1-5/docs`에 있다. 두 저장소를 나란히 clone한다.

교육장 네트워크는 밖으로 나가는 22번 포트를 막으므로, 보드에서는 먼저 `~/.ssh/config`에 GitHub SSH를 443번으로 우회하는 설정을 넣는다(`../docs/environment.md`의 네트워크 항목).

```bash
mkdir -p ~/work && cd ~/work
git clone git@github.com:AIP-1-5/docs.git
git clone git@github.com:AIP-1-5/AIGJ_01_012_timeseries-inference.git
cd AIGJ_01_012_timeseries-inference
cp .env.example .env            # 값은 팀 단톡방에서 받는다
claude --add-dir ../docs        # 과제 문서도 읽을 수 있게 Claude Code 실행
```

## 수집기 (과제 1)

MQTT 센서 메시지를 받아 보드 안 SQLite(`data/collector.db`)에 저장한다. 설계는 `specs/001-data-collection/`, 실행·시험 순서는 [quickstart.md](specs/001-data-collection/quickstart.md).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest -q                                  # 단위 시험

set -a; source .env; set +a                          # MQTT 계정 불러오기
.venv/bin/python -m collector check-config           # 설정 점검
.venv/bin/python -m collector run                    # 앞에서 실행 (Ctrl+C로 종료)
sudo deploy/install-service.sh                       # 서비스로 등록 (재부팅 후 자동 시작)

.venv/bin/python -m collector status                 # 지금 잘 쌓이고 있나 (끊긴 항목이 있으면 종료 코드 3)
.venv/bin/python -m collector report --hours 24      # docs/reports/001-collection-status.md
.venv/bin/python -m collector export --from 2026-10-21 --to 2026-10-22 --out data/export.csv
```

| 파일 | 역할 |
|---|---|
| `config/collector.toml` | 브로커, 구독 토픽, 메시지 해석 규칙, 센서별 전송 간격 (형식이 바뀌면 여기만 고친다) |
| `collector/` | `parser.py` 해석, `store.py` 저장, `mqtt_client.py` 접속·재접속, `coverage.py` 커버율·결측 원인, `report.py` 현황·보고서 |
| `deploy/` | systemd 서비스 템플릿과 설치 스크립트 |

어디서 무엇을 하나: 코드 작성·분석·학습은 맥, 수집기 실행과 Jetson이 필요한 작업(TensorRT, 보드 측정, 상시 서비스)은 보드. 자세한 분담은 `../docs/environment.md`.

과제 진행 순서 (과제 하나씩):

1. `/speckit-specify` — `../docs/assignment.md`의 과제 N 부분을 기준으로 spec 작성
2. `/speckit-plan` → `/speckit-tasks` — 계획과 할 일 목록. 여기서 멈추고 검토
3. `/speckit-implement` — 구현
4. `/speckit-converge` — 확인 기준을 충족할 때까지 반복
