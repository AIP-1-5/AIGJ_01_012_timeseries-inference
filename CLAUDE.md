# CLAUDE.md

## 이 저장소

교육장 CO₂·온도·습도 센서 데이터로 예측·이상 탐지·상태 추정을 하고, Jetson에서 상시 서비스로 운영하는 과제의 개인 저장소다.

- 과제 원문: `../docs/assignment.md` (무엇을 해결해야 하는지, 과제별 확인 기준과 산출물)
  Private 저장소 `AIP-1-5/docs`에 있다. 이 저장소 옆에 clone하고 `claude --add-dir ../docs`로 연다.
  파일이 안 보이면 작업을 시작하기 전에 사용자에게 알린다.
- 원칙: `.specify/memory/constitution.md` (누수 금지, 전처리 공용화, 측정 조건 기록 등). 작업 전에 읽는다.
- 과제 결과물: `docs/reports/`

## 작업 방식

- Spec Kit으로 진행한다. 과제 하나 = spec 하나, 번호는 과제 번호와 맞춘다(과제 1 → `001-data-collection`).
- 새 과제를 시작하는 첫 요청은 **읽고 계획만** 한다. `/speckit-specify` → `/speckit-plan` → `/speckit-tasks`까지 하고 멈춘다.
  사용자가 승인하기 전에는 구현하지 않는다.
- `../docs/assignment.md`의 해당 과제 "확인 기준"을 spec의 완료 조건으로 그대로 옮긴다.
- 구현 후에는 확인 기준을 하나씩 실제로 확인하고, 통과/미통과를 숫자와 함께 보고한다.
- 제공 키트(`AIP-1/timeseries-inference`) 코드가 들어오면 그것부터 실행하고 그 위에서 작업한다.
  아직 이 저장소에 없다.

## 장비와 역할

전체 환경 정보(보드 주소, 계정, 네트워크, 하지 말 것)는 팀 문서 저장소의 `../docs/environment.md`에 있고 아래에서 불러온다.

@../docs/environment.md

요약:

| | 보드 A (`team5-a`) | 보드 B (`team5-b`) | 맥 |
|---|---|---|---|
| 역할 | 운영: 수집기, 제출 서비스(24시간) | 실험: 학습, 변환, 측정 | 하드웨어가 필요 없는 작업 |
| 접속 | `ssh board-a` (Tailscale) | `ssh board-b` (Tailscale) | — |
| 규칙 | 실험·재부팅·코드 수정 금지, pull로 배포만 | 무거운 작업 전 팀에 알림 | — |

- Jetson Orin Nano Super 8GB(CPU·GPU 메모리 공유), JetPack 7.2.1(Jetson Linux r39.2.1), Ubuntu 24.04, ARM64, 전원 모드 MAXN SUPER.
- 패키지는 계정별 가상환경(`.venv`)에 설치하고, 설치 전 ARM64·JetPack/CUDA 호환성을 확인한다.
- 메모리를 크게 쓰는 작업 전에 `free -h`로 남은 메모리를 확인한다.
- 공용 큰 파일(데이터, DB, 모델 파일)은 보드의 `/srv/team5`. 개인 작업은 자기 계정·자기 폴더에서만 한다.
- MQTT 브로커는 보드 B 와이파이에서 접속된다. InfluxDB는 교육장 내부망 주소라 와이파이에서는 안 되고 내부망(유선) 연결이 필요해 보인다(2026-10-20 확인). InfluxDB 용도는 아직 미정이다.

## 하지 말 것

- 이 저장소는 Public이다. 과제 원문을 통째로 복사해 넣지 않는다(spec에 필요한 확인 기준만 옮긴다).
- 비밀값(MQTT 계정, InfluxDB 토큰)을 코드·문서·커밋·로그에 쓰지 않는다. `.env`에서 읽는다(`.env.example` 참고).
- 데이터, DB, `.pt` `.onnx` `.engine` 파일을 커밋하지 않는다(`.gitignore`에 있음).
- 시험 기간 데이터를 학습·선택에 쓰지 않는다.
- 보드에서 `sudo apt autoremove`, 보드 A 재부팅을 하지 않는다.
