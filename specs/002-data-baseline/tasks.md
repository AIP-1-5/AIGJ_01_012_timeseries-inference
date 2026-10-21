---

description: "Task list for 과제 2 — 데이터 이해와 기준선"
---

# Tasks: 과제 2 — 데이터 이해와 기준선

**Input**: Design documents from `/specs/002-data-baseline/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: plan.md가 `pytest` 단위 시험(정제·칸·분할 누수·기준선·평가 시점 재현성)을 요구하므로 포함한다. 시험을 먼저 쓰고 실패를 확인한 뒤 구현한다.

**어디서 하나**: 코드·단위 시험은 어디서든, **(보드 B)** 표시는 InfluxDB 내보내기와 시각 확인, **(맥)** 표시는 실제 데이터 분석. 보드 A는 쓰지 않는다.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 다른 파일이고 앞선 미완료 작업에 의존하지 않음
- **[Story]**: US1 정제·간격, US2 분할·정규화, US3 진단, US4 기준선, US5 진단서

---

## Phase 1: Setup

- [ ] T001 `pipeline/` 패키지와 폴더 만들기: `pipeline/__init__.py`, `artifacts/.gitkeep`, `docs/reports/figures/.gitkeep`
- [ ] T002 [P] `requirements-analysis.txt` 작성(`pandas`, `numpy`, `matplotlib`, `holidays`), README에 설치 안내
- [ ] T003 [P] `.gitignore` 확인: `data/`는 제외 유지, `artifacts/`와 `docs/reports/figures/`는 커밋 대상
- [ ] T004 [P] `pipeline/__main__.py`에 CLI 뼈대: `export-influx`, `check-time`, `preprocess`, `split`, `diagnose`, `baselines`, `report`, 공통 `--config` (contracts/cli.md)

---

## Phase 2: Foundational

- [ ] T005 [P] `config/preprocess.toml` 작성 (contracts/config.md: 허용 범위 CO₂ 300~5000·온도 0~45·습도 1~99, 5분 칸, 보간 15분, 값 고정 60분, 공간 4곳과 실습실 복도 센서 제외, 분할 28/28일, 평가 설정)
- [ ] T006 [P] `config/schedule.toml` 작성 (data-model.md: 실습실 평일 09:00 start, 11:30 partial "점심 1~3명 남을 수 있음", 13:00 start, 18:00 end, 출처 "사용자 제공 추정 일정 (2026-10-21)", 공휴일 KR)
- [ ] T007 `pipeline/settings.py`: 두 설정 파일 읽기와 검증(필수 키, 범위의 최소<최대, 공간 중복 금지)
- [ ] T008 `pipeline/loader.py`: 원시 CSV(InfluxDB 형식, 과제 1 수집기 export 형식)를 같은 열(`time, device_id, space, spot, metric, value`)로 읽기

**Checkpoint**: 설정과 원시 데이터를 읽을 수 있다

---

## Phase 3: 데이터 가져오기 (US1·US3의 전제)

- [ ] T009 `pipeline/influx.py`: 월 단위 Flux 조회(`urllib`, CSV 응답) → `data/raw/influx/<항목>/<YYYY-MM>.csv.gz` 스트리밍 저장, 받은 달 건너뛰기·이번 달 다시 받기, 토큰을 로그에 남기지 않기
- [ ] T010 `check-time` 명령: 과제 1 수집기 DB와 InfluxDB의 최근 이틀 같은 센서·항목 값을 짝지어 시각 차이 분포 출력 (research.md R2)
- [ ] T011 **(보드 B)** `export-influx --from 2025-08` 실행, 파일 크기·행 수 확인, `check-time` 결과 기록
- [ ] T012 **(맥)** `scp`로 `data/raw/influx` 가져오기

---

## Phase 4: User Story 1 - 믿을 수 있는 분석용 데이터 (Priority: P1) 🎯

**Goal**: 정제·5분 칸·공간 합치기·보간·고장 표시를 한 모듈로 (과제 3~8 공용)

**Independent Test**: 합성 원시 데이터로 범위 제거·칸 표기·중앙값·보간 경계·값 고정 표시가 기대대로, 두 번 실행 결과 동일

### Tests for User Story 1

- [ ] T013 [P] [US1] `tests/test_preprocess.py`: 범위 밖 값 제거와 건수, 칸 `[t−5분, t)` → `t` 표기(경계 값이 다음 칸으로 가지 않음), 공간 중앙값과 `exclude_devices`, 15분 이하만 보간(20분 빈칸은 결측 유지), 보간 칸 `interpolated=true`, 60분 같은 값 → `fault=stuck`, 중복 기록 한 번만, 같은 입력 두 번 → 같은 출력

### Implementation for User Story 1

- [ ] T014 [US1] `pipeline/preprocess.py`: 범위 제거 → 중복 제거 → 센서별 5분 칸 평균 → 공간 중앙값(`n_raw`, `n_sensors`) → 짧은 결측 보간 → 값 고정 표시 → 집계(`clean_stats`) (research.md R4·R5·R6·R9)
- [ ] T015 [US1] `preprocess` 명령 연결, 출력 `data/processed/clean_5min.csv.gz`, `clean_stats.csv`
- [ ] T016 **(맥)** [US1] 실제 데이터로 `preprocess` 실행, 공간·항목별 결측률과 제거 건수 확인

---

## Phase 5: User Story 2 - 기간 분할과 정규화 (Priority: P1)

**Goal**: 시간 순서 분할을 고정하고 정규화 값을 학습 기간에서만 계산 (완료 조건 1)

**Independent Test**: 겹침 없는 시간 순서 분할, 검증·시험 값을 바꿔도 정규화 값 동일

### Tests for User Story 2

- [ ] T017 [P] [US2] `tests/test_split.py`: 데이터 끝 기준 28/28일 경계(KST 자정), train<val<test 겹침 없음, **정규화 값이 검증·시험 값 변경에 영향받지 않음(SC-001)**, 보간·고장 칸 제외, `split.json`이 있으면 덮어쓰지 않음

### Implementation for User Story 2

- [ ] T018 [US2] `pipeline/split.py`: 분할 계산·저장·불러오기, `compute_normalization(clean, split)`·저장·불러오기 (research.md R7·R8)
- [ ] T019 [US2] `split` 명령 연결(`--force` 없으면 기존 파일 보존)
- [ ] T020 **(맥)** [US2] 실제 데이터로 `split` 실행, 시험 기간에 큰 결측·고장이 몰리지 않았는지 보고 필요하면 조정, `artifacts/split.json`·`normalization.json` 커밋

---

## Phase 6: User Story 3 - 데이터 진단 (Priority: P1)

**Goal**: 결측률 표, 평일 시간대 패턴, 일정 반응 시간, 어긋난 날, 고장 의심 구간

**Independent Test**: 일정 변화 뒤 알려진 지연으로 오르는 합성 CO₂에서 반응 시간이 맞게 나오고, 일정과 다른 날이 목록에 잡힌다

### Tests for User Story 3

- [ ] T021 [P] [US3] `tests/test_schedule.py`: 평일·주말·공휴일 판정(KR), 일정 변화 시각 목록, 반응 시간(합성: 09:00 뒤 7분에 +30ppm 넘음 → 7분), `partial`·`end` 종류별 분리, 평일인데 상승 < 100ppm인 날 → 어긋난 날, 주말인데 크게 오른 날 → 어긋난 날

### Implementation for User Story 3

- [ ] T022 [US3] `pipeline/schedule.py`: 일정 파일·공휴일·일정 변화 시각
- [ ] T023 [US3] `pipeline/diagnose.py`: 결측률·제거 건수 표, 평일 시간대별 평균(±표준편차) 그래프 `docs/reports/figures/002-hourly-<공간>-<항목>.png`, 반응 시간 분포 `002-response.png`(종류별), 어긋난 날 목록, 밤 CO₂ 바닥값 기반 영점 틀어짐 의심 표시 (research.md R9·R11)
- [ ] T024 [US3] `diagnose` 명령 연결, `data/processed/diagnosis.json`
- [ ] T025 **(맥)** [US3] 실제 데이터로 `diagnose` 실행, 그래프 확인 (점심 감소가 퇴실보다 작은지 등)

---

## Phase 7: User Story 4 - 기준선 3종 (Priority: P1)

**Goal**: 평가 시점 목록 고정, 기준선 3종의 5·15·30·60분 MAE (완료 조건 2·3)

**Independent Test**: 손으로 계산할 수 있는 합성 시계열에서 세 기준선의 예측과 MAE가 정확, 평가 시점 목록이 규칙대로 만들어지고 두 번 실행 결과 동일

### Tests for User Story 4

- [ ] T026 [P] [US4] `tests/test_baselines.py`: 현재값 유지·최근 1시간 평균(12칸, 9칸 이상)·어제 같은 시각 예측값, 평가 시점 규칙(보간·고장 정답 제외, 24시간 전 값 필요), **같은 입력이면 같은 `eval_points.csv`와 MAE (SC-002·SC-007)**, `eval_points.csv`가 있으면 그대로 사용

### Implementation for User Story 4

- [ ] T027 [US4] `pipeline/baselines.py`: 평가 시점 만들기·저장·불러오기, 기준선 3종 예측, 공간별·전체·평일 실습 시간 MAE와 평가 시점 수 (research.md R10)
- [ ] T028 [US4] `baselines` 명령 연결, `docs/reports/002-baselines.md` 성능표(측정 조건 포함)
- [ ] T029 **(맥)** [US4] 실제 데이터로 `baselines` 실행, `artifacts/eval_points.csv`·성능표 커밋

---

## Phase 8: User Story 5 - 진단서 (Priority: P2)

**Goal**: 1~2쪽 진단서, 결과마다 왜/그래서, 생각해 볼 점 답

- [ ] T030 [US5] `pipeline/report.py`: 진단서 초안 `docs/reports/002-diagnosis.md` (측정 조건, 정제 규칙과 이유, 결측률 표, 분할 기준과 "왜 무작위로 섞으면 안 되나", 패턴 그래프 링크, 반응 시간 중앙값·어긋난 날, 고장 의심과 처리, 기준선 요약, 자동 관찰 제안, 사람이 쓸 해석 칸)
- [ ] T031 [US5] `report` 명령 연결
- [ ] T032 **(맥)** [US5] 진단서 해석 작성: 가장 강한 기준선과 이유(완료 조건 3), '어제 같은 시각'이 약한/강한 이유와 강할 만한 공간, 고장 구간을 학습·평가에 넣을지, 점심 1~3명 잔류가 패턴에 보이는지

---

## Phase 9: Polish

- [ ] T033 [P] README에 분석 파이프라인 절 추가, `docs/cheatsheet.md`에 과제 2 명령 추가
- [ ] T034 [P] 과제 3 계획을 위한 메모: `artifacts/eval_points.csv`와 `normalization.json`을 반드시 쓸 것을 `specs/002-data-baseline/quickstart.md`와 다음 spec 초안에 명시
- [ ] T035 quickstart.md 전 과정을 처음부터 따라 해 보고 어긋난 명령 수정

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → Phase 3(데이터 가져오기)
- **US1**(전처리)은 Phase 2 뒤 바로 시작 가능(합성 데이터로 시험). 실제 실행(T016)은 Phase 3 뒤
- **US2**(분할)는 US1 출력이 필요
- **US3**(진단)는 US1 뒤. US2와 동시 가능
- **US4**(기준선)는 US1·US2 뒤
- **US5**(진단서)는 US3·US4 뒤

## Parallel Examples

```text
# 시험 먼저 동시에
T013 test_preprocess / T017 test_split / T021 test_schedule / T026 test_baselines

# 코드 동시에 (서로 다른 파일)
T009 influx.py / T014 preprocess.py / T022 schedule.py
```

## Implementation Strategy

1. **먼저 보드 B 내보내기(T009~T011)**: 데이터 양과 InfluxDB 시각 기준을 일찍 확인해야 이후 설계가 흔들리지 않는다
2. **전처리 → 분할 → 기준선**: 완료 조건 1·2·3에 직결
3. **진단과 진단서**: 해석(30%)의 중심. 점심 잔류 인원 같은 현장 정보를 그래프로 확인
4. 각 Checkpoint에서 확인 기준을 실제로 확인하고 숫자로 기록 (constitution 원칙 I)
