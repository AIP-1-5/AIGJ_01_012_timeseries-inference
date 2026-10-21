# CLI Contract: `python -m collector`

모든 명령은 저장소 루트에서 `.venv`의 Python으로 실행한다. 비밀값은 환경변수(`MQTT_USERNAME`, `MQTT_PASSWORD`)에서 읽는다. 공통 옵션 `--config <파일>`(기본 `config/collector.toml`).

## `run` — 수집기 실행

```
python -m collector run [--config config/collector.toml]
```

- 브로커에 접속해 설정의 소스 토픽을 구독하고 계속 저장한다. 종료 신호(SIGTERM, Ctrl+C)를 받으면 `stop` 사건을 남기고 끝낸다.
- 로그는 표준 출력(systemd 환경에서는 journald)으로 한 줄씩: 시각, 수준, 내용. 비밀번호는 출력하지 않는다.
- 1분마다 한 줄 요약 로그: 지난 1분 저장 건수, 중복 수, 실패 수, 연결 상태.
- 종료 코드: 정상 종료 0, 설정 오류 2(서비스가 무한 재시작하지 않도록 systemd에서 `RestartPreventExitStatus=2`), 그 밖의 오류 1.

## `status` — 지금 잘 쌓이고 있나

```
python -m collector status [--hours 24] [--space 실습실]
```

출력(표, 공간별로 묶음):

```
기준: 최근 24시간 (2026-10-20 10:00 ~ 2026-10-21 10:00 KST) · 보드 team5-b · 실데이터
수집기: 실행 중 · 마지막 연결 09:12:03 · 마지막 끊김 없음 · 오늘 중복 312건 · 실패 0건

공간     위치          센서        항목         건수   마지막 수신   경과    간격   커버율(간격)  커버율(1분)
실습실   후면 오른쪽    AM107      co2          1438   09:59:47     0:13    1분    99.9%        99.9%
실습실   전방 좌측      EM500-CO2  co2           719   09:59:56     0:04    2분    99.8%        49.9%
회의실   -             AM103      co2           144   09:57:12     2:48    10분   100.0%       10.0%
사무실   업무 공간 안쪽  AM103      co2             0   -            끊김    1분    0.0%         0.0%   ← 끊김
...
```

- 끊김 조건: data-model.md "끊김 표시".
- 종료 코드: 끊긴 센서·항목이 하나라도 있으면 3, 아니면 0(나중에 감시 스크립트에서 쓸 수 있게).

## `report` — 수집 현황 보고서

```
python -m collector report [--hours 24] [--out docs/reports/001-collection-status.md]
```

- 산출물 "수집 현황 보고"를 Markdown으로 만든다:
  - 측정 조건: 기간, 실데이터 여부, 보드, 전력 모드(`nvpmodel -q` 결과, 실패하면 "확인 불가"), 설정 파일과 코드 커밋
  - 센서·항목별 커버율 표(간격 기준, 1분 기준)와 공간별 요약
  - 결측 구간 목록: 시작, 끝, 길이, 추정 원인(research.md R8), 근거(수집기 사건, `fCnt` 차이, 배터리)
  - 중복 건수, 해석 실패 건수와 사유별 집계
  - 시계 점검: `received_at - measured_at` 중앙값
- 해석 문장("왜 이렇게 나왔고 무엇을 해야 하는가")은 사람이 채우는 칸을 남긴다(constitution V).

## `export` — 학습용 내보내기

```
python -m collector export --from 2026-10-20 --to 2026-10-21 [--metric co2 --metric temperature] --out data/export.csv
```

- 열: `measured_at, device_id, device_profile, space, spot, metric, value, unit, gw_time, received_at, fcnt`
- 읽기 전용 연결로 조회해 수집을 막지 않는다(FR-013). 출력 경로가 `data/` 밖이면 경고만 한다(커밋 방지는 `.gitignore`가 담당).

## `check-config` — 설정 점검

```
python -m collector check-config [--config ...] [--sample tests/fixtures/chirpstack_up.json]
```

- 설정 문법과 필수 항목을 검사한다. `--sample`을 주면 그 메시지를 해석한 결과(저장될 행)를 보여 주고 저장은 하지 않는다. 형식 변경 대응 시 설정을 고친 뒤 먼저 이것으로 확인한다.
