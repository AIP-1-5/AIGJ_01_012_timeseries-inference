---

description: "Task list for 과제 1 — 데이터 수집 체계"
---

# Tasks: 과제 1 — 데이터 수집 체계

**Input**: Design documents from `/specs/001-data-collection/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: plan.md가 `pytest` 단위 시험과 quickstart.md의 장애 시험을 요구하므로 시험 작업을 포함한다. 단위 시험은 해당 구현보다 먼저 쓰고 실패하는 것을 확인한다.

**Organization**: spec.md의 User Story별로 묶었다. 작업 위치는 plan.md의 구조(`collector/`, `config/`, `deploy/`, `tests/`)를 따른다.

**어디서 하나**: 코드 작성·단위 시험은 어디서든, 실제 수집·장애 시험은 **보드 B**, 최종 운영은 **보드 A**(pull과 서비스 등록만). **(보드)** 표시가 붙은 작업은 보드에서 사람이 직접 실행해야 한다.

**진행 상황 (2026-10-21)**: 코드 작업은 클라우드 세션에서 진행. 로컬 mosquitto로 예행 시험: 브로커 재시작 후 1초 만에 재접속, 무응답 감시 재접속 동작, 중복 메시지 1건 걸러짐 확인.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 다른 파일이고 앞선 미완료 작업에 의존하지 않아 동시에 할 수 있다
- **[Story]**: 해당 User Story (US1~US6)

---

## Phase 1: Setup (공통 준비)

**Purpose**: 폴더 구조와 의존성

- [x] T001 plan.md 구조대로 폴더 만들기: `collector/`, `config/examples/`, `deploy/`, `tests/fixtures/`, `docs/reports/.gitkeep`
- [x] T002 [P] 의존성 파일 작성: `requirements.txt`(`paho-mqtt>=2,<3`), `requirements-dev.txt`(`pytest`)
- [x] T003 [P] `collector/__init__.py`와 `collector/__main__.py`에 `argparse` 뼈대 만들기: 하위 명령 `run`, `status`, `report`, `export`, `check-config`와 공통 옵션 `--config`(기본 `config/collector.toml`) (contracts/cli.md)
- [x] T004 [P] 실제 관찰 형식을 따른 샘플 메시지 작성(장비·테넌트 ID는 가공한 값으로 바꿈): `tests/fixtures/chirpstack_up.json`(AM103, co2·temperature·humidity·battery), `tests/fixtures/chirpstack_up_no_spot.json`(EM320-TH, `spot` 없음), `tests/fixtures/chirpstack_up_skewed_gw.json`(`time`이 `nsTime`보다 약 5분 빠르고 `rxInfo` 2개), `tests/fixtures/format_changed.json`(필드 이름이 바뀐 가상 형식)

---

## Phase 2: Foundational (모든 Story의 전제)

**Purpose**: 설정 읽기, 저장소 스키마, 로그

**⚠️ CRITICAL**: 이 단계가 끝나야 Story 작업을 시작할 수 있다

- [x] T005 [P] `tests/test_store.py` 작성: 스키마 생성, WAL 모드 확인, `UNIQUE(message_key, metric)`에 의한 중복 무시, `rejects.payload` 8KB 자르기, 읽기 전용 연결로 조회 가능
- [x] T006 `collector/config.py` 구현: TOML 읽기(`tomllib`), 환경변수에서 `MQTT_USERNAME`·`MQTT_PASSWORD` 읽기, 필수 키(`fields.device_id`, `fields.space`, `fields.values`) 검증, 오류 시 종료 코드 2, 비밀번호를 로그·예외 메시지에 넣지 않기 (contracts/config.md)
- [x] T007 `collector/store.py`에 스키마 구현: data-model.md의 `measurements`(제약 "`UNIQUE(message_key, metric)`", 색인 `(device_id, metric, measured_at)`·`(space, measured_at)`·`(measured_at)`), `events`, `rejects`("원문(최대 8KB로 자름)"), `duplicates`. 시각은 UTC ISO 8601 문자열. `PRAGMA journal_mode=WAL`. 읽기 전용 연결 함수 포함
- [x] T008 [P] `config/collector.toml` 작성: contracts/config.md 예시 그대로, `client_id = "team5-b-jaeung-collector"`, `storage.path = "data/collector.db"`, 기대 간격(기본 1분, `EM500-CO2` 2분, `24e124725d089152`·`24e124128c140101` 10분)
- [x] T009 `collector/__main__.py`에 로그 설정: 표준 출력, 한 줄 형식(시각·수준·내용)

**Checkpoint**: 설정을 읽고 빈 DB를 만들 수 있다. `pytest tests/test_store.py` 통과

---

## Phase 3: User Story 1 - 센서 값이 끊김 없이 쌓인다 (Priority: P1) 🎯 MVP

**Goal**: MQTT 센서 메시지를 받아 센서 × 항목 × 시각으로, 받은 그대로 저장한다

**Independent Test**: 보드 B에서 `run`을 10분 돌린 뒤 센서·항목마다 약 10건(간격 1분 센서 기준)이 저장되어 있다

### Tests for User Story 1

- [x] T010 [P] [US1] `tests/test_parser.py` 작성: 점 경로(`a.b.c`, `a[].b`에서 가장 이른 시각) 해석, AM103 샘플 → 4행, `value` 0·음수는 그대로 저장, 숫자가 아닌 값 → `rejects`(`non_numeric:<항목>`), `spot` 없음 → NULL, 시계가 틀린 게이트웨이 샘플 → `measured_at`은 가장 이른 `nsTime`이고 `gw_time`은 최상위 `time`, `nsTime` 없음 → `measured_at = received_at`·`time_source = received_at`, `message_key` = `deduplicationId`(없으면 `devEui:fCnt:measured_at(분)`), `metrics.include`에 없는 항목은 저장 안 함, `rename`·`units` 적용

### Implementation for User Story 1

- [x] T011 [US1] `collector/parser.py` 구현: 설정의 `fields`·`metrics`로 메시지 → 측정값 행 목록, 실패 시 사유(`json_error`, `missing_field:<경로>`, `non_numeric:<항목>`) 반환 (T010 통과)
- [x] T012 [US1] `collector/store.py`에 저장 함수 구현: 측정값 `INSERT OR IGNORE` 후 무시된 건은 `duplicates`에 기록, `rejects`·`events` 기록 함수
- [x] T013 [US1] `collector/mqtt_client.py` 기본 구현: 설정의 브로커·계정으로 접속, 소스 토픽 구독, 메시지 → 해석 → 저장, 같은 메시지가 여러 소스에 맞으면 첫 소스만 적용, 사건 `start`·`connect`·`subscribe`·`stop` 기록, SIGTERM·Ctrl+C에 정상 종료
- [x] T014 [US1] `collector/__main__.py`의 `run` 명령 연결과 1분 요약 로그(저장 건수, 중복 수, 실패 수, 연결 상태)
- [ ] T015 **(보드)** [US1] 보드 B에서 확인: quickstart.md §0~§2대로 10분 실행 후 `sqlite3`로 센서·항목별 건수와 중복 기록 확인

**Checkpoint**: 앞에서 실행하는 동안 실제 데이터가 쌓인다

---

## Phase 4: User Story 2 - 사람 손 없이 다시 수집된다 (Priority: P1)

**Goal**: 브로커·네트워크 장애, 프로그램 종료, 재부팅 뒤 자동으로 수집을 이어 간다 (완료 조건 1·2)

**Independent Test**: 1883 포트를 막았다 풀면 60초 안에 저장 재개, 보드 B 재부팅 후 로그인 없이 수집 재개

### Tests for User Story 2

- [x] T016 [P] [US2] `tests/test_mqtt_client.py` 작성: 재연결 간격 설정(최소 1초, 최대 20초)이 클라이언트에 적용되는지, 무응답 감시 판단 함수(마지막 메시지 후 `stale_after_minutes` 경과 시 재접속 결정), 연속 재시도 사건이 첫 시도·성공 + 횟수로 합쳐지는지

### Implementation for User Story 2

- [x] T017 [US2] `collector/mqtt_client.py`에 재연결 구현: `reconnect_delay_set(1, 20)`, 고정 `client_id`, `clean_session=False`, QoS 1, `disconnect`·`reconnect_attempt`·`connect` 사건(재시도는 합쳐서 기록) (research.md R5)
- [x] T018 [US2] `collector/mqtt_client.py`에 무응답 감시 구현: 연결 상태인데 `stale_after_minutes` 동안 메시지가 없으면 `stale_reconnect` 사건을 남기고 다시 접속
- [x] T019 [US2] 저장 오류 처리: SQLite 오류는 `store_error` 사건으로 남기고 계속 수집, 복구 불가 오류는 종료 코드 1로 끝내 systemd가 다시 띄우게 하기 (`collector/mqtt_client.py`, `collector/store.py`)
- [x] T020 [P] [US2] `deploy/collector.service` 템플릿 작성: `After=network-online.target`, `Wants=network-online.target`, `User=`, `WorkingDirectory=`, `EnvironmentFile=<저장소>/.env`, `ExecStart=<저장소>/.venv/bin/python -m collector run`, `Restart=always`, `RestartSec=5`, `RestartPreventExitStatus=2`
- [x] T021 [US2] `deploy/install-service.sh` 작성: 실행 계정·저장소 경로를 템플릿에 채워 `/etc/systemd/system/collector.service`에 설치, `daemon-reload`, `enable --now`, 다시 실행해도 안전, hostname이 `team5-a`면 재부팅 금지 안내 출력
- [ ] T022 **(보드)** [US2] 보드 B에서 SC-001·SC-004 시험: quickstart.md §4대로 iptables 차단·해제 3회, 해제 시각→첫 새 저장 시각을 `docs/reports/001-failure-tests.md`에 표로 기록
- [ ] T023 **(보드)** [US2] 보드 B에서 SC-002 시험: `sudo reboot` 후 부팅 완료 시각·서비스 시작 시각·첫 새 저장 시각을 `docs/reports/001-failure-tests.md`에 기록

**Checkpoint**: 완료 조건 1·2 통과

---

## Phase 5: User Story 3 - 수집 현황을 한눈에 본다 (Priority: P1)

**Goal**: 센서·항목별 건수, 마지막 수신, 커버율, 결측 구간과 원인을 보여 준다 (완료 조건 3, 산출물 "수집 현황 보고")

**Independent Test**: 수집기를 몇 분 멈췄다 켠 뒤 `status`에서 끊김이 보이고, `report`에서 그 구간이 "수집기 쪽" 원인으로 표시된다

### Tests for User Story 3

- [x] T024 [P] [US3] `tests/test_coverage.py` 작성: 간격 1·2·10분 센서의 커버율과 1분 기준 커버율, 결측 구간("연속 빈 칸이 기대 간격 × 2 이상"), 원인 규칙 순서(수집기 사건 → `fCnt` 차이 2 이상 → `fCnt` 이어짐·배터리 낮음), 끊김 표시("`max(5분, 기대 간격 × 3)` 이상"), 시험 시간대 제외 옵션

### Implementation for User Story 3

- [x] T025 [US3] `collector/coverage.py` 구현: 커버율(센서·항목별, 공간별 묶음, 1분 기준 병기), 결측 구간, 원인 추정과 근거(research.md R8), 끊김 판정, 시계 점검(`received_at - measured_at` 중앙값) (T024 통과)
- [x] T026 [US3] `collector/report.py`에 `status` 표 구현: contracts/cli.md 출력 형식, 공간별 묶음, 수집기 상태 줄(마지막 연결·끊김, 오늘 중복·실패 수), 끊긴 항목이 있으면 종료 코드 3
- [x] T027 [US3] `collector/report.py`에 `report` Markdown 구현: 측정 조건(기간, 실데이터, hostname, `nvpmodel -q` 결과 또는 "확인 불가", 설정 파일, `git rev-parse HEAD`), 커버율 표, 결측 구간과 원인·근거, 중복·실패 집계, 시계 점검, 사람이 채울 "해석" 칸. `--hours`와 `--from/--to`로 기간 지정
- [x] T028 [US3] `collector/__main__.py`에 `status`·`report` 명령 연결
- [ ] T029 **(보드)** [US3] 보드 B에서 SC-003: 장애 시험이 없는 연속 24시간 이상 가동 후 `report`로 `docs/reports/001-collection-status.md` 생성, 간격 기준 커버율 95% 이상 확인, 결측 구간 해석 작성

**Checkpoint**: 완료 조건 3 통과 (보드 B 기준)

---

## Phase 6: User Story 4 - 형식이 바뀌어도 설정만 고친다 (Priority: P2)

**Goal**: 코드 수정 없이 설정만으로 형식 변경·센서 추가에 대응 (산출물 "형식 변경 대응 기록")

**Independent Test**: 시험용 로컬 브로커에 바뀐 형식을 보내고, 설정 파일만 고친 수집기가 올바르게 저장한다

### Tests for User Story 4

- [x] T030 [P] [US4] `tests/test_parser.py`에 형식 변경 시험 추가: `config/examples/format-change.toml`로 `tests/fixtures/format_changed.json`을 해석하면 올바른 공간·항목·값이 나온다

### Implementation for User Story 4

- [x] T031 [P] [US4] `config/examples/format-change.toml` 작성: `broker.host = "localhost"`, 바뀐 경로(`fields.values`, `fields.space`)와 `metrics.rename`, 다른 `client_id`·`storage.path` (contracts/config.md "형식 변경 대응 예시")
- [x] T032 [US4] `check-config` 명령 구현: 설정 검증, `--sample`로 해석 결과(저장될 행)만 출력하고 저장하지 않음 (`collector/__main__.py`)
- [ ] T033 **(보드)** [US4] 보드 B에서 SC-005: 로컬 `mosquitto` 실행, `mosquitto_pub`으로 바뀐 형식 전송, 설정만 고쳐 대응한 과정(바꾼 줄, 코드 diff 없음)을 `docs/reports/001-format-change.md`에 기록

---

## Phase 7: User Story 5 - 분석·학습이 수집을 방해하지 않는다 (Priority: P2)

**Goal**: 수집 중에도 읽기·내보내기가 가능하다

**Independent Test**: 수집 중 전체 내보내기를 돌려도 그 시간대 커버율이 떨어지지 않는다

- [x] T034 [US5] `export` 명령 구현: 읽기 전용 연결, 열 `measured_at, device_id, device_profile, space, spot, metric, value, unit, gw_time, received_at, fcnt`, `--from/--to/--metric/--out`, 출력 경로가 `data/` 밖이면 경고 (`collector/__main__.py`, `collector/store.py`)
- [ ] T035 **(보드)** [US5] 보드 B에서 SC-007: 수집 중 전체 내보내기 실행, 전후 커버율 비교를 `docs/reports/001-failure-tests.md`에 기록

---

## Phase 8: User Story 6 - 심화: 보관 정책 (Priority: P3, 선택)

**Goal**: 1년 뒤 저장소 크기 추정과 보관 정책 제안 (FR-017). 중복 처리(FR-016)는 실제 데이터에서 일어나 US1에 이미 포함했다.

- [ ] T036 [US6] `collector/report.py`의 보고서에 용량 추정 절 추가: 기간 내 행 수·DB 크기로 하루 증가량 계산, 1년 예상 크기, 다운샘플링·보관 정책 제안 칸

---

## Phase 9: Polish & 보드 A 배포

- [x] T037 [P] `README.md`에 수집기 사용법 절 추가(설치, `run`/`status`/`report`/`export`, 서비스 등록), quickstart.md 링크
- [ ] T038 **(보드)** 보드 A 배포: `nc -zv -w 5 data.withio.net 1883` 확인 → 저장소 clone·`.venv`·`.env` 준비 → `config/collector.toml`의 `client_id`(`team5-a-...`)와 `storage.path`(`/srv/team5/collector/collector.db`) 지정 → `sudo deploy/install-service.sh` → `status` 확인. 보드 A에서는 코드를 고치지 않는다
- [ ] T039 **(보드)** 보드 A에서 24시간 가동 후 `report`로 커버율 재확인, `docs/reports/001-collection-status.md`에 보드 A 결과 추가
- [ ] T040 quickstart.md 전 과정을 처음부터 따라 해 보고 어긋난 명령·설명 수정

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → **Foundational (Phase 2)** → Story 단계
- **US1 (P1)**: Foundational 뒤 시작. 다른 Story의 전제(수집이 돌아야 나머지를 시험할 수 있음)
- **US2 (P1)**: US1의 `mqtt_client.py` 위에 재연결·감시를 더한다. T020(서비스 파일)은 US1과 동시에 써도 된다
- **US3 (P1)**: 커버율 계산(T024·T025)은 US1과 동시에 개발 가능. 실제 확인(T029)은 US2의 서비스 등록 뒤 24시간
- **US4 (P2)**: US1의 `parser.py` 뒤. US2·US3과 독립
- **US5 (P2)**: US1의 `store.py` 뒤. 독립
- **US6 (P3)**: US3의 `report.py` 뒤
- **Phase 9**: 완료 조건 1~3을 보드 B에서 통과한 뒤 보드 A 배포

## Parallel Examples

```text
# Phase 1: 동시에
T002 requirements 파일 / T003 CLI 뼈대 / T004 샘플 메시지

# US1 시작 시: 동시에
T010 parser 시험 작성 / T024 coverage 시험 작성(US3) / T020 서비스 파일(US2)

# US1 완료 후: 동시에
US2(T016~T019) / US4(T030~T032) / US5(T034)
```

## Implementation Strategy

1. **MVP**: Phase 1·2 → US1 → US2. 이 시점에 보드 B에서 서비스로 계속 쌓이고 장애 뒤 스스로 복구된다. 바로 24시간 가동을 시작해 데이터를 쌓는다
2. **완료 조건 3**: US3를 만들고, MVP가 쌓아 둔 데이터로 24시간 보고서를 만든다
3. **산출물 보완**: US4(형식 변경 기록), US5(내보내기)
4. **선택**: US6
5. **배포**: 보드 A에 올려 24시간 재확인

각 Checkpoint에서 해당 확인 기준을 실제로 확인하고, 통과/미통과를 숫자와 함께 기록한다(constitution 원칙 I).
