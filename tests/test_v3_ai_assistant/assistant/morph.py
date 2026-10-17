"""
assistant/morph.py
형태소 분석 기반 질의·문서 토큰화 (kiwipiepy)

★ 왜 도입했나 — test v1 오답 13건 중 8건의 원인
  bigram 방식은 "받으려면 · 몇 개예요 · 부탁드려요 · 문의드립니다" 를 문서에 없는 정보어로 취급해
  최대 가중치로 점수를 깎았다. 어미·불용어 목록을 손으로 늘리는 방법은 dev 에서만 통했다(dev→test 과잉 전환 20.0%→34.6%).
  → 단어 목록이 아니라 **품사**로 거른다. 조사(J*) · 어미(E*) · 접사(XS*) · 보조용언(VX) · 의존명사(NNB) 는
    어떤 표현으로 오든 같은 규칙으로 빠진다.

★ 남기는 품사
  NNG·NNP·NR(명사) · VV·VA(용언 어간) · MAG(일반 부사) · XR(어근) · SL·SN·SH(외국어·숫자·한자)
  용언은 어간(lemma)으로 남는다 — "받으려면 · 받았는데 · 받아요" 가 모두 '받'이 된다.

★ 품사로 걸러지지 않는 기능성 어휘는 config 의 작은 목록으로 뺀다
  · 경동사(하·되·있·주·드리…)  · 질문 메타 명사(방법·절차·문의…)  · 담화 부사(좀·혹시·바로…)
  bigram 시절의 어미 목록과 달리 "활용형"이 아니라 "어간" 하나만 적으면 된다.

★ 지시어(마찬가지·똑같·동일…)는 따로 표시한다
  주제를 담지 않으므로 점수에서는 빼고, 파이프라인이 "직전 질문의 주제를 이어받으라"는 신호로 쓴다.

★ kiwipiepy 가 없으면 None 을 돌려준다 — 호출부가 bigram 방식으로 내려간다(CI · 경량 배포 대응).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from functools import lru_cache

from . import config as C

logger = logging.getLogger(__name__)

CONTENT_TAGS = frozenset({"NNG", "NNP", "NR", "VV", "VA", "MAG", "XR", "SL", "SN", "SH"})

_lock = threading.Lock()   # Streamlit 은 세션마다 다른 스레드에서 실행된다 — 분석기 호출을 직렬화한다


@dataclass(frozen=True)
class Analysis:
    tokens: tuple[str, ...]   # 점수에 쓰는 내용어 (등장 순, 중복 제거)
    deictic: bool             # 지시어가 있었는가 ("마찬가지인가요", "똑같나요")


@lru_cache(maxsize=1)
def _kiwi():
    try:
        from kiwipiepy import Kiwi
    except ImportError:
        logger.warning("kiwipiepy 가 설치되지 않았다 → bigram 토크나이저로 동작")
        return None
    try:
        return Kiwi()
    except Exception as exc:  # noqa: BLE001 - 모델 파일 손상 등. 서비스는 계속 돌아야 한다
        logger.warning("Kiwi 초기화 실패 → bigram 토크나이저로 동작: %s", exc)
        return None


def available() -> bool:
    return C.TOKENIZER == "kiwi" and _kiwi() is not None


def analyze(text: str) -> Analysis | None:
    """내용어 토큰과 지시어 여부를 돌려준다. 분석기를 쓸 수 없으면 None.

    text 는 표기 변이를 통일한 표준형이어야 한다(text.canonical_words). 구분자 '|' 는 공백으로 본다.
    """
    kiwi = _kiwi() if C.TOKENIZER == "kiwi" else None
    if kiwi is None:
        return None
    plain = " ".join(t for t in text.replace("|", " ").split() if t)
    if not plain:
        return Analysis((), False)
    with _lock:
        morphs = kiwi.tokenize(plain)

    tokens: list[str] = []
    deictic = False
    for m in morphs:
        tag = m.tag.split("-")[0]          # VV-R · VA-I (불규칙 활용 표시)를 기본 품사로 접는다
        if tag not in CONTENT_TAGS:
            continue
        if C.MORPH_PLACEHOLDER in m.form:
            continue                       # 제품명·버전을 지운 자리
        form = C.MORPH_SYNONYMS.get(m.form, m.form)
        if form in C.MORPH_DEICTIC:
            deictic = True
            continue
        if form in C.MORPH_STOP:
            continue
        if form not in tokens:
            tokens.append(form)
    return Analysis(tuple(tokens), deictic)
