# 벡터 검색 평가 — tune

- 실행 시각: 2026-10-17T08:16:30+00:00
- 임베딩 모델: `text-embedding-3-small` · 문항 63

| 지표 | local | hybrid | hybrid + 벡터 게이트 |
|---|---|---|---|
| under_handoff | 0 | 0 | — |
| wrong_answer | 2 | 2 | — |
| hit_at_1 | 0.944 | 0.944 | — |
| recall_at_3 | 0.972 | 0.972 | — |
| over_handoff_rate | 0.028 | 0.028 | — |
| decision_accuracy | 0.984 | 0.984 | — |

## ① 하이브리드 순위 채택 → **채택**

- ✅ Hit@1 hybrid 0.944 ≥ local 0.944
- ✅ wrong-answer hybrid 2 ≤ local 2
- ✅ under-handoff hybrid 0 = 0

## ② 벡터 게이트

- 켜지 않는다: 넘길 문항 최고 코사인 0.466 보다 높은 구제 대상이 없다
