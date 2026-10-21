# CLI Contract: `python -m pipeline`

저장소 루트에서 `.venv`의 Python으로 실행한다. 공통 옵션 `--config <파일>`(기본 `config/preprocess.toml`). 실행 위치는 명령마다 다르다.

| 명령 | 어디서 | 하는 일 | 입력 → 출력 |
|---|---|---|---|
| `export-influx` | **보드 B** | InfluxDB를 월 단위로 내보내기 | `.env`(INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET) → `data/raw/influx/<항목>/<YYYY-MM>.csv.gz` |
| `check-time` | 보드 B | InfluxDB와 과제 1 수집기 기록의 시각 차이 재기 (research.md R2) | 수집기 DB + InfluxDB 최근 이틀 → 화면 표, `data/processed/time_check.csv` |
| `preprocess` | 맥 | 정제·5분 칸·공간 합치기·고장 표시 | `data/raw/influx/**` → `data/processed/clean_5min.csv.gz`, `clean_stats.csv` |
| `split` | 맥 | 기간 나누기와 정규화 값 계산 | `clean_5min` → `artifacts/split.json`, `artifacts/normalization.json` |
| `diagnose` | 맥 | 결측률 표, 평일 시간대 그래프, 일정 반응, 어긋난 날 | `clean_5min`, `config/schedule.toml` → `docs/reports/figures/002-*.png`, `data/processed/diagnosis.json` |
| `baselines` | 맥 | 평가 시점 만들기, 기준선 3종 평가 | `clean_5min`, `split.json` → `artifacts/eval_points.csv`, `data/processed/baseline_predictions.csv.gz`, `docs/reports/002-baselines.md` |
| `report` | 맥 | 진단서 초안 만들기 (숫자·표·그래프 링크 + 해석 칸) | 위 결과 → `docs/reports/002-diagnosis.md` |

## 공통 규칙

- 모든 출력 표의 위에 측정 조건(데이터 기간, 실데이터 여부, 시험 기간)을 적는다.
- `split`은 `artifacts/split.json`이 이미 있으면 덮어쓰지 않고 멈춘다(`--force`로만 다시 만든다). 이후 과제가 공유하는 약속이기 때문이다.
- `baselines`는 `artifacts/eval_points.csv`가 이미 있으면 그 목록을 그대로 쓴다(`--rebuild-points`로만 다시 만든다).
- 같은 입력으로 다시 실행하면 같은 결과가 나와야 한다(난수 없음, 정렬 고정).

## `export-influx` 옵션

```
python -m pipeline export-influx [--from 2025-08] [--to 2026-10] [--metric co2 --metric temperature ...]
```

- 이미 받은 달 파일은 건너뛴다(`--overwrite`로 다시 받음). 이번 달은 항상 다시 받는다.
- 토큰은 화면·파일에 남기지 않는다.

## `report`

- 진단서는 1~2쪽 분량을 목표로 숫자와 표를 채우고, "왜 / 그래서" 해석과 "생각해 볼 점" 답을 쓰는 칸을 남긴다. 숫자에서 뽑을 수 있는 관찰은 자동 제안으로 적는다(과제 1 보고서와 같은 방식).
