# Implementation Plan: 과제 1 — 데이터 수집 체계

**Branch**: `001-data-collection` (작업 브랜치: `claude/brave-fermi-q8umi1`) | **Date**: 2026-10-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-data-collection/spec.md`

## Summary

MQTT 브로커(`data.withio.net:1883`)의 ChirpStack 센서 메시지를 구독해, 보드 안 SQLite 파일에 센서 × 항목 × 시각 단위로 받은 그대로 저장하는 Python 수집기를 만든다. 메시지 해석 규칙과 센서별 전송 간격은 TOML 설정으로 빼서 코드 수정 없이 형식 변경에 대응하고, 자동 재연결 + 무응답 감시 + systemd(`Restart=always`)로 브로커·네트워크 장애와 재부팅 뒤 사람 손 없이 복구한다. CLI로 현황(`status`), 24시간 보고서(`report`, 결측 구간과 원인 추정 포함), 학습용 내보내기(`export`)를 제공한다. 실제 메시지 관찰에서 나온 세 가지(게이트웨이 시계 5분 오차, 같은 메시지 이중 도착, 센서별 간격 1·2·10분)를 설계에 반영했다(research.md R3·R4·R8).

## Technical Context

**Language/Version**: Python 3.12 (Ubuntu 24.04 기본), 계정별 `.venv`

**Primary Dependencies**: `paho-mqtt` 2.x (순수 Python, ARM64 문제 없음). 표준 라이브러리 `sqlite3`, `tomllib`, `argparse`, `logging`. 개발용 `pytest`

**Storage**: 보드 로컬 SQLite 1개 파일, WAL 모드 (`data/collector.db`, 설정으로 변경)

**Testing**: `pytest` 단위 시험(해석·중복·커버율·결측 원인) + quickstart.md의 장애 시험 절차(실제 보드)

**Target Platform**: Jetson Orin Nano Super, JetPack 7.2.1, Ubuntu 24.04 ARM64. 개발·시험은 보드 B, 최종 운영은 보드 A. systemd 서비스

**Project Type**: 상시 실행 서비스(데몬) + CLI 도구

**Performance Goals**: 센서 약 15대 × 분당 최대 1건(항목 4개 → 분당 약 60행). 메시지 1건 처리·저장 100ms 이내. 브로커·네트워크 복구 후 60초 이내 재수집(목표 20초 이내)

**Constraints**: 상주 메모리 100MB 이하(같은 보드에 로컬 LLM·모델이 올라갈 예정), 비밀값은 `.env`/환경변수만, DB·데이터 파일은 커밋 금지, 보드 A 재부팅 금지, 공용 MQTT 계정이라 client ID 충돌 방지

**Scale/Scope**: 하루 약 8만 행(약 10~20MB/일 추정, 1년 4~7GB 수준 — 정확한 값은 심화 FR-017에서 실측으로 추정). 공간 4곳 이상, 센서 15대 안팎, 기본 항목 4개(co2, temperature, humidity, battery)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 원칙 | 판정 | 근거 |
|---|---|---|
| I. 확인 기준이 곧 완료 조건 | 통과 | spec "완료 조건"에 확인 기준 3개 원문, SC-001~003에 대응. 산출물 2종(수집 현황 보고, 형식 변경 대응 기록)이 `report` 명령과 `config/examples/format-change.toml` 절차로 연결됨 |
| II. 공정한 평가와 누수 금지 | 해당 적음 | 과제 1은 학습·평가가 없다. 받은 값을 정제 없이 원본으로 저장하고 `measured_at` 기준으로 내보내, 과제 2의 시간순 분할이 가능하게 한다 |
| III. 전처리는 한 곳에서 | 통과 | 수집기는 해석(번역)과 중복 제거만 하고 정제는 하지 않는다. 정제·리샘플링은 과제 2에서 공용 모듈로 만든다 |
| IV. 측정 조건 기록 | 통과 | `report`에 기간, 실데이터 여부, 보드, 전력 모드, 설정·코드 커밋을 자동 기록 |
| V. 결과에는 해석 | 통과 | 결측 구간마다 추정 원인과 근거를 자동으로 붙이고, 해석 문장 칸을 남긴다 |
| VI. 사람 손 없이 복구 | 통과 | systemd `Restart=always`, 부팅 시 시작, 자동 재연결(상한 20초), 무응답 감시, 설정만으로 형식 대응, 상태(끊김)를 `status`로 숨기지 않고 표시 |
| VII. 단순한 것부터 | 통과 | 외부 의존성 1개, DB 서버 없음(SQLite), 웹 화면 대신 CLI. 제공 키트가 아직 없어 키트 실행 단계는 해당 없음(키트가 오면 비교 후 조정) |
| 운영 제약 | 통과 | 개발·장애 시험은 보드 B, 보드 A는 pull·서비스 등록만. 비밀값은 `.env`, DB는 `data/`(커밋 제외). 메모리 예산 100MB |

**Phase 1 설계 후 재확인**: 위 판정 유지. 위반 없음 → Complexity Tracking 비움.

## Project Structure

### Documentation (this feature)

```text
specs/001-data-collection/
├── spec.md
├── plan.md              # 이 문서
├── research.md          # 기술 선택과 근거 (R1~R10)
├── data-model.md        # SQLite 표 구조
├── quickstart.md        # 실행·확인 기준 시험 절차
├── contracts/
│   ├── cli.md           # 명령(run/status/report/export/check-config)
│   └── config.md        # 설정 파일 형식
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks에서 생성
```

### Source Code (repository root)

```text
collector/
├── __init__.py
├── __main__.py          # CLI 진입점 (run/status/report/export/check-config)
├── config.py            # TOML 설정 + 환경변수 읽기, 검증
├── parser.py            # 점 경로 해석, 메시지 → 측정값 행 (설정 기반)
├── store.py             # SQLite 스키마, 저장(INSERT OR IGNORE), 조회, 사건 기록
├── mqtt_client.py       # 접속, 구독, 재연결, 무응답 감시, 1분 요약 로그
├── coverage.py          # 커버율, 결측 구간, 원인 추정
└── report.py            # status 표, Markdown 보고서

config/
├── collector.toml               # 운영 설정 (비밀값 없음)
└── examples/
    └── format-change.toml       # 형식 변경 대응 시험용

deploy/
├── collector.service            # systemd 템플릿
└── install-service.sh           # 설치(sudo), enable, start

tests/
├── fixtures/                    # 실제 형식을 따른 샘플 메시지 (장비 ID 등은 가공)
│   ├── chirpstack_up.json
│   ├── chirpstack_up_no_spot.json
│   ├── chirpstack_up_skewed_gw.json
│   └── format_changed.json
├── test_parser.py
├── test_store.py
└── test_coverage.py

requirements.txt                 # paho-mqtt
requirements-dev.txt             # pytest
docs/reports/                    # report 출력 (001-collection-status.md 등)
data/                            # collector.db, export.csv (커밋 제외)
```

**Structure Decision**: 단일 Python 패키지 `collector/`. 과제 8 서비스에서도 저장소 조회 코드를 재사용할 수 있도록 저장·조회는 `store.py`에 모은다. 웹 서버는 만들지 않는다(과제 8에서 조회 API를 만들 때 함께 다룬다).

## 확인 기준 ↔ 설계 연결

| 완료 조건 | 설계 요소 | 시험 (quickstart.md) |
|---|---|---|
| 1. 브로커 껐다 켜도 1분 안에 재수집 | 자동 재연결(상한 20초), 무응답 감시, 세션 유지 | SC-001: iptables로 1883 차단·해제, 3회 |
| 2. 재부팅 후 자동 재수집 | systemd 시스템 서비스, `After=network-online.target`, `Restart=always` | SC-002: 보드 B `sudo reboot` |
| 3. 24시간 커버율 95%, 결측 원인 설명 | 센서별 간격 기준 커버율, 결측 구간, `fCnt`·사건·배터리 기반 원인 추정, `report` | SC-003: 24시간 가동 후 보고서 |

## 위험과 대응

| 위험 | 대응 |
|---|---|
| 사무실 AM103 배터리 1% → 곧 끊김 | 결측 원인 "센서 쪽(배터리)"으로 자동 표시. 해당 센서는 보고서에서 따로 설명 |
| 공용 계정에서 client ID 충돌 | 설정에서 사람·보드별 client ID. 서로 끊는 현상이 보이면 사건 로그로 확인 |
| 브로커가 세션 보관 미지원 | 동작에는 영향 없음. 끊김 동안 손실은 `fCnt` 건너뜀으로 드러나고 보고서에 기록 |
| 보드 A의 MQTT 접속 불가 | 배포 전 `nc`로 확인. 안 되면 보드 A 네트워크 설정을 먼저 해결 |
| 교육장 InfluxDB에 쓰라는 요구가 나옴 | 저장 대상을 하나 추가하는 구조로 대응(현재 설계 유지) |

## Complexity Tracking

위반 없음.
