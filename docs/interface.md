# 교육장 공기 상태 추론 서비스 v1: 결과 형식

이 문서만 보고 다른 팀(대시보드, LLM 에이전트, 냉난방 제어)이 결과를 가져다 쓸 수 있게 정리했다. 기준 코드는 `service/`(과제 8)다.

## 1. 한눈에

| 항목 | 내용 |
| --- | --- |
| 운영 위치 | 보드 A (`team5-a`), Tailscale `100.113.228.46` |
| 계산 주기 | 매분 1회. 계산은 끝난 5분 칸(`slot`) 기준이라 값은 5분마다 바뀌고, 데이터 끊김 같은 경보는 1분 안에 반영된다 |
| 조회 API | `http://100.113.228.46:8080` (HTTP GET, JSON, UTF-8, 인증 없음, tailnet 안에서만 접근) |
| MQTT | `data.withio.net:1883` (수집과 같은 브로커와 계정), 토픽 접두어 `aip/team5/jaeung/inference` |
| 공간 slug | `lab` 실습실, `office` 사무실, `meeting` 회의실, `office-out` 사무실 밖(CO₂ 센서 없음) |
| 시각 | 모두 UTC ISO 8601 (`2026-10-22T05:00:00Z`). 한국 시각은 +9시간 |
| 단위 | CO₂ `ppm`, 온도 `℃`, 습도 `%RH`, 확률 0~1, 시간 `minutes`·`ms` |

## 2. MQTT 토픽

| 토픽 | retained | 언제 | 내용 |
| --- | --- | --- | --- |
| `<접두어>/<slug>/state` | 예 | 매분 | 그 공간의 최신 결과 전체 (§4의 공간 객체) |
| `<접두어>/alerts` | 아니오 | 경보가 생기거나 30분마다 반복, 사라질 때 | 경보 1건 (§5) |
| `<접두어>/status` | 예 | 매분, 연결·종료 때 | `{"status": "online" \| "degraded" \| "offline", "generated_at", "cycle_ms"}` |

- retained라서 새로 접속한 구독자도 구독 즉시 공간별 최신값과 서비스 상태를 받는다.
- 서비스가 비정상 종료되면 브로커가 LWT로 `status = offline`을 대신 발행한다.
- `degraded`는 한 주기 계산이 실패했다는 뜻이다. 다음 주기에 성공하면 `online`으로 돌아온다.
- QoS 1. 같은 경보가 두 번 올 수 있으므로 `(slug, type, time)`으로 중복을 거른다.

```bash
mosquitto_sub -h data.withio.net -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD" -t 'aip/team5/jaeung/inference/#' -v
```

## 3. 조회 API

| 경로 | 설명 |
| --- | --- |
| `GET /` | 사람이 보는 대시보드. 탭 두 개: **모델 판단**(공간별 CO₂·온도, 60분 예측선, 사용 여부, 경보)과 **센서 실측**(`/#sensors`, 센서별 실측값과 최근 3시간 그래프). 새로고침 간격 1초~30분 선택, 기본 10초 |
| `GET /health` | 서비스 상태. 200 = 마지막 성공 주기가 180초 안, 503 = 멈춤·실패 중 (`last_error` 포함) |
| `GET /v1/spaces` | 모든 공간 결과 (`spaces`: 공간 객체 목록, `stale`: 결과가 180초보다 오래됨) |
| `GET /v1/spaces/<slug>` | 한 공간 결과 (§4) |
| `GET /v1/spaces/<slug>/forecast?minutes=30` | N분 뒤 예측값. N은 5~60, 5의 배수 (아니면 400). 생략하면 12개 시점 전체 |
| `GET /v1/spaces/<slug>/anomaly` · `/state` · `/alerts` | 공간 객체의 해당 부분만 |
| `GET /v1/alerts` | `active`: 지금 켜진 경보 전체, `recent`: 최근 발송 기록(최대 100건) |
| `GET /v1/sensors?space=<slug 또는 이름>` | 센서 실측값 (§3.1). 모델 계산과 따로, 첫 계산 전에도 응답한다 |

없는 공간은 404, 첫 계산 전에는 503을 돌려준다. 예: "회의실 30분 뒤 CO₂는?"

```bash
$ curl -s 'http://100.113.228.46:8080/v1/spaces/meeting/forecast?minutes=30'
{"space": "회의실", "slot": "2026-10-22T05:00:00Z", "minutes": 30, "co2": 812.4, "temperature": 23.1, "unavailable": null}
```

### 3.1 센서 실측값 `/v1/sensors`

수집기 DB를 읽기 전용으로 읽어, 센서마다 항목별 마지막 값과 최근 3시간 그래프용 값을 돌려준다. 결과는 5초 동안 재사용한다(요청이 많아도 DB를 다시 읽지 않음).

| 필드 | 뜻 |
| --- | --- |
| `history` | 그래프 칸 정보: `start`(첫 칸 시작), `bucket_minutes`(5), `buckets`(칸 수), `metrics`(`co2`·`temperature`·`humidity`) |
| `spaces[]` | 공간별 묶음: `space`, `slug`(모델이 쓰는 공간만, 아니면 `null`), `total`(센서 수), `stale`(끊긴 센서 수), `sensors[]` |
| `sensors[].metrics` | 항목별 `{value, unit, at}`. CO₂·온도·습도·배터리, 센서에 따라 TVOC·기압·조도·소음 등 (숫자 항목은 모두) |
| `sensors[].history` | 항목별 5분 칸 평균 목록 (`null` = 그 칸에 값 없음, 10분 센서는 한 칸씩 빈다) |
| `sensors[].kind` · `stale` | `periodic`이면 기대 간격 × 3(최소 5분)보다 오래 조용할 때 `stale: true`. 문 열림처럼 사건이 있을 때만 보내는 센서는 `event`, `stale: null` |
| `sensors[].last_seen` · `age_minutes` | 마지막 수신 시각과 지난 분 |

```bash
$ curl -s 'http://100.113.228.46:8080/v1/sensors?space=lab' | jq '.spaces[0].sensors[0] | {spot, profile, stale, co2: .metrics.co2.value}'
{"spot": "후면 오른쪽", "profile": "AM107", "stale": false, "co2": 525}
```

## 4. 공간 객체 (MQTT `<slug>/state`와 API `/v1/spaces/<slug>`에서 같은 형식)

| 필드 | 형식·단위 | 뜻 |
| --- | --- | --- |
| `space`, `slug` | 문자열 | 공간 이름(한글), 영문 slug |
| `slot` | 시각 | 결과 기준 5분 칸의 끝 시각. `[slot−5분, slot)` 측정값까지 반영 |
| `sensors.total` / `.stale` / `.last_seen` | 개수 / 센서 ID 목록 / 시각 | 최근 26시간 안에 CO₂·온도·습도를 보고한 센서 수(문 열림 센서 등은 제외), 끊긴 센서(기대 간격 × 3, 최소 5분 넘게 없음: 1분 센서 5분, 10분 센서 30분), 마지막 수신 |
| `forecast.co2`, `forecast.temperature` | 객체 또는 없음 | `now`(지금 값), `as_of`, `points[{minutes, value}]` 5~60분 12개. 과거 2시간 중 75% 미만만 값이 있거나 최근 값이 15분 넘게 끊기면 빠진다 |
| `forecast_unavailable` | 문자열 | 예측을 못 낸 이유 (예: `최근 값이 끊김`) |
| `anomaly.score` / `.threshold` / `.is_anomaly` | 실수 / 실수 / 참·거짓 | 오토인코더 재구성 오차(최근 15분, 정규화 단위). `score > threshold`이면 이상 |
| `anomaly.cause` / `.by_metric` | `co2`·`temperature`·`humidity` / 항목별 점수 | 원인 센서 항목 (이상일 때만) |
| `rules[]` | `{rule, cause, time}` | 최근 칸에 걸린 규칙 경보 (`spike` 급변, `stuck` 값 고정, `night_level` 밤 영점, `hot_class`·`high_class` 수업 중 과열·과다) |
| `state.occupied` / `.occupied_probability` | 참·거짓 / 0~1 | 사용 중 여부 (센서 값만으로 추정) |
| `state.ventilation_alert` / `.ventilation_probability` / `.co2_over_limit_now` | 참·거짓 / 0~1 / 참·거짓 | 30분 안 CO₂ 1,000 ppm 도달 예상 또는 이미 초과 |
| `monitor.co2` / `.temperature` | `{mae_60, n}` (ppm / ℃, 개수) | 자체 채점: 60분 전에 낸 예측과 지금 실제값의 평균 절대 오차(최근 24시간, 서비스 재시작 시 초기화) |
| `alerts[]` | §5 | 지금 켜진 경보 |

```json
{"space": "실습실", "slug": "lab", "slot": "2026-10-22T05:00:00Z",
 "sensors": {"total": 5, "stale": [], "last_seen": "2026-10-22T05:02:00.000Z"},
 "forecast": {"co2": {"now": 850.0, "as_of": "2026-10-22T05:00:00Z",
                      "points": [{"minutes": 5, "value": 868.2}, "...", {"minutes": 60, "value": 1012.5}]},
              "temperature": {"now": 23.0, "as_of": "2026-10-22T05:00:00Z", "points": ["..."]}},
 "anomaly": {"score": 0.95, "threshold": 2.31, "is_anomaly": false, "cause": null,
             "by_metric": {"co2": 0.88, "temperature": 1.02, "humidity": 0.71}},
 "rules": [],
 "state": {"occupied": true, "occupied_probability": 0.90, "ventilation_alert": true,
           "ventilation_probability": 0.90, "co2_over_limit_now": false},
 "alerts": [{"type": "co2_forecast", "level": "warning", "minutes": 60, "message": "60분 뒤 CO₂ 1,000 ppm 초과 예상"},
            {"type": "ventilation", "level": "warning", "message": "곧 환기 필요 (30분 안 1,000 ppm)"}]}
```

## 5. 경보 (`alerts[]`, MQTT `alerts`)

| `type` | `level` | 조건 | 추가 필드 |
| --- | --- | --- | --- |
| `data_gap` | warning | `scope: "collector"`: 모든 센서가 5분 넘게 조용함(수집기·브로커·네트워크 끊김). `scope: "space"`: 이 공간의 센서만 모두 끊김. 오래된 값으로 예측을 지어내지 않는다(최근 값이 15분 넘으면 예측을 비움) | `scope` |
| `sensor_gap` | info | 일부 센서만 끊김. 나머지 센서로 계산은 계속한다 | |
| `co2_forecast` | warning | 지금 1,000 ppm 미만인데 60분 예측 안에 1,000 이상인 시점이 있음 | `minutes` (처음 넘는 시점) |
| `ventilation` | warning | `state.ventilation_alert`가 참 | |
| `anomaly` | warning | `anomaly.is_anomaly`가 참 | `cause` |
| `rule_<규칙>` | warning | `rules[]`에 걸린 규칙 | `cause` |
| `forecast_drift` | info | `monitor.co2.mae_60`이 75 ppm 초과(채점 12건 이상). 재학습 검토 신호 | `mae_60` |
| `rule_out_of_range` | warning | 최근 칸에 허용 범위(CO₂ 300~5000, 온도 0~45, 습도 1~99) 밖 원시값. 그 값은 계산에서 뺀다 | `cause`, `devices` |

MQTT `alerts` 메시지에는 위 필드에 `space`, `slug`, `time`, `status`가 더 붙는다. `status`는 `active`(처음), `repeat`(30분째 계속), `cleared`(사라짐) 중 하나다.

## 6. 쓰는 쪽이 알아야 할 것

- **전처리**: 학습(과제 2~5)과 같은 함수(`pipeline/preprocess.py`, `forecast/dataset.py`, `anomaly/model.py`, `state/features.py`)와 학습 기간 정규화 값(`normalization.json`)을 쓴다. 다르면 같은 공기에서도 입력 분포가 달라져 예측이 조용히 틀어진다(§ 과제 8 보고서).
- **모델 버전**: `/health`와 결과 최상위의 `models`(`forecast` 실행 이름, `anomaly`·`state` 있음 여부, 묶음 만든 시각)와 공간 객체의 `slot`으로 어떤 모델이 언제 데이터로 계산했는지 확인한다. 모델 파일 목록과 해시는 보드 A의 `data/models/manifest.json`에 있다.
- **한계**: 예측은 과거 1년(2025-09 ~ 2026-10) 교육장 데이터로 학습했다. 공간 배치나 센서가 바뀌면 다시 학습해야 한다. 회의실 CO₂ 센서는 10분 간격이라 회의실 값은 최대 10분 늦을 수 있다.
