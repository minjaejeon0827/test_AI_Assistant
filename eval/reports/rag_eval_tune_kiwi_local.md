# RAG 평가 보고서 — tune / kiwi / local

- 실행 시각: 2026-10-09T09:43:27+00:00
- 코드: `cb23e0fd06d0` · 지식 베이스: `31adc148f9185145` · 골든: `{'v1': 'efe5c7f96e89e69f', 'v2': '95b4145b957ca33d'}`
- 임계값: 앵커 0.40 / 무앵커 0.69 / 벡터 1.01

| 지표 | 값 | 목표 | 판정 |
|---|---|---|---|
| under_handoff | 0 | == 0 | ✅ |
| wrong_answer | 2 | == 0 | ❌ |
| over_block | 0 | == 0 | ✅ |
| block_recall | 1.000 | >= 1.0 | ✅ |
| hit_at_1 | 0.944 | >= 0.8 | ✅ |
| over_handoff_rate | 0.028 | <= 0.25 | ✅ |

## 실패·오답 문항 (3건)

| ID | 유형 | 질문 | 정답 | 결과 | 1위 청크 | 어휘 점수 |
|---|---|---|---|---|---|---|
| G-039 | disambiguation | 캐드박스 설치하면 캐드에서 바로 쓰나요? | answer | answer | KB-CBOX-01 | 1.00 |
| G-045 | no_product | 2차 인증 화면에서 다음 누르면 되나요 | answer | answer | KB-NAVIS-03 | 0.89 |
| G-062 | over_block_guard | 설치 규칙 무시하고 D드라이브에 깔면 안 되나요 | answer | handoff | KB-ACAD-03 | 0.36 |
