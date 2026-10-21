# Tasks: 과제 5 — 상태 추정

- [ ] T001 `state/features.py` 센서 특성(과거만), 달력·일정 특성(환기용)
- [ ] T002 `state/labels.py` 사용 여부 정답(일정, 점심·전이 제외), 환기 정답(이미 넘은 시각 제외), 관찰 기록 읽기
- [ ] T003 `state/train.py` 두 분류기, 검증 임계값, 시험 평가, 규칙 비교, 혼동 행렬
- [ ] T004 `state/report.py` 성적표 `docs/reports/005-classification.md`, 그래프 `figures/005-*.png`
- [ ] T005 `tests/test_state.py` 일정 특성이 사용 여부 입력에 없음, 이미 넘은 시각 제외, 미래 정보 없음, 임계값은 검증만
- [ ] T006 관찰 양식 `docs/observations/occupancy_template.csv`
- [ ] T007 (보드 B) 실데이터 실행, 결과 커밋
- [ ] T008 `docs/reports/005-state.md` 해석
