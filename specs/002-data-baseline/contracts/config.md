# Config Contract: `config/preprocess.toml`

정제 규칙과 공간 구성을 코드가 아니라 설정으로 둔다. 과제 3 이후와 과제 8 상시 서비스가 같은 파일을 읽는다(constitution 원칙 III).

```toml
[source]
influx_bucket = "iot-bucket"
metrics = ["co2", "temperature", "humidity", "battery"]
start = "2025-08-08"

[resample]
interval_minutes = 5          # 칸 [t-5분, t)의 평균을 t에 붙인다
max_interp_minutes = 15       # 이 길이 이하 결측만 선형 보간

[ranges]                      # 허용 범위 (밖이면 제거)
co2 = [300, 5000]
temperature = [0, 45]
humidity = [1, 99]

[faults]
# 같은 값이 이 시간(분) 이상 이어지면 값 고정 의심. 항목마다 다르다 (2026-10-21 실데이터 확인):
# 정상인 빈방에서 같은 값이 이어진 가장 긴 구간이 CO₂ 3.3시간, 온도·습도 약 15시간(밤새)이었고 24시간 이상은 없었다.
# 60분으로 두면 온도 0.1℃·습도 1% 해상도 때문에 정상인 밤이 고장으로 잡혔다(회의실 온도 14.5%).
stuck_minutes = { co2 = 360, temperature = 1440, humidity = 1440 }
night_co2_hours = ["02:00", "05:00"]
night_co2_excess_ppm = 150    # 밤 바닥값이 외기(420)보다 이만큼 높으면 영점 틀어짐 의심 (표시만)

[[spaces]]
space = "실습실"
co2 = "all"                   # 이 공간의 해당 항목 센서 전부 (중앙값)
temperature = "all"
humidity = "all"
exclude_devices = ["24e124136d151523"]   # 실습실 복도 EM300-TH (복도라 실내 대표가 아님)

[[spaces]]
space = "사무실"
co2 = "all"
temperature = "all"
humidity = "all"

[[spaces]]
space = "회의실"
co2 = "all"
temperature = "all"
humidity = "all"

[[spaces]]
space = "사무실 밖"
temperature = "all"           # CO₂ 없음, 참고용
humidity = "all"

[split]
test_days = 28
val_days = 28
boundary_hour = 0             # KST 자정 경계

[evaluation]
horizons_minutes = [5, 15, 30, 60]
targets = ["co2", "temperature"]
mean_window_minutes = 60
min_window_fraction = 0.75    # 최근 1시간 12칸 중 9칸 이상
class_hours = ["09:00", "18:00"]   # 부분집합 보고용 (평일)
```

## 규칙

- `[[spaces]]`에 없는 공간은 분석하지 않는다. 센서를 빼려면 `exclude_devices`에 적는다.
- 범위·보간·고장 기준을 바꾸면 `preprocess`부터 다시 돌리고, 진단서 측정 조건에 설정 파일 커밋을 적는다.
- `[split]`을 바꿔도 이미 고정된 `artifacts/split.json`은 그대로다(바꾸려면 `split --force`, 바꾼 이유를 진단서에 적는다).
