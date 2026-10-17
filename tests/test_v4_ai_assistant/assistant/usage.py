"""
assistant/usage.py
사용량 상한 판정 — 저장소에서 읽은 수치를 정책과 비교한다.

★ LLM 을 부르기 "전에" 판정한다
  호출한 뒤에 한도를 넘었다고 알려주면 이미 비용이 나간 뒤다.

★ 한도에 도달해도 서비스를 멈추지 않는다
  LLM 요약 없이 검색 결과(추출형 답변)로 계속 안내한다.
  "비용을 막는 것"과 "사용자를 막는 것"은 다르다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config as C


@dataclass(frozen=True)
class Quota:
    calls: int
    tokens: int
    daily_calls: int = 0
    # 기본값을 실행 시점에 읽는다 — import 시점에 고정하면 설정을 바꿔도 반영되지 않는다
    max_calls: int = field(default_factory=lambda: C.MAX_CALLS_PER_CONVERSATION)
    max_tokens: int = field(default_factory=lambda: C.MAX_TOKENS_PER_CONVERSATION)
    max_daily_calls: int = field(default_factory=lambda: C.MAX_CALLS_PER_DAY)

    @property
    def exhausted(self) -> bool:
        return (self.calls >= self.max_calls
                or self.tokens >= self.max_tokens
                or self.daily_calls >= self.max_daily_calls)

    @property
    def reason(self) -> str:
        if self.daily_calls >= self.max_daily_calls:
            return "오늘 전체 사용량 한도"
        if self.calls >= self.max_calls:
            return f"대화당 호출 한도({self.max_calls}회)"
        if self.tokens >= self.max_tokens:
            return f"대화당 토큰 한도({self.max_tokens:,})"
        return ""

    @property
    def call_ratio(self) -> float:
        return min(1.0, self.calls / self.max_calls) if self.max_calls else 1.0

    @property
    def token_ratio(self) -> float:
        return min(1.0, self.tokens / self.max_tokens) if self.max_tokens else 1.0
