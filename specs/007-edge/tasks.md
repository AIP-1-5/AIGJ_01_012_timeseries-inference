# Tasks: 과제 7

- [x] T001 `edge/__main__.py`: bench(가능한 실행 방식 자동 감지, 지연·차이·tegrastats), report
- [x] T001b `edge prepare`(맥): 모델과 시험 입력 2,000개를 `data/edge/bundle/`로 묶음 → 보드는 묶음만 읽고 측정 (보드에서 1년치 표본을 만들지 않음, 2026-10-22)
- [ ] T002 (보드 B) GPU 스택: CUDA판 torch + TensorRT 파이썬 (pip로 되는지 시도, 안 되면 사용자 sudo apt)
- [ ] T003 맥 `python -m edge prepare` → `scp -r data/edge/bundle board-b:…/data/edge/` → (보드 B, 메모리 상한 2G) `EDGE_PYTHON=.venv-gpu/bin/python ./edge.sh all`, 결과 커밋
- [ ] T004 `docs/reports/007-edge.md` 해석
