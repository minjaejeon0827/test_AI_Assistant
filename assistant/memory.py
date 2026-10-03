"""
assistant/memory.py
대화 이력 관리 — 최근 N턴은 원문, 그 이전은 요약으로 압축한다.

★ 윈도우만 쓰던 이전 방식의 문제
  최근 10턴만 보내면 요청 크기 상한은 고정되지만, 11턴 전에 말한 제품·버전·진행 단계를 잊는다.
  기술지원 대화는 "처음에 말한 제품"이 끝까지 중요하다.

★ 요약은 증분으로 갱신한다
  매 턴 전체를 다시 요약하면 요약 비용이 대화 길이에 비례해 늘어난다(윈도우 도입 이유와 같은 문제).
  → [이전 요약 + 이번에 밀려난 메시지]만 요약 모델에 보낸다. 밀려난 메시지가 SUMMARY_BATCH_MESSAGES
    개 쌓였을 때만 호출하므로 호출 수도 대화 길이의 1/4 수준이다.

★ 요약은 근거 문서가 아니다
  요약은 LLM 모델이 만든 2차 텍스트라 틀릴 수 있다. 시스템 프롬프트에 "맥락 참고용, 인용 금지"로 넣는다.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from . import config as C

logger = logging.getLogger(__name__)

_CITATION = re.compile(r"\s*\[KB-[A-Z0-9]+-\d{2}\]")

SUMMARY_SYSTEM = f"""기술지원 상담 대화의 요약을 갱신합니다.
[이전 요약]과 [추가 대화]를 합쳐 {C.SUMMARY_MAX_CHARS}자 이내 한국어로 다시 씁니다.

포함할 것: 고객이 쓰는 제품·버전, 진행한 단계, 해결된 문제, 아직 남은 문제
지킬 것: 대화에 나온 사실만 씁니다. 추측·조언·인사말은 쓰지 않습니다. 개조식 문장으로 씁니다."""


def usable(messages: list[dict]) -> list[dict]:
    """요청에 넣을 수 있는 메시지 — 오류 안내는 모델이 대화 내용으로 오인하지 않도록 뺀다."""
    return [m for m in messages if not m.get("error") and m.get("role") in ("user", "assistant")]


def split_history(messages: list[dict], keep_turns: int | None = None) -> tuple[list[dict], list[dict]]:
    """(요약 대상, 원문 유지) 로 나눈다. 유지 턴 수는 호출 시점의 설정을 읽는다."""
    msgs = usable(messages)
    keep = (C.KEEP_RECENT_TURNS if keep_turns is None else keep_turns) * 2
    if len(msgs) <= keep:
        return [], msgs
    return msgs[:-keep], msgs[-keep:]


def to_request(messages: list[dict]) -> list[dict]:
    """모델에 보낼 형식. 이전 답변의 근거 ID 태그는 지운다.

    지우지 않으면 모델이 이번 검색에 없는 예전 ID 를 다시 인용해
    '근거에 없는 인용'으로 판정되고 멀쩡한 답변이 상담원 연결로 넘어간다.
    """
    return [{"role": m["role"], "content": _CITATION.sub("", m["content"])} for m in messages]


def needs_update(older_count: int, summarized_count: int) -> bool:
    return older_count - summarized_count >= C.SUMMARY_BATCH_MESSAGES


def update_summary(previous: str, new_messages: list[dict],
                   complete_fn: Callable[[str, list[dict]], str]) -> str:
    """요약을 갱신한다. 실패하면 이전 요약을 그대로 돌려준다 — 요약 실패로 답변이 막히면 안 된다."""
    lines = "\n".join(f"{'고객' if m['role'] == 'user' else '상담'}: {m['content'][:500]}"
                      for m in to_request(new_messages))
    prompt = f"[이전 요약]\n{previous or '(없음)'}\n\n[추가 대화]\n{lines}"
    try:
        text = complete_fn(SUMMARY_SYSTEM, [{"role": "user", "content": prompt}]).strip()
    except Exception as exc:  # noqa: BLE001 - 요약은 부가 기능이다
        logger.warning("요약 갱신 실패 → 이전 요약 유지: %s", type(exc).__name__)
        return previous
    return clip(text) or previous


def clip(text: str, limit: int | None = None) -> str:
    limit = C.SUMMARY_MAX_CHARS if limit is None else limit
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def topic_trail(messages: list[dict], limit: int = 8) -> str:
    """키 없는 모드의 결정적 요약 — 지금까지 안내한 문서 제목을 순서대로 모은다.

    LLM 요약을 쓸 수 없어도 "어떤 제품의 어느 단계까지 안내했는지"는 남긴다.
    """
    seen: list[str] = []
    for m in messages:
        for title in (m.get("meta") or {}).get("sources", []):
            if title not in seen:
                seen.append(title)
    return " → ".join(seen[-limit:])
