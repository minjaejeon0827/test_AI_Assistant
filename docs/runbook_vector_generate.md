# 실행 런북 — 벡터 게이트 · 생성 평가 (Runbook)

- 대상: 추가 개선 사항 ②벡터 인덱스 생성 및 벡터 게이트 임계값 스윕 · ③LLM 생성 평가(Chat Completions vs Responses)
- 필요: `OPENAI_API_KEY` · 인터넷 연결 · 약 10분

---

## 1. 이 문서를 왜 만드는가

두 작업은 OpenAI API를 호출해야 측정할 수 있다. AI 도구(Claude) 활용해서 OpenAI API에 접속할 수 없어 **도구와 판정 규칙을 먼저 완성하고, 실행만 남겨 두었다.**

이 문서의 핵심은 **판정 규칙을 측정 전에 고정한 것**이다. 결과를 본 뒤 기준을 정하면, 원하는 결론에 맞춰 기준을 고르게 된다.
아래 규칙은 `scripts/eval_rag.py` 의 `HYBRID_ADOPT_RULE` · `recommend_vector()` · `BACKEND_SWITCH_RULE` 에 코드로 들어 있어,
명령을 실행하면 결론이 자동으로 나온다. **결과를 보고 이 값들을 고치지 않는다.**

---

## 2. 사전 등록 판정 규칙

### 2.1 하이브리드 순위 채택 (어휘 + 벡터)

tune 분할(dev + 퇴역 test v1, 63문항)에서 local 대비 아래를 **모두** 만족하면 채택한다.

| 조건 | 이유 |
|---|---|
| Hit@1 이 떨어지지 않는다 | 순위가 나빠지면 추출형 답변이 틀린 문서를 보여준다 |
| wrong-answer 가 늘지 않는다 | 엉뚱한 절차 안내는 상담원 연결보다 나쁘다 |
| under-handoff = 0 | 넘길 질문에 답하면 안 된다 |

기각되면 `.env` 에 `ASSISTANT_W_VECTOR=0` 을 넣는다(벡터는 게이트에만 쓴다).

### 2.2 벡터 게이트 임계값

| 항목 | 규칙 |
|---|---|
| 적용 범위 | 제품이 특정된(앵커) 질문만. "프린터 드라이버 설치"도 '설치' 때문에 코사인이 높게 나온다 |
| 넘길 문항(OUT) | 앵커 질문 중 정답이 상담원 연결인 문항의 1위 코사인 |
| 구제 대상(IN) | 1위가 정답인데 어휘 게이트를 못 넘은 문항의 1위 코사인 |
| 임계값 | OUT 최고점과, 그보다 높은 IN 최저점 사이의 한가운데 |
| 켜지 않는 경우 | OUT 최고점보다 높은 IN 이 없을 때 · 적용 시뮬레이션에서 under-handoff 가 1건이라도 생길 때 |

### 2.3 Responses API 기본값 전환

tune 분할 생성 평가에서 chat 대비 아래를 **모두** 만족하면 기본값을 `responses` 로 바꾼다. 동률이면 `responses` 를 고른다 — OpenAI가 표준 인터페이스로 안내하는 쪽이 장기 유지 비용이 낮다.

| 조건 | 기준 |
|---|---|
| 넘길 질문에 답함 | chat 이하 |
| 엉뚱한 문서 인용 | chat 이하 |
| 정답 문서 인용률 | chat − 0.02 이상 |
| 지연 p95 | chat × 1.2 이하 |

---

## 3. 비용 규모 (실측 토큰 추정)

| 단계 | 호출 | 토큰 (추정) |
|---|---|---|
| 벡터 인덱스 생성 | 임베딩 26청크 1회 | 약 4,800 |
| 벡터 평가 (tune) | 질문 임베딩 63회 | 약 1,500 |
| 생성 평가 (tune, 백엔드 2개) | LLM 35회 × 2 | 약 62,000 |
| 생성 평가 (test2, 확정 백엔드 1개) | LLM 33회 | 약 29,000 |

단가는 모델별로 다르고 수시로 바뀌므로 [OpenAI 요금 페이지](https://openai.com/api/pricing/)에서 확인한다.
한도를 걸어 두려면 OpenAI 대시보드의 월 사용 한도를 먼저 설정한다.

---

## 4. 실행 순서

```bash
# 0) 준비 — 키는 .env 에만 둔다 (.gitignore 등재됨)
cp .env.example .env            # OPENAI_API_KEY=sk-... 입력
export PYTHONUTF8=1             # Windows Git Bash: 입출력을 UTF-8 로 통일 (troubleshooting TS-03)
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
python scripts/build_kb.py --check   # md·json 동기화 확인 — 실패하면 1)로 가지 않는다 (TS-02)
python scripts/seal_check.py    # test2 봉인 유지 확인 — 깨졌으면 6)을 실행하지 않는다 (TS-05)
pytest -q                       # 108개 통과 확인 (키 불필요)

# 1) 벡터 인덱스 생성 — data/kb_index/ 에 vectors.npy · meta.json 생성
python scripts/build_index.py

# 2) 벡터 평가 — local vs hybrid 비교 + 사전 등록 규칙 적용
python scripts/eval_rag.py --vector
#    → eval/reports/rag_vector_tune.md 에 ① 하이브리드 채택 여부 ② 벡터 게이트 값이 나온다

# 3) 2)의 결론을 .env 에 반영 (보고서에 적힌 값 그대로)
#    ASSISTANT_HANDOFF_MIN_VECTOR=0.xx      ← 켜는 경우
#    ASSISTANT_W_VECTOR=0                   ← 하이브리드 기각인 경우

# 4) 생성 평가 — Chat Completions vs Responses (tune)
python scripts/eval_rag.py --generate --backend both
#    → eval/reports/rag_generate_tune.md        사람이 읽는 결론
#    → eval/reports/backend_decision.json       기계가 읽는 판정 (6)이 이 파일을 따른다 · 커밋한다)

# 5) 4)의 결론을 .env 에 반영
#    ASSISTANT_LLM_BACKEND=responses        ← 전환인 경우

# 6) 최종 측정 — 봉인된 test2 로 1회씩만
python scripts/eval_rag.py --split test2 --label final_hybrid
python scripts/eval_rag.py --split test2 --generate
#    백엔드는 적지 않는다 — 4)의 판정 파일을 자동으로 따른다(auto). 시작 줄에 "백엔드 … · 근거 tune 판정"이 찍힌다
#    다음 경우는 API 호출 전에 거부된다(exit 2): 판정 파일 없음 · --backend both · 판정과 다른 백엔드
#                                                · 판정 때와 다른 모델 · 같은 백엔드로 이미 측정함

# 7) 산출물 커밋
git add data/kb_index eval/reports .env.example && git commit -m "eval: 벡터 게이트·생성 평가 결과"
```

> 명령 예시에 `<…>` 자리표시자를 쓰지 않는다. Bash 에서 `<` 는 입력 리디렉션이라 그대로 붙여 넣으면 오류가 나고, 지우면 인자가 빠진다 (TS-06).
>
> 6)은 **한 번만** 실행한다. `eval/reports/test2_runs.log` 에 실행이 자동 기록되며, 횟수가 늘면 그 사실을 보고서에 적어야 한다.

---

## 5. 결과 기록 양식

`docs/evaluation.md` 의 **11절 미측정 항목** 표를 아래 양식으로 채운다.

| 항목 | 값 | 판정 | 근거 보고서 |
|---|---|---|---|
| 하이브리드 순위 채택 | Hit@1 local __ → hybrid __ · wrong-answer __ → __ | 채택 / 기각 | `rag_vector_tune.md` |
| 벡터 게이트 | OUT 최고 코사인 __ · 구제 __건 · 임계값 __ | 켬 / 끔 | `rag_vector_tune.md` |
| 기본 백엔드 | 정답 인용률 chat __ / responses __ · p95 __ / __ ms | chat / responses | `rag_generate_tune.md` |
| test2 (확정 설정) | under __ · wrong __ · Hit@1 __ · 과잉 전환 __ | | `rag_eval_test2_*_final_hybrid.md` |
| test2 생성 평가 | 정답 인용률 __ · 1위 오답 복구 __건 | | `rag_generate_test2.md` |

---

## 6. 결과별 대응

| 결과 | 대응 |
|---|---|
| 하이브리드 기각 | 기각 사실과 수치를 그대로 기록한다. 이 도메인은 5개 제품 절차가 문장이 거의 같아 임베딩이 제품을 구분하지 못할 수 있다 — 1라운드 설계에서 예상한 위험이다 |
| 벡터 게이트 끔 | 어휘 게이트만으로 운영한다. 미해결 의미 문제(G-045 · G-062 유형)는 생성 단계(상위 3개 근거)에 맡긴다 |
| responses 전환 | `config.py` 의 `LLM_BACKEND` 기본값을 바꾸고 README 마이그레이션 기록에 결과를 적는다 |
| 0)에서 `pytest` 실패 | 측정을 시작하지 않는다. `docs/troubleshooting.md` 의 진단 순서를 따른다 — 낡은 KB 로 만든 인덱스·보고서는 전부 다시 만들어야 한다 |
| `--backend: expected one argument` | 구버전 스크립트다. 최신 `eval_rag.py` 에서는 값 없이 써도 auto 로 해석된다. test2 는 `--backend` 를 아예 빼고 실행한다 (TS-06) |
| 생성 평가 오류 다수 | `errors` 열을 본다. 401 은 키, 404 는 모델명(`CHAT_MODELS`), 429 는 한도다 |

---

## 7. 되돌리기

벡터 관련 설정은 전부 환경 변수다. `.env` 에서 해당 줄을 지우면 이전 동작(local · chat)으로 돌아간다.
`data/kb_index/` 를 지우면 인덱스 없이 어휘 검색만 쓴다.
