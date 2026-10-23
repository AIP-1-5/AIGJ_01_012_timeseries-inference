<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/header-dark.svg">
  <img src="docs/assets/header-light.svg" alt="교육장 공기, 매분 판단합니다 — 교육장 CO₂ · 온도 · 습도 센서로 60분 뒤를 예측하고, 이상 · 사용 여부 · 환기 필요를 매분 알려 주는 Jetson 상시 추론 서비스" width="100%">
</picture>

<br>

![Jetson](https://img.shields.io/badge/Jetson_Orin_Nano_Super-8GB-E8E0D2?style=flat-square&labelColor=181715&color=E8E0D2&logo=nvidia&logoColor=FAF9F5)
![JetPack](https://img.shields.io/badge/JetPack-7.2.1-E8E0D2?style=flat-square&labelColor=181715&color=E8E0D2)
![Python](https://img.shields.io/badge/Python-3.12-E8E0D2?style=flat-square&labelColor=181715&color=E8E0D2&logo=python&logoColor=FAF9F5)
![ONNX Runtime](https://img.shields.io/badge/ONNX_Runtime-CPU-E8E0D2?style=flat-square&labelColor=181715&color=E8E0D2&logo=onnx&logoColor=FAF9F5)
![MQTT](https://img.shields.io/badge/MQTT-retained-E8E0D2?style=flat-square&labelColor=181715&color=E8E0D2&logo=mqtt&logoColor=FAF9F5)
![Spec Kit](https://img.shields.io/badge/Spec_Kit-과제_1~8-E8E0D2?style=flat-square&labelColor=181715&color=E8E0D2)

**[결과 한눈에](#결과-한눈에)** · [화면과 그래프](#화면과-그래프로-보는-결과) · [구조](#구조) · [서비스](#상시-추론-서비스-과제-8) · [시작하기](#시작하기) · [과제별 실행](#과제별-실행) · [보고서](#보고서)

</div>

<br>

<sub>00 · SUMMARY</sub>

## 한 줄 요약

> [!NOTE]
> 맥이 1년치 실데이터로 **학습**하고, 보드 A가 그 모델로 **매분 판단**해서 **API · MQTT · 대시보드**로 내보낸다.
> 모델과 임계값은 **검증 기간으로만** 골랐고, 시험 결과를 본 뒤 다시 고르지 않았다.

| 대시보드 | 결과 형식 | 제출 자료 |
|:---:|:---:|:---:|
| `http://100.113.228.46:8080` (Tailscale) | [docs/interface.md](docs/interface.md) | [final-presentation.pptx](docs/presentation/final-presentation.pptx) |

<br>

<sub>01 · RESULTS</sub>

## 결과 한눈에

시험 기간 2026-09-23 ~ 10-21 (실데이터, 시간 순서 분할) · 보드 전력 모드 MAXN SUPER

| 과제 | 무엇을 | 핵심 결과 | 판정 |
|:---:|---|---|:---:|
| 1 | 데이터 수집 | 두 보드 모두 30/30 항목 커버율 95% 이상 (최저 **98.3%**), 재접속 15~18초, 재부팅 56초 뒤 재수집 | 통과 |
| 2 | 진단 · 기준선 | 가장 강한 기준선 = 현재값 유지 (60분 CO₂ **36.3 ppm**) | 통과 |
| 3 | 60분 예측 | GRU 31.3 ppm, 기준선보다 **13.8%** 감소 (목표 30%) · 온도 32.6% 감소 · 2차 시험은 11-05 이후 | **미통과** · CO₂ |
| 4 | 이상 탐지 | 탐지율 **79%** (27/34, 목표 80%, 1건 모자람) · 헛경보 하루 0회 | **미통과** · 탐지율 |
| 5 | 상태 추정 | 환기 선제 경보 재현율 **88.0%** · macro-F1 0.626 > 규칙 0.608 · 사용 여부는 직접 관찰 6칸 정확도 **0%**, 빈 방 관찰 없음 | **미통과** · 사용 여부 |
| 6 | 사전학습 모델 | GRU 31.3 < Chronos-Bolt 32.4 ppm · Chronos 39~139 ms, +213 MB | 통과 |
| 7 | 엣지 최적화 | ONNX Runtime CPU **0.22 ms** (PyTorch의 46배), CO₂ 차이 0.25 ppm · TensorRT는 측정 못 함 | 통과 |
| 8 | 상시 서비스 | 보드 A **19.7시간 무중단**, 1,182/1,182주기 성공 · 수집기 10분 정지 시험은 아직 | 통과 · 정지 시험 남음 |

<details>
<summary><b>가장 의외였던 발견 — 검증에서 꼴찌였던 모델이 시험에서 1등이었다</b></summary>

<br>

| GRU 변형 (씨앗 3개 평균) | 검증 60분 CO₂ | 시험 60분 CO₂ |
|---|---:|---:|
| 센서 + 시간표 | 74.6 | 30.3 |
| 센서 + 시각 · 요일 ← 검증으로 선택 | **73.7** | 32.2 |
| 센서만 | 82.6 (꼴찌) | **30.0 (1등)** |

검증 기간(개강 직후)이 시험 기간(추석 연휴)보다 3배 더 출렁였다. 규칙대로 검증으로만 골라서 1등을 놓쳤다.
→ 기간 하나로 고르면 운에 맡기는 것. 다음에는 여러 기간으로 교차 검증하고, 서비스는 60분 전 예측을 실제값으로 스스로 채점한다.

</details>

<br>

<sub>02 · SCREENSHOTS</sub>

## 화면과 그래프로 보는 결과

숫자 표만으로는 감이 잘 안 오는 부분을 그림으로 모았다. 그림 아래에 **무엇을 보는 그림인지 → 왜 이렇게 나왔는지 → 그래서 무엇을 하는지**를 적었다.
새로 학습하거나 측정한 것은 없다. 숫자는 모두 [docs/reports/](docs/reports/)의 각 보고서에서 가져왔다.

### 대시보드 (과제 8)

<img src="docs/assets/readme/dashboard-overview.png" alt="대시보드 첫 화면: 환기 순서 표와 켜진 경보" width="100%">

보드 A 서비스 주소(`http://100.113.228.46:8080`)를 열면 처음 보이는 화면이다.
왼쪽 **환기 순서**는 1,000 ppm에 먼저 닿을 공간부터 줄을 세운다. 막대 끝이 지금 CO₂, 세로선이 60분 뒤 예측이다. 오른쪽 검은 칸은 지금 켜진 경보다.
맨 위 한 문장("실습실 CO₂가 약 55분 뒤 1,000 ppm을 넘을 것으로 보입니다")만 읽어도 무엇을 해야 하는지 알 수 있게 만들었다.

<img src="docs/assets/readme/dashboard-forecast.png" alt="실습실 3시간 실측과 60분 예측 그래프" width="100%">

공간별 그래프다. 검은 선이 지난 3시간 실측, 점선 오른쪽 코랄 선이 앞으로 60분 예측이고, 옅은 띠는 "지난 24시간 동안 예측이 이 정도 틀렸다"는 범위다.
오른쪽에는 온도·습도, 환기 필요 확률, 사용 중 확률, 이상 점수가 같이 나온다. 15분 동안 크게 떨어진 곳(그림의 −174 ppm)은 창문을 연 순간처럼 눈에 띄게 표시한다.

<img src="docs/assets/readme/dashboard-sensors.png" alt="센서별 실측값 표" width="100%">

맨 아래 **센서별 실측값** 표(`/#sensors`)다. 한 공간에 센서가 여러 대라서, 어느 센서가 끊겼는지(빨간 "끊김")와 배터리가 얼마 남았는지 한눈에 본다.
센서 하나가 끊겨도 나머지 센서로 계산을 이어 가고, 그 사실을 경보 칸에 "알림"으로 띄운다.

> [!NOTE]
> 위 세 장은 보드 A가 아니라 **저장소의 `service/dashboard.html`을 파일로 열어 찍은 화면**이다. 파일로 열면 `docs/interface.md` 형식의 **예시 데이터**를 그린다(화면 위쪽에 "예시 데이터"라고 나온다).
> 찍은 곳(클라우드)에서는 보드 A의 Tailscale 주소에 닿지 않기 때문이다. 화면 구성은 보드 A에서 실제로 보이는 것과 같고, 숫자만 예시다.

### 과제 1 · 수집이 얼마나 빠짐없이 됐나

<img src="docs/assets/readme/01-coverage.png" alt="공간별 24시간 수집 커버율" width="100%">

24시간 동안 받아야 할 메시지 중 실제로 받은 비율(커버율)이다. 기준은 95%인데 **가장 낮은 항목도 98.3%** 였다.
가장 낮은 것은 실습실 EM500-CO2인데, 이 센서는 2분마다 보내서 한 번만 놓쳐도 비율이 크게 깎인다. 그래서 커버율을 1분 칸이 아니라 **센서마다 정해진 전송 간격**으로 계산했다.
브로커 재시작 뒤 15~18초, 보드 재부팅 뒤 56초 만에 다시 받기 시작한 기록은 [001-failure-tests](docs/reports/001-failure-tests.md)에 있다.

### 과제 2 · 데이터는 어떻게 생겼나

<img src="docs/reports/figures/002-daily-co2.png" alt="공간별 하루 CO₂ 패턴" width="100%">

하루 동안 CO₂가 어떻게 오르내리는지 공간별로 평균 낸 그림이다. 파란 선(수업 있는 날)은 9시에 확 오르고, 점심에 떨어졌다가, 오후에 다시 오른다. 주황 선(주말·공휴일)은 거의 평평하다.
**사람이 있으면 오르고 없으면 내려간다**는 것이 이 데이터의 전부라고 해도 될 만큼 뚜렷하다. 그래서 "지금 값을 그대로 쓰는 것"(현재값 유지)이 생각보다 강한 기준선이 됐다(60분 CO₂ 오차 36.3 ppm).

### 과제 3 · 60분 뒤 예측 — 미통과

<img src="docs/assets/readme/03-forecast.png" alt="60분 예측 오차: 현재값 유지와 GRU 비교" width="100%">

막대가 낮을수록 잘 맞힌 것이다. 온도는 기준선보다 32.6% 줄여서 통과했지만, **CO₂는 13.8%만 줄여서 목표(30%)에 못 미쳤다.**
같은 모델이 검증 기간에서는 33%를 줄였다. 시험 기간(9/23 ~ 10/21)에 추석 연휴가 겹쳐 CO₂가 거의 움직이지 않는 시간이 많았고, 그러면 "지금 값 그대로"가 이미 잘 맞아서 더 줄일 여지가 작다.

<img src="docs/reports/figures/003-error-by-time.png" alt="시각별 60분 CO₂ 오차" width="100%">

시각별로 보면 이유가 더 잘 보인다. 실습실 9시 수업 시작 직후(파란 선이 300 ppm까지 튐)에는 모델이 오히려 더 틀린다. 사람이 들어오는 순간은 센서 값만 보고는 알 수 없기 때문이다.
반대로 저녁 퇴실 뒤(18~20시)에는 모델이 크게 앞선다. **다음 할 일:** 시험 결과를 보고 모델을 다시 고르지 않는다는 규칙을 지키려고, 미리 정해 둔 새 시험 기간(10-22 ~ 11-04)으로 11-05 이후 한 번만 다시 잰다.

### 과제 4 · 고장 찾기 — 미통과 (1건 모자람)

<img src="docs/assets/readme/04-detection.png" alt="고장 종류별 탐지 건수" width="100%">

시험 기간 데이터에 가짜 고장 34건을 심고 몇 건을 찾았는지 센 그림이다. **27/34 = 79%로 기준 80%에 1건 모자랐다.** 헛경보는 하루 0회로 기준(1회 이하)을 지켰다.
튄 값·냉방 정지는 다 잡았지만, **값 고정**(센서가 같은 숫자만 계속 보냄)은 절반만 잡았다. 값이 멈춰도 평소 범위 안에 있으면 "이상해 보이지 않기" 때문이다. 환기 정지 2건은 CO₂가 평소보다 조금 빨리 오르는 정도라 못 잡았다.

<img src="docs/reports/figures/004-threshold-seed22.png" alt="임계값에 따른 탐지율과 헛경보" width="70%">

임계값을 내리면 탐지율은 오르지만 헛경보도 늘어난다. 시험 표에서는 80%를 넘는 지점이 있었지만, 그 임계값은 **검증 기간에서 헛경보가 하루 1.71회**라 고를 수 없었다. 시험 결과를 보고 고르면 규칙 위반이라 그대로 두었다.

### 과제 5 · 방이 비었나, 환기가 필요한가 — 사용 여부 미통과

<img src="docs/reports/figures/005-observation.png" alt="실습실 직접 관찰 시간대의 센서·모델·규칙 판정 비교" width="80%">

10-22 오전 실습실에 사람이 17명, 17명, 5명 있던 시간을 직접 적고 모델과 비교했다. **모델은 6칸 모두 "비었다"고 했다(정확도 0%).**
셋째 줄을 보면 모델 확률이 0.3~0.75 사이에 머무는데, 임계값이 0.95로 너무 높다. 임계값은 검증 기간(개강 직후)에서 F1이 가장 높은 값으로 정했는데, 연휴가 많은 지금과는 분포가 달라 너무 깐깐해졌다.
빈 방 관찰이 아직 없어서 "빈 방을 사용 중으로 착각한 비율"도 계산하지 못했다. **다음 할 일:** 빈 방 시간(수업 전·퇴실 뒤)을 직접 관찰해 `docs/observations/occupancy.csv`에 넣고 다시 채점한다.
같은 과제의 **환기 선제 경보는 재현율 88.0%로 통과**했다(기준 85%).

### 과제 6 · 사전학습 모델과 비교

<img src="docs/assets/readme/06-pretrained.png" alt="GRU와 Chronos-Bolt의 오차와 추론 시간" width="100%">

인터넷에서 받은 대형 시계열 모델(Chronos-Bolt)을 추가 학습 없이 써 봤다. 기준선보다는 낫지만(32.4 ppm), **우리가 직접 학습한 작은 GRU(31.3 ppm)가 더 정확했다.**
속도 차이는 더 크다. Chronos small은 한 번 예측에 139 ms, 메모리 약 237 MB를 더 쓴다. GRU는 ONNX로 바꾸면 0.22 ms다. 로컬 LLM이 같은 보드에 올라갈 예정이라 메모리가 아까워서 **서비스에는 GRU를 쓴다.**

### 과제 7 · 보드에서 빠르게 돌리기

<img src="docs/assets/readme/07-edge.png" alt="실행 방식별 추론 시간" width="100%">

같은 GRU 모델을 실행 방식만 바꿔 보드 B에서 200번씩 잰 결과다. **ONNX Runtime(CPU)이 0.22 ms로 PyTorch CPU보다 46배 빨랐고**, 결과 차이도 0.25 ppm으로 기준(1 ppm) 안이다.
GPU가 더 빠를 것 같지만, 이 모델은 너무 작아서 GPU에 일을 보내고 받는 준비 시간이 계산 시간보다 길다(0.87 ms에서 더 안 내려감). GPU FP16은 결과 차이가 1.02 ppm으로 기준을 살짝 넘었다.
TensorRT는 보드에 파이썬 모듈이 없어서 재지 못했다(남은 일).

### 과제 8 · 상시 서비스 운영 기록

<img src="docs/assets/readme/08-service.png" alt="보드 A 상시 서비스 운영 기록과 경보 채점" width="100%">

보드 A에서 **19.7시간 동안 1,182번 계산해 한 번도 실패하지 않았다**(기준 12시간 무중단). 한 번 계산하는 데 1.3초 걸리고, 3분 넘게 멈춘 적이 없다.
오른쪽은 서비스가 낸 경보가 실제로 맞았는지 나중에 확인한 것이다. **"1,000 ppm 넘을 것" 예측 경보는 4/4 맞았고**, "환기 필요" 경보는 1/5만 맞았다. 환기 경보는 "30분 안에 1,000 ppm"을 기준으로 채점했는데, 틀린 4건도 826~910 ppm까지는 올랐다. 과제 5에서 재현율 85%를 맞추려고 임계값을 낮게 잡아 경보가 일찍 울리는 것이다. 그래서 사람에게 보내는 알림은 예측 경보를 기본으로 하고, 환기 경보는 "주의" 단계로 낮춘다.
수집기를 10분 멈췄을 때 끊김 경보가 뜨는지 보는 시험은 보드 A sudo가 필요해 **아직 하지 않았다.**

<br>

<sub>03 · ARCHITECTURE</sub>

## 구조

```mermaid
%%{init: {'theme':'base','themeVariables':{'fontFamily':'Pretendard, -apple-system, sans-serif','primaryColor':'#EFE9DE','primaryTextColor':'#141413','primaryBorderColor':'#E6DFD8','lineColor':'#6C6A64','secondaryColor':'#F5F0E8','tertiaryColor':'#FAF9F5','clusterBkg':'#F5F0E8','clusterBorder':'#E6DFD8','edgeLabelBackground':'#FAF9F5'}}}%%
flowchart LR
    S["교육장 센서<br/>LoRaWAN → MQTT"]
    I[("교육장 InfluxDB<br/>과거 1년치")]

    subgraph MAC["맥 — 학습"]
        T["전처리 · 학습<br/>예측 · 이상 · 상태"]
    end

    subgraph A["보드 A — 운영 24시간"]
        C["수집기<br/>MQTT → SQLite"]
        R["매분 추론<br/>같은 전처리 → ONNX"]
    end

    subgraph B["보드 B — 측정"]
        M["속도 · 메모리 측정<br/>시험용 수집기"]
    end

    U["API :8080<br/>MQTT · 대시보드"]

    S --> C --> R --> U

    classDef core fill:#CC785C,stroke:#A9583E,color:#141413
    classDef dark fill:#181715,stroke:#252320,color:#FAF9F5
    class R core
    class U dark
    I --> T -- "ONNX 모델" --> R
    T -- "측정 묶음" --> M
```

| 장비 | 역할 | 규칙 |
|---|---|---|
| 맥 | 학습 전부, 하드웨어가 필요 없는 작업 | 맥이 켜져 있을 때만 학습 |
| 보드 A `team5-a` | 수집기 · 상시 추론 서비스 | 실험 · 재부팅 · 코드 수정 금지, `git pull`로만 배포 |
| 보드 B `team5-b` | Jetson 속도 · 메모리 측정, 시험용 수집기 | **학습 금지** (코드가 거부, `pipeline/guard.py`), 무거운 작업은 메모리 2GB 상한 |

> 학습과 서비스가 **같은 전처리 코드**(`pipeline/preprocess.py`)를 써서 학습 때 본 입력과 운영 입력이 어긋나지 않는다.

<br>

<sub>04 · SERVICE</sub>

## 상시 추론 서비스 (과제 8)

매분: 수집 DB → 학습과 같은 전처리 → ONNX 예측 · 이상 점수 · 상태 판단 → 경보 → API · MQTT 발행

| 경로 | 내용 |
|---|---|
| `GET /` | 대시보드 — 환기 순서·켜진 경보 → 공간별 3시간 실측과 60분 예측 → 센서별 실측값 표(`/#sensors`), 30초마다 갱신 |
| `GET /health` | 서비스 상태, 주기 수, 실패 수, 모델 이름 |
| `GET /v1/spaces` | 모든 공간의 현재 판단 |
| `GET /v1/spaces/<slug>/forecast?minutes=60` | 5~60분 예측 |
| `GET /v1/spaces/<slug>/anomaly` · `/state` · `/alerts` | 이상 · 사용 여부 · 환기 · 경보 |
| `GET /v1/alerts` | 전체 경보 |
| `GET /v1/sensors?space=<slug>` | 센서별 실측값 (CO₂·온도·습도·배터리·TVOC·기압·조도·소음…)과 최근 3시간 |
| MQTT `aip/team5/jaeung/inference/<slug>/state` | 공간 상태 (retained) · `alerts` · `status`(LWT) |

- **무중단 모델 교체** — 모델 파일만 바꾸면 다음 주기에 새 모델을 쓴다 (manifest 해시 감시).
- **끊김 경보** — 센서가 멈추면 `sensor_gap`, 수집기가 5분 넘게 조용하면 `data_gap`(`scope: collector`).
- **스스로 채점** — 60분 전에 낸 예측을 실제값과 비교해 오차가 커지면 `forecast_drift` 경보.

필드 · 단위 · 예시는 [docs/interface.md](docs/interface.md)에 있다.

<br>

<sub>05 · GETTING STARTED</sub>

## 시작하기

과제 안내 원문은 팀 Private 저장소 `AIP-1-5/docs`에 있다. 두 저장소를 나란히 clone한다.

> 교육장 네트워크는 밖으로 나가는 22번 포트를 막는다. 보드에서는 먼저 `~/.ssh/config`에 GitHub SSH를 443번으로 우회하는 설정을 넣는다(`../docs/environment.md`의 네트워크 항목).

```bash
mkdir -p ~/work && cd ~/work
git clone git@github.com:AIP-1-5/docs.git
git clone git@github.com:AIP-1-5/AIGJ_01_012_timeseries-inference.git
cd AIGJ_01_012_timeseries-inference
cp .env.example .env            # 값은 팀 단톡방에서 받는다 (커밋 금지)
claude --add-dir ../docs        # 과제 문서도 읽을 수 있게 Claude Code 실행

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/pytest -q             # 단위 시험
```

| 설치 파일 | 어디서 | 용도 |
|---|---|---|
| `requirements.txt` · `requirements-dev.txt` | 모두 | 수집기 · 시험 |
| `requirements-analysis.txt` | 맥 | 전처리 · 분석 (과제 2) |
| `requirements-train.txt` | 맥 | 학습 (과제 3~6) |
| `requirements-service.txt` | 보드 A | 상시 서비스 (ONNX Runtime, torch 없음) |

`./<이름>.sh <명령>`은 `.env`를 불러온 뒤 `.venv/bin/python -m <이름> <명령>`을 실행하는 줄임이다. 자주 쓰는 명령은 [docs/cheatsheet.md](docs/cheatsheet.md)에 상황별로 모았다.

<br>

<sub>06 · RUN</sub>

## 과제별 실행

<details>
<summary><b>과제 1 · 수집기</b> — 보드 A · B</summary>

MQTT 센서 메시지를 받아 보드 안 SQLite(`data/collector.db`)에 저장한다. 설계 `specs/001-data-collection/`, 실행 · 시험 순서 [quickstart.md](specs/001-data-collection/quickstart.md).

```bash
./collector.sh check-config                 # 설정 점검
./collector.sh run                          # 앞에서 실행 (Ctrl+C로 종료)
sudo deploy/install-service.sh              # 서비스 collector-$USER 등록 (재부팅 후 자동 시작)

./collector.sh status                       # 지금 잘 쌓이고 있나 (끊긴 항목이 있으면 종료 코드 3)
./collector.sh report --hours 24            # docs/reports/001-collection-status.md
./collector.sh export --from 2026-10-21 --to 2026-10-22 --out data/export.csv
```

| 파일 | 역할 |
|---|---|
| `config/collector.toml` | 브로커, 토픽, 메시지 해석 규칙, 센서별 전송 간격 (형식이 바뀌면 여기만 고친다) |
| `collector/` | `parser.py` 해석 · `store.py` 저장 · `mqtt_client.py` 재접속 · `coverage.py` 커버율 · 결측 원인 · `report.py` 보고서 |

</details>

<details>
<summary><b>과제 2 · 데이터 진단 · 기준선</b> — 보드 B에서 내려받고 맥에서 분석</summary>

```bash
# 보드 B: InfluxDB는 보드 B 유선으로만 닿는다
./pipeline.sh export-influx --from 2025-08   # data/raw/influx/<항목>/<YYYY-MM>.csv.gz
./pipeline.sh check-time                     # InfluxDB 시각 vs 수집기 시각

# 맥
./pipeline.sh preprocess    # 정제 · 5분 칸 · 공간 중앙값 · 보간 · 고장 표시
./pipeline.sh split         # 기간 분할(artifacts/split.json), 정규화 값(학습 기간만)
./pipeline.sh diagnose      # 결측률 · 하루 패턴 · 일정 반응 → docs/reports/figures/002-*.png
./pipeline.sh baselines     # 평가 시점 고정(artifacts/eval_points.csv), 기준선 MAE
./pipeline.sh report        # docs/reports/002-diagnosis.md 초안
```

`config/preprocess.toml`과 `artifacts/`는 과제 3~8이 함께 쓰는 약속이라 커밋한다.

</details>

<details>
<summary><b>과제 3~6 · 예측 · 이상 탐지 · 상태 · 사전학습</b> — 맥</summary>

```bash
./forecast.sh samples && ./forecast.sh train && ./forecast.sh select   # 검증 기간으로만 선택
./forecast.sh evaluate --split test && ./forecast.sh compare && ./forecast.sh analyze

./anomaly.sh all            # train → calibrate(검증) → evaluate(시험 1회)
./state.sh run              # 사용 여부 · 환기 경보 학습 · 평가
./state.sh observe --db data/collector.db   # 직접 관찰 기록으로 채점
./pretrained.sh predict && ./pretrained.sh report
```

보드에서 학습 명령을 실행하면 `pipeline/guard.py`가 거부한다.

</details>

<details>
<summary><b>과제 7 · 엣지 최적화</b> — 맥에서 묶고 보드 B에서 측정</summary>

```bash
./edge.sh prepare                                        # 맥: 모델 + 시험 입력 2,000개 → data/edge/bundle/
scp -r data/edge/bundle board-b:~/work/AIGJ_01_012_timeseries-inference/data/edge/
free -h                                                  # 보드 B: 남은 메모리 확인
systemd-run --user --scope -p MemoryMax=2G env EDGE_PYTHON=.venv-gpu/bin/python ./edge.sh all
                                                         # 보드 B: 측정 → docs/reports/007-comparison.md
```

</details>

<details>
<summary><b>과제 8 · 상시 서비스</b> — 보드 A</summary>

```bash
./service.sh export                 # 학습 결과를 data/models/로 묶기 (ONNX 변환)
./service.sh once                   # 한 주기만 계산해 JSON 출력 (배포 확인)
./service.sh run                    # 매분 계산 + HTTP API + MQTT
./service.sh summary --hours 12 --score   # 주기 기록 → docs/reports/008-service-log.md
sudo deploy/install-inference.sh    # systemd 등록 (재부팅 후 자동 시작)
```

보드가 끊겼을 때(네트워크·IP 문제), 다른 실습과 같이 쓸 때: [docs/runbook-outage.md](docs/runbook-outage.md)

</details>

<br>

<sub>07 · REPORTS</sub>

## 보고서

| 과제 | 해석 보고서 | 자동 생성 표 · 기록 |
|:---:|---|---|
| 1 | [001-findings](docs/reports/001-findings.md) | [보드 A](docs/reports/001-collection-status.md) · [보드 B](docs/reports/001-collection-status-b.md) · [장애 시험](docs/reports/001-failure-tests.md) |
| 2 | [002-diagnosis](docs/reports/002-diagnosis.md) | [002-baselines](docs/reports/002-baselines.md) |
| 3 | [003-forecast](docs/reports/003-forecast.md) | [모델 비교](docs/reports/003-model-comparison.md) · [시험 기록](docs/reports/003-test-log.md) |
| 4 | [004-anomaly](docs/reports/004-anomaly.md) | [004-detection](docs/reports/004-detection.md) |
| 5 | [005-state](docs/reports/005-state.md) | [005-classification](docs/reports/005-classification.md) |
| 6 | [006-pretrained](docs/reports/006-pretrained.md) | [006-comparison](docs/reports/006-comparison.md) |
| 7 | [007-edge](docs/reports/007-edge.md) | [007-comparison](docs/reports/007-comparison.md) |
| 8 | [008-service](docs/reports/008-service.md) | [docs/interface.md](docs/interface.md) |
| 전체 | [000-final-report](docs/reports/000-final-report.md) | [제출 PPT](docs/presentation/final-presentation.pptx) |

<br>

<sub>08 · LAYOUT</sub>

## 저장소 구성

```text
.
├── collector/   pipeline/                  과제 1 수집기 · 과제 2 전처리 · 분할 · 기준선 (공용)
├── forecast/    anomaly/    state/         과제 3 · 4 · 5 모델
├── pretrained/  edge/                      과제 6 사전학습 비교 · 과제 7 엣지 측정
├── service/                                과제 8 상시 추론 서비스 (+ dashboard.html)
├── config/      artifacts/                 설정 · 과제 간 공유 약속 (분할 · 정규화 · 평가 시점)
├── deploy/                                 systemd 서비스와 설치 스크립트
├── docs/        reports/ · presentation/   결과 보고서 · 제출 자료 · 결과 형식 문서
├── specs/                                  과제별 spec · plan · tasks (Spec Kit)
└── tests/                                  단위 시험
```

<br>

<sub>09 · RULES</sub>

## 지키는 규칙

- 학습 · 검증 · 시험은 **시간 순서**로 나눈다. 섞지 않는다.
- 정규화 값은 **학습 기간**으로만 계산한다. 시험 기간은 학습 · 선택에 쓰지 않는다.
- 기준선과 모델은 **같은 평가 시점 · 같은 정답**으로 비교한다.
- 모든 결과표에 **측정 조건**(데이터 기간, 실데이터 여부, 시험 기간, 전력 모드)과 **해석**(왜 · 그래서)을 붙인다.
- 비밀값은 `.env`에서만 읽는다. 데이터 · DB · `.pt` `.onnx` `.engine`은 커밋하지 않는다.

### Spec Kit으로 과제 진행

1. `/speckit-specify` — 과제 문서의 확인 기준을 spec의 완료 조건으로
2. `/speckit-plan` → `/speckit-tasks` — 계획과 할 일. 여기서 멈추고 검토
3. `/speckit-implement` — 구현
4. `/speckit-converge` — 확인 기준을 충족할 때까지 반복

작업 규칙과 장비 정보는 [CLAUDE.md](CLAUDE.md), 원칙은 [.specify/memory/constitution.md](.specify/memory/constitution.md).
