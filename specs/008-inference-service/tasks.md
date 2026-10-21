# Tasks: 과제 8

- [x] T001 `service/engine.py`: 매분 계산 (예측·이상·규칙·범위 밖·상태·끊김)
- [x] T002 `service/export.py`: 보드 B에서 모델 묶음(ONNX) + manifest
- [x] T003 `service/__main__.py`: run(MQTT + HTTP + 주기 기록), once, summary
- [x] T004 `tests/test_service.py`: export → 한 주기, 끊김 경보와 복구, 범위 밖, API, 실패 후 회복, 요약
- [x] T005 `deploy/inference.service`, `deploy/install-inference.sh`, `service.sh`, `requirements-service.txt`
- [x] T006 `docs/interface.md` 결과 형식 문서
- [ ] T007 (보드 B) 학습 끝난 뒤 `./service.sh export`, 보드 A로 `data/models/` 복사
- [ ] T008 (보드 A) pull, `.venv`에 requirements-service 설치, tmux에서 `./service.sh run`
- [ ] T009 (사용자, sudo) `sudo deploy/install-inference.sh` — 재부팅 자동 시작
- [ ] T010 (10-22 14:35 이후) 수집기 10분 정지 시험, 결과 기록
- [ ] T011 12시간 뒤 `./service.sh summary`, `docs/reports/008-service.md` 해석
