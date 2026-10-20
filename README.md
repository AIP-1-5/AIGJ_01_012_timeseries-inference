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

과제 진행 순서 (과제 하나씩):

1. `/speckit-specify` — `../docs/assignment.md`의 과제 N 부분을 기준으로 spec 작성
2. `/speckit-plan` → `/speckit-tasks` — 계획과 할 일 목록. 여기서 멈추고 검토
3. `/speckit-implement` — 구현
4. `/speckit-converge` — 확인 기준을 충족할 때까지 반복
