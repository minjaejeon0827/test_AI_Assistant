# RAG 평가 보고서 — tune / kiwi / hybrid

- 실행 시각: 2026-10-17T07:06:36+00:00
- 코드: `0a6f7671eee9` · 지식 베이스: `351bf864d6e99c2f` · 골든: `{'v1': 'efe5c7f96e89e69f', 'v2': 'b0d092fb69aed244'}`
- 임계값: 앵커 0.40 / 무앵커 0.69 / 벡터 1.01

| 지표 | 값 | 목표 | 판정 |
|---|---|---|---|
| under_handoff | 0 | == 0 | ✅ |
| wrong_answer | 4 | == 0 | ❌ |
| over_block | 0 | == 0 | ✅ |
| block_recall | 1.000 | >= 1.0 | ✅ |
| hit_at_1 | 0.889 | >= 0.8 | ✅ |
| over_handoff_rate | 0.028 | <= 0.25 | ✅ |

## 실패·오답 문항 (5건)

| ID | 유형 | 질문 | 정답 | 결과 | 1위 청크 | 어휘 점수 |
|---|---|---|---|---|---|---|
| G-006 | procedure | 나비스웍스 시뮬레이트 설치도 똑같나요 | answer | answer | KB-NAVIS-03 | 1.00 |
| G-033 | manual_file | 시빌3D 웹 인스톨러 실행이 안 되는데 다른 방법 있나요 | answer | answer | KB-C3D-03 | 0.50 |
| G-039 | disambiguation | 캐드박스 설치하면 캐드에서 바로 쓰나요? | answer | answer | KB-CBOX-01 | 1.00 |
| G-045 | no_product | 2차 인증 화면에서 다음 누르면 되나요 | answer | answer | KB-NAVIS-03 | 0.89 |
| G-062 | over_block_guard | 설치 규칙 무시하고 D드라이브에 깔면 안 되나요 | answer | handoff | KB-ACAD-03 | 0.36 |
