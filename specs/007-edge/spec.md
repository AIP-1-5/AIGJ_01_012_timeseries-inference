# Feature Specification: 과제 7 — 엣지 최적화

**Feature Branch**: `007-edge` | **Created**: 2026-10-21 | **Status**: Draft

## 완료 조건 (과제 7 확인 기준, 원문 그대로)

1. 변환 후 CO₂ 예측 차이 **1ppm 이하**, 분류 결과가 바뀐 비율 0.1% 이하
2. 측정 조건(전력 모드, 클럭 고정 여부, 반복 횟수, 워밍업)을 함께 기록했다

## 범위

- 모델: 과제 3 예측(GRU), 과제 4 오토인코더. "분류 결과가 바뀐 비율"은 오토인코더의 경보 판정(점수 > 임계값)이 변환 뒤 바뀐 비율로 잰다. 과제 5 분류기는 트리 모델이라 TensorRT 대상이 아니다.
- 실행 방식: PyTorch CPU(기준), PyTorch GPU(FP32·FP16), ONNX Runtime(가능한 제공자), TensorRT FP16(보드에서 빌드).
- 지연: 배치 1(한 공간)·4(네 공간), 워밍업 20·반복 200, 평균·p95.
- 결과 차이: 시험 기간 표본 2,000개, PyTorch CPU FP32 대비, 원래 단위(CO₂ ppm, 온도 ℃).
- 자원: tegrastats로 평균 전력(VDD_IN), GPU 사용률(GR3D_FREQ), 최대 RAM.
- 클럭: `jetson_clocks`는 sudo가 필요해 고정하지 않는다(측정 조건에 적음).

## 제약

- 보드 B에 TensorRT·CUDA 툴킷과 CUDA판 PyTorch가 없다(2026-10-21 확인). 설치에 sudo(apt `nvidia-jetpack`)가 필요하면 사용자가 한다. 없는 방식은 '없음'으로 기록한다.
