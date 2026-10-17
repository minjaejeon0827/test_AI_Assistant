"""
assistant/retriever.py
지식 베이스 검색기 — 어휘 · 벡터 하이브리드

★ 두 가지 모드를 갖는다
  hybrid — 사전 임베딩된 vectors.npy + 질문 임베딩 + 어휘 점수
  local  — 어휘 점수만. 키가 없거나 인덱스가 낡았거나 임베딩 호출이 실패하면 여기로 내려간다.
  키가 없는 채용 담당자도 데모를 돌려볼 수 있어야 하고, CI 에서도 평가가 돌아야 한다.

★ 어휘 점수 = IDF 가중 소프트 커버리지  (질문의 정보량 중 문서가 덮는 비율, 0~1)
  · "설치" 처럼 모든 청크에 나오는 말은 IDF 가 0 에 가까워 점수에 거의 기여하지 않는다.
  · 문서에 없는 말("포토샵", "1603")은 IDF 가 커서 커버리지를 크게 깎는다.
    → 지식 베이스 밖 질문이 자연스럽게 낮은 점수를 받는다. 이것이 상담원 연결 게이트의 근거다.

★ 점수의 단위(unit)는 토크나이저가 정한다
  kiwi   — 형태소 분석 후 남긴 내용어 어간 하나가 한 단위다("받으려면" → '받').
  bigram — 띄어쓰기 단위 어절의 bigram 묶음이 한 단위다(형태소 분석기가 없을 때의 폴백).
  단위마다 일치율(완전 일치 1, 아니면 bigram 부분 일치율)과 가중치(IDF)를 구해 가중 평균한다.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Callable

from . import config as C
from . import morph
from .kb import Chunk, KnowledgeBase
from .text import SEP, bigrams, canonical, canonical_words, strip_suffix

logger = logging.getLogger(__name__)

Embedder = Callable[[str], "list[float] | None"]


@dataclass(frozen=True)
class Unit:
    key: str | None           # kiwi: 어간 토큰 / bigram: None
    grams: frozenset[str]     # 부분 일치용 bigram

    @property
    def label(self) -> str:
        return self.key or "".join(sorted(self.grams))[:8]


@dataclass(frozen=True)
class Query:
    units: tuple[Unit, ...]
    deictic: bool             # "마찬가지 · 똑같나요" 같은 지시어가 있었다

    @property
    def empty(self) -> bool:
        return not self.units


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
    empty_query: bool          # 제품명·기능어를 지우고 나니 남은 내용 없음
    tokenizer: str = "kiwi"    # 임계값은 토크나이저마다 다르다

    @property
    def top(self) -> Hit | None:
        return self.hits[0] if self.hits else None

    def passes_gate(self, anchored: bool) -> bool:
        """근거 게이트 — 어휘 근거 또는 의미 근거 중 하나라도 충분한가.

        anchored: 질문(또는 직전 대화)에 지원 제품명이 있는가.
          제품명이 있으면 도메인이 보장되므로 "그 제품의 어느 주제인가"만 확인하면 된다.
          없으면 도메인부터 불확실하므로 더 높은 어휘 근거를 요구한다.
        벡터 게이트는 기본적으로 앵커 질문에만 쓴다(VECTOR_GATE_ANCHORED_ONLY).
          "프린터 드라이버 설치" 같은 무관한 질문도 '설치' 때문에 코사인이 높게 나오기 때문이다.
        """
        if self.empty_query:
            return anchored   # "오토캐드요" 처럼 제품만 말한 경우 → 호출부가 되묻기로 처리한다
        if self.tokenizer == "bigram":
            threshold = C.HANDOFF_MIN_LEXICAL_BIGRAM if anchored else C.HANDOFF_MIN_LEXICAL_UNANCHORED_BIGRAM
        else:
            threshold = C.HANDOFF_MIN_LEXICAL if anchored else C.HANDOFF_MIN_LEXICAL_UNANCHORED
        if self.gate_lexical >= threshold:
            return True
        if C.VECTOR_GATE_ANCHORED_ONLY and not anchored:
            return False
        return self.gate_vector is not None and self.gate_vector >= C.HANDOFF_MIN_VECTOR


class Retriever:
    def __init__(self, kb: KnowledgeBase, embedder: Embedder | None = None) -> None:
        self.kb = kb
        self.embedder = embedder
        self.tokenizer = "kiwi" if morph.available() else "bigram"

        # 문서 쪽 색인: 토큰 집합(kiwi 전용) + bigram 집합(부분 일치·bigram 모드)
        self._tokens: dict[str, frozenset[str]] = {}
        self._grams: dict[str, frozenset[str]] = {}
        self._q_tokens: dict[str, frozenset[str]] = {}
        self._q_grams: dict[str, frozenset[str]] = {}
        for c in kb.chunks:
            self._tokens[c.id], self._grams[c.id] = self._doc_index(c.embed_text)
            self._q_tokens[c.id], self._q_grams[c.id] = self._doc_index(" ".join(c.questions))

        self._kb_text = canonical(" ".join(c.embed_text for c in kb.chunks))   # bigram 모드 문제 상황 검사용
        n = len(kb.chunks)
        self._idf_unseen = math.log((n + 1) / (C.UNSEEN_IDF_AS_DF + 0.5))
        self._idf_gram = self._idf_table(self._grams.values(), n)
        self._idf_token = self._idf_table(self._tokens.values(), n)

        self._vectors = None
        self._vector_ids: list[str] = []
        self._load_vectors()

    # region 색인

    @staticmethod
    def _idf_table(sets, n: int) -> dict[str, float]:
        df: dict[str, int] = {}
        for s in sets:
            for x in s:
                df[x] = df.get(x, 0) + 1
        return {x: math.log((n + 1) / (d + 0.5)) for x, d in df.items()}

    def _doc_index(self, text: str) -> tuple[frozenset[str], frozenset[str]]:
        if self.tokenizer == "kiwi":
            a = morph.analyze(canonical_words(text))
            tokens = frozenset(a.tokens) if a else frozenset()
            grams = frozenset(g for t in tokens for g in bigrams(t))
            # 원문 bigram 도 넣는다 — 형태소 분석기가 복합어를 다르게 끊어도 부분 일치가 잡히게
            return tokens, grams | frozenset(bigrams(canonical(text)))
        return frozenset(), frozenset(bigrams(canonical(text)))

    # endregion 색인

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

    def analyze(self, residual: str) -> Query:
        """제품명·버전을 지운 질문을 점수 단위로 나눈다."""
        if self.tokenizer == "kiwi":
            a = morph.analyze(residual)
            if a is not None:
                units = tuple(Unit(t, frozenset(bigrams(t))) for t in a.tokens)
                return Query(units, a.deictic)
        units: list[Unit] = []
        deictic = False
        for w in residual.split(SEP):
            base = strip_suffix(w)
            if any(base.startswith(d) for d in C.MORPH_DEICTIC if len(d) > 1):
                deictic = True
                continue
            grams = frozenset(g for g in bigrams(base) if g not in C.QUERY_STOP_BIGRAMS)
            if grams:
                units.append(Unit(None, grams))
        return Query(tuple(units), deictic)

    def query_words(self, residual: str) -> tuple[Unit, ...]:
        """하위 호환 — 점수 단위 목록."""
        return self.analyze(residual).units

    def weight(self, u: Unit) -> float:
        if u.key is not None:
            return self._idf_token.get(u.key, self._idf_unseen)
        return sum(self._idf_gram.get(g, self._idf_unseen) for g in u.grams) / len(u.grams)

    def match(self, u: Unit, chunk_id: str, *, questions_only: bool = False) -> float:
        tokens = (self._q_tokens if questions_only else self._tokens)[chunk_id]
        grams = (self._q_grams if questions_only else self._grams)[chunk_id]
        if u.key is not None:
            if u.key in tokens:
                return 1.0
            # ★ 영문·숫자는 부분 일치를 주지 않는다. 'manage' 가 'manager'(Identity Manager)에
            #   100% 부분 일치해 라이선스 문서와 설치 준비 문서가 동점이 됐다(G-025).
            #   부분 일치는 띄어쓰기가 흔들리는 한국어 복합어("설치파일" ↔ "설치 파일")를 위한 장치다.
            if u.key.isascii():
                return 0.0
        return len(u.grams & grams) / len(u.grams) if u.grams else 0.0

    def uncovered_problem(self, query: Query, residual: str) -> str | None:
        """질문의 문제 상황 어휘 중 지식 베이스가 전혀 다루지 않는 것을 돌려준다."""
        if self.tokenizer == "kiwi":
            for u in query.units:
                if u.key in C.PROBLEM_WORDS and u.key not in self._idf_token:
                    return u.key
            return None
        flat = residual.replace(SEP, "")
        for w in sorted(C.PROBLEM_WORDS):
            if len(w) >= 2 and w in flat and w not in self._kb_text:
                return w
        return None

    def is_generic(self, units) -> bool:
        """모든 단위가 대부분의 청크에 나오는 일반 용어인가("설치", "파일")."""
        return bool(units) and all(self.weight(u) < C.GENERIC_WORD_MAX_IDF for u in units)

    def coverage(self, units, chunk_id: str, *, questions_only: bool = False) -> float:
        """단위별 일치율의 IDF 가중 평균 (0~1).

        ★ 단위마다 한 표씩 갖는다. '라이선스'(bigram 3개)와 '등록'(1개)이 같은 무게를 갖게 하려는 것이다.
        """
        num = den = 0.0
        for u in units:
            w = self.weight(u)
            num += w * self.match(u, chunk_id, questions_only=questions_only)
            den += w
        return num / den if den > 0 else 0.0

    # endregion 어휘 점수

    def search(self, question: str, residual: str, candidates: list[Chunk], k: int | None = None,
               query: Query | None = None) -> SearchResult:
        """후보 청크 안에서 상위 k개를 고른다. 어떤 경우에도 예외를 던지지 않는다."""
        k = k or C.TOP_K
        query = query or self.analyze(residual)
        units = query.units
        vec = self._vector_scores(question) if candidates else None
        mode = "hybrid" if vec is not None else "local"

        scored: list[tuple[float, Hit]] = []
        for c in candidates:
            lex = self.coverage(units, c.id) if units else 0.0
            v = vec.get(c.id) if vec else None
            score = lex if v is None else C.HYBRID_W_LEXICAL * lex + C.HYBRID_W_VECTOR * v
            tie = self.coverage(units, c.id, questions_only=True) if units else 0.0
            scored.append((tie, Hit(c, round(lex, 4), None if v is None else round(v, 4), round(score, 4))))

        # 정렬 키: ① 점수  ② 예상 질문과의 일치(동점 깨기)  ③ 지식 베이스 순서
        #   ③ "오토캐드 설치 방법"처럼 구분 없는 질문은 첫 단계(설치 준비)부터 안내하는 것이 맞다.
        scored.sort(key=lambda t: (-t[1].score, -t[0], t[1].chunk.order))
        hits = [h for _, h in scored][:k]
        return SearchResult(
            hits=hits,
            mode=mode,
            gate_lexical=hits[0].lexical if hits else 0.0,
            gate_vector=hits[0].vector if hits else None,
            empty_query=not units,
            tokenizer=self.tokenizer,
        )
