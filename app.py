"""
app.py
* 테스트용 기술지원 AI Assistant (Streamlit + OpenAI)

변경 이력
- 2026.09  openai 0.28.1 → 3.x SDK 마이그레이션
           응답 스트리밍, 시스템 프롬프트, 예외 처리, 대화 이력 윈도우 적용

패키지 설치
- pip install openai
- pip install streamlit

패키지 삭제
- pip uninstall openai
- pip uninstall streamlit

참고
- OpenAI Python SDK      : https://github.com/openai/openai-python
- 모델 카탈로그(수시 변경)  : https://developers.openai.com/api/docs/models
- 오류 코드 레퍼런스       : https://developers.openai.com/api/docs/guides/error-codes
- Streamlit chat 요소    : https://docs.streamlit.io/develop/api-reference/chat

- with 문
참고: https://docs.python.org/ko/3/reference/compound_stmts.html#index-16
참고 2: https://velog.io/@hyungraelee/Python-with

- yield 표현식
참고: https://docs.python.org/ko/3/reference/expressions.html#yieldexpr

- FastAPI, Streamlit, OpenAI 챗봇 만들기
참고: https://youtu.be/n_MhxO16EaY?si=TyDrasy7Pa7OdTO3
참고 2: https://dongdongfather.tistory.com/286
참고 3: https://youtu.be/I_InS5HGtmE?si=lXODkiQ_A2PAfhUq
참고 4: https://github.com/streamlit/llm-examples/blob/main/Chatbot.py#L1C1-L29C44

- Python 기반 웹 애플리케이션 UI 프레임워크 오픈 소스 패키지 streamlit
참고: https://docs.streamlit.io/get-started/installation
참고 2: https://streamlit.io/generative-ai

- None or Empty String Check
참고: https://stackoverflow.com/questions/9573244/how-to-check-if-the-string-is-empty-in-python
참고 2: https://hello-bryan.tistory.com/131
참고 3: https://jino-dev-diary.tistory.com/42
참고 4: https://claude.ai/chat/eaf7856e-1b5e-4c26-992e-de1683005638
"""

from __future__ import annotations

import time
from typing import Iterator

import streamlit as st
from openai import (
    OpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)

# region 설정 상수

APP_TITLE = "[테스트] 상상플렉스 AI Assistant"
APP_CAPTION = "AI Assistant는 실수를 할 수 있습니다. 응답을 반드시 다시 확인해 주세요."

# 모델명은 수시로 추가·폐기되므로 코드 곳곳에 흩뿌리지 않고 여기 한 곳에서만 관리한다.
# 폐기된 모델을 호출하면 NotFoundError가 발생하며, 아래 _ERROR_GUIDE에서 안내 문구로 변환된다.
MODEL_OPTIONS: dict[str, str] = {
    "gpt-5.6-terra": "균형형 — 품질과 비용의 절충 (기본값)",
    "gpt-5.6-luna": "경량형 — 비용 민감 워크로드",
    "gpt-5.6-sol": "고성능형 — 복잡한 문의 대응",
}
DEFAULT_MODEL = "gpt-5.6-terra"

# 대화 이력 윈도우: 최근 N턴(user+assistant 쌍)만 API 요청에 포함한다.
# 전체 이력을 매번 보내면 토큰 사용량이 대화 길이에 비례해 선형 증가하기 때문이다.
MAX_HISTORY_TURNS = 10

REQUEST_TIMEOUT = 30.0   # 단일 요청 타임아웃(초)
MAX_RETRIES = 2          # SDK 내장 자동 재시도 횟수 (429/5xx 대상)

# 시스템 프롬프트로 역할을 고정한다.
# 현재 검색(RAG) 단계가 없으므로 "모르면 모른다고 답하라"는 지침을 명시해
# 사실이 아닌 설치 절차를 지어내는 것을 억제한다.
SYSTEM_PROMPT = """당신은 Autodesk 제품 설치 기술지원 담당자입니다.

응답 규칙:
1. 한국어로, 단계별 번호를 붙여 간결하게 답변합니다.
2. 확실하지 않은 정보는 추측하지 않고 모른다고 말합니다.
3. 설치 경로·버전·라이선스처럼 정확성이 중요한 내용은
   반드시 공식 문서 확인이 필요하다고 덧붙입니다.
4. 답변이 어려운 문의는 기술지원 콜센터 연결을 안내합니다.
5. 기술지원과 무관한 주제는 정중히 범위를 벗어난다고 안내합니다."""

GREETING = "안녕하세요. Autodesk 제품 설치 관련하여 어떤 도움이 필요하신가요?"

# endregion 설정 상수

# region OpenAI 클라이언트


def get_client(api_key: str) -> OpenAI:
    """
    Description: OpenAI 클라이언트를 세션 단위로 생성·재사용한다.

                 st.cache_resource를 쓰지 않은 이유:
                 캐시는 프로세스 전역이므로 여러 사용자가 접속하는 배포 환경에서
                 한 사용자의 API 키가 다른 사용자에게 재사용될 수 있다.
                 st.session_state는 브라우저 세션 단위이므로 API 키 타인 재사용 문제가 발생하지 않는다.

    Parameters: api_key - 사용자가 입력한 OpenAI API 키

    Returns: OpenAI 클라이언트 인스턴스
    """

    if st.session_state.get("_client_key") != api_key:
        st.session_state["_client"] = OpenAI(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT,
            max_retries=MAX_RETRIES,
        )
        st.session_state["_client_key"] = api_key

    return st.session_state["_client"]


# endregion OpenAI 클라이언트

# region 대화 이력


def init_session() -> None:
    """
    Description: 세션 상태 초기화 (최초 1회만 실행)

    Parameters: 없음.

    Returns: 없음.
    """

    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {"role": "assistant", "content": GREETING, "error": False}
        ]


def reset_session() -> None:
    """
    Description: 대화 이력 초기화 (API 키는 유지)

    Parameters: 없음.

    Returns: 없음.
    """

    st.session_state["messages"] = [
        {"role": "assistant", "content": GREETING, "error": False}
    ]


def build_request_messages(max_turns: int = MAX_HISTORY_TURNS) -> list[dict[str, str]]:
    """
    Description: API 요청용 messages 배열을 구성한다.

                 두 가지를 처리한다.
                 1) 오류 메시지 제외 - 화면에는 남기되 요청에는 넣지 않는다.
                    오류 문구가 컨텍스트에 섞이면 모델이 이를 대화 내용으로 오인한다.
                 2) 최근 N턴만 유지 - 토큰 사용량의 무한 증가를 막는다.

    Parameters: max_turns - 요청에 포함할 최대 턴 수 (user+assistant 쌍 기준)

    Returns: system 프롬프트가 맨 앞에 붙은 messages 배열
    """

    usable = [m for m in st.session_state["messages"] if not m.get("error")]
    trimmed = usable[-(max_turns * 2):]

    return [{"role": "system", "content": SYSTEM_PROMPT}] + [
        {"role": m["role"], "content": m["content"]} for m in trimmed
    ]


# endregion 대화 이력

# region 스트리밍 응답


def stream_answer(
    client: OpenAI,
    model: str,
    messages: list[dict[str, str]],
    temperature: float | None = None,
) -> Iterator[str]:
    """
    Description: Chat Completions 스트리밍 호출 후 텍스트 조각(delta)을 순차 반환한다.
                 제너레이터이므로 st.write_stream에 그대로 전달할 수 있다.

    Parameters: client - OpenAI 클라이언트
                model - 모델명
                messages - 요청 messages 배열
                temperature - 샘플링 온도 (None이면 미전송)

    Returns: 응답 텍스트 조각 이터레이터
    """

    params: dict = {"model": model, "messages": messages, "stream": True}

    if temperature is not None:
        params["temperature"] = temperature

    try:
        stream = client.chat.completions.create(**params)
    except BadRequestError:
        # 추론(reasoning) 계열 모델은 temperature를 지원하지 않아 400을 반환한다.
        # 파라미터를 제거하고 1회만 재시도한다. (무한 재시도 방지)
        if "temperature" not in params:
            raise
        params.pop("temperature")
        stream = client.chat.completions.create(**params)

    for chunk in stream:
        if not chunk.choices:
            continue   # 사용량(usage) 전용 청크 등 choices가 비어 오는 경우가 있다.

        delta = chunk.choices[0].delta

        if delta and delta.content:
            yield delta.content  # 값 전달하고 실행 양보(중단)하며 상태 기억 처리


# endregion 스트리밍 응답

# region 오류 처리

# 예외 타입 → (사용자 안내 문구, 조치 방법)
# 스택 트레이스를 그대로 노출하지 않고 "무엇을 하면 되는지"만 전달하는 것이 목적이다.
_ERROR_GUIDE: dict[type[Exception], tuple[str, str]] = {
    AuthenticationError: (
        "API 키가 올바르지 않습니다.",
        "사이드바의 키를 다시 확인해 주세요. 키 앞뒤 공백도 확인 대상입니다.",
    ),
    PermissionDeniedError: (
        "선택한 모델에 접근 권한이 없습니다.",
        "다른 모델을 선택하거나 계정 등급을 확인해 주세요.",
    ),
    NotFoundError: (
        "요청한 모델을 찾을 수 없습니다.",
        "모델이 폐기되었을 수 있습니다. 사이드바에서 다른 모델을 선택해 주세요.",
    ),
    RateLimitError: (
        "요청 한도 또는 크레딧을 초과했습니다.",
        "잠시 후 다시 시도하거나 결제 상태를 확인해 주세요.",
    ),
    BadRequestError: (
        "요청 형식이 올바르지 않습니다.",
        "대화를 초기화한 뒤 다시 시도해 주세요.",
    ),
    APITimeoutError: (
        f"응답 시간이 {REQUEST_TIMEOUT:.0f}초를 초과했습니다.",
        "질문을 짧게 나누어 다시 시도해 주세요.",
    ),
    APIConnectionError: (
        "OpenAI 서버에 연결하지 못했습니다.",
        "네트워크 연결 또는 방화벽 설정을 확인해 주세요.",
    ),
}


def to_user_message(exc: Exception) -> str:
    """
    Description: 예외를 사용자에게 보여줄 안내 문구로 변환한다.

    Parameters: exc - 발생한 예외 객체

    Returns: 사용자 안내 문구
    """

    for exc_type, (reason, action) in _ERROR_GUIDE.items():
        if isinstance(exc, exc_type):
            return f"**{reason}**\n\n{action}"

    if isinstance(exc, APIStatusError):
        return (
            f"**OpenAI 서버 오류가 발생했습니다. (HTTP {exc.status_code})**\n\n"
            "잠시 후 다시 시도해 주세요."
        )

    if isinstance(exc, OpenAIError):
        return "**OpenAI API 호출 중 오류가 발생했습니다.**\n\n잠시 후 다시 시도해 주세요."

    return "**예상하지 못한 오류가 발생했습니다.**\n\n대화를 초기화한 뒤 다시 시도해 주세요."


# endregion 오류 처리

# region 화면 구성


def render_sidebar() -> tuple[str, str, float]:
    """
    Description: 사이드바 렌더링 (API 키 입력, 모델 선택, 온도 조절, 대화 초기화)

    Parameters: 없음.

    Returns: api_key - 입력받은 OpenAI API 키
             model - 선택한 모델명
             temperature - 샘플링 온도
    """

    with st.sidebar:
        st.subheader("설정")

        # API 키를 코드나 .env가 아닌 화면 입력으로 받는다.
        # 공개 저장소에 키가 남지 않고, 여러 사용자가 각자 키로 테스트할 수 있다.
        api_key = st.text_input(
            "OpenAI API Key",
            key="chatbot_api_key",
            type="password",
            placeholder="sk-...",
            help="입력한 키는 브라우저 세션에만 유지되며 서버나 저장소에 기록되지 않습니다.",
        ).strip()

        model = st.selectbox(
            "모델",
            options=list(MODEL_OPTIONS.keys()),
            index=list(MODEL_OPTIONS.keys()).index(DEFAULT_MODEL),
            format_func=lambda name: f"{name} · {MODEL_OPTIONS[name]}",
        )

        temperature = st.slider(
            "응답 다양성 (temperature)",
            min_value=0.0, max_value=1.0, value=0.2, step=0.1,
            help="기술지원 답변은 일관성이 중요하므로 낮은 값을 권장합니다.",
        )

        st.divider()

        turns = len([m for m in st.session_state["messages"] if not m.get("error")])
        st.caption(f"화면 표시 메시지 {turns}개 · 요청 포함 최대 {MAX_HISTORY_TURNS}턴")

        if st.button("대화 초기화", use_container_width=True):
            reset_session()
            st.rerun()

        st.divider()
        st.caption("[상상플렉스](https://www.ssflex.co.kr/) · [㈜상상진화](https://imbu.co.kr/)")

    return api_key, model, temperature


def render_history() -> None:
    """
    Description: 지금까지의 대화 이력을 화면에 출력한다.

    Parameters: 없음.

    Returns: 없음.
    """

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            if msg.get("error"):
                st.error(msg["content"])
            else:
                st.markdown(msg["content"])


def handle_prompt(prompt: str, client: OpenAI, model: str, temperature: float) -> None:
    """
    Description: 사용자 질문 한 건을 처리한다. (이력 추가 → 스트리밍 응답 → 이력 반영)

    Parameters: prompt - 사용자 질문
                client - OpenAI 클라이언트
                model - 모델명
                temperature - 샘플링 온도

    Returns: 없음.
    """

    st.session_state["messages"].append(
        {"role": "user", "content": prompt, "error": False}
    )

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        started_at = time.perf_counter()

        try:
            answer = st.write_stream(
                stream_answer(
                    client=client,
                    model=model,
                    messages=build_request_messages(),
                    temperature=temperature,
                )
            )
            elapsed = time.perf_counter() - started_at
            st.caption(f"{model} · {elapsed:.1f}초")

            st.session_state["messages"].append(
                {"role": "assistant", "content": answer, "error": False}
            )

        except Exception as exc:   # noqa: BLE001 - 어떤 예외에도 화면이 깨지지 않아야 한다.
            message = to_user_message(exc)
            st.error(message)

            # 오류도 이력에 남겨 사용자가 상황을 인지하게 하되,
            # build_request_messages에서 제외되므로 다음 요청 컨텍스트는 오염되지 않는다.
            st.session_state["messages"].append(
                {"role": "assistant", "content": message, "error": True}
            )


def main() -> None:
    """
    Description: 애플리케이션 진입점

    Parameters: 없음.

    Returns: 없음.
    """

    st.set_page_config(page_title=APP_TITLE, page_icon="🛠️", layout="centered")

    init_session()

    st.header(APP_TITLE)
    st.caption(APP_CAPTION)
    st.divider()

    api_key, model, temperature = render_sidebar()
    render_history()

    prompt = st.chat_input("Autodesk 제품 설치 관련 문의를 입력해 주세요.")

    if not prompt:
        return

    if not api_key:
        st.info("좌측 사이드바에 OpenAI API Key를 먼저 입력해 주세요.")
        st.stop()

    handle_prompt(
        prompt=prompt,
        client=get_client(api_key),
        model=model,
        temperature=temperature,
    )


# endregion 화면 구성


if __name__ == "__main__":
    main()
