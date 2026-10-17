# 트러블슈팅 · 의사결정 기록 — Windows Git Bash 설치 · pytest 실패

- 발생: 2026-10, 개발자 PC(Windows · 한국어 로캘 · Git Bash · Python venv)
- 증상: ① `pip install -r requirements.txt` 실패 ② `pytest -q` 3건 실패
- 결론: **코드 버그가 아니라 "환경 차이 1건 + 데이터 동기화 누락 1건"**. 테스트는 제 역할을 했다.
- 상태: 저장소 쪽 재발 방지 조치 완료(91개 통과). 사용자 PC에서 아래 [4. 해결 절차](#4-해결-절차-사용자-pc) 실행 필요.

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
