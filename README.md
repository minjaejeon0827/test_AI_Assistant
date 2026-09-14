<div align="center">

# 테스트용 기술지원 AI Assistant

**Autodesk 제품 설치 기술지원 챗봇에 LLM 모델 기술 적용하여 고객 상담 기능 도입 가능 여부를<br/>검증하기 위해 만든 Streamlit 기반 프로토타입**

㈜상상진화 · Autodesk 공식 파트너 회사 기술지원 솔루션<br/>
2024.12 ~ 2025.12 · 기획/개발 1인 담당

<br/>

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.52-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-gpt--3.5--turbo-412991?style=flat-square&logo=openai&logoColor=white)
![Status](https://img.shields.io/badge/Status-PoC%20(개발%20중단)-9E9E9E?style=flat-square)

</div>

---

## 해당 저장소 위치

㈜상상진화 기술지원 솔루션 프로젝트는 **두 개의 저장소**로 구성되었다.

| 단계 | 저장소 | 역할 | 상태 |
|---|---|---|---|
| 1단계 | [KakaoChatbot](https://github.com/minjaejeon0827/KakaoChatbot) | 규칙 기반 카카오 챗봇 (AWS Lambda 서버리스) | 개발 완료 · 서비스 오픈 중단 |
| 2단계 | **test_AI_Assistant** *(현재 저장소)* | 자유 형식 질문 대응 LLM 모델 상담 프로토타입 | PoC 완료 · 개발 중단 |

1단계 챗봇은 **버튼으로 선택하는 정형 문의**를 자동화 처리한다. 다만 "설치 중 오류 발생!(코드 1603)" 같은 **비정형 질문은 구조상 처리할 수 없다.** 이 저장소는 그 공백을 LLM 모델 사용하여 대처할 수 있는지 확인하기 위한 최소 실험이다.

> **프로젝트 배경 · 아키텍처 · 트러블슈팅 전체 내용은 [1단계 저장소 README](https://github.com/minjaejeon0827/KakaoChatbot) 참고.**

---

## 검증 목표와 결과

| 검증 항목 | 결과 |
|---|---|
| 비개발자도 쓸 수 있는 대화형 UI를 빠르게 만들 수 있는가 | Streamlit으로 약 50줄 수준에서 구현 가능 확인 |
| API 키를 저장소에 남기지 않고 안전하게 다룰 수 있는가 | 사이드바 입력 + 세션 메모리 보관 방식으로 해결 |
| 멀티턴 대화 맥락을 유지할 수 있는가 | `st.session_state` 기반 이력 누적으로 유지 확인 |
| **Autodesk 파트너 회사 기술지원 지식을 반영한 답변이 가능한가** | **불가 — 범용 모델 그대로는 Autodesk 제품 설치 절차를 정확히 답변하지 못함** |

마지막 항목이 이 PoC의 결론이다. **검색 단계(RAG) 없이 LLM 모델만 붙이는 방식으로는 기술지원 업무에 투입할 수 없다**는 것을 확인했고,
이 판단이 1단계 저장소의 RAG 파이프라인 설계로 이어졌다.

---

## 시연

<!-- TODO: assets/screenshot.png 추가 후 아래 주석 해제 -->
<!--
<div align="center">
  <img src="assets/screenshot.png" alt="사이드바에 API 키를 입력하고 채팅으로 질문과 답변을 주고받는 화면" width="820"/>
</div>
-->

---

## 동작 구조

<div align="center">
  <img src="assets/assistant-flow.png" alt="사용자 입력이 세션 상태에 누적되고 전체 대화 이력이 OpenAI API로 전송되어 응답을 받는 흐름" width="760"/>
</div>

---

## 구현 상세

코드는 짧지만 아래처럼 세 가지 판단이 들어가 있다.

### 1. API 키를 코드나 환경 변수가 아닌 화면 입력으로 받은 이유

```python
openai_api_key = st.text_input("OpenAI API Key", key="chatbot_api_key", type="password")
```

공개 저장소이므로 키가 코드나 `.env`에 남으면 유출 위험이 있다. 사이드바 입력 방식은 **키가 세션 메모리에만 존재하고 저장소·서버 어디에도 기록되지 않는다.** `type="password"`로 화면 노출도 차단했다. 여러 사람이 각자 자기 키로 테스트할 수 있다는 점도 사내 검증용으로는 장점이다.

### 2. 대화 이력을 `st.session_state`로 관리한 이유

```python
if "messages" not in st.session_state:
    st.session_state["messages"] = [{"role": "assistant", "content": "오늘 어떤 도움을 드릴까요?"}]
```

Streamlit은 사용자 입력이 있을 때마다 **스크립트 전체를 처음부터 다시 실행**한다. 일반 변수에 이력을 담으면 매번 초기화되므로, 재실행 사이에도 값이 유지되는 `st.session_state`를 사용해야 한다.

초기 인사말을 이력에 함께 넣은 것은 화면 렌더링 로직을 하나로 통일하기 위해서이다. 인사말을 별도 처리하면 출력 분기가 두 벌이 된다.

### 3. 매 요청마다 전체 이력을 전송하는 구조

```python
response = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=st.session_state.messages)
```

LLM API는 **무상태(stateless)** 이다. 직전 대화를 기억하지 못하므로, 맥락을 유지하려면 매 턴 전체 이력을 다시 보내야 한다.

이 구조는 동작하지만 **대화가 길어질수록 토큰 사용량이 선형으로 늘어나고 결국 컨텍스트 한계에 도달**한다. 아래 [확인된 한계](#확인된-한계)에 정리했습니다.

---

## 기술 스택

| 구분 | 사용 기술 |
|---|---|
| 언어 | Python 3.11 |
| UI | Streamlit 1.52 |
| LLM | OpenAI `gpt-3.5-turbo` (`openai` 0.28.1) |

<!-- TODO: 실제 사용한 Python 버전으로 수정 -->

---

## 실행 방법

### 1. 저장소 복제 및 가상환경 생성

```bash
git clone https://github.com/minjaejeon0827/test_AI_Assistant.git
cd test_AI_Assistant

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

### 2. 패키지 설치

```bash
pip install -r requirements.txt
```

> `openai==0.28.1`은 구버전 SDK입니다. 이미 1.x 버전이 설치되어 있다면 먼저 제거해야 합니다.
> ```bash
> pip uninstall openai -y && pip install openai==0.28.1
> ```

### 3. 실행

```bash
streamlit run app.py
```

브라우저가 열리면 좌측 사이드바에 [OpenAI API 키](https://platform.openai.com/api-keys)를 입력한 뒤 질문을 입력한다.

---

## 확인된 한계

PoC 과정에서 드러난 문제들이다. **이 목록이 곧 1단계 저장소의 RAG 설계 근거가 되었다.**

| 한계 | 원인 | 실제 영향 | 개선 방향 |
|---|---|---|---|
| **Autodesk 파트너회사 기술지원 지식 미반영** | 범용 LLM 모델을 그대로 호출, 검색 단계 없음 | AutoCAD 제품 설치 절차 질문 시 일반적인 내용으로만 답변. 실무 투입 불가 | 제품 설치 가이드 문서를 벡터화해 검색 후 근거와 함께 답변 (RAG) |
| 토큰 비용 선형 증가 | LLM 모델 무상태 → 매 턴 전체 이력 재전송 | 대화가 길어질수록 비용 상승, 컨텍스트 한계 도달 | 최근 N턴 윈도우 또는 이력 요약 압축 |
| 오류 처리 부재 | `try/except` 미적용 | 잘못된 키·요청 한도 초과 시 예외 화면 그대로 노출 | 예외 유형별 분기 + 사용자 안내 문구 |
| 응답 대기 중 피드백 없음 | 스트리밍 미적용 | 수 초간 화면이 멈춘 것처럼 보임 | `stream=True` + `st.write_stream` |
| 구버전 SDK 사용 | `openai==0.28.1` (2023.10) | 최신 모델·기능 사용 불가 | `openai>=1.x` 마이그레이션 |
| 대화 기록 휘발 | `st.session_state`는 브라우저 세션 한정 | 새로고침 시 대화 소실 | 외부 저장소 연동 |
| 답변 신뢰성 보장 장치 없음 | 근거 문서 없이 생성 | 제품 설치 안내의 경우 오답 안내 시 사용자에게 실질적 피해 발생 | 근거 미검색 시 상담원 연결 전환 필요 |

---

## 개선 계획

이 프로젝트는 회사 사정으로 인하여 중단되었으나, 이후 진행할 프로젝트에 적용할 순서를 다음과 같이 정리해 두었다.

- [ ] `openai` 1.x 마이그레이션 및 응답 스트리밍 적용
- [ ] 시스템 프롬프트로 기술지원 담당자 역할 고정
- [ ] 예외 처리 및 사용량 상한 설정
- [ ] **제품 설치 가이드 문서 기반 RAG 파이프라인 연결** — 1단계 저장소 `utils/openAI.py` 소스파일의 PoC 코드 활용
- [ ] 근거 문서 미검색 시 상담원 연결 전환 로직
- [ ] 응답 정확도 평가셋 구성 및 측정

---

## 관련 저장소

| 저장소 | 내용 |
|---|---|
| [KakaoChatbot](https://github.com/minjaejeon0827/KakaoChatbot) | 1단계 — 규칙 기반 카카오 챗봇, 서버리스(AWS Lambda) 아키텍처, 트러블슈팅 전체 기록 |
| **test_AI_Assistant** | *(현재 저장소)* 2단계 — LLM 모델 상담 프로토타입 |

---

<div align="center">

**전민재** (Minjae Jeon)

[![GitHub](https://img.shields.io/badge/GitHub-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/minjaejeon0827)
<!-- TODO: 이메일 주소 확인 후 수정 -->
[![Email](https://img.shields.io/badge/Email-D14836?style=flat-square&logo=gmail&logoColor=white)](mailto:minjaejeon0827@gmail.com)

본 저장소는 ㈜상상진화 재직 중 수행한 기술 검증(PoC) 결과물이며,<br/>
공개한 소스코드는 프로토타입 모델입니다.

</div>
