"""
assistant/retriever.py
지식 베이스 검색기 — 어휘 · 벡터 하이브리드

★ 두 가지 모드를 갖는다
  hybrid — 사전 임베딩된 vectors.npy + 질문 임베딩 + 어휘 점수
  local  — 어휘 점수만. 키가 없거나 인덱스가 낡았거나 임베딩 호출이 실패하면 여기로 내려간다.
  키가 없는 채용 담당자도 데모를 돌려볼 수 있어야 하고, CI 에서도 평가가 돌아야 한다.

★ 어휘 점수 = IDF 가중 커버리지  (질문의 정보량 중 문서가 덮는 비율, 0~1)
  · Jaccard 는 긴 문서에서 값이 0.01~0.05 로 눌려 임계값을 잡을 수 없었다.
  · "설치" 처럼 모든 청크에 나오는 bigram 은 IDF 가 0 에 가까워 점수에 거의 기여하지 않는다.
  · 문서에 없는 bigram("포토샵", "1603")은 IDF 가 가장 커서 커버리지를 크게 깎는다.
    → 지식 베이스 밖 질문이 자연스럽게 낮은 점수를 받는다. 이것이 상담원 연결 게이트의 근거다.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Callable

from . import config as C
from .kb import Chunk, KnowledgeBase
from .text import SEP, bigrams, canonical, strip_suffix

logger = logging.getLogger(__name__)

Embedder = Callable[[str], "list[float] | None"]


@dataclass
class Hit:
    chunk: Chunk
    lexical: float
    vector: float | None
    score: float


@dataclass
class SearchResult:
    hits: list[Hit]
    mode: str                  # "hybrid" | "local"
    gate_lexical: float        # 1위 청크 어휘 커버리지
    gate_vector: float | None  # 1위 청크 코사인 유사도
    empty_query: bool          # 제품명·어미 지우고 나니 남은 내용 없음

    @property
    def top(self) -> Hit | None:
        return self.hits[0] if self.hits else None

    def passes_gate(self, anchored: bool) -> bool:
        """근거 게이트 — 어휘 근거 또는 의미 근거 중 하나라도 충분한가.

        anchored: 질문(또는 직전 대화)에 지원 제품명이 있는가.
          제품명이 있으면 도메인이 보장되므로 "그 제품의 어느 주제인가"만 확인하면 된다.
          없으면 도메인부터 불확실하므로 더 높은 어휘 근거를 요구한다.
        """
        if self.empty_query:
            return anchored   # "오토캐드요" 처럼 제품만 말한 경우 → 호출부가 되묻기로 처리한다
        threshold = C.HANDOFF_MIN_LEXICAL if anchored else C.HANDOFF_MIN_LEXICAL_UNANCHORED
        if self.gate_lexical >= threshold:
            return True
        return self.gate_vector is not None and self.gate_vector >= C.HANDOFF_MIN_VECTOR


class Retriever:
    def __init__(self, kb: KnowledgeBase, embedder: Embedder | None = None) -> None:
        self.kb = kb
        self.embedder = embedder

        self._grams = {c.id: bigrams(canonical(c.embed_text)) for c in kb.chunks}
        # 예상 질문만의 bigram — 사용자 표현과 직접 닮은 청크를 동점에서 앞세운다
        self._qgrams = {c.id: bigrams(canonical(" ".join(c.questions))) for c in kb.chunks}
        n = len(kb.chunks)
        df: dict[str, int] = {}
        for grams in self._grams.values():
            for g in grams:
                df[g] = df.get(g, 0) + 1
        self._idf_seen = {g: math.log((n + 1) / (d + 0.5)) for g, d in df.items()}
        # 문서에 한 번도 없는 bigram 은 "한 번 나온 bigram"과 같은 정보량으로 본다(상한).
        self._idf_unseen = math.log((n + 1) / (C.UNSEEN_IDF_AS_DF + 0.5))

        self._vectors = None
        self._vector_ids: list[str] = []
        self._load_vectors()

    # region 벡터 인덱스

    def _load_vectors(self) -> None:
        if not C.VECTOR_ENABLED or not (C.VECTORS_NPY.exists() and C.INDEX_META.exists()):
            return
        try:
            import numpy as np

            meta = json.loads(C.INDEX_META.read_text(encoding="utf-8"))
            if meta.get("source_sha256") != self.kb.source_sha256:
                logger.warning("kb_index 가 지식 베이스보다 낡았다 → local 모드. build_index.py 재실행 필요")
                return
            if meta.get("model") != C.EMBED_MODEL:
                logger.warning("인덱스 임베딩 모델(%s) ≠ 설정(%s) → local 모드", meta.get("model"), C.EMBED_MODEL)
                return
            self._vectors = np.load(C.VECTORS_NPY)
            self._vector_ids = meta["ids"]
        except Exception as exc:  # noqa: BLE001 - 인덱스 문제로 서비스가 멈추면 안 된다
            logger.warning("벡터 인덱스 로딩 실패 → local 모드: %s", exc)
            self._vectors = None

    @property
    def mode(self) -> str:
        return "hybrid" if self._vectors is not None and self.embedder is not None else "local"

    def _vector_scores(self, question: str) -> dict[str, float] | None:
        if self.mode != "hybrid":
            return None
        try:
            import numpy as np

            emb = self.embedder(question) if self.embedder else None
            if emb is None:
                return None
            q = np.asarray(emb, dtype="float32")
            q /= float(np.linalg.norm(q)) or 1.0
            sims = self._vectors @ q
            # 코사인을 그대로 쓴다(음수만 0 으로). (v+1)/2 로 옮기면 무관한 문장도 0.55 이상이 되어
            # 임계값의 의미가 흐려진다.
            return {cid: max(0.0, float(s)) for cid, s in zip(self._vector_ids, sims)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("벡터 검색 실패 → 어휘 점수만 사용: %s", type(exc).__name__)
            return None

    # endregion 벡터 인덱스

    # region 어휘 점수

    def _idf(self, gram: str) -> float:
        return self._idf_seen.get(gram, self._idf_unseen)

    def query_words(self, residual: str) -> list[set[str]]:
        """질문을 단어별 bigram 묶음으로 나눈다. 조사·어미를 떼고, 끝말 bigram 은 버린다."""
        words: list[set[str]] = []
        for w in residual.split(SEP):
            grams = {g for g in bigrams(strip_suffix(w)) if g not in C.QUERY_STOP_BIGRAMS}
            if grams:
                words.append(grams)
        return words

    def is_generic(self, words: list[set[str]]) -> bool:
        """모든 단어가 대부분의 청크에 나오는 일반 용어인가("설치", "파일")."""
        return bool(words) and all(
            sum(self._idf(g) for g in grams) / len(grams) < C.GENERIC_WORD_MAX_IDF for grams in words
        )

    def coverage(self, words: list[set[str]], chunk_id: str, *, questions_only: bool = False) -> float:
        """단어 단위 소프트 커버리지 (0~1).

        단어마다  일치율 = 문서에 있는 bigram 비율,  가중치 = bigram IDF 평균
        → Σ(가중치 × 일치율) / Σ가중치

        ★ bigram 이 아니라 단어가 한 표씩 갖는다.
          '라이선스'(bigram 3개)와 '등록'(1개)이 같은 무게를 갖게 하려는 것이다.
          bigram 단위로 세면 긴 단어일수록 점수를 과하게 좌우한다.
        """
        doc = (self._qgrams if questions_only else self._grams)[chunk_id]
        num = den = 0.0
        for grams in words:
            weight = sum(self._idf(g) for g in grams) / len(grams)
            num += weight * len(grams & doc) / len(grams)
            den += weight
        return num / den if den > 0 else 0.0

    # endregion 어휘 점수

    def search(self, question: str, residual: str, candidates: list[Chunk], k: int | None = None) -> SearchResult:
        """후보 청크 안에서 상위 k개를 고른다. 어떤 경우에도 예외를 던지지 않는다."""
        k = k or C.TOP_K
        words = self.query_words(residual)
        empty = not words
        vec = self._vector_scores(question) if candidates else None
        mode = "hybrid" if vec is not None else "local"

        scored: list[tuple[float, Hit]] = []
        for c in candidates:
            lex = self.coverage(words, c.id) if words else 0.0
            v = vec.get(c.id) if vec else None
            score = lex if v is None else C.HYBRID_W_LEXICAL * lex + C.HYBRID_W_VECTOR * v
            tie = self.coverage(words, c.id, questions_only=True) if words else 0.0
            scored.append((tie, Hit(c, round(lex, 4), None if v is None else round(v, 4), round(score, 4))))

        # 정렬 키: ① 점수  ② 예상 질문과의 일치(동점 깨기)  ③ 지식 베이스 순서
        #   ③ "오토캐드 설치 방법"처럼 구분 없는 질문은 첫 단계(설치 준비)부터 안내하는 것이 맞다.
        scored.sort(key=lambda t: (-t[1].score, -t[0], t[1].chunk.order))
        scored = [h for _, h in scored]
        hits = scored[:k]
        return SearchResult(
            hits=hits,
            mode=mode,
            gate_lexical=hits[0].lexical if hits else 0.0,
            gate_vector=hits[0].vector if hits else None,
            empty_query=empty,
        )
