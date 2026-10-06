"""
app.py
* 테스트용 기술지원 AI Assistant (Streamlit + OpenAI + RAG)

변경 이력
- 2026.09  openai 0.28.1 → 3.x SDK 마이그레이션
           응답 스트리밍, 시스템 프롬프트, 예외 처리, 대화 이력 윈도우 적용
- 2026.09  Autodesk 제품 설치 가이드 8종 지식 베이스 RAG 연결
           근거 게이트 · 상담원 연결 전환, 이력 요약 압축, SQLite 대화 저장, 사용량 상한,
           Chat Completions / Responses API 어댑터, 키 없이 동작하는 추출형 답변 모드

패키지 설치
- pip install -r requirements.txt
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

import os
import tempfile
import time
import uuid

import streamlit as st

from assistant import config as C
from assistant import llm, memory
from assistant.embed import make_embedder
from assistant.kb import KBUnavailable, load_kb, load_links
from assistant.pipeline import (
    ConversationState,
    Decision,
    build_prompt,
    decide,
    extractive_answer,
    handoff_message,
    postcheck,
    pretty_citations,
)
from assistant.retriever import Retriever
from assistant.store import Store
from assistant.usage import Quota

# region 설정 상수

APP_TITLE = "[테스트] 상상플렉스 AI Assistant"
GREETING = "안녕하세요. Autodesk 제품 설치 관련하여 어떤 도움이 필요하신가요?"

# endregion 설정 상수

# region 공유 자원 (비밀 정보 없음 · 읽기 전용 → cache_resource 로 전역 공유해도 안전하다)


@st.cache_resource(show_spinner=False)
def get_kb():
    """지식 베이스. 읽지 못하면 (None, 사유) — 근거 없이 답변하지 않도록 모든 질문을 상담원으로 넘긴다."""
    try:
        return load_kb(), ""
    except KBUnavailable as exc:
        return None, str(exc)


@st.cache_resource(show_spinner=False)
def get_links() -> dict[str, str]:
    return load_links()


@st.cache_resource(show_spinner=False)
def get_store() -> tuple[Store | None, str]:
    """대화 저장소. 설정 경로 → 임시 폴더 순으로 시도하고, 둘 다 실패하면 저장 없이 동작한다."""
    for path in (C.DB_PATH, os.path.join(tempfile.gettempdir(), "assistant.db")):
        try:
            return Store(path), str(path)
        except Exception:  # noqa: BLE001 - 읽기 전용 파일 시스템 등
            continue
    return None, ""


# endregion 공유 자원

# region 세션 자원 (API 키가 얽힌 객체는 세션 단위로만 보관한다)


def get_client(api_key: str):
    """키가 바뀔 때만 클라이언트를 새로 만든다. 전역 캐시에 두면 다른 사용자에게 재사용될 수 있다."""
    if st.session_state.get("_client_key") != api_key:
        st.session_state["_client"] = llm.make_client(api_key)
        st.session_state["_client_key"] = api_key
    return st.session_state["_client"]


def get_retriever(kb, api_key: str) -> Retriever:
    """키가 있고 벡터 인덱스가 있으면 하이브리드, 아니면 어휘 검색만 쓴다."""
    tag = "vector" if api_key and C.VECTORS_NPY.exists() else "local"
    if st.session_state.get("_retriever_tag") != (tag, api_key):
        embedder = make_embedder(api_key) if tag == "vector" else None
        st.session_state["_retriever"] = Retriever(kb, embedder)
        st.session_state["_retriever_tag"] = (tag, api_key)
    return st.session_state["_retriever"]


def resolve_api_key(sidebar_key: str) -> tuple[str, str]:
    """(키, 출처). 사이드바 입력 > 배포자 키(.env · 환경 변수 · secrets)."""
    if sidebar_key:
        return sidebar_key, "사용자 키"
    server = os.getenv("OPENAI_API_KEY", "")
    if not server:
        try:
            server = st.secrets.get("OPENAI_API_KEY", "")
        except Exception:  # noqa: BLE001 - secrets.toml 이 없으면 예외가 난다
            server = ""
    return (server, "배포자 키") if server else ("", "")


# endregion 세션 자원

# region 대화 상태


def load_conversation(store: Store | None) -> None:
    """URL 의 대화 ID 로 이전 대화를 복원한다. 새로고침해도 대화가 사라지지 않는다."""
    if "messages" in st.session_state:
        return
    conv_id = st.query_params.get("c")
    if store and conv_id and store.exists(conv_id):
        saved = store.load_state(conv_id)
        st.session_state.update(
            conv_id=conv_id,
            messages=store.load_messages(conv_id),
            conv_state=ConversationState.from_dict(saved).to_dict(),
            summary=saved.get("summary", ""),
            summarized_count=saved.get("summarized_count", 0),
        )
    else:
        # 대화 ID 는 첫 질문 때 만든다. 방문만 한 사람의 빈 대화가 DB 에 쌓이지 않게 한다.
        st.session_state.update(conv_id=None, messages=[], conv_state={}, summary="", summarized_count=0)
    st.session_state.setdefault("usage_fallback", [0, 0])


def ensure_conversation(store: Store | None) -> str:
    if not st.session_state["conv_id"]:
        conv_id = store.create_conversation() if store else uuid.uuid4().hex
        st.session_state["conv_id"] = conv_id
        st.query_params["c"] = conv_id
    return st.session_state["conv_id"]


def reset_conversation(store: Store | None, delete: bool) -> None:
    if delete and store and st.session_state.get("conv_id"):
        store.delete_conversation(st.session_state["conv_id"])
    for k in ("conv_id", "messages", "conv_state", "summary", "summarized_count", "usage_fallback"):
        st.session_state.pop(k, None)
    st.query_params.clear()


def add_message(store: Store | None, role: str, content: str, *, error: bool = False,
                meta: dict | None = None) -> None:
    st.session_state["messages"].append({"role": role, "content": content, "error": error, "meta": meta or {}})
    if store:
        store.append_message(st.session_state["conv_id"], role, content, error=error, meta=meta)


def save_state(store: Store | None) -> None:
    if store:
        store.save_state(st.session_state["conv_id"], summary=st.session_state["summary"],
                         summarized_count=st.session_state["summarized_count"],
                         state=st.session_state["conv_state"])


def current_quota(store: Store | None) -> Quota:
    conv_id = st.session_state.get("conv_id")
    if store and conv_id:
        calls, tokens = store.usage_totals(conv_id)
        return Quota(calls, tokens, store.daily_calls())
    calls, tokens = st.session_state.get("usage_fallback", [0, 0])
    return Quota(calls, tokens)


def log_usage(store: Store | None, **row) -> None:
    if store:
        store.log_usage(st.session_state["conv_id"], **row)
    elif row.get("backend") in C.LLM_BACKENDS:
        fb = st.session_state["usage_fallback"]
        fb[0] += 1
        fb[1] += row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)


# endregion 대화 상태

# region 답변 처리


def render_sources(meta: dict) -> None:
    if meta.get("caption"):
        st.caption(meta["caption"])
    if meta.get("sources"):
        with st.expander("📎 근거 문서", expanded=False):
            for title in meta["sources"]:
                st.markdown(f"- {title}")


def reply_fixed(ctx: dict, prompt: str, d: Decision) -> None:
    """LLM 없이 끝나는 판정 — 차단 · 형식 오류 · 인사 · 되묻기 · 상담원 연결."""
    store = ctx["store"]
    text = d.message
    if d.kind == "handoff" and store:
        store.record_handoff(st.session_state["conv_id"], d.reason, prompt)
        text += "\n\n지금까지의 대화는 상담원이 이어서 확인할 수 있도록 저장되었습니다."
    st.markdown(text)
    meta = {"kind": d.kind, "reason": d.reason}
    add_message(store, "assistant", text, meta=meta)
    log_usage(store, kind="answer", backend="none", decision=d.kind, reason=d.reason,
              retrieval_mode=d.mode, top_score=d.gate_lexical, latency_ms=d.latency_ms)


def reply_extractive(ctx: dict, d: Decision, notice: str = "") -> None:
    """근거 문서를 그대로 안내한다 — 키 없음 · 사용량 한도 · LLM 장애 시."""
    text, _ = extractive_answer(d, ctx["links"])
    if notice:
        st.info(notice)
    st.markdown(text)
    meta = {"kind": "answer", "mode": "extractive", "sources": [d.top.chunk.title],
            "cited": [d.top.chunk.id], "caption": "🔎 검색 결과 안내 (AI 요약 미사용)"}
    render_sources(meta)
    add_message(ctx["store"], "assistant", text, meta=meta)
    log_usage(ctx["store"], kind="answer", backend="extractive", decision="answer",
              retrieval_mode=d.mode, top_score=d.gate_lexical, latency_ms=d.latency_ms, grounded=True)


def reply_generative(ctx: dict, prompt: str, d: Decision) -> None:
    """근거 문서를 LLM 에 넣어 답변을 생성하고, 사후 검증을 통과한 것만 남긴다."""
    store, client, backend, model = ctx["store"], ctx["client"], ctx["backend"], ctx["model"]
    _, recent = memory.split_history(st.session_state["messages"][:-1])   # 방금 넣은 질문은 뺀다
    p = build_prompt(prompt, d, memory.to_request(recent), st.session_state["summary"], ctx["links"])

    state = llm.StreamState()
    placeholder = st.empty()
    t0 = time.perf_counter()
    try:
        with placeholder.container():
            st.write_stream(llm.stream_answer(client, backend=backend, model=model, system=p.system,
                                              messages=p.messages, temperature=ctx["temperature"],
                                              state=state))
    except Exception as exc:  # noqa: BLE001 - 어떤 예외에도 화면이 깨지지 않게 한다
        placeholder.empty()
        log_usage(store, kind="answer", backend=backend, model=model, decision="error",
                  reason=type(exc).__name__, latency_ms=int((time.perf_counter() - t0) * 1000))
        # ★ degrade — 오류 안내만 하고 끝내지 않는다. 근거 문서가 이미 있으니 그대로 안내한다.
        reply_extractive(ctx, d, notice=f"{llm.to_user_message(exc)} 검색 결과로 대신 안내합니다.")
        return
    latency_ms = int((time.perf_counter() - t0) * 1000)

    pc = postcheck(state.text, d, p.allowed_sources, handoff_signal=state.handoff)
    if pc.kind == "answer":
        shown = pretty_citations(pc.text)
        with placeholder.container():
            st.markdown(shown)
        if pc.removed:
            st.caption(f"근거 문서에 없는 링크·연락처 {len(pc.removed)}건을 삭제했습니다.")
        titles = [h.chunk.title for h in d.hits if h.chunk.id in pc.cited] or [h.chunk.title for h in d.hits]
        meta = {"kind": "answer", "mode": "generative", "sources": titles, "cited": pc.cited,
                "caption": f"🤖 {model} · {backend} · {latency_ms / 1000:.1f}초"}
    else:
        # 모델이 전환 신호를 냈거나, 검색하지 않은 문서를 인용했다 → 스트리밍한 글을 지우고 상담원으로
        shown = pc.text
        if pc.kind == "handoff" and store:
            store.record_handoff(st.session_state["conv_id"], pc.reason, prompt)
            shown += "\n\n지금까지의 대화는 상담원이 이어서 확인할 수 있도록 저장되었습니다."
        with placeholder.container():
            st.markdown(shown)
        meta = {"kind": pc.kind, "reason": pc.reason}
    render_sources(meta)
    add_message(store, "assistant", shown, meta=meta)
    log_usage(store, kind="answer", backend=backend, model=model,
              prompt_tokens=state.usage.prompt_tokens, completion_tokens=state.usage.completion_tokens,
              estimated=state.usage.estimated, latency_ms=latency_ms, decision=pc.kind, reason=pc.reason,
              retrieval_mode=d.mode, top_score=d.gate_lexical, grounded=pc.grounded)
    maybe_update_summary(ctx)


def maybe_update_summary(ctx: dict) -> None:
    """최근 N턴 밖으로 밀려난 메시지가 쌓이면 요약을 갱신한다."""
    older, _ = memory.split_history(st.session_state["messages"])
    done = st.session_state["summarized_count"]
    if not memory.needs_update(len(older), done):
        return

    def complete_fn(system: str, messages: list[dict]) -> str:
        t0 = time.perf_counter()
        text, usage = llm.complete(ctx["client"], backend=ctx["backend"], model=C.SUMMARY_MODEL,
                                   system=system, messages=messages)
        log_usage(ctx["store"], kind="summary", backend=ctx["backend"], model=C.SUMMARY_MODEL,
                  prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                  estimated=usage.estimated, latency_ms=int((time.perf_counter() - t0) * 1000))
        return text

    st.session_state["summary"] = memory.update_summary(st.session_state["summary"], older[done:], complete_fn)
    st.session_state["summarized_count"] = len(older)


def handle_prompt(prompt: str, ctx: dict) -> None:
    store = ctx["store"]
    ensure_conversation(store)
    add_message(store, "user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            if ctx["kb"] is None:
                d = Decision("handoff", "kb_unavailable", handoff_message("kb_unavailable"))
                reply_fixed(ctx, prompt, d)
                return
            state = ConversationState.from_dict(st.session_state["conv_state"])
            d, new_state = decide(prompt, state, ctx["kb"], ctx["retriever"])
            st.session_state["conv_state"] = new_state.to_dict()

            if d.kind != "answer":
                reply_fixed(ctx, prompt, d)
            elif ctx["client"] is None:
                reply_extractive(ctx, d)
            elif (quota := current_quota(store)).exhausted:
                reply_extractive(ctx, d, notice=f"{quota.reason}에 도달해 AI 요약 없이 검색 결과로 안내합니다.")
            else:
                reply_generative(ctx, prompt, d)
        except Exception as exc:  # noqa: BLE001
            msg = llm.to_user_message(exc)
            st.error(msg)
            add_message(store, "assistant", msg, error=True)   # 화면엔 남기고 다음 요청에서는 뺀다
        finally:
            if st.session_state.get("conv_id"):
                save_state(store)


# endregion 답변 처리

# region 화면 구성


def render_settings() -> tuple[str, str, str, str, float]:
    with st.sidebar:
        st.subheader("설정")
        # 공개 저장소이므로 키는 화면 입력으로 받는다. 세션 메모리에만 있고 DB 에도 저장하지 않는다.
        sidebar_key = st.text_input("OpenAI API Key (선택)", type="password",
                                    help="없으면 검색 결과를 그대로 안내하는 추출형 모드로 동작합니다.").strip()
        api_key, source = resolve_api_key(sidebar_key)
        model = st.selectbox("모델", list(C.CHAT_MODELS), index=list(C.CHAT_MODELS).index(C.DEFAULT_MODEL),
                             format_func=lambda m: f"{m} — {C.CHAT_MODELS[m].split(' — ')[0]}",
                             disabled=not api_key)
        with st.expander("고급 설정"):
            backend = st.radio("LLM 호출 방식", C.LLM_BACKENDS, index=C.LLM_BACKENDS.index(C.LLM_BACKEND),
                               format_func={"chat": "Chat Completions", "responses": "Responses API"}.get,
                               horizontal=True, disabled=not api_key)
            temperature = st.slider("temperature", 0.0, 1.0, C.DEFAULT_TEMPERATURE, 0.1, disabled=not api_key)
    return api_key, source, model, backend, temperature


def render_status(ctx: dict, key_source: str, kb_error: str, store_path: str) -> None:
    store = ctx["store"]
    with st.sidebar:
        st.divider()
        st.subheader("상태")
        if kb_error:
            st.error("지식 베이스를 불러오지 못해 모든 문의를 상담원에게 연결합니다.")
        else:
            kb = ctx["kb"]
            mode = "하이브리드 (어휘 + 벡터)" if ctx["retriever"].mode == "hybrid" else "어휘 검색"
            st.caption(f"📚 지식 베이스 {len(kb.chunks)}개 문서 · 제품 {len(kb.products)}종 · {mode}")
        if ctx["client"]:
            st.caption(f"🤖 생성형 답변 · {key_source}")
            q = current_quota(store)
            st.progress(q.call_ratio, text=f"호출 {q.calls} / {q.max_calls}회")
            st.progress(q.token_ratio, text=f"토큰 {q.tokens:,} / {q.max_tokens:,}")
        else:
            st.caption("🔎 추출형 답변 (API 키 없음) — 근거 문서를 그대로 안내합니다")

        summary = st.session_state["summary"] or memory.topic_trail(st.session_state["messages"])
        if summary:
            with st.expander("이전 대화 요약"):
                st.write(summary)

        if st.session_state.get("conv_id"):
            st.caption(f"대화 ID `{st.session_state['conv_id'][:8]}…` — 주소를 저장하면 이어서 볼 수 있습니다")
        if not store:
            st.warning("저장소를 쓸 수 없어 새로고침하면 대화가 사라집니다.")
        c1, c2 = st.columns(2)
        if c1.button("새 대화", use_container_width=True):
            reset_conversation(store, delete=False)
            st.rerun()
        if c2.button("대화 삭제", use_container_width=True, disabled=not st.session_state.get("conv_id")):
            reset_conversation(store, delete=True)
            st.rerun()


def render_history() -> None:
    with st.chat_message("assistant"):
        st.markdown(GREETING)
    for m in st.session_state["messages"]:
        with st.chat_message(m["role"]):
            text = m["content"]
            if m["role"] == "assistant" and (m.get("meta") or {}).get("mode") == "generative":
                text = pretty_citations(text)
            if m.get("error"):
                st.error(text)
            else:
                st.markdown(text)
            if m["role"] == "assistant":
                render_sources(m.get("meta") or {})


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🛠️")
    st.title(APP_TITLE)

    kb, kb_error = get_kb()
    store, store_path = get_store()
    load_conversation(store)
    api_key, key_source, model, backend, temperature = render_settings()

    ctx = {
        "kb": kb,
        "store": store,
        "links": get_links(),
        "client": get_client(api_key) if api_key else None,
        "retriever": get_retriever(kb, api_key) if kb else None,
        "model": model,
        "backend": backend,
        "temperature": temperature,
    }

    render_history()
    prompt = st.chat_input("Autodesk 제품 설치 관련 문의를 입력해 주세요.")
    if prompt:
        handle_prompt(prompt, ctx)
    render_status(ctx, key_source, kb_error, store_path)   # 방금 쓴 사용량까지 반영해 마지막에 그린다


if __name__ == "__main__":
    main()
