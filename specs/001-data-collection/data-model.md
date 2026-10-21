# Data Model: 과제 1 — 데이터 수집 체계

저장소: SQLite 1개 파일(WAL 모드). 시각은 모두 UTC ISO 8601 문자열(`2026-10-21T00:31:12.103Z`)로 저장하고, 보고서에서 한국 시간으로 바꿔 보여 준다.

## measurements — 측정값

센서 메시지 1건에서 항목 수만큼 행이 생긴다(예: AM103 메시지 1건 → co2, temperature, humidity, battery 4행).

| 열 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER PK | |
| `message_key` | TEXT | 중복 판별 키. `deduplicationId`, 없으면 `devEui:fCnt:measured_at(분)` |
| `device_id` | TEXT | 센서 ID(`devEui`) |
| `device_profile` | TEXT | 센서 종류(`AM103`, `EM300-TH` …) |
| `space` | TEXT | 공간(`tags.location`, 예: 실습실). 없으면 `unknown` |
| `spot` | TEXT NULL | 공간 안 위치(`tags.spot`) |
| `metric` | TEXT | 항목(`co2`, `temperature`, `humidity`, `battery` …), 설정의 이름 바꾸기 적용 후 |
| `value` | REAL | 받은 값 그대로(정제하지 않음, FR-002) |
| `unit` | TEXT NULL | 설정에서 정한 단위(`ppm`, `°C`, `%`) |
| `measured_at` | TEXT | 기준 시각(가장 이른 `nsTime`) |
| `time_source` | TEXT | `ns_time` 또는 `received_at`(fallback) |
| `gw_time` | TEXT NULL | 메시지 최상위 `time`(게이트웨이 시계) |
| `received_at` | TEXT | 보드가 받은 시각 |
| `fcnt` | INTEGER NULL | 센서 메시지 일련번호 |
| `source` | TEXT | 설정의 소스 이름(예: `chirpstack`) |
| `topic` | TEXT | 받은 토픽 |

- 제약: `UNIQUE(message_key, metric)` → 중복은 `INSERT OR IGNORE`로 무시(R4)
- 색인: `(device_id, metric, measured_at)`, `(space, measured_at)`, `(measured_at)`
- 규칙: `value`가 숫자가 아니면 그 항목은 저장하지 않고 `rejects`에 남긴다. 숫자면 0이나 음수도 그대로 저장한다.

## events — 수집기 사건

| 열 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER PK | |
| `at` | TEXT | 사건 시각(보드 시계) |
| `kind` | TEXT | `start`, `stop`, `connect`, `disconnect`, `reconnect_attempt`, `subscribe`, `stale_reconnect`, `store_error`, `config_loaded`, `clock_check` |
| `detail` | TEXT NULL | 설명(오류 메시지, 끊김 사유 코드 등). 비밀번호는 넣지 않는다 |

- 색인: `(at)`, `(kind, at)`
- `reconnect_attempt`는 많아질 수 있어 같은 끊김 동안은 첫 시도와 성공만 남기고 나머지는 횟수로 합친다.

## rejects — 해석 실패·알 수 없는 메시지

| 열 | 타입 | 설명 |
|---|---|---|
| `id` | INTEGER PK | |
| `received_at` | TEXT | |
| `topic` | TEXT | |
| `reason` | TEXT | `json_error`, `no_matching_source`, `missing_field:<경로>`, `non_numeric:<항목>` |
| `payload` | TEXT | 원문(최대 8KB로 자름) |

- 설정에 없는 토픽은 구독 자체를 안 하므로 여기 오지 않는다. 구독한 토픽인데 규칙에 맞지 않는 메시지만 남는다.

## duplicates — 걸러낸 중복

| 열 | 타입 | 설명 |
|---|---|---|
| `received_at` | TEXT | |
| `message_key` | TEXT | |
| `device_id` | TEXT | |

- 하루 중복 건수와 어느 센서에서 많은지 보고하는 데 쓴다.

## 설정에서 오는 값 (DB에 저장하지 않음)

- **소스(source)**: 이름, 구독 토픽, 경로 매핑, 항목 목록, 이름 바꾸기, 단위 → `contracts/config.md`
- **기대 전송 간격**: 센서 종류 기본값 + 장비별 덮어쓰기(분). 커버율 계산에 쓴다.

## 파생 값 (조회할 때 계산)

- **커버율**: `(기대 간격 칸 중 측정값이 있는 칸 수) / (전체 칸 수)`, 센서·항목별, 공간별 묶음, 1분 기준 값 병기
- **결측 구간**: 연속 빈 칸이 기대 간격 × 2 이상인 구간의 시작·끝·길이
- **원인 추정**: research.md R8 규칙 (수집기 사건 → `fCnt` 건너뜀 → `fCnt` 이어짐/배터리)
- **끊김 표시**: 마지막 `measured_at` 이후 경과 시간이 `max(5분, 기대 간격 × 3)` 이상
- **시계 점검**: `received_at - measured_at`의 중앙값(보드 시계 또는 서버 시계 오차, 네트워크 지연)
