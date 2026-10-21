# Model I/O Contract (과제 6·7·8이 쓰는 형식)

과제 7(ONNX·TensorRT)과 과제 8(상시 서비스)은 이 문서만 보고 모델을 부를 수 있어야 한다.

## 입력 (배치 B)

| 이름 | 모양 | 형식 |
|---|---|---|
| `past` | B × 24 × F_past | float32. 칸 순서는 오래된 것 → t. 특성 순서는 아래 표 |
| `future` | B × 12 × C | float32. t+5분 → t+60분 |
| `space` | B × 4 | float32 원-핫. 순서: 실습실, 사무실, 회의실, 사무실 밖 |

`past` 특성 순서: `co2_z, temperature_z, humidity_z, co2_ok, temperature_ok, humidity_ok, [달력·일정 C개]`
- `*_z` = (값 − 평균) / 표준편차, `artifacts/normalization.json`의 그 공간·항목 값. 쓸 수 없는 칸은 0
- `*_ok` = 그 칸 값을 쓸 수 있으면 1 (관측 또는 보간), 아니면 0

달력·일정 C 순서 (`full` 기준 6개): `tod_sin, tod_cos, dow_sin, dow_cos, class_day, in_class`
- `tod` = 칸 시작 시각(KST)의 하루 중 비율, `dow` = 월 0 … 일 6
- `class_day` = 실습실 일정 요일이고 공휴일이 아니면 1
- `in_class` = 실습실이고 수업 시간이면 1, 점심이면 0.5, 그 외 0

## 출력

| 이름 | 모양 | 형식 |
|---|---|---|
| `delta` | B × 12 × 2 | float32. `[:, k, 0]` = CO₂, `[:, k, 1]` = 온도의 (t+5(k+1)분 값 − t 값), 정규화 단위 |

원래 단위 예측 = `y(t) + delta × 표준편차`. CO₂가 없는 공간(사무실 밖)의 CO₂ 출력은 쓰지 않는다.

## 전처리는 과제 2와 같아야 한다

서비스는 `pipeline.preprocess`(5분 칸, 센서 이어 쓰기, 중앙값, 15분 이하 보간)와 `forecast.dataset`의 특성 함수를 그대로 불러 쓴다. 학습 때와 다르게 만들면 같은 모델이 다른 숫자를 낸다(과제 8 해결할 것 3).
