# Config Contract: `config/collector.toml`

비밀값(브로커 계정)은 이 파일에 쓰지 않는다. 환경변수 이름만 적는다. 이 파일은 커밋한다.

```toml
[broker]
host = "data.withio.net"
port = 1883
username_env = "MQTT_USERNAME"
password_env = "MQTT_PASSWORD"
client_id = "{hostname}-{user}-collector" # {hostname}, {user}가 자동으로 채워진다. 사람·보드마다 달라야 한다 (공용 계정이라 겹치면 서로 끊김)
keepalive_seconds = 30
reconnect_min_seconds = 1
reconnect_max_seconds = 20                # 브로커 복구 후 1분 기준을 지키기 위한 상한
stale_after_minutes = 5                   # 연결돼 보이는데 이 시간 동안 메시지가 없으면 다시 접속
qos = 1
clean_session = false

[storage]
path = "data/collector.db"                # 보드 A 운영 시 공용 경로로 바꾼다

[[sources]]
name = "chirpstack"
topic = "application/+/device/+/event/up"
format = "json"

  [sources.fields]                        # 메시지 안의 위치 (점 경로)
  device_id = "deviceInfo.devEui"
  device_profile = "deviceInfo.deviceProfileName"
  space = "deviceInfo.tags.location"
  spot = "deviceInfo.tags.spot"           # 없어도 된다
  ns_time = "rxInfo[].nsTime"             # 배열이면 가장 이른 값
  gw_time = "time"
  fcnt = "fCnt"
  dedup_key = "deduplicationId"
  values = "object"                       # 측정값 묶음

  [sources.metrics]
  include = ["co2", "temperature", "humidity", "battery"]
  rename = {}                             # 예: { temp = "temperature" }
  units = { co2 = "ppm", temperature = "°C", humidity = "%", battery = "%" }

[intervals]                               # 기대 전송 간격(분), 커버율 계산용
default = 1
by_profile = { "EM500-CO2" = 2 }
by_device = { "24e124725d089152" = 10, "24e124128c140101" = 10 }   # 회의실 AM103, 실습실 전방 우측 AM107 (2026-10-21 관찰)
```

## 규칙

- `fields`의 필수 키: `device_id`, `space`, `values`. 나머지는 없으면 비워 두고, 그 메시지에 해당 값이 없으면 NULL로 저장한다.
- 점 경로 문법: `a.b.c`는 중첩 키, `a[].b`는 배열의 각 원소에서 `b`를 모은다(시각이면 가장 이른 값).
- `metrics.include`에 없는 항목은 저장하지 않는다. 비우면(`[]`) 숫자인 항목을 전부 저장한다.
- 소스를 여러 개 둘 수 있다. 같은 메시지가 여러 소스 토픽에 맞으면 첫 번째 소스만 적용한다.
- 설정을 바꾼 뒤에는 `check-config`로 확인하고 서비스를 재시작한다(`sudo systemctl restart collector-$USER`). 코드 수정은 필요 없다(FR-005).

## 형식 변경 대응 예시 (산출물용)

`config/examples/format-change.toml`: 시험용 로컬 브로커(`localhost:1883`)에 "필드 이름이 바뀐" 메시지(예: `object.co2` → `measurements.CO2_ppm`, `tags.location` → `tags.room`)를 보낸다고 가정하고, `fields.values`, `fields.space`, `metrics.rename`만 고쳐 대응한다. 무엇을 바꿨는지 보고서에 기록한다.
