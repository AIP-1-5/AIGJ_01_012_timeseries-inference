# 과제 8 상시 서비스 운영 기록 (자동 생성)

`python -m service summary --since 2026-10-22T12:14 --score`를 보드 A에서 2026-10-23 08:0x KST에 실행한 출력이다(사용자 실행, 출력 원문 그대로). 해석은 [008-service.md](008-service.md).

- 생성: `python -m service summary` / 기록 파일 `data/service/cycles.csv` / 보드 `team5-a`
- 기간: 2026-10-22 12:14 ~ 2026-10-23 07:55 KST (19.68시간), 주기 60초

| 항목 | 값 |
| --- | --- |
| 주기 수 (성공 / 실패) | 1182 (1182 / 0) |
| 기대 주기 수 대비 성공 | 100.0% (1182 / 1182) |
| 계산 시간 p50 / p95 / 최대 | 1298 / 1411 / 1593 ms |
| 가장 긴 기록 간격 | 61초 |
| 멈춘 구간 (간격 > 180초) | 0회 |

## 경보 (주기 × 공간 기준 횟수)

| 공간:종류 | 주기 수 |
| --- | --- |
| lab:forecast_drift | 100 |
| lab:ventilation | 65 |
| meeting:co2_forecast | 5 |
| meeting:forecast_drift | 850 |
| meeting:ventilation | 111 |
| office:co2_forecast | 75 |
| office:forecast_drift | 450 |
| office:ventilation | 240 |

## 경보 채점 (실제 CO₂로 확인)

같은 공간·종류의 경보는 30분 안이면 한 이벤트. 환기 경보는 30분, CO₂ 초과 예측은 60분 안에 공간 CO₂(5분 중앙값)가 1,000 ppm에 닿으면 맞음.

| 공간 | 종류 | 경보 시각 (KST) | 창 안 최대 CO₂ | 맞음 |
| --- | --- | --- | ---: | --- |
| meeting | ventilation | 2026-10-22 12:14 | 908 | X |
| lab | ventilation | 2026-10-22 13:05 | 826 | X |
| office | ventilation | 2026-10-22 14:20 | 1000 | O |
| lab | ventilation | 2026-10-22 15:20 | 877 | X |
| lab | ventilation | 2026-10-22 16:45 | 910 | X |
| meeting | co2_forecast | 2026-10-22 12:50 | 1061 | O |
| office | co2_forecast | 2026-10-22 14:20 | 1000 | O |
| office | co2_forecast | 2026-10-22 16:10 | 1066 | O |
| office | co2_forecast | 2026-10-22 17:35 | 1006 | O |

- `ventilation`: 1/5 맞음 (20%)
- `co2_forecast`: 4/4 맞음 (100%)
