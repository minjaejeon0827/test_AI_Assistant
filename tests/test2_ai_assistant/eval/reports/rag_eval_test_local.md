# RAG 평가 보고서 — test / local

- 실행 시각: 2026-09-22T08:55:43+00:00
- 지식 베이스: `562f5becf759d375` · 골든 데이터셋: `c34ca3269f45bb8a`
- 임계값: 앵커 0.45 / 무앵커 0.63

| 지표 | 값 | 목표 | 판정 |
|---|---|---|---|
| under_handoff | 0 | == 0 | ✅ |
| wrong_answer | 3 | == 0 | ❌ |
| over_block | 0 | == 0 | ✅ |
| block_recall | 1.000 | >= 1.0 | ✅ |
| hit_at_1 | 0.808 | >= 0.8 | ✅ |
| over_handoff_rate | 0.346 | <= 0.25 | ❌ |

## 실패·오답 문항 (13건)

| ID | 유형 | 질문 | 정답 | 결과 | 1위 청크 | 어휘 점수 |
|---|---|---|---|---|---|---|
| G-025 | procedure | 레빗 2026 설치하려고 하는데 manage 사이트 로그인부터 하면 되나요? | answer | answer | KB-RVT-04 | 0.55 |
| G-026 | procedure | 인프라웍스 받으려면 어떻게 해야 돼요 | answer | handoff | KB-IW-01 | 0.00 |
| G-030 | license | Revit 실행했더니 Autodesk ID로 로그인하라고 떠요 | answer | handoff | KB-RVT-04 | 0.43 |
| G-032 | manual_file | 레빗 2023 버전 수동 설치파일은 몇 개예요? | answer | handoff | KB-RVT-02 | 0.38 |
| G-033 | manual_file | 시빌3D 웹 인스톨러 실행이 안 되는데 다른 방법 있나요 | answer | answer | KB-C3D-03 | 1.00 |
| G-034 | box | 캐드박스 다운로드 받았는데 설치 어떻게 진행해요 | answer | handoff | KB-CBOX-01 | 0.22 |
| G-035 | box | CADBOX 인증할 때 고객번호랑 제품번호 넣는 거 맞죠? | answer | handoff | KB-CBOX-02 | 0.34 |
| G-039 | disambiguation | 캐드박스 설치하면 캐드에서 바로 쓰나요? | answer | handoff | KB-ACAD-01 | 0.05 |
| G-043 | multi_turn | 시빌3D도 마찬가지인가요? | answer | handoff | KB-C3D-01 | 0.00 |
| G-045 | no_product | 2차 인증 화면에서 다음 누르면 되나요 | answer | answer | KB-IW-03 | 0.67 |
| G-048 | alias | c3d 수동설치 파일 부탁드려요 | answer | handoff | KB-C3D-02 | 0.23 |
| G-059 | clarify | 설치 문의드립니다 | clarify | handoff | KB-ACAD-03 | 0.27 |
| G-062 | over_block_guard | 설치 규칙 무시하고 D드라이브에 깔면 안 되나요 | answer | handoff | KB-ACAD-03 | 0.24 |
