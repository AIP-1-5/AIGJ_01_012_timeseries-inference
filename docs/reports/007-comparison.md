# 과제 7 엣지 최적화 측정표

`python -m edge bench`가 만든 표. 측정 2026-10-22T05:36:27Z. 해석은 [007-edge.md](007-edge.md).

## 측정 조건

| 항목 | 값 |
|---|---|
| 장비 | team5-b Linux aarch64, # R39 (release), REVISION: 2.1, GCID: 46758480, BOARD: generic, EABI: aarch64, DATE: Fri Aug  7 05:54:22 AM UTC 2026 |
| 전력 모드 | NV Power Mode: MAXN_SUPER |
| 클럭 | 고정 안 함 (sudo 필요, 기본 동적 클럭) |
| 반복 | 워밍업 20회, 측정 200회, 배치 1(한 공간)·4(네 공간) |
| 소프트웨어 | Python 3.12.3, torch 2.14.1+cu130 (CUDA True), onnxruntime 1.31.0 ['AzureExecutionProvider', 'CPUExecutionProvider'], TensorRT None |
| 결과 차이 | PyTorch CPU FP32 출력 대비, 시험 기간 표본 2,000개, 원래 단위 |

## gru-no_schedule-L120-s3 (forecast)

| 실행 방식 | 배치 1 평균 / p95 (ms) | 배치 4 평균 / p95 (ms) | 결과 차이 | 평균 전력 | GPU 사용률 | 최대 RAM |
|---|---:|---:|---|---:|---:|---:|
| pytorch_cpu_fp32 | 10.274 / 11.116 | 10.320 / 12.690 | 기준 | 8.71 W | 0% | 3329 MB |
| pytorch_gpu_fp32 | 0.871 / 0.997 | 0.867 / 0.898 | co2_max_ppm 0.0119, co2_mean_ppm 4.986e-05, temp_max_c 1.157e-05 | 7.66 W | 9% | 3438 MB |
| pytorch_gpu_fp16 | 0.850 / 0.967 | 0.877 / 0.925 | co2_max_ppm 1.022, co2_mean_ppm 0.01037, temp_max_c 0.001974 | 7.44 W | 13% | 3512 MB |
| onnxruntime_cpu | 0.223 / 0.238 | 0.536 / 0.550 | co2_max_ppm 0.2543, co2_mean_ppm 0.002874, temp_max_c 0.0003886 | 8.52 W | 0% | 3494 MB |
| tensorrt_fp16 | - | - | TensorRT 없음: No module named 'tensorrt' | - | - | - |

## autoencoder (autoencoder)

| 실행 방식 | 배치 1 평균 / p95 (ms) | 배치 4 평균 / p95 (ms) | 결과 차이 | 평균 전력 | GPU 사용률 | 최대 RAM |
|---|---:|---:|---|---:|---:|---:|
| pytorch_cpu_fp32 | 0.822 / 0.991 | 0.975 / 1.689 | 기준 | 8.91 W | 0% | 3488 MB |
| pytorch_gpu_fp32 | 0.729 / 0.753 | 0.728 / 0.773 | score_max_diff 1.341e-07, alarm_changed_rate 0 | 8.02 W | 5% | 3484 MB |
| pytorch_gpu_fp16 | 0.730 / 0.755 | 0.721 / 0.745 | score_max_diff 0.000357, alarm_changed_rate 0 | 7.42 W | 5% | 3481 MB |
| onnxruntime_cpu | 0.064 / 0.134 | 0.054 / 0.055 | score_max_diff 0.0001289, alarm_changed_rate 0 | - | - | - |
| tensorrt_fp16 | - | - | TensorRT 없음: No module named 'tensorrt' | - | - | - |

과제 5 분류기(HistGradientBoosting)는 트리 모델이라 TensorRT 변환 대상이 아니다. CPU에서 한 번 판정이 1 ms 안쪽이라 그대로 쓴다.
