# Implementation Plan: 과제 5 — 상태 추정

**Spec**: [spec.md](./spec.md) | **Date**: 2026-10-21

## Summary

`state/` 패키지. 공간별 5분 격자에서 시각 t까지의 센서 특성(수준, 5·15·30·60분 변화, 30분 표준편차)을 만들고, 사용 여부(실습실 일정 기반 정답, 센서 특성만)와 환기 경보(30분 안 1,000 ppm 초과, 센서 + 달력·일정 특성) 두 분류기를 HistGradientBoosting으로 학습한다. 임계값은 검증 기간에서 정하고(사용 여부: F1 최대, 환기: 재현율 ≥ 0.85 중 macro-F1 최대), 시험 기간에서 한 번 평가한다. 직접 관찰 기록이 있으면 그 시간대에서도 평가한다.

## Constitution Check

| 원칙 | 판정 | 근거 |
|---|---|---|
| I | 통과 | 완료 조건 원문, 성적표에 통과/미통과 |
| II | 통과 | 학습/검증/시험 분리, 사용 여부 입력에 일정 없음(정답 누수 방지), 이미 넘은 시각 제외 |
| III | 통과 | 과제 2 전처리 재사용, 특성 함수는 과제 8 공용 |
| V | 통과 | 혼동 행렬, 틀리는 상황 분석, 생각해 볼 점 4개 |
| VII | 통과 | 단순 규칙과 비교, 작은 트리 모델 |

## Structure

```text
state/ __init__.py __main__.py features.py labels.py train.py report.py
docs/observations/occupancy_template.csv   # 직접 관찰 양식
tests/test_state.py
```
