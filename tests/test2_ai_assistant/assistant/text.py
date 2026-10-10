"""
assistant/text.py
텍스트 정규화 — 제품명 감지 · 검색 · 입력 검증이 모두 같은 규칙을 쓴다.

★ 규칙이 모듈마다 다르면 "감지는 됐는데 검색이 안 되는" 사고가 난다.
  정규화는 이 파일 하나에서만 정의한다.
"""

from __future__ import annotations

import re
import unicodedata

from . import config as C

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff\u2060]")
_NON_WORD = re.compile(r"[^0-9a-z가-힣|]")
_SEPS = re.compile(r"[^0-9a-z가-힣]+")
# 동사 어간 표기 → 표준어.  "깔끔" · "켜켜이" 같은 단어는 건드리지 않도록 뒤 글자를 본다.
_STEM_RULES = (
    (re.compile(r"깔(?=[고아려기았면다])"), "설치"),  # 깔고 · 깔아 · 깔려면 · 깔다가
    (re.compile(r"켜(?=[야면는고서요기])"), "실행"),  # 켜야 · 켜면 · 켜는
)

SEP = "|"   # 단어 경계·마스킹 구분자 — bigram 이 이 문자를 가로지르지 않는다

# 조사·어미. 긴 것부터 한 번만 뗀다. 떼고 남은 어간이 2글자 미만이면 떼지 않는다.
#   "설치는" → "설치",  "받아요" → "받아요"(받 1글자라 유지 — 어미 bigram 은 불용어로 걸러진다)
_SUFFIXES = tuple(sorted(
    "하려고요 하려면 하려고 되나요 하나요 할까요 인가요 는데요 하다가 해야 하면 하는 하고 해도 에서 으로 한테 까지 부터 다가 "
    "나요 까요 해요 돼요 되요 어요 아요 세요 인데 는데 은데 려면 이요 는 은 이 가 을 를 에 로 도 만 요 야 고 면".split(),
    key=len, reverse=True,
))


def normalize(text: str) -> str:
    """유니코드 합자·전각·대소문자·제로폭 문자를 통일한다."""
    return unicodedata.normalize("NFKC", _ZERO_WIDTH.sub("", text or "")).lower()


def squeeze(text: str) -> str:
    """영문·숫자·한글 음절만 남긴다. "오토 캐드" · "Revit-Box" 같은 띄어쓰기 변형을 흡수한다."""
    return _NON_WORD.sub("", normalize(text).replace(SEP, " "))


def _standardize(text: str) -> str:
    s = normalize(text)
    for variant, standard in C.SYNONYMS:
        s = s.replace(variant, standard)
    for pattern, standard in _STEM_RULES:
        s = pattern.sub(standard, s)
    return s


def canonical(text: str) -> str:
    """검색·감지용 표준형(띄어쓰기 제거). 문서 쪽 bigram 과 별칭 매칭에 쓴다."""
    return _NON_WORD.sub("", _standardize(text))


def canonical_words(text: str) -> str:
    """표준형이되 단어 경계를 | 로 남긴다. 질문 쪽 관련도 계산에 쓴다.

    ★ 띄어쓰기를 지우고 bigram 을 만들면 단어 경계에 가짜 bigram 이 생긴다.
      "라이센스 등록하는 법 좀" → '스등' · '는법' · '법좀' 은 어느 문서에도 없어서
      IDF 최댓값을 받고, 정작 100% 일치한 '라이선스'의 점수를 덮어버렸다(실측 0.171).
    """
    return _SEPS.sub(SEP, _standardize(text)).strip(SEP)


def strip_suffix(word: str, times: int = 2) -> str:
    """조사·어미를 최대 2번 뗀다. 한국어는 어간 + 조사 + 어미가 겹쳐 붙는다.

    "설치하려면" → "설치",  "바꿔도되나요" → "바꿔도" → "바꿔"
    """
    for _ in range(times):
        for suf in _SUFFIXES:
            if word.endswith(suf) and len(word) - len(suf) >= 2:
                word = word[: -len(suf)]
                break
        else:
            break
    return word


def bigrams(s: str) -> set[str]:
    """문자 bigram 집합. 형태소 분석기 없이도 한국어 부분 일치를 잡는다.

    구분자(|)를 가로지르는 쌍은 만들지 않는다 — 제품명을 지운 자리에서
    앞뒤 글자가 붙어 가짜 bigram 이 생기는 것을 막는다.
    """
    return {s[i:i + 2] for i in range(len(s) - 1) if SEP not in s[i:i + 2]}
