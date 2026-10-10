"""
assistant/llm.py
LLM 호출 어댑터 — Chat Completions 와 Responses API 를 같은 인터페이스로 감싼다.

★ 'Responses API 전환 검토'의 결론이 이 파일이다
  전환 여부를 코드 수정으로 결정하지 않고 설정(ASSISTANT_LLM_BACKEND)으로 결정한다.
  호출부(pipeline · app)는 어느 API 인지 모른다. 골든 데이터셋 생성 평가를 두 방식으로 각각 돌려
  인용 유효율 · 지연 시간이 동등 이상이면 기본값을 responses 로 바꾼다.

★ 상담원 전환 신호([[HANDOFF]])를 스트림 앞부분에서 붙잡는다
  모델이 "근거 문서로 답할 수 없다"고 판단하면 이 토큰만 출력하도록 지시한다.
  스트리밍을 그대로 흘리면 화면에 "[[HAN..." 이 잠깐 보인다.
  → 앞부분이 토큰의 접두어인 동안만 버퍼에 잡아 두고, 아니라고 판명되면 즉시 흘려보낸다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)

from . import config as C

logger = logging.getLogger(__name__)

HANDOFF_TOKEN = "[[HANDOFF]]"


class LLMStreamError(RuntimeError):
    """스트리밍 도중 서버가 실패 이벤트를 보냈다(Responses API)."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False     # 서버가 사용량을 주지 않아 글자 수로 추정했다

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class StreamState:
    """스트리밍이 끝난 뒤 호출부가 읽는 결과. 제너레이터는 반환값을 쉽게 돌려줄 수 없어 상태 객체를 쓴다."""
    usage: Usage = field(default_factory=Usage)
    handoff: bool = False
    parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.parts)


def make_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, timeout=C.REQUEST_TIMEOUT, max_retries=C.MAX_RETRIES)


def estimate_tokens(text: str) -> int:
    """사용량을 못 받았을 때의 보수적 추정. 한국어는 대략 글자 2개 ≈ 1토큰으로 본다."""
    return max(1, len(text) // 2)


def _create(create_fn, params: dict):
    """temperature 를 지원하지 않는 모델이면 한 번만 빼고 재시도한다.

    ★ 오류 메시지에 'temperature' 가 있을 때만 재시도한다.
      모든 400 을 재시도하면 진짜 설정 오류(잘못된 파라미터)가 가려진다.
    """
    try:
        return create_fn(**params)
    except BadRequestError as exc:
        if "temperature" in params and "temperature" in str(exc).lower():
            logger.warning("%s 는 temperature 를 지원하지 않아 기본값으로 재호출한다", params.get("model"))
            retry = {k: v for k, v in params.items() if k != "temperature"}
            return create_fn(**retry)
        raise


# region 백엔드별 스트림


def _chat_deltas(client: OpenAI, model: str, system: str, messages: list[dict],
                 temperature: float | None, state: StreamState) -> Iterator[str]:
    params = {
        "model": model,
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
        "stream_options": {"include_usage": True},   # 마지막 청크에 사용량이 실린다
    }
    if temperature is not None:
        params["temperature"] = temperature
    for chunk in _create(client.chat.completions.create, params):
        if chunk.usage:
            state.usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _responses_deltas(client: OpenAI, model: str, system: str, messages: list[dict],
                      temperature: float | None, state: StreamState) -> Iterator[str]:
    params = {"model": model, "instructions": system, "input": messages, "stream": True}
    if temperature is not None:
        params["temperature"] = temperature
    for event in _create(client.responses.create, params):
        etype = getattr(event, "type", "")
        if etype == "response.output_text.delta":
            yield event.delta
        elif etype == "response.completed":
            u = event.response.usage
            if u:
                state.usage = Usage(u.input_tokens, u.output_tokens)
        elif etype in ("response.failed", "error"):
            detail = getattr(event, "message", "") or getattr(getattr(event, "response", None), "error", "")
            raise LLMStreamError(f"Responses API 스트림 실패: {detail}")


def _deltas(client: OpenAI, backend: str, **kw) -> Iterator[str]:
    if backend == "responses":
        return _responses_deltas(client, **kw)
    return _chat_deltas(client, **kw)


# endregion 백엔드별 스트림


def _hold_sentinel(deltas: Iterator[str], state: StreamState) -> Iterator[str]:
    """응답 앞부분이 [[HANDOFF]] 인지 판명될 때까지만 붙잡아 둔다."""
    it = iter(deltas)
    buf = ""
    for piece in it:
        buf += piece
        head = buf.lstrip()
        if head.startswith(HANDOFF_TOKEN):
            state.handoff = True
            for _ in it:        # 나머지를 소비해야 마지막 청크의 사용량까지 받는다
                pass
            return
        if HANDOFF_TOKEN.startswith(head):
            continue            # 아직 판단 불가 ("[[HAN")
        break
    else:
        # 스트림이 판단 불가 상태로 끝났다. 토큰 접두어만 남았다면 잘린 전환 신호로 본다.
        if buf.strip():
            state.handoff = True
        return
    state.parts.append(buf)
    yield buf
    for piece in it:
        state.parts.append(piece)
        yield piece


def stream_answer(client: OpenAI, *, backend: str, model: str, system: str,
                  messages: list[dict], temperature: float | None, state: StreamState) -> Iterator[str]:
    """답변을 스트리밍한다. 끝난 뒤 state 에 본문 · 사용량 · 전환 여부가 채워진다."""
    deltas = _deltas(client, backend, model=model, system=system, messages=messages,
                     temperature=temperature, state=state)
    yield from _hold_sentinel(deltas, state)
    if state.usage.total == 0:
        prompt = system + "".join(m["content"] for m in messages)
        state.usage = Usage(estimate_tokens(prompt), estimate_tokens(state.text), estimated=True)


def complete(client: OpenAI, *, backend: str, model: str, system: str,
             messages: list[dict], temperature: float | None = None) -> tuple[str, Usage]:
    """스트림을 끝까지 받아 한 번에 돌려준다. 요약처럼 화면에 흘릴 필요가 없는 호출용."""
    state = StreamState()
    text = "".join(_deltas(client, backend, model=model, system=system, messages=messages,
                           temperature=temperature, state=state))
    if state.usage.total == 0:
        prompt = system + "".join(m["content"] for m in messages)
        state.usage = Usage(estimate_tokens(prompt), estimate_tokens(text), estimated=True)
    return text, state.usage


# region 오류 안내

# 순서가 중요하다 — APITimeoutError 는 APIConnectionError 의 하위 클래스라 먼저 검사한다.
_ERROR_GUIDE: dict[type[Exception], tuple[str, str]] = {
    AuthenticationError: ("API 키가 올바르지 않습니다.",
                          "사이드바의 키를 다시 확인해 주세요. 키 앞뒤 공백도 확인 대상입니다."),
    PermissionDeniedError: ("선택한 모델에 접근 권한이 없습니다.",
                            "다른 모델을 선택하거나 계정 등급을 확인해 주세요."),
    NotFoundError: ("요청한 모델을 찾을 수 없습니다.",
                    "모델이 폐기되었을 수 있습니다. 사이드바에서 다른 모델을 선택해 주세요."),
    RateLimitError: ("요청 한도 또는 크레딧을 초과했습니다.",
                     "잠시 후 다시 시도하거나 결제 상태를 확인해 주세요."),
    BadRequestError: ("요청 형식이 올바르지 않습니다.",
                      "대화를 초기화한 뒤 다시 시도해 주세요."),
    APITimeoutError: (f"응답 시간이 {C.REQUEST_TIMEOUT:.0f}초를 초과했습니다.",
                      "질문을 짧게 나누어 다시 시도해 주세요."),
    APIConnectionError: ("OpenAI 서버에 연결하지 못했습니다.",
                         "네트워크 연결 또는 방화벽 설정을 확인해 주세요."),
}


def to_user_message(exc: Exception) -> str:
    """예외를 사용자 안내 문구(원인 + 조치)로 바꾼다."""
    for exc_type, (reason, action) in _ERROR_GUIDE.items():
        if isinstance(exc, exc_type):
            return f"**{reason}** {action}"
    if isinstance(exc, APIStatusError):
        return f"**OpenAI 서버 오류가 발생했습니다. (HTTP {exc.status_code})** 잠시 후 다시 시도해 주세요."
    if isinstance(exc, (OpenAIError, LLMStreamError)):
        return "**OpenAI API 호출 중 오류가 발생했습니다.** 잠시 후 다시 시도해 주세요."
    return "**예상하지 못한 오류가 발생했습니다.** 대화를 초기화한 뒤 다시 시도해 주세요."


# endregion 오류 안내
