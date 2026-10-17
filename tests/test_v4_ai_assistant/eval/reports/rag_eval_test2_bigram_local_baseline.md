# RAG 평가 보고서 — test2 / bigram / local / baseline

- 실행 시각: 2026-10-09T09:41:00+00:00
- 코드: `43815ae83a34` · 지식 베이스: `c5c04a85285e2058` · 골든: `{'v1': 'efe5c7f96e89e69f', 'v2': '95b4145b957ca33d'}`
- 임계값: 앵커 0.45 / 무앵커 0.63 / 벡터 1.01

| 지표 | 값 | 목표 | 판정 |
|---|---|---|---|
| under_handoff | 0 | == 0 | ✅ |
| wrong_answer | 2 | == 0 | ❌ |
| over_block | 0 | == 0 | ✅ |
| block_recall | 1.000 | >= 1.0 | ✅ |
| hit_at_1 | 0.838 | >= 0.8 | ✅ |
| over_handoff_rate | 0.405 | <= 0.25 | ❌ |

## 문체별 정답 답변률

| 문체 | 정답 | 문항 | 비율 |
|---|---|---|---|
| colloquial | 6 | 11 | 55% |
| mixed | 2 | 2 | 100% |
| polite_long | 2 | 4 | 50% |
| situation | 3 | 10 | 30% |
| standard | 7 | 10 | 70% |

## 실패·오답 문항 (18건)

| ID | 유형 | 질문 | 정답 | 결과 | 1위 청크 | 어휘 점수 |
|---|---|---|---|---|---|---|
| T2-001 | procedure | 오토캐드 2026 설치하려면 어느 사이트에서 시작하나요 | answer | handoff | KB-ACAD-03 | 0.26 |
| T2-002 | procedure | AutoCAD 다운받는 곳 알려주실 수 있을까요? 처음 설치해봐서요 | answer | handoff | KB-ACAD-04 | 0.37 |
| T2-003 | manual_file | 오토캐드 2023 오프라인 설치용 파일도 있어요? | answer | handoff | KB-ACAD-02 | 0.14 |
| T2-005 | license | AutoCAD 켰더니 Autodesk ID로 로그인 버튼이 나오는데 이거 누르면 되나요 | answer | handoff | KB-ACAD-04 | 0.23 |
| T2-008 | install_step | civil 3d 설치 중에 추가 구성요소 고르는 화면 나왔어요 | answer | handoff | KB-C3D-03 | 0.24 |
| T2-019 | procedure | 레빗 2025 설치하는 순서 처음부터 알려주세요 | answer | answer | KB-RVT-04 | 1.00 |
| T2-020 | manual_file | Revit 2024는 설치파일이 5개나 되던데 다 받아야 하나요? | answer | handoff | KB-RVT-02 | 0.24 |
| T2-021 | install_step | 레빗 설치 100% 되고 나서 시작 버튼 누르면 끝인가요 | answer | handoff | KB-RVT-03 | 0.40 |
| T2-022 | license | 레빗 정품인증 하는 법 좀 알려줘요 | answer | handoff | KB-RVT-04 | 0.13 |
| T2-025 | box | 에너지박스 설치파일 받는 곳이 어디예요 | answer | handoff | KB-EBOX-01 | 0.15 |
| T2-026 | box | EnergyBOX는 오토캐드에서 바로 실행하는 거 아닌가요? | answer | handoff | KB-ACAD-04 | 0.05 |
| T2-027 | box | 에너지박스 실행할 때 AutoCAD 버전 선택하는 창이 나와요 | answer | handoff | KB-EBOX-02 | 0.45 |
| T2-028 | box | RevitBOX 2023용 설치파일 받고 싶어요 | answer | handoff | KB-RBOX-01 | 0.07 |
| T2-029 | box | 레빗박스 설치 끝났는데 고객번호 어디에 넣어요? | answer | handoff | KB-RBOX-02 | 0.21 |
| T2-032 | multi_turn | 나비스웍스도 똑같아요? | answer | handoff | KB-NAVIS-01 | 0.00 |
| T2-034 | multi_turn | 웹인스톨러가 실행이 안 되면요? | answer | answer | KB-RVT-03 | 1.00 |
| T2-036 | no_product | DLL 파일 항상 로드 누르라고 나오는데 눌러도 되나요 | answer | handoff | KB-CBOX-02 | 0.42 |
| T2-058 | clarify | 설치 관련해서 여쭤볼게 있어요 | clarify | handoff | KB-ACAD-01 | 0.01 |
