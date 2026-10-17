# RAG 평가 보고서 — test2 / kiwi / hybrid / final_hybrid

- 실행 시각: 2026-10-17T07:57:46+00:00
- 코드: `3163dd6c84d9` · 지식 베이스: `31adc148f9185145` · 골든: `{'v1': 'efe5c7f96e89e69f', 'v2': '95b4145b957ca33d'}`
- 임계값: 앵커 0.40 / 무앵커 0.69 / 벡터 1.01

| 지표 | 값 | 목표 | 판정 |
|---|---|---|---|
| under_handoff | 0 | == 0 | ✅ |
| wrong_answer | 2 | == 0 | ❌ |
| over_block | 0 | == 0 | ✅ |
| block_recall | 1.000 | >= 1.0 | ✅ |
| hit_at_1 | 0.946 | >= 0.8 | ✅ |
| over_handoff_rate | 0.108 | <= 0.25 | ✅ |

## 문체별 정답 답변률

| 문체 | 정답 | 문항 | 비율 |
|---|---|---|---|
| colloquial | 8 | 11 | 73% |
| mixed | 2 | 2 | 100% |
| polite_long | 3 | 4 | 75% |
| situation | 10 | 10 | 100% |
| standard | 8 | 10 | 80% |

## 실패·오답 문항 (7건)

| ID | 유형 | 질문 | 정답 | 결과 | 1위 청크 | 어휘 점수 |
|---|---|---|---|---|---|---|
| T2-001 | procedure | 오토캐드 2026 설치하려면 어느 사이트에서 시작하나요 | answer | answer | KB-ACAD-03 | 0.54 |
| T2-003 | manual_file | 오토캐드 2023 오프라인 설치용 파일도 있어요? | answer | handoff | KB-ACAD-02 | 0.09 |
| T2-019 | procedure | 레빗 2025 설치하는 순서 처음부터 알려주세요 | answer | answer | KB-RVT-04 | 1.00 |
| T2-022 | license | 레빗 정품인증 하는 법 좀 알려줘요 | answer | handoff | KB-RVT-04 | 0.15 |
| T2-025 | box | 에너지박스 설치파일 받는 곳이 어디예요 | answer | handoff | KB-EBOX-01 | 0.25 |
| T2-029 | box | 레빗박스 설치 끝났는데 고객번호 어디에 넣어요? | answer | handoff | KB-RBOX-02 | 0.27 |
| T2-058 | clarify | 설치 관련해서 여쭤볼게 있어요 | clarify | handoff | KB-ACAD-01 | 0.02 |
