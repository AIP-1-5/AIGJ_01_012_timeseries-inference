# 최종 제출물

과제 안내 "4. 최종 제출물"에 맞춘 묶음이다. 숫자는 모두 `docs/reports/`의 과제별 보고서에서 옮겼다(2026-10-23 결과 기준).

| 제출물 | 파일 · 위치 |
| --- | --- |
| 결과 보고서 (과제 1~8, 표·그래프 중심) | [final-report.pptx](final-report.pptx) · [final-report.pdf](final-report.pdf) — 본문 29장 + 예비 1장 |
| 상시 서비스 | 보드 A `http://100.113.228.46:8080` (tailnet), MQTT `aip/team5/jaeung/inference/<공간>/state` |
| 결과 형식 문서 | [docs/interface.md](../interface.md) |
| 발표 | 같은 PPT의 요약·과제별 장·"가장 의외였던 발견"(25~26장) |

## 구성

1. 표지 · 한눈에 보는 결과 · 확인 기준 결과(20개 중 16개) · 구조와 공통 측정 조건 · 평가 기준별 근거
2. 수집과 기준선 (과제 1·2)
3. 예측 · 탐지 · 상태 (과제 3·4·5·6)
4. Jetson에서 운영 (과제 7·8)
5. 가장 의외였던 발견 · 운영하며 겪은 일 · 한계와 다음 할 일

모든 결과 장에 측정 조건(데이터 기간, 실데이터/합성, 시험 기간, 전력 모드)을 배지나 표로 적었다. 차트는 PowerPoint의 "데이터 편집"으로 숫자를 고칠 수 있는 네이티브 차트다. 대시보드 그림(22장)은 예시 데이터로 그린 화면이다.

## 다시 만들기

`spec.json`이 덱의 원본이다. 크림·코랄 디자인 스킬(`cream-coral-pptx`)의 엔진으로 만든다. 제목은 Pretendard Bold, 본문은 Pretendard SemiBold다.

```bash
npm install pptxgenjs@3.12.0 sharp@0.33.5
node <스킬>/scripts/build_deck.js docs/submission/spec.json        # → final-report.pptx
python3 <스킬>/scripts/render_preview.py docs/submission/final-report.pptx   # → PDF, 미리보기
```

이 덱은 엔진에 두 가지를 더한 판으로 만들었다. 하나는 제목을 굵게 하는 `fonts.headBold`이고, 다른 하나는 차트 값 축 시작·끝(`min`·`max`)과 pptxgenjs 옵션(`options`)을 그대로 넘기는 것이다. 원래 엔진으로 만들면 제목이 보통 굵기로 나오고, 차트 축이 자동으로 정해진다.
