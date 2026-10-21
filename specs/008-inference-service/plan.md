# Plan: 과제 8

## 구조

```
보드 B: ./service.sh export → data/models/ (forecast.onnx, autoencoder.onnx, state.pkl, normalization.json, *.json, manifest.json)
          │ scp
보드 A: collector-<계정> → data/collector.db (WAL, 읽기 전용으로 연다)
        inference-<계정> (python -m service run)
          ├─ 계산 스레드: 매분 engine.run_cycle()
          │    수집기 DB 최근 26시간 → pipeline.preprocess (과제 2와 같은 함수) → 공간별 5분 표
          │    → 예측 ONNX (forecast.dataset.space_arrays) / 오토인코더 ONNX + 규칙 / 분류기 두 개 / 센서 끊김
          ├─ MQTT 발행 (retained state, alerts, status + LWT)
          └─ HTTP 서버 스레드 (ThreadingHTTPServer, 마지막 성공 결과를 읽기만)
```

## 결정

- **ONNX Runtime CPU, 스레드 2개**: 모델이 작아(파라미터 10만 이하) 주기당 계산 대부분이 전처리다. 보드 메모리는 이후 LLM과 나눠 쓴다. 과제 7에서 ONNX 결과가 PyTorch와 같은지 확인한다.
- **끝난 5분 칸만 사용**: 학습 표본은 `[t−5, t)` 칸이 다 찬 값이다. 서비스가 반쯤 찬 칸을 쓰면 학습과 입력이 달라진다.
- **끊김 판정은 센서별 기대 간격 기준**: 수집기 설정(`[intervals]`)의 센서별 간격 × 3, 최소 15분. 회의실 센서(10분 간격)가 매번 끊김으로 잡히지 않게 한다.
- **끊기면 비운다**: 최근 값이 15분 넘게 없으면 예측을 내지 않는다(오래된 값을 '지금'으로 쓰지 않는다).
- **경보 중복 억제**: 같은 (공간, 종류)는 처음, 30분마다, 해제 때만 MQTT로 보낸다. API의 `alerts`는 항상 현재 상태다.
- **피클 버전 고정**: 분류기 피클은 scikit-learn 버전이 같아야 하므로 manifest에 버전을 적고 다르면 시작하지 않는다.

## 확인 방법

| 기준 | 방법 |
| --- | --- |
| 1 12시간 | `./service.sh summary --since <시작>` → `docs/reports/008-service-log.md` (주기 수, 성공률, 멈춤 구간) |
| 2 끊김 | 보드 A에서 `systemctl stop collector-<계정>` 10분 → `/v1/alerts`·MQTT `alerts`에 `data_gap` active → start → cleared. 시각과 함께 기록 |
| 3 API | `curl /v1/spaces`, `/v1/spaces/meeting/forecast?minutes=30` 실제 응답 |
| 4 문서 | `docs/interface.md` |
| 자동 시작 | 보드 B에서 서비스 설치 후 재부팅 시험 (보드 A는 재부팅 금지) |
