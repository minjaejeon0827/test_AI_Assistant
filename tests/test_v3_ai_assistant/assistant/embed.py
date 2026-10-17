"""
assistant/embed.py
질문 임베딩 — 실패하면 None 을 돌려 검색기가 어휘 점수만으로 동작하게 한다.

★ 성공한 결과만 캐시한다
  파이프라인은 같은 질문으로 검색을 두 번 할 수 있다(버전 때문에 답할 수 없는 경우를 구분할 때).
  캐시가 없으면 임베딩 API 를 두 번 부른다.
  실패(None)까지 캐시하면 일시 장애 한 번으로 그 질문은 세션 내내 벡터 검색을 못 쓴다.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Callable

from . import config as C

logger = logging.getLogger(__name__)

_CACHE_SIZE = 256


def make_embedder(api_key: str, client=None) -> Callable[[str], "list[float] | None"]:
    """질문 1건을 임베딩하는 함수를 만든다. client 는 테스트에서 가짜 객체를 넣기 위한 것이다."""
    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=C.EMBED_TIMEOUT, max_retries=1)
    cache: OrderedDict[str, list[float]] = OrderedDict()

    def embed(text: str) -> list[float] | None:
        key = text[:2000]
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        try:
            resp = client.embeddings.create(model=C.EMBED_MODEL, input=key)
            vector = list(resp.data[0].embedding)
        except Exception as exc:  # noqa: BLE001 - 임베딩 실패로 답변이 막히면 안 된다
            logger.warning("질문 임베딩 실패 → 어휘 점수만 사용: %s", type(exc).__name__)
            return None
        cache[key] = vector
        if len(cache) > _CACHE_SIZE:
            cache.popitem(last=False)
        return vector

    return embed
