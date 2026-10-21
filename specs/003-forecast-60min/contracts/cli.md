# CLI Contract: `python -m forecast`

맥에서 저장소 루트의 `.venv`로 실행한다. 과제 2의 `./pipeline.sh preprocess`·`split`·`baselines`가 먼저 끝나 있어야 한다. 공통 옵션 `--config <파일>`(기본 `config/forecast.toml`).

| 명령 | 하는 일 | 입력 → 출력 |
|---|---|---|
| `samples` | 표본 만들기와 점검(개수, 빠진 이유, 기간별 수) | `clean_5min`, `split.json`, `normalization.json`, `schedule.toml` → 화면 표 (`--save`면 `data/forecast/samples_<variant>.npz`) |
| `train` | 학습 (검증 기간으로 조기 종료) | 표본 → `data/forecast/runs/<run_id>/` |
| `select` | 검증 결과로 최종 모델 고르기 | `runs/*/val_metrics.json` → `data/forecast/selection.json` |
| `evaluate` | 평가. `--split val`(언제나) / `--split test`(selection 뒤에만) | 모델 → `data/forecast/predictions_<run_id>.csv.gz`, 시험이면 `docs/reports/003-test-log.md`에 한 줄 |
| `compare` | 기준선 + 모델 비교표, 일정 포함/제외 요약 | 예측 파일 + `baseline_predictions.csv.gz` → `docs/reports/003-forecast.md` 표 부분 |
| `analyze` | 공간별·시각별 오차, 오차 상위 순간 | 예측 파일 → `docs/reports/figures/003-*.png`, `data/forecast/analysis.json` |

## `train` 옵션

```
python -m forecast train [--kind gru] [--variant full] [--seed 1] [--lookback 120] [--target delta] [--device cpu]
python -m forecast train --all        # config의 variants × seeds 전부
```

## 규칙

- `evaluate --split test`는 `selection.json`이 없으면 거부한다(종료 코드 2). 시험 결과를 보고 모델을 다시 고르지 않는다.
- 같은 설정·같은 씨앗이면 같은 결과(CPU 기준). 실행마다 `config.json`에 코드 커밋과 데이터 기간을 남긴다.
- 모든 표 위에 측정 조건(데이터 기간, 실데이터, 시험 기간, 학습 장비)을 적는다.
