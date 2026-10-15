"""
assistant/pipeline.py
질문 1건의 처리 흐름

  입력 검증 → 인사 → 제품·버전 감지 → 범위 게이트 → 검색 → 근거 게이트 → 답변 → 사후 검증
    │         │                     │                 │           │        │
  blocked  smalltalk            handoff(미지원  handoff(근거 부족) 생성형   handoff
  invalid                       제품·버전)      clarify(되묻기)    추출형   (전환 신호·인용 오류)

★ decide() 는 LLM 을 부르지 않는다
  그래서 평가 · 테스트가 키 없이 결정적으로 돈다(재현성 제1요건).
  LLM 은 decide() 가 "답변해도 된다"고 판정한 뒤에만 호출된다.

★ 상담원 연결은 실패가 아니라 안전장치다
  기술지원에서 잘못된 제품 설치 안내는 재설치 · 라이선스 오류 같은 실제 피해로 이어진다.
  답을 모르는 질문을 상담원에게 넘기는 것이 잘못된 답을 하는 것보다 낫다.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from . import config as C
from . import guard
from .kb import Chunk, KnowledgeBase, detect, render_links
from .llm import HANDOFF_TOKEN
from .retriever import Hit, Retriever
from .text import squeeze

_CITATION = re.compile(r"\[(KB-[A-Z0-9]+-\d{2})\]")
_SMALLTALK_HEAD = ("안녕", "감사", "고마", "수고", "hello", "hi", "thank")

SYSTEM_PROMPT = """당신은 ㈜상상진화의 Autodesk 제품 설치 기술지원 담당자입니다.

반드시 지킬 규칙
1. [근거 문서]에 적힌 내용만으로 답합니다. 문서에 없는 절차 · 버전 · 파일명 · 링크 · 연락처를 만들지 않습니다.
2. 근거로 쓴 문장 끝에 근거 문서 ID 를 대괄호로 붙입니다. 형식 예: …클릭합니다. [KB-XXXX-00]
3. [근거 문서]로 답할 수 없는 질문이면 다른 말 없이 [[HANDOFF]] 만 출력합니다.
4. 질문에 제품명이 없고 제품마다 절차가 다를 수 있으면, 안내한 뒤 사용 중인 제품을 물어봅니다.
5. 한국어 존댓말로, 절차는 번호 목록으로 간결하게 씁니다.
6. [이전 대화 요약]은 맥락 참고용입니다. 근거 문서가 아니므로 인용하지 않습니다."""
# ↑ 형식 예의 ID(KB-XXXX-00)는 일부러 존재하지 않는 값이다.
#   실제 ID 를 예로 들면 모델이 문서를 읽지 않고 그 ID 를 베껴도 인용 검사를 통과한다(평가 오염).

_HANDOFF_HEAD = {
    "unsupported_product": "문의하신 제품은 현재 안내 자료가 없는 제품입니다.",
    "unsupported_version": "문의하신 버전은 안내 자료가 없는 버전입니다.",
    "low_relevance": "문의하신 내용과 일치하는 설치 안내 자료를 찾지 못했습니다.",
    "kb_unavailable": "지금은 설치 안내 자료를 불러올 수 없습니다.",
    "llm_declined": "안내 자료만으로는 정확히 답변드리기 어려운 문의입니다.",
    "invalid_citation": "답변의 근거를 확인하지 못해 안내를 중단했습니다.",
}


@dataclass
class ConversationState:
    """검색 맥락. "라이선스는요?" 처럼 제품을 생략한 후속 질문을 직전 제품으로 잇는다."""
    active_product: str | None = None
    active_versions: list[int] = field(default_factory=list)
    last_residual: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> ConversationState:
        d = d or {}
        return cls(d.get("active_product"), list(d.get("active_versions") or []), d.get("last_residual", ""))


@dataclass
class Decision:
    kind: str                           # answer | handoff | clarify | smalltalk | blocked | invalid
    reason: str = ""
    message: str = ""                   # answer 가 아닌 경우 사용자에게 보여줄 고정 문구
    hits: list[Hit] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    versions: list[int] = field(default_factory=list)
    anchored: bool = False              # 제품이 특정됐는가(질문 또는 직전 대화)
    contextual: bool = False            # 직전 대화의 제품을 이어받았는가
    ambiguous: bool = False             # 제품 미지정인데 여러 제품 문서가 후보다
    needs_clarify: bool = False         # 제품만 말했다 → 첫 단계를 안내하고 되묻는다
    mode: str = "local"
    gate_lexical: float = 0.0
    gate_vector: float | None = None
    latency_ms: int = 0

    @property
    def top(self) -> Hit | None:
        return self.hits[0] if self.hits else None

    @property
    def hit_ids(self) -> list[str]:
        return [h.chunk.id for h in self.hits]


# region 판정


def _is_smalltalk(question: str) -> bool:
    s = squeeze(question)
    return len(s) <= 12 and s.startswith(_SMALLTALK_HEAD)


def _smalltalk_reply(question: str) -> str:
    s = squeeze(question)
    if s.startswith(("감사", "고마", "thank", "수고")):
        return "도움이 되었다니 다행입니다. 다른 Autodesk 제품 설치 문의가 있으면 편하게 말씀해 주세요."
    return "안녕하세요. Autodesk 제품 설치와 관련해 어떤 도움이 필요하신가요?"


def handoff_message(reason: str, detail: str = "") -> str:
    head = _HANDOFF_HEAD.get(reason, _HANDOFF_HEAD["low_relevance"])
    return f"{head}{detail}\n\n정확한 안내를 위해 **{C.SUPPORT_CONTACT}**로 연결해 드리겠습니다."


def _version_detail(kb: KnowledgeBase, products: list[str]) -> str:
    parts = []
    for p in products:
        vs = sorted({v for c in kb.chunks if c.product == p for v in c.versions})
        if vs:
            parts.append(f"{p} {vs[0]}~{vs[-1]}")
    return f" (안내 가능한 버전: {', '.join(parts)})" if parts else ""


def _clarify_message(kb: KnowledgeBase, product: str | None) -> str:
    if product:
        sections = " · ".join(dict.fromkeys(c.section for c in kb.chunks if c.product == product))
        return f"{product}의 어떤 단계가 궁금하신가요? ({sections})"
    return f"어떤 Autodesk 제품 설치를 도와드릴까요? 안내 가능한 제품: {', '.join(kb.products)}"


def decide(question: str, state: ConversationState, kb: KnowledgeBase,
           retriever: Retriever) -> tuple[Decision, ConversationState]:
    """질문 1건의 처리 방향을 정한다. LLM 호출 없음."""
    t0 = time.perf_counter()

    def done(d: Decision, s: ConversationState) -> tuple[Decision, ConversationState]:
        d.latency_ms = int((time.perf_counter() - t0) * 1000)
        return d, s

    chk = guard.check_input(question)
    if chk.action != "pass":
        return done(Decision(chk.action, reason=chk.rule, message=chk.message), state)
    if _is_smalltalk(question):
        return done(Decision("smalltalk", message=_smalltalk_reply(question)), state)

    det = detect(question, kb)
    new = ConversationState(state.active_product, list(state.active_versions), state.last_residual)
    products, versions, residual = list(det.products), list(det.versions), det.residual
    contextual = False
    empty = not retriever.query_words(residual)

    if det.products:
        new.active_product, new.active_versions = det.products[0], list(det.versions)
        # "레빗은요?" — 제품만 바꿔 같은 주제를 다시 묻는 경우 직전 질문의 주제를 이어받는다
        if empty and state.last_residual and det.products[0] != state.active_product:
            residual, empty = state.last_residual, False
    elif state.active_product and not det.unsupported:
        products, contextual = [state.active_product], True
        if versions:
            new.active_versions = versions
        else:
            versions = list(state.active_versions)

    common = {"products": products, "versions": versions, "contextual": contextual}

    # ── 범위 게이트: 지식 베이스에 없는 제품 · 버전 ──────────────────────────────
    if det.unsupported and not det.products:
        return done(Decision("handoff", "unsupported_product",
                             handoff_message("unsupported_product"), **common), new)

    candidates: list[Chunk] = [c for c in kb.chunks if not products or c.product in products]
    if versions:
        in_version = [c for c in candidates if set(versions) & set(c.versions)]
        if not in_version:
            return done(Decision("handoff", "unsupported_version",
                                 handoff_message("unsupported_version", _version_detail(kb, products)),
                                 **common), new)
        candidates = in_version

    anchored = bool(products)
    common["anchored"] = anchored

    # ── 되묻기: 제품만 말했거나, 제품 없이 일반 용어만 있다 ──────────────────────
    if empty:
        if det.products:   # "오토캐드요" → 첫 단계를 안내하고 어느 단계인지 되묻는다
            first = candidates[0]
            return done(Decision("answer", hits=[Hit(first, 0.0, None, 0.0)], needs_clarify=True,
                                 **common), new)
        return done(Decision("clarify", "empty_query", _clarify_message(kb, new.active_product
                                                                         if contextual else None),
                             **common), new)
    words = retriever.query_words(residual)
    if not anchored and retriever.is_generic(words):
        return done(Decision("clarify", "no_product", _clarify_message(kb, None), **common), new)

    # ── 검색 + 근거 게이트 ─────────────────────────────────────────────────────
    res = retriever.search(question, residual, candidates)
    common.update(mode=res.mode, gate_lexical=res.gate_lexical, gate_vector=res.gate_vector)
    if not res.passes_gate(anchored):
        # 버전을 빼고 다시 찾았을 때 근거가 있다면 "주제는 있는데 그 버전 자료가 없는" 경우다.
        # 사유를 구분해야 "2024 라이선스 인증" 문의에 어느 버전 자료가 있는지 알려줄 수 있다.
        if versions and products:
            wide = retriever.search(question, residual, [c for c in kb.chunks if c.product in products])
            if wide.passes_gate(anchored):
                top = wide.top.chunk
                detail = f" ({top.title} 안내는 {top.version_label} 버전 기준입니다)"
                return done(Decision("handoff", "unsupported_version",
                                     handoff_message("unsupported_version", detail), hits=res.hits,
                                     **common), new)
        return done(Decision("handoff", "low_relevance", handoff_message("low_relevance"),
                             hits=res.hits, **common), new)

    new.last_residual = residual
    ambiguous = not anchored and len({h.chunk.product for h in res.hits}) > 1
    return done(Decision("answer", hits=res.hits, ambiguous=ambiguous, **common), new)


# endregion 판정

# region 답변 조립


@dataclass
class Prompt:
    system: str
    messages: list[dict]
    allowed_sources: str      # 출력 검증이 허용할 주소 · 번호 출처


def _evidence(hit: Hit, links: dict[str, str]) -> str:
    c = hit.chunk
    body, _ = render_links(c.content, links)
    caution = f"\n주의사항: {' / '.join(c.cautions)}" if c.cautions else ""
    return f"[{c.id}] {c.title} (적용 버전: {c.version_label})\n{body}{caution}"


def build_prompt(question: str, decision: Decision, history: list[dict], summary: str,
                 links: dict[str, str]) -> Prompt:
    evidence = "\n\n".join(_evidence(h, links) for h in decision.hits)
    notes = []
    if decision.ambiguous:
        notes.append("질문에 제품명이 없습니다. 안내 후 사용 중인 Autodesk 제품을 물어보세요.")
    if decision.contextual and decision.products:
        notes.append(f"질문에 제품명이 없어 직전 대화의 Autodesk 제품({decision.products[0]}) 기준으로 검색했습니다.")
    if decision.needs_clarify:
        notes.append("고객이 제품명만 말했습니다. 첫 단계를 안내한 뒤 어느 단계가 궁금한지 물어보세요.")

    system = f"{SYSTEM_PROMPT}\n\n[근거 문서]\n{evidence}"
    if notes:
        system += "\n\n[참고]\n" + "\n".join(f"- {n}" for n in notes)
    if summary:
        system += f"\n\n[이전 대화 요약]\n{summary}"
    messages = [*history, {"role": "user", "content": question}]
    return Prompt(system, messages, allowed_sources=f"{evidence}\n{C.SUPPORT_CONTACT}")


def extractive_answer(decision: Decision, links: dict[str, str]) -> tuple[str, str]:
    """LLM 없이 근거 문서를 그대로 안내한다. (키 없음 · 사용량 한도 · LLM 장애 시)

    ★ 문서 원문을 그대로 보여주므로 환각이 원리적으로 없다. 대신 질문에 맞춰 다듬지 못한다.
    """
    top = decision.top
    c = top.chunk
    body, _ = render_links(c.content, links)
    lines = []
    if decision.ambiguous:
        lines.append(f"제품명이 없어 **{c.product}** 기준으로 안내드립니다. "
                     "사용 중인 Autodesk 제품을 알려주시면 해당 제품 기준으로 다시 안내드리겠습니다.\n")
    elif decision.contextual:
        lines.append(f"직전에 말씀하신 **{c.product}** 기준으로 안내드립니다.\n")
    lines.append(f"**{c.title}** (적용 버전: {c.version_label})\n")
    lines.append(body)
    if c.cautions:
        lines.append("\n> ⚠️ " + " / ".join(c.cautions))
    related = [h.chunk for h in decision.hits[1:] if h.chunk.product == c.product]
    if related and not decision.needs_clarify:
        lines.append("\n함께 확인하면 좋은 안내: " + ", ".join(r.section for r in related))
    if decision.needs_clarify:
        lines.append("\n다른 단계(수동 설치파일 · 설치 진행 · 라이선스 활성화 등)가 궁금하시면 말씀해 주세요.")
    return "\n".join(lines), f"{body}\n{C.SUPPORT_CONTACT}"


@dataclass
class PostCheck:
    kind: str                  # answer | handoff | blocked
    reason: str
    text: str
    cited: list[str]
    grounded: bool
    removed: tuple[str, ...] = ()


def postcheck(text: str, decision: Decision, allowed_sources: str, *, handoff_signal: bool = False) -> PostCheck:
    """생성된 답변을 검사한다 — 전환 신호 · 인용 유효성 · 미확인 링크."""
    if handoff_signal or HANDOFF_TOKEN in text or not text.strip():
        return PostCheck("handoff", "llm_declined", handoff_message("llm_declined"), [], False)

    cited = list(dict.fromkeys(_CITATION.findall(text)))
    invalid = [c for c in cited if c not in decision.hit_ids]
    if invalid:
        # 검색하지 않은 문서를 인용했다 = 근거를 지어냈다. 설치 안내에서 가장 위험한 실패다.
        return PostCheck("handoff", "invalid_citation", handoff_message("invalid_citation"), cited, False)

    out = guard.check_output(text, allowed_sources)
    if out.action == "blocked":
        return PostCheck("blocked", "output_leak", out.text, cited, False)

    grounded = bool(cited)
    final = out.text
    if not grounded:
        final += f"\n\n> ⚠️ 근거 문서 표시가 없는 답변입니다. 정확한 내용은 {C.SUPPORT_CONTACT}로 확인해 주세요."
    return PostCheck("answer", "", final, cited, grounded, out.removed)


def pretty_citations(text: str) -> str:
    """[KB-ACAD-01] → `KB-ACAD-01` (화면 표시용)."""
    return _CITATION.sub(lambda m: f" `{m.group(1)}`", text)


# endregion 답변 조립
