# 트러블슈팅 · 의사결정 기록 — Windows Git Bash 설치 · pytest 실패

- 발생: 2026-10, 개발자 PC(Windows · 한국어 로캘 · Git Bash · Python venv)
- 증상: ① `pip install -r requirements.txt` 실패 ② `pytest -q` 3건 실패 ③ (2차, 10-17) `test_test2_seal_is_intact` 1건 실패 → [7절](#7-2차-장애--ts-05-test2-봉인-테스트-실패) ④ (3차, 10-17) `eval_rag.py --backend` 인자 누락 → [8절](#8-3차-장애--ts-06-test2-생성-평가-명령-인자-오류)
- 결론: **코드 버그가 아니라 "환경 차이 1건 + 데이터 동기화 누락 1건"**. 테스트는 제 역할을 했다.
- 상태: 저장소 쪽 재발 방지 조치 완료(108개 통과). 사용자 PC에서 아래 [4. 해결 절차](#4-해결-절차-사용자-pc) 실행 필요.

---

## 1. 무엇이 일어났나 (로그 원문 요약)

| # | 명령 | 결과 | 핵심 로그 |
|---|---|---|---|
| 1 | `pip install -r requirements.txt` (pip 23.2.1) | 실패 | `UnicodeDecodeError: 'cp949' codec can't decode byte 0xec in position 9` |
| 2 | `python -m pip install --upgrade pip` → 재설치 (pip 26.2.1) | 성공 | openai 3.33.0 · streamlit 1.66.0 · numpy 2.5.4 · kiwipiepy 0.24.0 · pytest 9.1.1 |
| 3 | `pytest -q` | **3 failed** | 아래 표 |

| 실패 테스트 | 직접 원인 |
|---|---|
| `test_kb_retriever.py::test_kb_json_is_in_sync_with_md` | `json 31adc148… != md d6b035e1…` |
| `test_kb_retriever.py::test_vector_index_roundtrip_and_stale_detection` | `build_index.assert_kb_is_current()` → `SystemExit` |
| `test_eval_tools.py::test_vector_report_runs_end_to_end` | 같은 `SystemExit` |

> **읽는 법**: 3건은 원인이 1개다. 뒤의 2건은 "md 가 json 보다 최신이면 인덱스를 만들지 않는다"는 안전장치가 정상 작동한 결과다.

---

## 2. 원인 분석 (근거 포함)

### TS-01 · pip 설치 실패 — requirements.txt 의 한글 주석

| 근거 | 내용 |
|---|---|
| 위치 | `position 9` = 1행 `# 직접 의존성만…` 의 '의'(UTF-8 `EC 9D 98`) 첫 바이트 `0xec` |
| 메커니즘 | pip 23.x 는 BOM·인코딩 선언이 없는 requirements 를 **OS 로캘 인코딩(cp949)** 으로 읽는다. UTF-8 한글은 cp949 로 해석되지 않는다 |
| 왜 pip 업그레이드로 풀렸나 | 최신 pip 는 UTF-8 로 먼저 읽는다 |
| 왜 CI 는 통과했나 | GitHub Actions(Ubuntu) 로캘은 UTF-8 이다 → **CI 만으로는 못 잡는 오류** |

### TS-02 · pytest 3건 — md 가 바뀌고 json 을 다시 만들지 않음

| 근거 | 내용 |
|---|---|
| json 해시 | `31adc148…` = 배포본 `install_kb.md` 의 해시 (저장소와 동일) |
| md 해시 | `d6b035e1…` = 사용자 PC 의 md 가 **배포본과 다른 내용** |
| 기계적 차이 배제 | 배포본에 BOM 추가 · CRLF 변환 · 끝 줄바꿈 추가/삭제 · 둘의 조합을 모두 계산했으나 `d6b035e1…` 이 나오지 않았다 → **글자 수준의 실제 편집**이 있다 |
| 결론 | 누군가(본인 · 에디터 자동 서식 · 병합) md 를 고쳤고 `build_kb.py` 를 실행하지 않았다 |

> 테스트를 고쳐서 "통과시키면" 안 되는 이유: 이 상태로 `build_index.py` 를 돌리면 **낡은 청크로 벡터를 만들고**, 평가 보고서 수치도 실제 지식 베이스와 어긋난다. 런북 1)~6) 의 측정 결과 전체가 무효가 된다.

### 잠재 위험 (이번엔 안 터졌지만 재현으로 확인)

| ID | 재현 | 결과 |
|---|---|---|
| TS-03 | `PYTHONIOENCODING=cp949 python scripts/build_kb.py --check` | `UnicodeEncodeError: '✅'` — 해결 명령 자체가 이모지 출력에서 죽는다 |
| TS-02′ | 내용 그대로 메모장에서 "UTF-8(BOM)" 저장 | 이전 코드: 해시가 달라져 **거짓 실패** |
| TS-04 | `.env` 를 메모장 ANSI(cp949)로 저장 | `'utf-8' codec can't decode byte 0xb8` — 앱이 import 에서 멈춤 |

---

## 3. 의사결정 (리더 판단)

### 결정 원칙

1. **테스트를 약하게 만들지 않는다.** 동기화 검사는 "내용이 바뀌면 반드시 실패"를 유지한다.
2. **거짓 실패만 없앤다.** 내용이 같은데 실패하는 경우(BOM · CRLF)는 도구가 틀린 것이다.
3. **원인 1개 = 실패 1개.** 연쇄 실패는 진단 시간을 늘린다.
4. **해결 안내는 오류 메시지 안에 있어야 한다.** 문서를 찾지 않아도 다음 행동을 알 수 있게 한다.
5. **CI 가 못 잡는 환경 차이는 테스트로 고정한다.**

### 결정 표

| ID | 선택지 | 결정 | 이유 |
|---|---|---|---|
| D-1 | (a) README 에 "pip 업그레이드 하라"만 적기 (b) requirements 를 ASCII 로 | **(b) + (a)** | 문서는 안 읽힐 수 있다. 파일이 ASCII 면 어떤 pip · 로캘에서도 실패할 수 없다. 테스트 `test_requirements_files_are_ascii_only` 로 고정 |
| D-2 | (a) json 을 다시 만들어 테스트 통과 (b) md 변경을 먼저 확인 | **(b)** | md 변경이 의도치 않은 것이면 (a)는 **잘못된 지식을 정답으로 굳힌다**. `git diff` 확인이 1분이면 끝난다 |
| D-3 | (a) 해시 비교 제거/완화 (b) BOM · CRLF 만 정규화 | **(b)** | 내용 변경은 계속 잡는다 (`test_md_hash_still_detects_real_edits`) |
| D-4 | (a) 연쇄 실패 유지 (b) 전제 깨지면 SKIP | **(b)** | `kb_in_sync` fixture: 1 failed + 2 skipped(사유 표시). 전제가 복구되면 자동으로 다시 실행된다 |
| D-5 | (a) 이모지 제거 (b) 출력 오류만 `replace` | **(b)** | 한글 출력은 그대로, 표현 못 하는 문자만 `?`. 출력 문구 수정 없이 3개 스크립트에 한 줄 |
| D-6 | (a) pre-commit 훅 강제 (b) 선택 설치 + CI | **(b)** | 훅은 clone 마다 1회 `git config core.hooksPath .githooks` 필요. 강제 수단은 CI(`build_kb.py --check`)가 이미 맡는다. 훅은 "푸시 전에 알게 하는" 편의다 |
| D-7 | `.gitattributes` | **추가** | 저장소 안 줄바꿈을 LF 로 고정. Windows 체크아웃 CRLF 는 해시가 이미 무시한다 |

### 하지 않은 것과 이유

- **테스트 삭제 · `xfail` 처리**: 실제 데이터 불일치를 숨긴다.
- **해시 대신 수정 시각(mtime) 비교**: Git 체크아웃은 mtime 을 보존하지 않아 항상 "최신"이 된다.
- **`PYTHONUTF8=1` 을 코드에서 강제**: 인터프리터 시작 후에는 바꿀 수 없다. 사용자 셸 설정(런북 0단계)으로 안내한다.

---

## 4. 해결 절차 (사용자 PC)

```bash
# 0) 최신 코드 받기 (이번 수정 포함)
git pull                               # 또는 배포 zip 덮어쓰기
export PYTHONUTF8=1                    # 선택: ~/.bashrc 에 넣으면 매번 안 해도 된다

# 1) md 가 무엇이 바뀌었는지 본다 — 여기서 판단이 갈린다
git status data/
git diff data/install_kb.md

# 2-a) 내가 의도해서 고친 것이면 → json 재생성
python scripts/build_kb.py             # URL · 전화번호가 들어 있으면 여기서 실패한다(정상)
python scripts/build_kb.py --check     # ✅ 동기화 OK (26개 청크)
#      벡터 인덱스(data/kb_index)를 이미 만들었다면 다시 만든다(키 필요)
#      python scripts/build_index.py

# 2-b) 의도하지 않은 변경이면 → 되돌리기
git checkout -- data/install_kb.md
python scripts/build_kb.py --check

# 3) 확인
pytest -q                              # 91 passed

# 4) 재발 방지 (clone 마다 1회)
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit         # macOS·Linux 만 필요 (Windows Git 은 실행 권한을 보지 않는다)
```

| 1)의 결과 | 의미 | 갈 곳 |
|---|---|---|
| `git diff` 에 내가 쓴 문장이 보인다 | 의도한 수정 | 2-a · 청크 수가 26이 아니면 `test_kb_json_is_in_sync_with_md` 의 `len == 26` 도 함께 고친다 |
| 공백 · 따옴표 · 줄 정렬만 바뀌었다 | 에디터 자동 서식 | 2-b · 에디터의 "저장 시 서식" 끄기 |
| `git diff` 가 비어 있다 | Git 밖에서 받은 파일(zip 등) | 저장소 원본과 비교: `sha256sum data/install_kb.md` |
| 충돌 표시(`<<<<<<<`)가 있다 | 병합 잔재 | 충돌 해결 후 2-a |

---

## 5. 재발 방지 장치 (저장소 반영 내용)

| 파일 | 변경 | 막는 문제 |
|---|---|---|
| `requirements.txt` | 주석 영문화 (ASCII 전용) | TS-01 |
| `assistant/kb.py` `md_source_hash` | UTF-8 BOM 제거 후 해시 | TS-02′ 거짓 실패 |
| `scripts/build_kb.py` | md 를 `utf-8-sig` 로 읽기 · `--check` 실패 시 해시와 해결 명령 출력 | TS-02 안내 |
| `assistant/console.py` (신규) | `safe_console()` — stdout/stderr `errors="replace"` | TS-03 |
| `scripts/build_kb.py` · `build_index.py` · `eval_rag.py` | `main()` 첫 줄 `safe_console()` | TS-03 |
| `assistant/config.py` `_load_dotenv` | UTF-8(BOM) → 실패 시 cp949 로 읽기 · 경로 인자(테스트용) | TS-04 |
| `tests/conftest.py` | `kb_in_sync` fixture · `SYNC_FIX` 안내문 | 연쇄 실패 |
| `tests/test_kb_retriever.py` | 동기화 실패 메시지에 해시 + 해결 절차 | TS-02 안내 |
| `tests/test_windows_env.py` (신규, 6개) | 위 4가지를 회귀 테스트로 고정 | CI 가 Linux 여도 Windows 문제 재발을 잡는다 |
| `.gitattributes` (신규) | `* text=auto eol=lf` | 줄바꿈 혼재 |
| `.githooks/pre-commit` (신규) | md 가 스테이징되면 `build_kb.py --check` | 동기화 누락 커밋 |

검증 결과 (Linux, Python 3):

| 상황 | 이전 | 이후 |
|---|---|---|
| 정상 | 85 passed | **91 passed** |
| md 만 수정 (사용자 PC 상황 재현) | 3 failed | **1 failed + 2 skipped** — 실패 메시지에 해결 절차 |
| md 를 메모장 BOM + CRLF 로만 재저장 | 3 failed (거짓) | **통과** |
| cp949 콘솔에서 `build_kb.py --check` | `UnicodeEncodeError` | 정상 판정(종료 코드 0/1) |

---

## 6. 이력서 · 면접용 한 줄 요약

> Windows 개발 환경에서 발생한 설치 실패와 테스트 3건 실패를 로그 바이트 위치(`0xec @ 9`)와 해시 비교로 **환경 문제 1건 + 데이터 동기화 누락 1건**으로 분리 진단했다. 검사를 완화하지 않고 거짓 실패(BOM · CRLF · 콘솔 인코딩)만 제거했으며, 연쇄 실패를 1건으로 줄이고 해결 절차를 오류 메시지에 넣었다. CI(Linux)가 잡지 못하는 Windows 환경 차이는 회귀 테스트 6개로 고정했다.

---

## 7. 2차 장애 — TS-05 · test2 봉인 테스트 실패

### 7.1 로그

```
$ python scripts/build_kb.py --check
✅ 동기화 OK (26개 청크)                        ← TS-02 는 해결됨
$ pytest -q
....................F.......  (85개 중 1 failed)
FAILED tests/test_eval_tools.py::test_test2_seal_is_intact
  assert 'b0d092fb69aed244…' in '# test v2 봉인 기록 … | sha256 | `95b4145b957c…'
```

| 관찰 | 의미 |
|---|---|
| 테스트가 **85개** · 실패 위치 21번째 | 1차 수정(91개)이 PC에 **아직 적용되지 않았다** — 첨부한 `test_eval_tools.py` 도 수정 전 버전이다 |
| `build_kb.py --check` 통과 | 지식 베이스는 정상. 이번 실패와 무관 |
| 현재 파일 해시 `b0d092fb…` ≠ 봉인 `95b4145b…` | `eval/golden_rag_v2.json` 이 봉인 후 바뀌었다 |

### 7.2 원인 분석

| 가설 | 검증 | 결과 |
|---|---|---|
| Windows CRLF 체크아웃 | `_hash` 가 CRLF→LF 변환 후 계산 → CRLF 만으로는 `95b4145b…` 그대로 | **배제** |
| 메모장 BOM | BOM 추가본(CRLF 여부 무관) `aa3c266a…` | **배제** |
| 에디터 JSON 재서식 (들여쓰기 1·2·4·탭 · ASCII 이스케이프 · 구분자 · 끝 줄바꿈 유무 · BOM 조합 120여 가지) | 어느 조합도 `b0d092fb…` 아님 | **배제** |
| **필드 값 변경** | 남은 유일한 설명 | 무엇이 바뀌었는지는 `seal_check.py` 로 확인 |

가장 가능성이 큰 경우는 **사람 검수**다. `SEAL_test2.md` 는 "사람 검수(최소 무작위 20문항)를 거치면 `reviewed_by` 를 채운다"고 했고, 2라운드 남은 과제가 바로 이 검수였다.

> **설계 결함 발견**: 봉인 규칙은 검수 기록을 남기라고 하면서, 봉인 검사는 **파일 전체 바이트**를 봤다.
> 규칙대로 행동하면 봉인이 깨지는 구조였다. 문제는 사용자가 아니라 봉인 설계에 있다.

### 7.3 의사결정

| ID | 선택지 | 결정 | 이유 |
|---|---|---|---|
| D-8 | (a) `SEAL_test2.md` 해시를 새 값으로 바꿔 통과 (b) 무엇이 바뀌었는지 먼저 진단 | **(b)** | (a)는 봉인을 무의미하게 만든다. 정답을 고쳤다면 "처음 보는 문항" 주장이 무너지는데, 해시 교체는 그 사실을 지운다 |
| D-9 | 봉인 기준: (a) 파일 해시 유지 (b) **평가 내용 해시** | **(b)** | 채점에 안 쓰이는 `notes`·`author`·`created_at`·`reviewed_by`·`reviewed_at`·`review_note` 만 제외. 질문·정답·분할·유형은 한 글자만 바뀌어도 깨진다. 모르는 새 필드도 깨진다(보수적) |
| D-10 | 기준 변경의 정당성 | **원본에서 계산** | 내용 해시 `0a0e8108…` 는 봉인 파일(`95b4145b…`) **그 자체**에서 계산했다 → 봉인 대상이 바뀌지 않았다. 변경 사실은 `SEAL_test2.md` 봉인 후 이벤트에 기록 |
| D-11 | 봉인 깨진 상태의 `eval_rag.py --split test2` | **실행 거부 (exit 2)** | 깨진 세트 점수가 보고서에 "최종 성능"으로 섞이는 것을 막는다. 꼭 필요하면 `--allow-broken-seal` (보고서·실행 기록에 `seal: broken` 기록) |
| D-12 | 진단 도구 | **`scripts/seal_check.py` 신설** | 해시는 "다르다"만 말한다. 리더가 판단하려면 **어느 문항의 어느 필드가** 바뀌었는지 봐야 한다 |

### 7.4 해결 절차 (사용자 PC)

```bash
# 0) 1차 + 2차 수정 적용 (누적 패치 — 1차 패치를 적용하지 않았어도 이것 하나면 된다)
git apply --check fix_windows_pytest_v2.patch && git apply fix_windows_pytest_v2.patch
pytest --collect-only -q | tail -1          # 99 tests collected 이어야 적용 완료

# 1) 무엇이 바뀌었는지 진단
python scripts/seal_check.py                # HEAD(커밋본) 대비 문항·필드별 변경
#   커밋까지 해 버렸다면: python scripts/seal_check.py --against <봉인 시점 커밋>
#                        git log --oneline -- eval/golden_rag_v2.json 으로 커밋을 찾는다

# 2) 판정표대로 처리 → 3) pytest -q   (99 passed)
```

| `seal_check.py` 결과 | 판정 | 조치 |
|---|---|---|
| ✅ 메타데이터만 변경 (`reviewed_by` 등) | **봉인 유지** | 그대로 커밋. 검수 결과를 `eval/REVIEW_test2.md` 에 기록 |
| ❌ 평가 필드 변경 · 의도하지 않음 (에디터 · 실수) | 봉인 복구 가능 | `git checkout -- eval/golden_rag_v2.json` (검수 기록이 섞였다면 `reviewed_by` 만 다시 채운다) |
| ❌ 평가 필드 변경 · **검수에서 정답 오류 발견** | **봉인 깨짐 (정당한 사유)** | 아래 7.5 절차. 봉인 해시는 **절대 덮어쓰지 않는다** |
| ❌ 문항 추가 · 삭제 | 새 세트 | 7.5 와 같음 |

### 7.5 검수로 정답이 틀린 문항을 찾았을 때

정답 라벨 오류를 고치는 것은 옳다. 다만 **고친 뒤의 점수는 더 이상 "봉인 세트 점수"가 아니다.** 순서:

1. 원본 `golden_rag_v2.json` 은 그대로 둔다 (기존 보고서의 근거 파일).
2. 수정본은 **`golden_rag_v2_1.json`** 으로 새로 만들고, 변경 문항마다 `REVIEW_test2.md` 에 *문항 · 이전 정답 · 새 정답 · 근거(KB 청크)* 를 남긴다.
3. **개선 전 스냅샷과 개선 후 코드를 v2.1 로 각각 1회** 다시 측정한다. 라벨만 바뀌었으므로 두 시스템을 같은 잣대로 다시 재는 것은 공정하다.
4. 보고서에는 **v2 결과와 v2.1 결과를 나란히** 적는다. v2 결과(과잉 전환 40.5%→13.5% 등)를 지우지 않는다.
5. v2.1 은 이미 내용을 본 세트이므로 "처음 보는 문항" 주장은 v2 결과로만 한다. 다음 개선부터의 미공개 평가는 **test v3** 로 한다.

### 7.6 재발 방지 (저장소 반영)

| 파일 | 변경 |
|---|---|
| `scripts/seal_check.py` (신규) | 내용 해시 · 봉인 판정(intact / metadata_only / broken) · Git 대비 문항·필드 diff |
| `eval/SEAL_test2.md` | `content_sha256` 행 추가 · 봉인 규칙 5 보강 · 2026-10-17 이벤트 기록 |
| `scripts/eval_rag.py` | test2 측정 전 봉인 검사(깨지면 exit 2) · `--allow-broken-seal` · 보고서·실행 기록에 `seal` 상태 |
| `tests/test_eval_tools.py` | 봉인 테스트를 내용 해시로 · 실패 메시지에 해결 절차 · 회귀 테스트 8개 (검수 메타 허용 · 서식/순서 무시 · 질문/정답/분할/새 필드 변경 감지 · diff 분류 · 측정 거부) |
| `eval/REVIEW_test2.md` (신규) | 사람 검수 기록 양식 |

| 상황 | 이전 | 이후 |
|---|---|---|
| 검수자가 `reviewed_by` 20건 채움 + 에디터 재서식 | FAILED (거짓 경보) | **passed** · `seal_check`: 메타데이터만 변경 |
| 정답 1건 · 질문 1건 수정 | FAILED · 해시만 출력 | FAILED · 해결 절차 출력 · `seal_check` 가 T2-002 `expected`, T2-006 `question` 을 짚음 · `--split test2` 측정 거부 |

### 7.7 이력서 · 면접용 한 줄

> 봉인 평가셋 무결성 테스트가 실패했을 때 해시를 갱신해 통과시키지 않고, 원인을 진단하다 **"검수 기록을 남기라는 규칙"과 "파일 전체 해시 봉인"이 충돌하는 설계 결함**을 찾았다. 봉인 기준을 채점 필드만 대상으로 하는 내용 해시로 바꾸되 원본 파일에서 계산해 봉인 연속성을 유지했고, 정답이 바뀐 경우에는 측정을 차단하고 버전 분리(v2 → v2.1) 절차를 문서화했다.

---

## 8. 3차 장애 — TS-06 · test2 생성 평가 명령 인자 오류

### 8.1 로그

```
$ python scripts/eval_rag.py --split test2 --generate --backend
eval_rag.py: error: argument --backend: expected one argument
```

| 관찰 | 의미 |
|---|---|
| `usage:` 에 `--allow-broken-seal` 이 있다 | 2차 수정(v2.2)이 적용된 상태다. 첨부한 `eval_rag.py` 도 v2.2 와 같다 |
| argparse 단계에서 종료 | **API 호출 0회 · 비용 0원 · `test2_runs.log` 기록 없음 · 봉인 영향 없음** — test2 는 아직 소모되지 않았다 |
| 실행한 명령 = 런북 6) 두 번째 줄에서 `<5)의 결론>` 을 지운 것 | 원인은 사용자가 아니라 **런북의 자리표시자 설계** |

### 8.2 원인 분석

| 층 | 원인 | 근거 |
|---|---|---|
| 직접 원인 | `--backend` 뒤 값 누락 | argparse 는 `choices` 가 있는 옵션에 값이 없으면 종료한다 |
| 문서 원인 | 런북이 `<5)의 결론>` 자리표시자를 썼다 | Bash 에서 `<` 는 입력 리디렉션이라 그대로 붙여 넣으면 `syntax error near unexpected token ')'` 로 실패한다. 지우면 지금처럼 값이 빠진다. **어느 쪽이든 실패하는 예시** |
| 설계 원인 | tune 의 결론(어느 백엔드)이 **보고서 문장 속에만** 있었다 | 사람이 보고서를 읽고 → `.env` 에 옮기고 → 명령에 다시 옮겨 적는 3단계 수작업. 옮겨 적기가 빠지거나 틀리면 봉인 test2 를 엉뚱한 백엔드로 1회 소모한다 |
| 잠재 위험 | 기존 기본값이 `both` 였다 | `--backend` 를 아예 빼고 실행했다면 test2 에서 두 백엔드를 비교하고 `decide_backend` 로 **봉인 세트에서 백엔드를 골랐을 것**이다. 오류가 난 덕분에 피했다 |

### 8.3 의사결정

| ID | 선택지 | 결정 | 이유 |
|---|---|---|---|
| D-13 | (a) 런북 오타만 고침 (b) 판정을 파일로 남기고 test2 가 그 파일을 따름 | **(b)** | (a)는 다음에도 사람이 옮겨 적는다. tune 판정을 `backend_decision.json` 으로 남기면 **사전 등록 규칙의 결론이 그대로 test2 에 전달**된다 |
| D-14 | test2 기본 백엔드: (a) both 유지 (b) **auto** | **(b)** | test2 는 확정된 1개만 잰다. both 는 봉인 세트로 고르는 것이므로 거부 |
| D-15 | `--backend` 를 값 없이 쓸 때 | **auto 로 해석** (`nargs="?"`, `const="auto"`) | 사용자가 실제로 친 명령이 안전한 동작을 하게 한다. 무엇으로 해석했는지 시작 줄에 출력한다 |
| D-16 | 판정과 다른 백엔드 직접 지정 | **거부** | 결과를 알고 고르는 것과 같다. 판정을 바꾸려면 tune 을 다시 돌린다 |
| D-17 | 판정 때와 다른 모델 | **거부** | 모델이 바뀌면 판정 근거(인용률 · 지연)가 무효다 |
| D-18 | 같은 백엔드로 test2 생성 평가 재실행 | **거부** (`--allow-test2-rerun` 로 해제, 기록) | 최종 측정 1회 원칙 + API 비용. 해제해도 실행 기록에 남는다 |
| D-19 | 검사 순서 | **API 키 확인보다 먼저** | 잘못된 조합은 키 · 네트워크 · 비용 없이 멈춘다 |

### 8.4 해결 절차 (사용자 PC)

```bash
# 0) 패치 적용 확인
pytest -q                                          # 108 passed

# 1) tune 판정이 있는지 확인 — 없으면 런북 4)부터
ls eval/reports/backend_decision.json

# 2) (없으면) 런북 4): tune 에서 두 백엔드 비교 → 판정 파일 생성
python scripts/eval_rag.py --generate --backend both
cat eval/reports/backend_decision.json             # "backend": "chat" 또는 "responses"

# 3) 런북 5): 판정을 운영 기본값에 반영 (.env)
#    ASSISTANT_LLM_BACKEND=<판정 파일의 backend 값>

# 4) 런북 6): test2 최종 측정 — 백엔드를 적지 않는다
python scripts/eval_rag.py --split test2 --generate
#    시작 줄 확인: "생성 평가  test2 · 백엔드 responses · 근거 tune 판정 (2026-…) · 모델 …"
```

| 4)의 출력 | 의미 | 조치 |
|---|---|---|
| `tune 판정 파일 … 이 없다` | 런북 4)를 건너뛰었다 | 2) 실행 |
| `test2 에서는 백엔드를 비교하지 않는다` | `--backend both` 를 붙였다 | 옵션을 뺀다 |
| `tune 판정은 X 인데 Y 를 지정했다` | 판정과 다른 백엔드 | 옵션을 뺀다(auto) |
| `tune 판정 모델 … 과 지금 모델 … 이 다르다` | `ASSISTANT_MODEL` 변경 | 모델을 맞추거나 바꾼 모델로 4) 재실행 |
| `이미 1회 실행했다` | 최종 측정 완료 | 기존 `rag_generate_test2.md` 를 쓴다 |
| `⚠️ 운영 기본값 … 이 판정과 다르다` | 런북 5) 미반영 (측정은 진행됨) | `.env` 에 반영 |

### 8.5 재발 방지 (저장소 반영)

| 파일 | 변경 |
|---|---|
| `scripts/eval_rag.py` | `BACKEND_DECISION` · `save_backend_decision()` (tune 판정 저장) · `resolve_backends()` (test2 규칙 5가지) · `--backend` 값 생략 시 auto · `--allow-test2-rerun` · `build_parser()` 분리 · test2 생성 기록에 모델 · 판정 근거 · 봉인 상태 |
| `docs/runbook_vector_generate.md` | 4) 판정 파일 안내 · 6) `--backend` 제거 · 자리표시자 금지 원칙 · 결과별 대응에 이 오류 추가 |
| `tests/test_eval_tools.py` | 회귀 테스트 9개 — 값 없는 `--backend` = auto · 판정 따름 · 판정 없음 · both 거부 · 판정과 다른 백엔드 거부 · 모델 불일치 거부 · 재실행 거부 · tune 기본 both · 키 없이도 거부 |

| 상황 (API 키 없음) | 이전 | 이후 |
|---|---|---|
| `--split test2 --generate --backend` · 판정 없음 | argparse 오류 | `❌ tune 판정 파일이 없다 → 런북 4) 먼저` |
| 같은 명령 · 판정 `responses` | argparse 오류 | auto → responses 로 진행 (키 확인 단계로) |
| `--split test2 --generate` (옵션 생략) | **test2 에서 both 비교 · 백엔드 선택 (봉인 위반)** | auto |
| `--split test2 --generate --backend chat` · 판정 `responses` | chat 으로 측정 | 거부 |

### 8.6 이력서 · 면접용 한 줄

> 평가 명령 인자 오류를 단순 오타로 넘기지 않고, "사전 등록 판정을 사람이 보고서에서 옮겨 적는 구조"가 봉인 평가셋을 잘못된 설정으로 소모할 수 있는 위험임을 짚었다. 판정 결과를 기계가 읽는 파일로 남겨 최종 측정이 자동으로 따르게 하고, 봉인 세트에서의 비교 · 판정 불일치 · 모델 불일치 · 재실행을 API 호출 전에 차단했다.

---

## 부록 A. 장애 기록 양식 (다음 사건용 템플릿)

```markdown
## TS-xx · <한 줄 증상>

- 발생일 / 환경: YYYY-MM-DD · OS · 셸 · Python · 주요 패키지 버전
- 영향: (설치 불가 / 테스트 실패 n건 / 운영 영향 없음 …)
- 재현 명령: `...`
- 핵심 로그 (원문 3줄 이내):

### 원인
| 근거 | 내용 |
|---|---|
| 관찰 | |
| 배제한 가설 | (무엇을 계산·실행해서 배제했는가) |
| 결론 | |

### 결정
| 선택지 | 결정 | 이유 |
|---|---|---|

### 조치 · 검증
| 파일 | 변경 | 검증(테스트 이름 / 명령) |
|---|---|---|

### 재발 방지
- [ ] 회귀 테스트 추가
- [ ] 오류 메시지에 해결 절차 포함
- [ ] 문서(README / 런북) 갱신
```

## 부록 B. 진단에 쓴 명령

```bash
# 오류 위치의 바이트 확인 — position 9 가 무슨 글자인가
python -c "print(open('requirements.txt','rb').read()[:12])"

# 지금 PC 의 md 해시 vs json 에 기록된 해시
python -c "from assistant.kb import md_source_hash; print(md_source_hash())"
python -c "import json; print(json.load(open('data/install_kb.json', encoding='utf-8'))['source_sha256'])"

# 파일이 BOM · CRLF 를 갖고 있는가
file data/install_kb.md                       # "with BOM", "with CRLF line terminators"

# Python 이 쓰는 인코딩
python -c "import sys, locale; print(sys.stdout.encoding, locale.getpreferredencoding(), sys.flags.utf8_mode)"
```
