"""Streamlit AppTest — 화면 흐름을 헤드리스로 돌린다."""

from __future__ import annotations

import sqlite3

import pytest
import streamlit as st
from conftest import FakeClient, cite_first
from streamlit.testing.v1 import AppTest

from assistant import config as C
from assistant import llm

APP = "../app.py"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "DB_PATH", tmp_path / "t.db")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    st.cache_resource.clear()      # 저장소 캐시가 테스트 사이에 새지 않게 한다
    yield


def ask(at: AppTest, q: str) -> AppTest:
    at.chat_input[0].set_value(q).run()
    assert not at.exception, at.exception
    return at


def texts(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_no_key_answers_from_documents_and_restores_after_refresh():
    at = AppTest.from_file(APP, default_timeout=30).run()
    ask(at, "오토캐드 라이센스 등록하는 법 좀")
    assert "AutoCAD · 라이선스 활성화" in texts(at)
    conv_id = at.query_params["c"][0] if isinstance(at.query_params["c"], list) else at.query_params["c"]

    again = AppTest.from_file(APP, default_timeout=30)       # 새로고침 = 새 세션 + 같은 주소
    again.query_params["c"] = conv_id
    again.run()
    assert "오토캐드 라이센스 등록하는 법 좀" in texts(again)


def test_out_of_scope_goes_to_agent_and_is_recorded():
    at = AppTest.from_file(APP, default_timeout=30).run()
    ask(at, "포토샵 설치 방법 알려주세요")
    assert "기술지원 콜센터" in texts(at)
    rows = sqlite3.connect(C.DB_PATH).execute("SELECT reason FROM handoffs").fetchall()
    assert rows == [("unsupported_product",)]


def _with_key(monkeypatch, reply) -> tuple[AppTest, FakeClient]:
    fake = FakeClient(reply)
    monkeypatch.setattr(llm, "make_client", lambda key: fake)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.text_input[0].input("sk-test").run()
    return at, fake


def test_generative_answer_is_cited_and_usage_logged(monkeypatch):
    at, fake = _with_key(monkeypatch, cite_first)
    ask(at, "오토캐드 라이센스 등록하는 법 좀")
    assert "`KB-ACAD-04`" in texts(at)                                   # 인용 표시
    assert "[근거 문서]" in fake.chat.completions.calls[0]["messages"][0]["content"]
    total = sqlite3.connect(C.DB_PATH).execute(
        "SELECT SUM(prompt_tokens + completion_tokens) FROM usage_log WHERE backend='chat'").fetchone()[0]
    assert total == 150


def test_model_handoff_signal_replaces_answer(monkeypatch):
    at, _ = _with_key(monkeypatch, "[[HANDOFF]]")
    ask(at, "오토캐드 라이센스 등록하는 법 좀")
    assert "안내 자료만으로는" in texts(at) and "[[HANDOFF]]" not in texts(at)


def test_llm_failure_degrades_to_document_answer(monkeypatch):
    at, fake = _with_key(monkeypatch, "unused")

    def down(**params):
        import httpx2 as httpx
        raise __import__("openai").APIConnectionError(request=httpx.Request("POST", "https://x"))

    fake.chat.completions.create = down
    ask(at, "오토캐드 라이센스 등록하는 법 좀")
    assert "AutoCAD · 라이선스 활성화" in texts(at)                     # 오류 안내만 하고 끝내지 않는다
    assert any("연결하지 못했습니다" in i.value for i in at.info)


def test_quota_exhausted_falls_back_without_calling_llm(monkeypatch):
    monkeypatch.setattr(C, "MAX_CALLS_PER_CONVERSATION", 1)
    at, fake = _with_key(monkeypatch, cite_first)
    ask(at, "오토캐드 라이센스 등록하는 법 좀")
    ask(at, "레빗 설치 폴더 위치 바꿔도 돼요?")
    assert len(fake.chat.completions.calls) == 1                         # 두 번째는 호출하지 않았다
    assert any("호출 한도" in i.value for i in at.info)


def test_old_turns_are_summarized_and_fed_to_next_request(monkeypatch):
    """오래된 맥락 손실 해결 확인 — 밀려난 대화가 요약되어 다음 요청의 시스템 프롬프트에 들어간다."""
    monkeypatch.setattr(C, "KEEP_RECENT_TURNS", 1)
    monkeypatch.setattr(C, "SUMMARY_BATCH_MESSAGES", 2)
    at, fake = _with_key(monkeypatch, cite_first)
    ask(at, "오토캐드 2026 설치 방법 알려주세요")
    ask(at, "라이선스 인증은요?")                 # 첫 턴이 윈도우 밖으로 밀려나 요약된다
    ask(at, "설치 위치 바꿔도 되나요?")
    calls = fake.chat.completions.calls
    summary_calls = [c for c in calls if "요약을 갱신" in c["messages"][0]["content"]]
    assert summary_calls and summary_calls[0]["model"] == C.SUMMARY_MODEL
    last_answer = [c for c in calls if "[근거 문서]" in c["messages"][0]["content"]][-1]
    assert "[이전 대화 요약]" in last_answer["messages"][0]["content"]
    assert "AutoCAD 2026" in last_answer["messages"][0]["content"]
    assert len(last_answer["messages"]) == 1 + 2 + 1    # 시스템 + 최근 1턴(2개) + 이번 질문
