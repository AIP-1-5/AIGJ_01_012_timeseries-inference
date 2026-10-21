# Quickstart & 확인 기준 시험: 과제 2

명령 형식은 `contracts/cli.md`, 설정은 `contracts/config.md`.

## 0. 준비

```bash
# 맥과 보드 B 모두
cd ~/work/AIGJ_01_012_timeseries-inference
git pull
.venv/bin/pip install -r requirements-analysis.txt   # 맥: pandas, numpy, matplotlib, holidays
.venv/bin/pytest -q
```

- 보드 B의 `export-influx`는 표준 라이브러리만 쓴다(추가 설치 없음). `check-time`만 pandas가 필요하다.
- 보드 B의 `.env`에 `INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`(조직 ID), `INFLUX_BUCKET`이 있어야 한다.
- `./pipeline.sh <명령>` = `.env`를 불러온 뒤 `.venv/bin/python -m pipeline <명령>`.

## 1. 보드 B: 데이터 내보내기와 시각 확인

```bash
ssh board-b
tmux new -A -s export                              # 오래 걸리므로 tmux 안에서
cd ~/work/AIGJ_01_012_timeseries-inference
git pull
./pipeline.sh export-influx --from 2025-08         # 끊기면 같은 명령을 다시 (받은 달은 건너뜀)
.venv/bin/pip install pandas && ./pipeline.sh check-time   # InfluxDB 시각 vs 수집기 기준 시각
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
./pipeline.sh preprocess      # 공간·항목별 결측률, 제거 건수, 설정에 없는 공간 목록
./pipeline.sh split           # 기간과 기간별 관측 비율 (시험 기간에 결측이 몰렸나)
./pipeline.sh diagnose        # 그래프와 일정 반응
./pipeline.sh baselines       # 성능표
./pipeline.sh report          # 진단서 초안 (✍️ 칸은 직접 쓴다)
```

처음 실행하면 `preprocess`가 출력하는 "설정에 없어 분석하지 않은 공간" 목록을 보고 `config/preprocess.toml`의 공간 이름이 InfluxDB `location` 태그와 같은지 확인한다.


## 4. 확인 기준

| 완료 조건 | 확인 방법 | 기대 |
|---|---|---|
| 1. 정규화는 학습 기간 데이터로만 | `pytest -k normalization` (검증·시험 값을 바꿔도 결과 동일) + `artifacts/normalization.json`의 `computed_from`·`period` | 통과, 기간이 `split.json`의 train과 같음 |
| 2. 기준선과 이후 모델이 같은 시점·조건 | `artifacts/eval_points.csv`가 커밋돼 있고 `baselines`가 그 목록 전체를 씀. `pytest tests/test_baselines.py` (두 번 실행 결과 동일, 고정 목록 재사용) | 통과. 과제 3 plan에 "eval_points.csv로 평가" 명시 |
| 3. 가장 강한 기준선과 이유 | `docs/reports/002-baselines.md` 표 + 진단서 해석 | 표(3 × 2 × 4, 공간별·전체)와 숫자 근거가 있는 설명 |

과제 3 이후: 모델 평가는 반드시 `artifacts/eval_points.csv`의 시점으로, 입력 정규화는 `artifacts/normalization.json`으로 한다(`pipeline.baselines.load_points`, `pipeline.split.load_normalization`).

## 5. 커밋할 것

`artifacts/`(분할·정규화·평가 시점), `docs/reports/002-*.md`, `docs/reports/figures/002-*.png`. `data/`는 커밋하지 않는다.
