# Implementation Plan: 과제 3 — 60분 예측

**Branch**: `003-forecast-60min` (작업 브랜치: `claude/brave-fermi-q8umi1`) | **Date**: 2026-10-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-forecast-60min/spec.md`

## Summary

과제 2의 정제 결과·분할·정규화·평가 시점을 그대로 써서, 공간·시각마다 과거 2시간(24칸)의 CO₂·온도·습도와 미래 60분의 달력·일정 정보를 입력으로 받아 60분(12칸) 뒤까지의 **변화량**을 한 번에 내는 모델을 맥에서 학습한다.

진행 순서:
1. 같은 입력의 선형 모델로 설계를 확인한다(단순한 것부터).
2. 2층 GRU를 학습한다.
3. 일정 포함 / 일정 제외(시각·요일만) / 센서값만 세 변형을 씨앗 3개로 학습해 일정 정보의 가치를 잰다.
4. 검증 기간으로만 최종 모델을 고르고, `selection.json`을 남긴 뒤 시험 기간을 한 번 평가한다.
5. 과제 2 기준선과 같은 시점·같은 지표로 비교표를 만들고, 공간별·순간별 오차를 해석한다.

## Technical Context

**Language/Version**: Python 3.12 (3.11 호환)

**Primary Dependencies**:
- `torch` — 학습, `requirements-train.txt`. 맥 arm64 설치 파일 사용
- 과제 2의 `pipeline` 재사용 — pandas, numpy, matplotlib, holidays
- 보드에는 설치하지 않는다(과제 7에서 ONNX로 옮김)

**Storage**:
- 파일만 쓴다. 입력은 과제 2 산출물.
- 커밋하지 않음: 모델·표본·예측 → `data/forecast/`
- 커밋함: 보고서·그래프·시험 기록 → `docs/reports/`, 설정 → `config/forecast.toml`

**Testing**: `pytest`. 합성 데이터로 시험하는 것:
- 표본 규칙
- 누수: 학습 정답이 학습 기간 안, 검증·시험 값을 바꿔도 학습 표본이 같음
- 미래 정보: 달력·일정만 들어가는지
- 출력 모양·ONNX 내보내기 가능
- 평가 정합성: 변화량 0 모델 = 현재값 유지 MAE
- 시험 평가 잠금: `selection.json` 없으면 거부

**Target Platform**: 학습·평가는 맥(Apple Silicon, CPU 기본·MPS 선택). 모델은 과제 7에서 보드 B로 옮겨 측정하고, 과제 8에서 보드 A에서 운영한다.

**Project Type**: 학습·평가 파이프라인 + CLI (`python -m forecast`)

**Performance Goals**:
- 학습 한 번(변형 1 × 씨앗 1)이 맥 CPU에서 30분 안. 전체 9회는 몇 시간 안
- 모델 파라미터 10만 개 이하(보드에서 한 시점 예측 1초 미만, SC-007)

**Constraints**:
- 시험 기간은 선택 뒤 한 번만 평가한다
- 과제 2 전처리·분할·정규화·평가 시점을 바꾸지 않는다
- 데이터·모델 파일은 커밋하지 않는다
- 같은 씨앗이면 같은 결과(CPU)

**Scale/Scope**: 표본 수 = 학습 약 357일 × 288칸 × 4공간 중 사용 가능 표본(대략 30만), 검증 약 3만, 시험은 `eval_points.csv` 기준 약 4만 8천(시점×h).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. 확인 기준이 곧 완료 조건 | 통과 | spec 완료 조건 3개 원문, SC-001~003, quickstart §5에 확인 방법과 기대 숫자 |
| II. 공정한 평가와 누수 금지 | 통과 | 과제 2 분할·정규화·평가 시점 재사용, 학습·검증 표본의 정답이 각 기간 안, 시험 평가 잠금(`selection.json`), 누수 자동 시험, 보간·고장 정답 제외 |
| III. 전처리는 한 곳에서 | 통과 | `pipeline.preprocess` + `forecast/dataset.py`(특성 함수) 하나를 학습·과제 8 서비스가 같이 씀. 입출력 형식 문서(contracts/model-io.md) |
| IV. 측정 조건 기록 | 통과 | 실행마다 `config.json`(커밋, 데이터 기간, 씨앗, 장비), 모든 표 위에 측정 조건 |
| V. 결과에는 해석 | 통과 | 공간별·순간별 분석, 일정 가치 결론 문장, 생각해 볼 점 3개 답 |
| VI. 사람 손 없이 복구 | 해당 적음 | 학습은 상시 실행이 아님. 학습이 끊기면 같은 명령으로 다시(실행 단위가 작음) |
| VII. 단순한 것부터 | 통과 | 현재값 유지(과제 2) → 선형 → GRU 순서. GRU는 선형보다 나을 때만 최종 후보. 3종 비교는 심화(B) |
| 운영 제약 | 통과 | 학습은 맥, 보드 미사용. 모델 작게(보드 메모리·속도 예산은 과제 7에서 실측) |

**Phase 1 설계 후 재확인**: 위 판정 유지. 위반 없음.

## Project Structure

### Documentation (this feature)

```text
specs/003-forecast-60min/
├── spec.md
├── plan.md              # 이 문서
├── research.md          # R1~R10: 장비, 표본, 입력, 출력·모델, 손실, 누수, 하이퍼파라미터, 평가, 분석, 결과물
├── data-model.md        # 표본·설정·실행 기록·선택·시험 기록·예측 형식
├── quickstart.md        # 실행 순서와 확인 기준
├── contracts/
│   ├── cli.md           # python -m forecast 명령
│   └── model-io.md      # 모델 입출력 형식 (과제 6·7·8용)
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
forecast/
├── __init__.py
├── __main__.py          # CLI: samples, train, select, evaluate, compare, analyze
├── settings.py          # config/forecast.toml 읽기
├── features.py          # 달력·일정 특성 (변형별)                              ★ 과제 8 공용
├── dataset.py           # 표본 만들기: 과거 창, 미래 정보, 변화량 정답, 마스크  ★ 과제 8 공용
├── model.py             # linear, gru (심화: lstm, tcn), ONNX 내보내기 가능한 구조
├── train.py             # 가려진 L1 손실, 조기 종료, 실행 기록
├── select.py            # 검증 기준 고르기 → selection.json
├── evaluate.py          # val/test 예측, 시험 잠금, 시험 기록
├── compare.py           # 기준선 + 모델 비교표, 일정 포함/제외 요약 (pipeline.baselines 재사용)
└── analyze.py           # 공간별·시각별 오차, 오차 상위 순간, 그래프

config/forecast.toml
requirements-train.txt
tests/
├── test_forecast_features.py
├── test_forecast_dataset.py   # 누수·미래 정보·마스크
├── test_forecast_model.py     # 모양, 변화량 0 = 현재값 유지
└── test_forecast_evaluate.py  # 평가 정합성, 시험 잠금
docs/reports/                  # 003-forecast.md, 003-test-log.md, figures/003-*.png
```

**Structure Decision**: 과제 2의 `pipeline/`(데이터·기준선)과 분리된 `forecast/` 패키지. 데이터 읽기·정답 정의·지표는 `pipeline`을 불러 써서 기준선과 같은 계산을 보장한다.

## 확인 기준 ↔ 설계 연결

| 완료 조건 | 설계 요소 | 확인 |
|---|---|---|
| 1. 60분 CO₂ 30% 이상 개선 | 변화량 출력(현재값 유지에서 출발), 일정·시각 미래 정보, GRU | 비교표 60분 CO₂ 전체 ≤ 현재값 유지 × 0.7 |
| 2. 온도 60분이 현재값 유지보다 좋음 | 같은 모델의 온도 출력, 같은 비중 손실 | 비교표 60분 온도 전체 < 현재값 유지 |
| 3. 시험 기간 미사용 | 기간 안 정답 규칙, 검증만으로 조기 종료·선택, `selection.json` 잠금, 시험 기록 | 누수 자동 시험, 선택 시각 < 시험 시각 |

## 위험과 대응

| 위험 | 대응 |
|---|---|
| 온도는 현재값 유지가 0.15℃로 이미 매우 작아 이기기 어렵다 | 변화량 출력이 기본 0이라 최소한 비슷하게 시작한다. 검증에서 온도가 지면 온도 손실 비중·입력(습도, 일정)을 검증 기간으로만 조정하고 그 과정을 기록한다 |
| 60분 CO₂ 30% 개선이 안 나옴 | 오차가 몰린 순간(수업 시작·퇴실)을 검증 기간에서 분석해 입력을 보강한다(미래 일정 칸 해상도, 과거 길이). 시험 기간은 보지 않는다. 끝내 안 되면 결과를 그대로 보고하고 원인을 해석한다 |
| 주말 사용(64일)은 일정에 없다 | 현재 CO₂ 흐름 입력으로 알아채게 한다. 주말 사용일 오차를 따로 보고한다 |
| 회의실 예약 자료가 없다 | 회의실 일정 정보는 시각·요일뿐이라는 한계를 결과에 적고, 강사님께 예약 자료를 요청한다(심화 B) |
| MPS 결과가 실행마다 조금 다름 | 재현용 기본은 CPU. MPS로 빠르게 탐색한 뒤 최종 실행은 CPU |
| 학습 시간이 길어짐 | 표본 수는 그대로 두고 에폭·조기 종료로 조절. 9회 학습은 tmux나 백그라운드로(맥이 잠들지 않게 `caffeinate`) |

## Complexity Tracking

| 추가한 복잡도 | 이유 | 더 단순한 대안을 안 쓴 이유 |
|---|---|---|
| GRU (선형 위에) | 과제가 딥러닝 1종 이상을 요구. 수업 시작처럼 흐름이 바뀌는 순간을 선형보다 잘 잡을 것으로 기대 | 선형은 비교 기준으로 함께 보고한다. GRU가 검증에서 선형보다 못하면 원인부터 찾는다 |
| 세 변형 × 씨앗 3개 | "일정 정보의 가치"를 흔들림과 구분해 숫자로 답해야 함 | 씨앗 1개로는 차이가 난수 때문인지 알 수 없다 |
