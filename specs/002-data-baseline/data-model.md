# Data Model: 과제 2 — 데이터 이해와 기준선

시각은 저장할 때 UTC, 보고서·그래프는 한국 시간(KST). 파일은 `data/`(커밋 안 함)와 `artifacts/`(커밋함)에 둔다.

## 원시 내보내기 — `data/raw/influx/<항목>/<YYYY-MM>.csv.gz` (보드 B → 맥)

| 열 | 설명 |
|---|---|
| `time` | InfluxDB `_time` (UTC) |
| `device_id` | 센서 ID |
| `space` | `location` 태그 |
| `spot` | `spot` 태그 (없으면 빈칸) |
| `metric` | `co2`, `temperature`, `humidity`, `battery` |
| `value` | 값 그대로 |

과제 1 수집기 내보내기(`collector export`)도 같은 열 이름으로 바꿔 읽을 수 있게 한다(`time` ← `measured_at`).

## 정제 결과 — `data/processed/clean_5min.csv.gz`

공간 × 항목 × 5분 시각 한 줄.

| 열 | 설명 |
|---|---|
| `time` | 5분 칸의 끝 시각 (`[t−5분, t)` 칸) |
| `space`, `metric` | |
| `value` | 정제·간격 맞춤·공간 합치기 후 값. 긴 결측이면 비어 있음 |
| `n_raw` | 그 칸을 만든 원시 값 개수(공간 안 모든 센서 합) |
| `n_sensors` | 값을 낸 센서 수 |
| `interpolated` | 짧은 결측을 선형 보간한 칸이면 true |
| `fault` | 고장 의심 구간이면 사유(`stuck` 등), 아니면 빈칸 |

## 정제 집계 — `data/processed/clean_stats.csv`

공간 × 항목 × 규칙별 건수: `removed_out_of_range`, `duplicates`, `slots_total`, `slots_observed`, `slots_interpolated`, `slots_missing`, `slots_fault`. 진단서의 결측률 표가 이 파일에서 나온다.

## 기간 분할 — `artifacts/split.json` (커밋)

```json
{
  "timezone": "Asia/Seoul",
  "train": {"start": "2025-08-08T00:00", "end": "2026-08-26T00:00"},
  "val":   {"start": "2026-08-26T00:00", "end": "2026-09-23T00:00"},
  "test":  {"start": "2026-09-23T00:00", "end": "2026-10-21T00:00"},
  "data_end": "2026-10-21T00:00",
  "note": "끝 시각은 포함하지 않는다. 값은 예시이며 실제 데이터 끝에 맞춰 정한다"
}
```

규칙: `train.end == val.start`, `val.end == test.start`, 겹침 없음, 시간 순서.

## 정규화 값 — `artifacts/normalization.json` (커밋)

```json
{
  "computed_from": "train",
  "period": {"start": "...", "end": "..."},
  "values": {"실습실": {"co2": {"mean": 612.3, "std": 180.1, "n": 89000}}, "...": {}}
}
```

보간·고장 칸을 빼고 계산한다.

## 일정 — `config/schedule.toml` (커밋)

```toml
source = "사용자 제공 추정 일정 (2026-10-21)"
holidays = "KR"            # 주말과 대한민국 공휴일은 일정 없음

[[spaces]]
space = "실습실"
weekdays = ["mon", "tue", "wed", "thu", "fri"]
events = [
  { time = "09:00", kind = "start", label = "오전 실습 시작" },
  { time = "11:30", kind = "partial", label = "점심 시작 (1~3명 남을 수 있음)" },
  { time = "13:00", kind = "start", label = "오후 실습 시작" },
  { time = "18:00", kind = "end",   label = "퇴실" },
]
```

`kind`: `start`(사람이 들어옴), `end`(모두 나감), `partial`(대부분 나가지만 일부 남음). 반응 분석은 종류별로 따로 집계한다.

## 평가 시점 — `artifacts/eval_points.csv` (커밋)

| 열 | 설명 |
|---|---|
| `space` | |
| `metric` | `co2` 또는 `temperature` |
| `time` | 예측 시각 `t` (UTC) |
| `weekday_class_hours` | 평일 09:00~18:00이면 true (부분집합 보고용) |

규칙은 research.md R10. 과제 3·6 모델 평가가 이 파일을 그대로 읽는다.

## 기준선 결과 — `data/processed/baseline_predictions.csv.gz`, `docs/reports/002-baselines.md`

예측 파일: `space, metric, time, horizon_min, baseline, y_true, y_pred`. 성능표: 기준선 × 공간 × 항목 × `horizon_min`(5·15·30·60) × MAE × 평가 시점 수, 전체와 평일 실습 시간 부분집합.

## 진단 결과 — `docs/reports/figures/002-*.png`, `docs/reports/002-diagnosis.md`

- `002-hourly-<공간>-<항목>.png`: 평일 시간대별 평균(±표준편차)
- `002-response.png`: 일정 변화 뒤 CO₂ 반응 시간 분포
- 진단서 본문: 측정 조건, 정제 규칙과 이유, 결측률·제거 건수 표, 분할 기준과 이유, 패턴 해석, 반응 시간 중앙값과 어긋난 날 목록, 고장 의심 구간과 처리, 기준선 요약과 해석, "생각해 볼 점" 답
