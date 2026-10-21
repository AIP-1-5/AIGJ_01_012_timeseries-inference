# Implementation Plan: 과제 2 — 데이터 이해와 기준선

**Branch**: `002-data-baseline` (작업 브랜치: `claude/brave-fermi-q8umi1`) | **Date**: 2026-10-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-data-baseline/spec.md`

## Summary

교육장 InfluxDB의 실데이터(2025-08-08~)를 보드 B에서 월 단위로 내보내 맥으로 옮기고, 설정 파일로 정의한 정제 규칙(허용 범위, 15분 이하 보간, 값 고정 표시)과 5분 칸(`[t−5분, t)` → `t`), 공간별 중앙값으로 분석용 시계열을 만든다. 시간 순서로 학습·검증(28일)·시험(28일)을 나눠 `artifacts/split.json`에 고정하고, 정규화 값은 학습 기간 관측값으로만 계산한다. 결측률·평일 시간대 패턴·실습실 추정 일정 대비 CO₂ 반응 시간과 어긋난 날을 진단하고, 평가 시점 목록(`artifacts/eval_points.csv`)을 고정한 뒤 기준선 3종의 5·15·30·60분 MAE를 낸다. 이 전처리·분할·평가 시점은 과제 3 이후와 과제 8 서비스가 그대로 쓴다.

## Technical Context

**Language/Version**: Python 3.12 (3.11 호환)

**Primary Dependencies**: `pandas`, `numpy`, `matplotlib`, `holidays` (`requirements-analysis.txt`로 분리. 수집기 의존성은 그대로 `paho-mqtt` 하나). InfluxDB 조회는 표준 라이브러리 `urllib`

**Storage**: 파일. `data/raw/influx/*.csv.gz`, `data/processed/*`(커밋 안 함), `artifacts/*.json|csv`(커밋)

**Testing**: `pytest` 단위 시험 (합성 시계열로 정제·보간·칸 표기·공간 중앙값·분할·정규화 누수·기준선 값·평가 시점 재현성)

**Target Platform**: 내보내기·시각 확인은 보드 B(InfluxDB가 보드 B 유선으로만 닿음), 나머지 분석은 맥. 전처리 모듈은 과제 8에서 보드가 불러 쓸 수 있게 플랫폼 독립으로 작성

**Project Type**: 분석 파이프라인 + CLI

**Performance Goals**: 1년치(공간 4 × 항목 3 × 5분 칸 ≈ 130만 칸) 전처리 1분 이내(맥), 기준선 평가 1분 이내

**Constraints**: 누수 금지(분할·정규화·평가 시점), 같은 입력이면 같은 결과, 토큰은 `.env`에서만, 데이터 파일 커밋 금지, 보드 B에서는 내보내기만(메모리)

**Scale/Scope**: 원시 행 수천만 단위 가능(센서 15대 × 1분 × 1년) → 월 단위 파일로 나눠 처리

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. 확인 기준이 곧 완료 조건 | 통과 | spec 완료 조건 3개 원문, SC-001~003, quickstart §4에 확인 방법. 산출물(진단서, 기준선 성능표)이 `report`·`baselines` 출력으로 연결 |
| II. 공정한 평가와 누수 금지 | 통과 | 시간 순서 분할 고정(`split.json`), 정규화는 학습 기간 관측값만(자동 시험), 보간 값은 정답에서 제외, 평가 시점 목록 고정·공유, 시험 기간은 기준선 평가에만 사용 |
| III. 전처리는 한 곳에서 | 통과 | `pipeline/preprocess.py` + `config/preprocess.toml` 하나로, 과제 3~8이 같은 모듈·설정을 쓴다 |
| IV. 측정 조건 기록 | 통과 | 모든 표 위에 데이터 기간·실데이터·시험 기간, 진단서에 설정·코드 커밋 |
| V. 결과에는 해석 | 통과 | 진단서에 왜/그래서 칸, 자동 제안, "생각해 볼 점" 2개 답 |
| VI. 사람 손 없이 복구 | 해당 적음 | 과제 2는 상시 실행이 아니다. 내보내기는 달 단위로 다시 받기 가능 |
| VII. 단순한 것부터 | 통과 | 기준선부터, 표준 분석 라이브러리, 모델 없음. 제공 키트가 오면 원천만 바꿔 끼워 비교 |
| 운영 제약 | 통과 | 보드 B는 내보내기만, 분석은 맥. 토큰 `.env`, 데이터 `data/`(커밋 제외) |

**Phase 1 설계 후 재확인**: 위 판정 유지. 위반 없음.

## Project Structure

### Documentation (this feature)

```text
specs/002-data-baseline/
├── spec.md
├── plan.md              # 이 문서
├── research.md          # R1~R12: 원천, 시각 확인, 정제 규칙, 칸, 공간 합치기, 분할, 정규화, 고장, 기준선, 일정
├── data-model.md        # 파일 형식
├── quickstart.md        # 실행 순서와 확인 기준
├── contracts/
│   ├── cli.md           # python -m pipeline 명령
│   └── config.md        # config/preprocess.toml
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
pipeline/
├── __init__.py
├── __main__.py          # CLI: export-influx, check-time, preprocess, split, diagnose, baselines, report
├── settings.py          # config/preprocess.toml, config/schedule.toml 읽기
├── influx.py            # InfluxDB 월 단위 내보내기 (urllib, Flux → CSV)
├── loader.py            # 원시 CSV 읽기 (InfluxDB 형식, 수집기 export 형식)
├── preprocess.py        # 범위 제거 → 5분 칸 → 공간 중앙값 → 보간 → 고장 표시 → 집계   ★ 과제 3~8 공용
├── split.py             # 기간 분할, 정규화 값 계산·저장·불러오기                     ★ 공용
├── schedule.py          # 일정 파일, 공휴일, 일정 변화 시각 목록
├── diagnose.py          # 결측률 표, 평일 시간대 그래프, 반응 시간, 어긋난 날
├── baselines.py         # 기준선 3종, 평가 시점 만들기, MAE                           ★ eval_points는 공용
└── report.py            # 진단서·성능표 Markdown

config/
├── preprocess.toml
└── schedule.toml

artifacts/               # 커밋: split.json, normalization.json, eval_points.csv
requirements-analysis.txt
tests/
├── test_preprocess.py
├── test_split.py
├── test_baselines.py
└── test_schedule.py
docs/reports/            # 002-diagnosis.md, 002-baselines.md, figures/002-*.png
```

**Structure Decision**: 과제 1의 `collector/`(상시 수집, 의존성 최소)와 분리된 `pipeline/` 패키지. 과제 3 이후 학습 코드와 과제 8 서비스는 `pipeline.preprocess`, `pipeline.split`, `artifacts/`를 불러 쓴다.

## 확인 기준 ↔ 설계 연결

| 완료 조건 | 설계 요소 | 확인 |
|---|---|---|
| 1. 정규화는 학습 기간만 | `split.compute_normalization(clean, split)`이 train만 잘라 계산, `normalization.json`에 기간 기록 | 자동 시험: 검증·시험 값을 바꿔도 결과 동일 |
| 2. 같은 시점·같은 조건 | `artifacts/eval_points.csv` 고정·커밋, 기준선과 이후 모델이 같은 파일 사용, 보간·고장 정답 제외 규칙 공통 | 자동 시험: 두 번 실행 결과 동일. 과제 3 plan에 사용 명시 |
| 3. 가장 강한 기준선과 이유 | `baselines` 성능표(공간별·전체·평일 실습 시간), `report` 해석 칸 | 진단서에 숫자 근거 |

## 위험과 대응

| 위험 | 대응 |
|---|---|
| InfluxDB 저장 시각이 게이트웨이 시계(5분 오차)를 따름 | `check-time`으로 먼저 확인. 오차가 있으면 해당 기간·센서를 진단서에 적고, 반응 시간 분석에서 오차 범위를 함께 보고 |
| 추정 일정이 과거 기간과 다름 (방학, 과정 시작 전) | 어긋난 날 목록으로 드러내고, 반응 시간은 일정과 맞은 날만으로도 따로 계산 |
| 사무실 CO₂ 센서(배터리 1%)가 끊김 | 결측으로 남기고 결측률·진단서에 원인 표시. 다른 공간 값으로 채우지 않음 |
| 시험 기간(최근 4주)에 큰 결측·고장이 몰림 | 진단 후 경계 조정, `split --force`와 이유 기록 |
| 내보내기 양이 커서 보드 B 메모리·디스크 부담 | 월 단위 스트리밍 저장(한 달씩 받아 바로 gzip), 보드에서 집계하지 않음 |
| 10분 센서(회의실)는 5분 칸 절반이 빈칸 | 15분 이하 보간 규칙으로 채워지지만 "보간" 표시가 붙어 정답에서 빠짐 → 회의실 평가 시점 수가 적다는 점을 성능표에 표시 |

## Complexity Tracking

위반 없음.
