"""
assistant/kb.py
지식 베이스 로딩 · 제품/버전 감지 · 사내 배포 링크 치환

★ 제품 감지가 검색보다 먼저다
  5개 Autodesk 제품 설치 가이드는 제품명만 다르고 문장이 거의 같다.
  임베딩·어휘 유사도 어느 쪽으로도 "AutoCAD 절차"와 "Revit 절차"를 구분할 수 없다.
  → 질문에서 제품명을 먼저 뽑아 검색 후보를 그 제품으로 좁힌다.

★ 긴 별칭부터 매칭한다
  "레빗박스"를 "레빗"으로, "캐드박스"를 "캐드"로 잘못 읽으면 다른 제품 절차를 안내하게 된다.
  긴 별칭이 먼저 자리를 차지하고, 그 자리는 짧은 별칭이 다시 쓰지 못하게 가린다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import config as C
from .text import SEP, canonical, canonical_words

logger = logging.getLogger(__name__)

_VERSION = re.compile(r"(?<!\d)(20(?:1[5-9]|2\d|3[0-5]))(?!\d)")
_LINK = re.compile(r"\{\{link:([a-z0-9_]+)\}\}")


class KBUnavailable(RuntimeError):
    """지식 베이스를 읽지 못했다 — 근거 없이 답변하지 않도록 호출부가 상담원 연결로 전환한다."""


@dataclass(frozen=True)
class Chunk:
    id: str
    product: str
    versions: tuple[int, ...]
    section: str
    content: str
    cautions: tuple[str, ...]
    questions: tuple[str, ...]
    embed_text: str
    order: int

    @property
    def title(self) -> str:
        return f"{self.product} · {self.section}"

    @property
    def version_label(self) -> str:
        return "·".join(str(v) for v in self.versions)


@dataclass(frozen=True, eq=False)   # eq=False → 식별자 해시. dict 필드가 있어도 캐시 키로 쓸 수 있다
class KnowledgeBase:
    chunks: tuple[Chunk, ...]
    products: dict[str, tuple[str, ...]]   # 제품 키 → 표준형 별칭
    unsupported: tuple[str, ...]           # 표준형 별칭
    source_sha256: str
    _by_id: dict[str, Chunk] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {c.id: c for c in self.chunks})

    def get(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)

    def first_chunk(self, product: str) -> Chunk | None:
        return next((c for c in self.chunks if c.product == product), None)


@dataclass
class Detection:
    products: list[str]         # 지원 제품 (질문 등장 순)
    unsupported: list[str]      # 미지원 제품 별칭
    versions: list[int]
    residual: str               # 제품명·버전을 지운 나머지 (관련도 계산용)


# region 로딩


def md_source_hash(path: Path | None = None) -> str:
    """원본 md 의 해시. 빌드·인덱스·평가가 모두 이 함수 하나로 계산한다.

    ★ 줄바꿈을 LF 로 통일한 뒤 해시한다.
      Windows 에서 편집하면 CRLF, Git 체크아웃 후 Linux CI 에서는 LF 가 된다.
      바이트 그대로 해시하면 내용이 같은데도 "인덱스가 낡았다"는 거짓 경보가 난다.
    """
    import hashlib

    raw = (path or C.KB_MD).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _parse(data: dict) -> KnowledgeBase:
    chunks = tuple(
        Chunk(
            id=c["id"],
            product=c["product"],
            versions=tuple(c["versions"]),
            section=c["section"],
            content=c["content"],
            cautions=tuple(c.get("cautions", [])),
            questions=tuple(c.get("questions", [])),
            embed_text=c["embed_text"],
            order=i,
        )
        for i, c in enumerate(data["chunks"])
    )
    products = {k: tuple(canonical(a) for a in v) for k, v in data["products"].items()}
    unsupported = tuple(canonical(a) for a in data.get("unsupported", []))
    return KnowledgeBase(chunks, products, unsupported, data["source_sha256"])


@lru_cache(maxsize=4)
def load_kb(path: Path | None = None) -> KnowledgeBase:
    """install_kb.json 을 1회만 읽는다.

    ★ 읽지 못하면 빈 KB 로 조용히 넘어가지 않는다.
      빈 KB 는 "근거가 없다"가 아니라 "검사할 수단이 없다"이다.
      이 상태에서 LLM 만으로 답하면 PoC 초기와 똑같은 환각 답변으로 되돌아간다.
    """
    path = path or C.KB_JSON
    if not path.exists():
        raise KBUnavailable(f"지식 베이스 파일이 없다: {path} → python scripts/build_kb.py 실행")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        kb = _parse(data)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise KBUnavailable(f"지식 베이스 파싱 실패({type(exc).__name__}): {path}") from exc
    if not kb.chunks:
        raise KBUnavailable(f"지식 베이스에 청크가 없다: {path}")
    return kb


# endregion 로딩

# region 제품·버전 감지


@lru_cache(maxsize=8)
def _alias_table(kb: KnowledgeBase) -> tuple[tuple[str, str | None], ...]:
    """(별칭, 제품 키) 목록을 긴 별칭부터 정렬한다. 미지원 제품은 키가 None."""
    table = [(a, key) for key, aliases in kb.products.items() for a in aliases if a]
    table += [(a, None) for a in kb.unsupported if a]
    return tuple(sorted(table, key=lambda x: -len(x[0])))


def detect(question: str, kb: KnowledgeBase) -> Detection:
    """질문에서 제품·버전을 뽑고, 그 자리를 지운 나머지 문장(단어 경계 유지)을 돌려준다.

    별칭은 띄어쓰기를 지운 문자열에서 찾는다("오토 캐드" → "오토캐드").
    지울 때는 단어 경계가 남아 있는 원래 문자열에서 같은 글자를 지운다 — 위치 대응표를 쓴다.
    """
    spaced = list(canonical_words(question))
    pos = [i for i, ch in enumerate(spaced) if ch != SEP]   # 압축 문자열 위치 → 원래 위치
    squeezed = [spaced[i] for i in pos]

    def mask(start: int, length: int) -> None:
        for j in range(start, start + length):
            squeezed[j] = SEP                # 짧은 별칭이 이 자리를 다시 못 쓰게 가린다
            spaced[pos[j]] = SEP             # 관련도 계산에서도 제품명을 뺀다

    found: list[tuple[int, str]] = []
    unsupported: list[str] = []
    for alias, key in _alias_table(kb):
        start = "".join(squeezed).find(alias)
        if start == -1:
            continue
        if key is None:
            unsupported.append(alias)
        else:
            found.append((start, key))
        while start != -1:
            mask(start, len(alias))
            start = "".join(squeezed).find(alias)

    versions: set[int] = set()
    for m in list(_VERSION.finditer("".join(squeezed))):
        versions.add(int(m.group(1)))
        mask(m.start(), 4)

    products = list(dict.fromkeys(key for _, key in sorted(found)))
    return Detection(products, unsupported, sorted(versions), "".join(spaced))


# endregion 제품·버전 감지

# region 사내 배포 링크


def load_links(path: Path | None = None) -> dict[str, str]:
    """links.local.json 을 읽는다. 없으면 빈 사전 — 링크 대신 콜센터 안내 문구가 나간다."""
    path = path or C.LINKS_LOCAL
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, str) and v.startswith("https://")}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("링크 파일 읽기 실패 → 링크 없이 동작: %s", exc)
        return {}


def render_links(text: str, links: dict[str, str]) -> tuple[str, list[str]]:
    """{{link:키}} 를 실제 주소 또는 안내 문구로 바꾼다. 치환된 주소 목록을 함께 돌려준다."""
    used: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        url = links.get(m.group(1))
        if url:
            used.append(url)
            return url
        return f"(설치 파일 링크는 {C.SUPPORT_CONTACT}로 문의해 주세요)"

    return _LINK.sub(_sub, text), used


# endregion 사내 배포 링크
