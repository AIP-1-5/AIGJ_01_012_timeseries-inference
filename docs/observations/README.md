# 직접 관찰 기록 (과제 5)

일정 기반 정답이 실제와 얼마나 다른지 확인하려고 사람이 직접 본 것을 적는다.

- 파일: `occupancy.csv` (양식: `occupancy_template.csv`를 복사해서 쓴다)
- 한 줄 = 한 구간. `start_kst`, `end_kst`는 한국 시간 `YYYY-MM-DD HH:MM`, `people`은 그 구간 동안 있던 사람 수(대략, 0이면 비어 있음)
- 30분 단위 정도로 적으면 충분하다. 수업 시작·점심·퇴실 앞뒤가 가장 쓸모 있다(모델이 가장 많이 헷갈리는 때)
- 개인 이름은 적지 않는다. 사람 수만 적는다
- 채점: 보드 A의 `data/collector.db`를 복사해 와서 `python -m state observe --db <복사한 collector.db>` → `docs/reports/005-observation.md`
  (관찰한 시간의 실시간 센서로 모델·규칙을 판정해 비교한다. 학습은 하지 않는다)

## 난방 기록 (`heating.csv`)

실습실 난방을 켜고 끈 시각, 설정 온도, 창문 여부. 과제 3·4·5 결과를 해석할 때만 쓰고 학습·모델 선택에는 쓰지 않는다.
