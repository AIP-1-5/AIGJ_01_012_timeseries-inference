# Quickstart & 확인 기준 시험: 과제 3

명령 형식은 `contracts/cli.md`, 모델 입출력은 `contracts/model-io.md`, 파일 형식은 `data-model.md`.

## 0. 준비 (맥)

```bash
cd ~/work/AIGJ_01_012_timeseries-inference
git pull
.venv/bin/pip install -r requirements-train.txt     # torch
.venv/bin/pytest -q
ls data/processed/clean_5min.csv.gz artifacts/        # 과제 2 결과가 있어야 함
```

## 1. 표본 점검

```bash
.venv/bin/python -m forecast samples
```

기대: 기간별 표본 수(학습 수십만, 검증·시험 수만), 빠진 이유별 수. 시험 표본 수가 `eval_points.csv`와 맞는다.

## 2. 단순한 것부터: 선형 → GRU

```bash
.venv/bin/python -m forecast train --kind linear --variant full --seed 1
.venv/bin/python -m forecast evaluate --split val --run linear-full-L120-s1
.venv/bin/python -m forecast train --kind gru --variant full --seed 1
.venv/bin/python -m forecast evaluate --split val --run gru-full-L120-s1
```

기대: 검증 기간에서 선형도 현재값 유지보다 낫고, GRU가 선형보다 낫다. 아니면 입력 설계부터 다시 본다(constitution VII).

## 3. 일정 포함/제외 × 씨앗 3개

```bash
.venv/bin/python -m forecast train --all            # full / no_schedule / sensor_only × 씨앗 1·2·3
.venv/bin/python -m forecast select                 # 검증 기간으로만 고르기 → selection.json
```

## 4. 시험 평가 (한 번), 비교표, 분석

```bash
.venv/bin/python -m forecast evaluate --split test --all   # 고른 뒤에만 실행된다
.venv/bin/python -m forecast compare
.venv/bin/python -m forecast analyze
```

## 5. 확인 기준

| 완료 조건 | 확인 방법 | 기대 |
|---|---|---|
| 1. 60분 CO₂가 현재값 유지보다 30% 이상 작다 | `docs/reports/003-forecast.md` 비교표 전체 60분 CO₂ | 고른 모델 MAE ≤ 현재값 유지 × 0.7 (≈ 25.4 ppm) |
| 2. 온도 60분이 현재값 유지보다 좋다 | 같은 표 60분 온도 | 고른 모델 MAE < 현재값 유지 (≈ 0.15℃) |
| 3. 시험 기간을 학습·선택에 안 썼다 | `pytest -k leakage`, `selection.json`의 `decided_at` < `003-test-log.md` 첫 시험 시각 | 통과, 순서 맞음 |
