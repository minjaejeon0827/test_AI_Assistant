"""LLM 모델 어댑터 · 이력 요약 · 저장소 · 사용량 상한"""

from __future__ import annotations

import httpx2 as httpx
import openai
import pytest
from conftest import FakeClient

from assistant import config as C
from assistant import llm, memory
from assistant.store import Store, is_valid_conv_id
from assistant.usage import Quota


def _stream(backend, reply, **kw):
    st = llm.StreamState()
    out = "".join(llm.stream_answer(FakeClient(reply), backend=backend, model="m", system="s",
                                    messages=[{"role": "user", "content": "q"}], temperature=0.2, state=st, **kw))
    return out, st


@pytest.mark.parametrize("backend", ["chat", "responses"])
def test_both_backends_stream_text_and_usage(backend):
    out, st = _stream(backend, "설치를 진행합니다. [KB-ACAD-01]")
    assert out == st.text == "설치를 진행합니다. [KB-ACAD-01]"
    assert st.usage.total > 0 and not st.usage.estimated and not st.handoff


@pytest.mark.parametrize("backend", ["chat", "responses"])
def test_handoff_token_split_across_chunks_never_reaches_screen(backend):
    out, st = _stream(backend, "[[HANDOFF]]")          # 4글자씩 쪼개져 들어온다
    assert out == "" and st.handoff and st.usage.total > 0   # 나머지를 소비해 사용량까지 받았다


def test_text_starting_like_token_is_released():
    out, st = _stream("chat", "[[참고]] 설치 위치는 C 드라이브입니다.")
    assert out.startswith("[[참고]]") and not st.handoff


def test_usage_is_estimated_when_server_omits_it():
    client = FakeClient("답변")
    client.chat.completions.usage = None
    st = llm.StreamState()
    list(llm.stream_answer(client, backend="chat", model="m", system="s",
                           messages=[{"role": "user", "content": "q"}], temperature=None, state=st))
    assert st.usage.estimated and st.usage.total > 0


def _bad_request(msg):
    return openai.BadRequestError(msg, response=httpx.Response(400, request=httpx.Request("POST", "https://x")), body=None)


def test_temperature_unsupported_retries_once_without_it():
    client = FakeClient("답변")
    real = client.chat.completions.create

    def create(**params):
        if "temperature" in params:
            raise _bad_request("Unsupported value: 'temperature' does not support 0.2")
        return real(**params)

    client.chat.completions.create = create
    text, _ = llm.complete(client, backend="chat", model="m", system="s",
                           messages=[{"role": "user", "content": "q"}], temperature=0.2)
    assert text == "답변"


def test_other_bad_requests_are_not_swallowed():
    client = FakeClient("답변")

    def create(**params):
        raise _bad_request("Invalid 'messages'")

    client.chat.completions.create = create
    with pytest.raises(openai.BadRequestError):
        llm.complete(client, backend="chat", model="m", system="s", messages=[], temperature=0.2)


def test_error_messages_are_user_friendly():
    req = httpx.Request("POST", "https://x")
    assert "API 키" in llm.to_user_message(openai.AuthenticationError("x", response=httpx.Response(401, request=req), body=None))
    assert "초과" in llm.to_user_message(openai.APITimeoutError(request=req))   # 연결 오류보다 먼저 매칭
    assert "예상하지 못한" in llm.to_user_message(ValueError())


def test_history_split_excludes_errors_and_strips_citations():
    msgs = [{"role": "user", "content": f"q{i}"} for i in range(3)]
    msgs.append({"role": "assistant", "content": "오류", "error": True})
    msgs.append({"role": "assistant", "content": "클릭합니다. [KB-ACAD-01]"})
    older, recent = memory.split_history(msgs, keep_turns=1)
    assert [m["content"] for m in older] == ["q0", "q1"]
    assert memory.to_request(recent)[-1]["content"] == "클릭합니다."


def test_summary_is_incremental_and_survives_failure():
    seen = {}

    def fn(system, messages):
        seen["prompt"] = messages[0]["content"]
        return "새 요약" * 200

    out = memory.update_summary("이전 요약", [{"role": "user", "content": "레빗 2025"}], fn)
    assert "이전 요약" in seen["prompt"] and "레빗 2025" in seen["prompt"]
    assert len(out) <= C.SUMMARY_MAX_CHARS

    def boom(system, messages):
        raise RuntimeError

    assert memory.update_summary("이전 요약", [], boom) == "이전 요약"
    assert memory.needs_update(older_count=5, summarized_count=0)
    assert not memory.needs_update(older_count=5, summarized_count=4)


def test_store_roundtrip_usage_and_delete(tmp_path):
    s = Store(tmp_path / "a.db")
    cid = s.create_conversation()
    assert is_valid_conv_id(cid) and not is_valid_conv_id("../../etc") and s.exists(cid)
    s.append_message(cid, "user", "질문")
    s.append_message(cid, "assistant", "답", meta={"sources": ["AutoCAD · 설치 준비"]})
    s.save_state(cid, summary="요약", summarized_count=2, state={"active_product": "AutoCAD"})
    assert [m["content"] for m in s.load_messages(cid)] == ["질문", "답"]
    assert s.load_state(cid)["active_product"] == "AutoCAD"

    s.log_usage(cid, kind="answer", backend="chat", prompt_tokens=100, completion_tokens=20)
    s.log_usage(cid, kind="answer", backend="extractive")          # LLM 모델 미사용은 세지 않는다
    assert s.usage_totals(cid) == (1, 120) and s.daily_calls() == 1

    s.record_handoff(cid, "low_relevance", "질문")
    s.delete_conversation(cid)
    assert not s.exists(cid) and s.load_messages(cid) == []


def test_quota_reads_limits_at_runtime(monkeypatch):
    monkeypatch.setattr(C, "MAX_CALLS_PER_CONVERSATION", 2)
    q = Quota(calls=2, tokens=0)
    assert q.exhausted and "호출 한도" in q.reason
    assert Quota(calls=0, tokens=0, daily_calls=10**6).reason == "오늘 전체 사용량 한도"
