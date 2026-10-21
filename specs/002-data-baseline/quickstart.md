# Quickstart & 확인 기준 시험: 과제 2

명령 형식은 `contracts/cli.md`, 설정은 `contracts/config.md`.

## 0. 준비

```bash
# 맥과 보드 B 모두
cd ~/work/AIGJ_01_012_timeseries-inference
git pull
.venv/bin/pip install -r requirements-analysis.txt   # pandas, numpy, matplotlib, holidays
.venv/bin/pytest -q
```

보드 B의 `.env`에 `INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`(조직 ID), `INFLUX_BUCKET`이 있어야 한다.

## 1. 보드 B: 데이터 내보내기와 시각 확인

```bash
ssh board-b
cd ~/work/AIGJ_01_012_timeseries-inference
.venv/bin/python -m pipeline export-influx --from 2025-08
.venv/bin/python -m pipeline check-time            # InfluxDB 시각 vs 수집기 기준 시각
du -sh data/raw/influx
```

기대: 항목별 월 파일이 생긴다. `check-time`의 시각 차이 중앙값이 0에 가깝다(아니면 진단서에 적는다).

## 2. 맥으로 가져오기

```bash
# 맥에서
cd ~/work/AIGJ_01_012_timeseries-inference
mkdir -p data/raw && scp -r board-b:~/work/AIGJ_01_012_timeseries-inference/data/raw/influx data/raw/
```

## 3. 맥: 전처리 → 분할 → 진단 → 기준선 → 진단서

```bash
.venv/bin/python -m pipeline preprocess
.venv/bin/python -m pipeline split
.venv/bin/python -m pipeline diagnose
.venv/bin/python -m pipeline baselines
.venv/bin/python -m pipeline report
```

## 4. 확인 기준

| 완료 조건 | 확인 방법 | 기대 |
|---|---|---|
| 1. 정규화는 학습 기간 데이터로만 | `pytest -k normalization` (검증·시험 값을 바꿔도 결과 동일) + `artifacts/normalization.json`의 `computed_from`·`period` | 통과, 기간이 `split.json`의 train과 같음 |
| 2. 기준선과 이후 모델이 같은 시점·조건 | `artifacts/eval_points.csv`가 커밋돼 있고 `baselines`가 그 목록 전체를 씀. `pytest -k eval_points` (두 번 실행 결과 동일) | 통과. 과제 3 plan에 "eval_points.csv로 평가" 명시 |
| 3. 가장 강한 기준선과 이유 | `docs/reports/002-baselines.md` 표 + 진단서 해석 | 표(3 × 2 × 4, 공간별·전체)와 숫자 근거가 있는 설명 |

## 5. 커밋할 것

`artifacts/`(분할·정규화·평가 시점), `docs/reports/002-*.md`, `docs/reports/figures/002-*.png`. `data/`는 커밋하지 않는다.
