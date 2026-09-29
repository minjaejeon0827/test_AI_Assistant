"""테스트 공용 픽스처 — 가짜 OpenAI 클라이언트로 키 없이 생성 경로까지 검증한다."""

from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

import pytest

from assistant.kb import load_kb
from assistant.retriever import Retriever


@pytest.fixture(scope="session")
def kb():
    return load_kb()


@pytest.fixture
def retriever(kb):
    return Retriever(kb)


def _pieces(text: str, n: int = 4) -> list[str]:
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def _reply(reply, params) -> str:
    return reply(params) if callable(reply) else reply


class FakeChat:
    def __init__(self, reply, usage=(120, 30)):
        self.reply, self.usage, self.calls = reply, usage, []

    def create(self, **params):
        self.calls.append(params)
        chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p))], usage=None)
                  for p in _pieces(_reply(self.reply, params))]
        if self.usage:
            chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(
                prompt_tokens=self.usage[0], completion_tokens=self.usage[1])))
        return iter(chunks)


class FakeResponses:
    def __init__(self, reply, usage=(110, 25)):
        self.reply, self.usage, self.calls = reply, usage, []

    def create(self, **params):
        self.calls.append(params)
        events = [SimpleNamespace(type="response.output_text.delta", delta=p)
                  for p in _pieces(_reply(self.reply, params))]
        events.append(SimpleNamespace(type="response.completed", response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))))
        return iter(events)


def fake_vector(text: str, dim: int = 8) -> list[float]:
    """텍스트마다 다르고 실행마다 같은 벡터(결정적)."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    return [b / 255 - 0.5 for b in h[:dim]]


class FakeEmbeddings:
    def __init__(self):
        self.calls = 0

    def create(self, model, input, **_):
        self.calls += 1
        items = input if isinstance(input, list) else [input]
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=fake_vector(t)) for i, t in enumerate(items)])


class FakeClient:
    def __init__(self, reply="답변입니다."):
        self.chat = SimpleNamespace(completions=FakeChat(reply))
        self.responses = FakeResponses(reply)
        self.embeddings = FakeEmbeddings()


def cite_first(params) -> str:
    """시스템 프롬프트의 첫 근거 문서 ID 를 인용하는 가짜 답변. 요약 요청이면 요약을 돌려준다."""
    system = params.get("instructions") or params["messages"][0]["content"]
    if "요약을 갱신" in system:
        return "- 고객 제품: AutoCAD 2026\n- 라이선스 인증 안내 완료"
    ids = re.findall(r"^\[(KB-[A-Z0-9]+-\d{2})\]", system, re.M)
    return f"1. 안내드린 절차대로 진행합니다. [{ids[0]}]"
