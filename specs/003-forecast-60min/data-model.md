# Data Model: 과제 3 — 60분 예측

시각은 저장할 때 UTC, 보고서는 KST. 커밋하지 않는 파일은 `data/forecast/`, 커밋하는 파일은 `config/`, `docs/reports/`.

## 입력 (과제 2 산출물, 그대로 사용)

| 파일 | 쓰는 것 |
|---|---|
| `data/processed/clean_5min.csv.gz` | 공간 × 항목 × 5분 칸 값, `interpolated`, `fault` |
| `artifacts/split.json` | 학습·검증·시험 기간 |
| `artifacts/normalization.json` | 공간·항목별 평균·표준편차 (학습 기간) |
| `artifacts/eval_points.csv` | 시험 평가 시점과 h별 표시 |
| `config/schedule.toml` | 실습실 추정 시간표, 공휴일 |

## 표본 (메모리 안에서 만들고, 필요하면 `data/forecast/samples_<variant>.npz`로 저장)

공간 s, 예측 시각 t 하나.

| 이름 | 모양 | 내용 |
|---|---|---|
| `past` | L × F_past (기본 24 × (6 + C)) | 칸마다 CO₂·온도·습도(정규화, 없으면 0) + 사용 가능 표시 3 + 달력·일정 특성 C |
| `future` | 12 × C | t+5 ~ t+60분 칸의 달력·일정 특성 (미리 알 수 있는 정보만) |
| `space` | 4 | 공간 원-핫 (실습실, 사무실, 회의실, 사무실 밖) |
| `target` | 12 × 2 | `y(t+h) − y(t)` (정규화 단위), CO₂·온도 |
| `target_mask` | 12 × 2 | 그 칸 정답이 관측값이면 1 (보간·고장·결측·항목 없음이면 0) |
| `base` | 2 | `y(t)` (정규화 단위). 예측값 = `base + 출력` → 원래 단위로 되돌림 |
| `meta` | | 공간, t, 기간(train/val/test) |

달력·일정 특성 C (변형별):

| 특성 | `full` | `no_schedule` | `sensor_only` |
|---|:-:|:-:|:-:|
| 하루 시각 sin, cos | ✓ | ✓ | |
| 요일 sin, cos | ✓ | ✓ | |
| 일정 있는 날 (평일·공휴일 반영) | ✓ | | |
| 실습실 수업 중 (수업 1, 점심 0.5, 그 외 0, 실습실 외 공간은 0) | ✓ | | |

표본 규칙:
- `y(t)` 관측(보간·고장 아님), 과거 창의 75% 이상 사용 가능
- 학습 표본: t, t+60분 모두 학습 기간. 검증 표본: 모두 검증 기간
- 시험 표본: `eval_points.csv`의 시각만 (h별 표시를 그대로 평가에 사용)

## 모델 설정 — `config/forecast.toml` (커밋)

```toml
[data]
lookback_minutes = 120
min_window_fraction = 0.75

[model]
kind = "gru"            # linear | gru | lstm | tcn
hidden = 64
layers = 2
dropout = 0.1
target = "delta"        # delta | value

[train]
seeds = [1, 2, 3]
batch_size = 512
learning_rate = 1e-3
max_epochs = 30
patience = 5
device = "cpu"

[variants]
names = ["full", "no_schedule", "sensor_only"]
```

## 학습 결과 — `data/forecast/runs/<run_id>/` (커밋 안 함)

| 파일 | 내용 |
|---|---|
| `model.pt` | 가중치 |
| `config.json` | 그 실행의 설정 전체, 변형, 씨앗, 코드 커밋, 데이터 기간 |
| `history.csv` | 에폭별 학습·검증 손실, 검증 h별 MAE |
| `val_metrics.json` | 고른 에폭의 검증 MAE (h × 항목 × 공간) |

`run_id` = `<kind>-<variant>-L<lookback>-s<seed>`

## 고르기 기록 — `data/forecast/selection.json` (커밋 안 함, 요약은 보고서에)

```json
{
  "chosen": "gru-full-L120-s1",
  "criterion": "검증 기간 60분 (CO₂ MAE/현재값 유지 + 온도 MAE/현재값 유지) / 2",
  "candidates": [{"run_id": "...", "val_score": 0.71}],
  "model_sha256": "…",
  "decided_at": "2026-10-23T…Z"
}
```

## 시험 평가 기록 — `docs/reports/003-test-log.md` (커밋)

시험 평가를 돌릴 때마다 한 줄: 시각, run_id, 모델 해시, 60분 CO₂·온도 MAE. 같은 해시로 다시 돌리면 새 줄을 쓰지 않고 기존 결과를 보여 준다.

## 예측 결과 — `data/forecast/predictions_<run_id>.csv.gz` (커밋 안 함)

`space, metric, time, horizon_min, model, y_true, y_pred, weekday_class_hours` (과제 2의 `baseline_predictions.csv.gz`와 같은 열) → 기준선과 바로 합쳐 비교표를 만든다.

## 비교표·분석 — `docs/reports/003-forecast.md`, `docs/reports/figures/003-*.png` (커밋)

- 모델 비교표: 기준선 3종 + 선형 + GRU(변형별) × h × 항목 × 공간·전체·평일 수업 시간 × MAE·시점 수
- 일정 포함/제외: 변형별 씨앗 3개 60분 CO₂ MAE 평균·범위
- `003-error-by-time.png`: 일정 변화 시각 전후 시각별 평균 오차
- `003-error-by-space.png`: 공간별 60분 MAE vs 현재값 유지
- 오차 상위 순간 목록
