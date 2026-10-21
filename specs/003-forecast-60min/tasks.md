---

description: "Task list for 과제 3 — 60분 예측"
---

# Tasks: 과제 3 — 60분 예측

**Input**: Design documents from `/specs/003-forecast-60min/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md. 과제 2 결과(`data/processed/clean_5min.csv.gz`, `artifacts/`)가 맥에 있어야 한다.

**Tests**: plan.md가 누수·미래 정보·평가 정합성·시험 잠금 시험을 요구하므로 포함한다. 시험을 먼저 쓰고 실패를 확인한 뒤 구현한다.

**어디서 하나**: 코드·단위 시험은 어디서든, **(맥)** 표시는 실제 학습·평가. 보드는 쓰지 않는다.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 다른 파일이고 앞선 미완료 작업에 의존하지 않음
- **[Story]**: US1 표본, US2 학습·비교, US3 일정 가치, US4 분석, US5 심화

---

## Phase 1: Setup

- [ ] T001 `forecast/` 패키지 만들기: `forecast/__init__.py`, `data/forecast/`는 `.gitignore`의 `data/`로 이미 제외됨을 확인
- [ ] T002 [P] `requirements-train.txt` 작성(`torch>=2.4`), README에 과제 3 설치 안내(맥 전용, 보드에 설치하지 않음)
- [ ] T003 [P] `config/forecast.toml` 작성 (data-model.md 그대로: `lookback_minutes = 120`, `min_window_fraction = 0.75`, `kind = "gru"`, `hidden = 64`, `layers = 2`, `dropout = 0.1`, `target = "delta"`, `seeds = [1, 2, 3]`, `batch_size = 512`, `learning_rate = 1e-3`, `max_epochs = 30`, `patience = 5`, `device = "cpu"`, `names = ["full", "no_schedule", "sensor_only"]`)
- [ ] T004 [P] `forecast/settings.py`: forecast.toml 읽기·검증 (lookback이 5분의 배수, variants 이름이 세 가지 중 하나, target이 delta|value), 과제 2 `pipeline.settings`를 함께 불러 경로 공유

---

## Phase 2: Foundational

- [ ] T005 [P] `tests/test_forecast_features.py`: 하루 시각·요일 sin/cos 값, 공휴일(2026-05-05)과 대체공휴일(2026-03-02)의 `class_day=0`, 실습실 `in_class`(09:00~11:30·13:00~18:00 = 1, 11:30~13:00 = 0.5, 그 외 0, 다른 공간은 항상 0), 변형별 특성 개수(`full` 6, `no_schedule` 4, `sensor_only` 0)
- [ ] T006 `forecast/features.py`: 칸 시작 시각(KST) 기준 달력·일정 특성, `config/schedule.toml`·`pipeline.schedule` 재사용, 변형별 열 선택 (contracts/model-io.md의 순서 `tod_sin, tod_cos, dow_sin, dow_cos, class_day, in_class`)

**Checkpoint**: 달력·일정 특성이 학습과 과제 8 서비스에서 같은 함수로 나온다

---

## Phase 3: User Story 1 - 학습용 입력·출력 (Priority: P1) 🎯

**Goal**: 과거 2시간 창 + 미래 60분 달력·일정 + 12칸 변화량 정답·마스크 표본을 누수 없이 만든다

**Independent Test**: 같은 입력이면 같은 표본, 학습 표본 정답은 모두 학습 기간 안, 시험 표본이 eval_points와 짝이 맞는다

### Tests for User Story 1

- [ ] T007 [P] [US1] `tests/test_forecast_dataset.py`:
  - **누수(`leakage`)**: 학습 표본의 t와 t+60분이 모두 학습 기간 안. 검증·시험 기간 값을 크게 바꿔도 학습 표본 배열이 같다
  - **미래 정보**: `future`에는 달력·일정 특성만 있고 센서값이 없다. `past` 마지막 칸 = 시각 t
  - **정답**: `target = y(t+h) − y(t)`(정규화). 보간·고장·결측 정답 칸은 `target_mask = 0`. 회의실 10분 센서는 5·15분 칸 마스크 0
  - **표본 거르기**: `y(t)` 보간이면 제외, 창 사용 가능 < 75%면 제외, 제외 이유별 집계
  - **시험 표본**: `eval_points.csv`의 시각과 같은 집합
  - 같은 입력 두 번 → 같은 배열

### Implementation for User Story 1

- [ ] T008 [US1] `forecast/dataset.py`:
  - 공간별 격자 시계열 → `past`(L × (6 + C)), `future`(12 × C), `space`(4), `target`(12 × 2), `target_mask`, `base`, `meta`
  - 정규화는 `artifacts/normalization.json`, 기간은 `artifacts/split.json`(학습·검증 표본은 t, t+60분이 그 기간 안)
  - 시험 표본은 `artifacts/eval_points.csv`의 시각
  - (data-model.md 표본 규칙)
- [ ] T009 [US1] `forecast/__main__.py` CLI 뼈대와 `samples` 명령: 기간·공간별 표본 수, 제외 이유, 시험 표본 수 vs eval_points (`--save`면 npz)
- [ ] T010 **(맥)** [US1] 실제 데이터로 `samples` 실행, 표본 수와 제외 이유 기록 (긴 결측 2026-05-10~26, 사무실 2026-01~03이 빠지는지)

---

## Phase 4: User Story 2 - 학습하고 기준선과 비교 (Priority: P1)

**Goal**: 선형 → GRU 학습, 검증으로만 고르기, 시험 한 번 평가, 기준선과 같은 시점 비교표 (완료 조건 1·2·3)

**Independent Test**: 변화량 0 모델이 현재값 유지 MAE를 정확히 재현, 시험 잠금이 동작, 비교표에 같은 시점 수

### Tests for User Story 2

- [ ] T011 [P] [US2] `tests/test_forecast_model.py`: linear·gru 출력 모양 B × 12 × 2, 파라미터 수 ≤ 100,000, `torch.onnx.export` 가능, 같은 씨앗 → 같은 초기 가중치
- [ ] T012 [P] [US2] `tests/test_forecast_evaluate.py`:
  - **정합성**: 변화량 0을 내는 모델의 h별 MAE가 `pipeline.baselines`의 현재값 유지 MAE와 같다(합성 데이터)
  - **시험 잠금**: `selection.json`이 없으면 `evaluate --split test`가 종료 코드 2
  - **시험 기록**: 같은 모델 해시로 다시 평가하면 `003-test-log.md`에 새 줄을 쓰지 않는다
  - **가려진 L1 손실**: mask 0 칸은 손실·기울기에 영향 없음

### Implementation for User Story 2

- [ ] T013 [US2] `forecast/model.py`: `linear`(past 펼침 + future + space → 24), `gru`(2층 GRU hidden 64 → 마지막 상태 ⊕ future 펼침 ⊕ space → MLP → 12 × 2), 출력은 변화량
- [ ] T014 [US2] `forecast/train.py`: 가려진 L1(CO₂·온도 같은 비중), Adam, 검증 손실 기준 조기 종료(patience 5), 씨앗 고정(torch·numpy·random), `data/forecast/runs/<run_id>/`에 `model.pt`·`config.json`(코드 커밋, 데이터 기간, 장비)·`history.csv`·`val_metrics.json`. `run_id = <kind>-<variant>-L<lookback>-s<seed>`
- [ ] T015 [US2] `forecast/evaluate.py`:
  - 예측 → 원래 단위(`y(t) + delta × std`) → `predictions_<run_id>.csv.gz`(열은 과제 2 `baseline_predictions`와 같게)
  - `--split test`는 `selection.json`이 있을 때만 돈다
  - 시험 기록 `docs/reports/003-test-log.md`(시각, run_id, 모델 sha256, 60분 MAE). 같은 해시면 기존 결과 표시
- [ ] T016 [US2] `forecast/select.py`: 검증 60분 (CO₂ MAE/현재값 유지 + 온도 MAE/현재값 유지)/2가 가장 작은 실행 → `data/forecast/selection.json`(`chosen`, `criterion`, `candidates`, `model_sha256`, `decided_at`)
- [ ] T017 [US2] `forecast/compare.py`: 시험 예측 + 과제 2 `baseline_predictions.csv.gz` → h × 항목 × 공간·전체·평일 수업 시간 MAE와 시점 수 표, 완료 조건 1·2 통과/미통과 줄, 측정 조건 블록(`pipeline.baselines.conditions_block` 재사용) → `docs/reports/003-forecast.md`의 표 부분
- [ ] T018 [US2] CLI 연결: `train`(옵션·`--all`), `select`, `evaluate`, `compare`
- [ ] T019 **(맥)** [US2] 선형 → GRU(`full`, 씨앗 1) 학습, **검증** MAE로 현재값 유지·선형과 비교. GRU가 선형보다 못하면 원인 분석 후 진행(constitution VII)

---

## Phase 5: User Story 3 - 일정 정보의 가치 (Priority: P1)

**Goal**: `full` / `no_schedule` / `sensor_only` × 씨앗 3개로 "일정 정보를 넣으면 60분 CO₂ 오차가 ○ppm 줄어든다"

**Independent Test**: 세 변형이 일정 외 같은 설정이고, 차이가 씨앗 범위와 함께 나온다

- [ ] T020 [US3] `forecast/compare.py`에 변형 요약 추가: 변형별 시험 60분 CO₂·온도 MAE 씨앗 평균·최소·최대, `full − no_schedule`(일정 가치)과 `no_schedule − sensor_only`(시각·요일 가치), 공간별·평일 수업 시간별, 결론 문장 자동 초안
- [ ] T021 **(맥)** [US3] `train --all` (9회, `caffeinate`로 맥이 잠들지 않게) → `select` → `evaluate --split test --all` → `compare`
- [ ] T022 **(맥)** [US3] 완료 조건 1·2 확인과 일정 가치 결론을 `docs/reports/003-forecast.md`에 숫자로 기록

---

## Phase 6: User Story 4 - 공간별·순간별 분석 (Priority: P2)

**Goal**: 어디서·언제 틀리는지와 이유 (산출물 2, 생각해 볼 점 3개)

- [ ] T023 [P] [US4] `forecast/analyze.py`:
  - 공간별 60분 MAE와 현재값 유지 대비 비율 → `003-error-by-space.png`
  - 평일 일정 변화 시각(09:00·11:30·13:00·18:00) 전후 ±2시간 시각별 평균 오차(모델 vs 현재값 유지) → `003-error-by-time.png`
  - 오차 상위 50개 순간(공간, KST 시각, 요일, 일정 상태, 분류: 수업 시작·점심·퇴실·주말 사용·회의·기타)
  - 주말 사용일과 연휴 사이 휴강일의 오차 따로 집계
  - → `data/forecast/analysis.json`
- [ ] T024 [US4] 값 직접 예측 변형(`--target value`, GRU `full` 씨앗 1) 학습·검증 비교 (생각해 볼 점 2)
- [ ] T025 [US4] CLI `analyze` 연결
- [ ] T026 **(맥)** [US4] `analyze` 실행, 보고서에 공간별 분석·오차 큰 순간·생각해 볼 점 3개 답(회의실이 어려운 이유와 없는 정보·값 vs 변화량·가장 큰 오차 순간)을 해석과 함께 작성

---

## Phase 7: User Story 5 - 심화(B) (Priority: P3, 선택)

- [ ] T027 [P] [US5] `forecast/model.py`에 `lstm`, `tcn`(dilated causal conv) 추가, 모양·ONNX 시험 확장
- [ ] T028 **(맥)** [US5] 모델 3종 × 과거 1·2·4시간 검증 MAE 표, 최적 조합 근거(검증 기준), 고른 뒤 시험
- [ ] T029 **(맥)** [US5] 한 공간(예: 사무실)을 학습에서 빼고 그 공간 시험 MAE (일반화)

---

## Phase 8: Polish

- [ ] T030 [P] `docs/cheatsheet.md`에 과제 3 명령, README에 `forecast/` 절
- [ ] T031 [P] contracts/model-io.md를 실제 구현과 맞춰 갱신(특성 순서, 정규화, 출력), 과제 7·8 메모
- [ ] T032 quickstart.md 전 과정을 처음부터 따라 해 보고 어긋난 명령 수정, 확인 기준 3개 통과/미통과를 숫자로 tasks.md에 기록

---

## Dependencies & Execution Order

- Phase 1 → Phase 2(특성) → **US1**(표본)
- **US2**(학습·비교)는 US1 뒤. 실제 학습(T019)은 T010 뒤
- **US3**(일정 가치)는 US2의 학습·선택·평가·비교 코드 위에서
- **US4**(분석)는 US2 예측 파일이 있으면 시작 가능, 최종 보고는 US3 뒤
- **US5**(심화)는 선택. US3 끝난 뒤

## Parallel Examples

```text
# 시험 먼저 동시에
T005 test_forecast_features / T007 test_forecast_dataset / T011 test_forecast_model / T012 test_forecast_evaluate

# 코드 동시에 (서로 다른 파일)
T013 model.py / T016 select.py / T023 analyze.py
```

## Implementation Strategy

1. **MVP = US1 + US2 (GRU `full` 한 개)**: 완료 조건 1·2·3을 먼저 확인할 수 있다
2. **US3**: 같은 코드로 변형·씨앗만 늘려 일정 가치 결론
3. **US4**: 해석(30%)의 중심. 오차가 큰 순간을 근거로 과제 8 경보 설계에 연결
4. 시험 기간은 `select` 뒤 한 번. 시험 결과를 보고 다시 고르지 않는다
5. 각 Checkpoint에서 확인 기준을 실제로 확인하고 숫자로 기록 (constitution 원칙 I)
