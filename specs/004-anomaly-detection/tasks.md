# Tasks: 과제 4 — 이상 탐지

- [ ] T001 `config/anomaly.toml`, `anomaly/` 패키지, `anomaly.sh`
- [ ] T002 [P] `tests/test_anomaly.py`: 주입(씨앗 재현, 원본 불변, 겹침 없음, 종류별 모습), 규칙(급변·고정·밤 상승·냉방·환기), 경보 묶기, 이벤트 매칭(탐지·지연·헛경보), 임계값 선택이 검증만 사용
- [ ] T003 `anomaly/inject.py` 고장 5종 주입 + 고장 목록
- [ ] T004 `anomaly/features.py` 창 특성 (과제 8 공용)
- [ ] T005 `anomaly/model.py` 오토인코더 학습·점수·원인 항목
- [ ] T006 `anomaly/rules.py` 규칙 5종 (기준값은 학습 기간)
- [ ] T007 `anomaly/alarms.py` 경보 묶기, `anomaly/evaluate.py` 매칭·지표·임계값 표
- [ ] T008 `anomaly/report.py` 성적표·임계값 그래프 → `docs/reports/004-detection.md`, `figures/004-threshold.png`
- [ ] T009 CLI: `inject`, `train`, `calibrate`(검증), `evaluate`(시험), `report`
- [ ] T010 (보드 B) 실데이터 실행, 결과 커밋
- [ ] T011 `docs/reports/004-anomaly.md` 해석: 종류별 놓친 이유, 운영 제안, 생각해 볼 점
