# 과제 1 산출물: 메시지 형식 변경 대응 기록

과제 1 산출물 "메시지 형식을 바꿔야 하는 상황 하나를 가정하고, 무엇을 바꿔서 대응했는지 기록"이다.

## 측정 조건

| 항목 | 값 |
|---|---|
| 일시 | 2026-10-21 14:39 KST |
| 장소 | 보드 A (`team5-a`) 시험용 로컬 브로커 `localhost:1883` (원래 보드 B에서 하기로 했으나 보드 A에서 실행됨. 운영 수집기와 브로커·저장 파일이 달라 영향 없음, 시험 후 시험용 브로커와 저장 파일 제거) |
| 데이터 | 가정한 새 형식의 시험 메시지 1건 (`tests/fixtures/format_changed.json`) |
| 수집기 | 운영과 같은 코드, 설정만 `config/examples/format-change.toml` |

## 가정한 상황

센서 업체가 메시지 형식을 바꿨다. 필드 이름과 위치가 달라졌고, 네트워크 서버 시각(`nsTime`) 대신 보낸 시각(`sentAt`) 하나만 온다.

| 의미 | 기존 형식 (ChirpStack) | 바뀐 형식 |
|---|---|---|
| 센서 ID | `deviceInfo.devEui` | `device.eui` |
| 센서 종류 | `deviceInfo.deviceProfileName` | `device.model` |
| 공간 | `deviceInfo.tags.location` | `device.tags.room` |
| 공간 안 위치 | `deviceInfo.tags.spot` | `device.tags.position` |
| 기준 시각 | `rxInfo[].nsTime` | `sentAt` |
| 일련번호 | `fCnt` | `seq` |
| 중복 판별 ID | `deduplicationId` | `msgId` |
| 측정값 묶음 | `object` | `measurements` |
| 항목 이름 | `co2`, `temperature`, `humidity` | `CO2_ppm`, `temp_c`, `rh_pct` |

## 무엇을 바꿔서 대응했나

`config/examples/format-change.toml`에서 아래만 바꿨다. **코드는 한 줄도 바꾸지 않았다** (시험 후 `git status --short` 출력 없음).

```toml
[[sources]]
name = "new-format"
topic = "test/new-format"

  [sources.fields]
  device_id = "device.eui"
  device_profile = "device.model"
  space = "device.tags.room"
  spot = "device.tags.position"
  ns_time = "sentAt"
  fcnt = "seq"
  dedup_key = "msgId"
  values = "measurements"

  [sources.metrics]
  include = ["co2", "temperature", "humidity"]
  rename = { CO2_ppm = "co2", temp_c = "temperature", rh_pct = "humidity" }
```

- `fields`: 바뀐 위치를 점 경로로 다시 가리켰다
- `metrics.rename`: 새 항목 이름을 기존 이름(`co2` 등)으로 바꿔, 저장소와 이후 과제(전처리·학습)가 이름 변화를 몰라도 되게 했다
- 설정을 고친 뒤 `check-config --sample`로 저장 없이 해석 결과를 먼저 확인했다

## 결과

1. **저장 없이 미리 보기** (`check-config --sample`): 3행, 실패 없음
   - `실습실 / 창가  NEW-CO2  co2=812ppm`, `temperature=23.4°C`, `humidity=58%`, `fcnt=7`, `key=fmt-0001`, 기준 시각 `2026-10-21T00:40:00Z`
2. **실제 수집**: 시험용 브로커에 바뀐 형식 메시지를 보내자 시험용 수집기가 받아 `NEW-CO2` 센서의 co2·temperature·humidity 3개 항목으로 저장했다 (`status`에 3줄). 시험 메시지의 시각이 오전이라 `← 끊김`으로 표시된 것은 정상이다.
3. **단위 시험**: `tests/test_parser.py::test_format_change_handled_by_config_only`가 같은 상황을 자동으로 확인한다.

## 해석

- **왜 설정만으로 됐나**: 수집기는 메시지 안의 위치를 코드가 아니라 설정의 점 경로(`a.b.c`, 배열은 `a[].b`)로 찾는다. 형식이 바뀌어도 "어디에 무엇이 있는지"만 다시 적으면 된다(research.md R7).
- **그래서**: 새 센서가 추가되거나 업체가 형식을 바꿔도 운영 중인 수집기 코드를 배포할 필요 없이 설정 파일을 고치고 `sudo systemctl restart collector-$USER`만 하면 된다. 항목 이름을 `rename`으로 맞추므로 과제 2 이후의 코드도 바뀌지 않는다.
- **한계**: 값의 단위가 바뀌는 경우(예: °F로 온다)는 이름 바꾸기만으로는 대응할 수 없다. 지금 설정은 단위 표시만 붙이고 값은 그대로 저장한다(정제하지 않는 원칙). 단위 변환이 필요해지면 과제 2 전처리에서 처리하거나, 설정에 변환 규칙을 추가하는 기능이 필요하다. 시각 필드가 ISO 8601이 아닌 형식(예: 밀리초 숫자)으로 바뀌는 경우도 지금은 해석하지 못하고 수신 시각으로 대신한다.
