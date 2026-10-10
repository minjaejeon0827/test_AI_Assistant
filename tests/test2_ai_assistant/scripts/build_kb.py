"""
scripts/build_kb.py
data/install_kb.md  →  data/install_kb.json

실행:
    python scripts/build_kb.py            # 빌드
    python scripts/build_kb.py --check    # CI 용: 재생성 없이 md ↔ json 동기화만 확인

★ 검증에 실패하면 json 을 만들지 않는다
  특히 다운로드 URL · 전화번호가 본문에 다시 들어오면 빌드를 멈춘다.
  원본 Autodesk 제품 설치 가이드에 작성된 프로그램 설치 URL 57개는 2025-06 발급분이라 이미 만료됐을 가능성이 높다.
  만료 링크 안내 시 사용자에게 오안내를 하는 것이다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import config as C
from assistant.kb import md_source_hash

PRODUCT_RE = re.compile(r"^##\s+PRODUCT\s*\|\s*(\w+)\s*\|\s*(.+?)\s*$")
UNSUPPORTED_RE = re.compile(r"^##\s+UNSUPPORTED\s*\|\s*(.+?)\s*$")
CHUNK_RE = re.compile(r"^##\s+(KB-[A-Z0-9]+-\d{2})\s*\|\s*(\w+)\s*\|\s*([\d,\s]+?)\s*\|\s*(.+?)\s*$")
SECTION_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")

SECTION_KEY = {"내용": "content", "예상 질문": "questions", "주의사항": "cautions"}
LIST_FIELDS = {"questions", "cautions"}

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
PHONE_RE = re.compile(r"\b0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}\b")
LINK_RE = re.compile(r"\{\{link:([a-z0-9_]+)\}\}")
LINK_SYNTAX_RE = re.compile(r"\{\{.*?\}\}")

MIN_QUESTIONS = 2


def parse(md_text: str) -> tuple[dict, list, list[dict]]:
    products: dict[str, list[str]] = {}
    unsupported: list[str] = []
    chunks: list[dict] = []
    cur: dict | None = None
    section: str | None = None
    buf: list[str] = []
    in_comment = False

    def flush() -> None:
        nonlocal buf, section
        if cur is not None and section:
            if section in LIST_FIELDS:
                cur[section] = [ln.lstrip("- ").strip() for ln in buf if ln.strip().startswith("-")]
            else:
                cur[section] = "\n".join(ln for ln in buf if ln.strip()).strip()
        buf, section = [], None

    for line in md_text.replace("\r\n", "\n").splitlines():
        stripped = line.strip()
        if in_comment:
            in_comment = "-->" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            continue
        if (m := PRODUCT_RE.match(stripped)):
            products[m.group(1)] = [a.strip() for a in m.group(2).split(",") if a.strip()]
            continue
        if (m := UNSUPPORTED_RE.match(stripped)):
            unsupported += [a.strip() for a in m.group(1).split(",") if a.strip()]
            continue
        if (m := CHUNK_RE.match(stripped)):
            flush()
            if cur:
                chunks.append(cur)
            cur = {
                "id": m.group(1),
                "product": m.group(2),
                "versions": [int(v) for v in re.findall(r"\d{4}", m.group(3))],
                "section": m.group(4),
                "content": "",
                "questions": [],
                "cautions": [],
            }
            continue
        if stripped.startswith("#") or cur is None:
            continue
        if (s := SECTION_RE.match(stripped)) and s.group(1) in SECTION_KEY:
            flush()
            section = SECTION_KEY[s.group(1)]
            continue
        if stripped == "---":
            flush()
            continue
        if section:
            buf.append(line)

    flush()
    if cur:
        chunks.append(cur)
    return products, unsupported, chunks


def build_embed_text(c: dict, aliases: list[str]) -> str:
    """검색 품질을 좌우하는 부분. 본문 + 주의사항 + 예상 질문을 함께 넣는다.

    ★ 예상 질문 넣은 이유
      사용자는 "오토캐드 깔려면?" 처럼 구어체로 묻는데 본문은 절차 문장이다.
      본문만 넣으면 어휘·임베딩 양쪽에서 거리가 멀어 검색이 안 된다.
    """
    content = LINK_RE.sub("", c["content"])
    parts = [
        f"{c['product']} ({', '.join(aliases)}) {' '.join(map(str, c['versions']))} {c['section']}",
        content,
    ]
    if c["cautions"]:
        parts.append("주의사항: " + " / ".join(c["cautions"]))
    parts.append("예상 질문: " + " / ".join(c["questions"]))
    return "\n".join(parts).strip()


def validate(products: dict, unsupported: list, chunks: list[dict]) -> list[str]:
    errs: list[str] = []
    if not products:
        errs.append("PRODUCT 선언이 없다")

    # 별칭 충돌 — 같은 별칭이 두 제품을 가리키면 감지 결과가 입력 순서에 좌우된다
    owner: dict[str, str] = {}
    for key, aliases in products.items():
        for a in aliases:
            k = a.lower().replace(" ", "")
            if k in owner and owner[k] != key:
                errs.append(f"별칭 충돌: '{a}' → {owner[k]} / {key}")
            owner[k] = key
    for a in unsupported:
        if a.lower().replace(" ", "") in owner:
            errs.append(f"미지원 별칭 '{a}' 이 지원 제품 별칭과 겹친다")

    seen: set[str] = set()
    for c in chunks:
        cid = c["id"]
        if cid in seen:
            errs.append(f"{cid}: ID 중복")
        seen.add(cid)
        if c["product"] not in products:
            errs.append(f"{cid}: PRODUCT 에 없는 제품 '{c['product']}'")
        if not c["versions"]:
            errs.append(f"{cid}: 버전이 없다")
        if not c["content"]:
            errs.append(f"{cid}: '내용'이 비어 있다")
        if len(c["questions"]) < MIN_QUESTIONS:
            errs.append(f"{cid}: '예상 질문'이 {len(c['questions'])}개 — 최소 {MIN_QUESTIONS}개 필요")

        body = "\n".join([c["content"], *c["cautions"], *c["questions"]])
        for url in URL_RE.findall(body):
            hint = " (서명 URL — 만료·유출 위험)" if "authparam" in url else ""
            errs.append(f"{cid}: URL 을 본문에 넣지 않는다{hint} → {{{{link:키}}}} 로 바꾼다: {url[:60]}")
        if PHONE_RE.search(body):
            errs.append(f"{cid}: 전화번호는 본문에 넣지 않는다 → config.SUPPORT_CONTACT 로 관리")
        for token in LINK_SYNTAX_RE.findall(body):
            if not LINK_RE.fullmatch(token):
                errs.append(f"{cid}: 링크 자리표시자 형식 오류 '{token}' (예: {{{{link:cadbox_setup}}}})")
    return errs


def warn_if_index_stale(kb_hash: str) -> None:
    """KB 를 다시 만들었으면 벡터 인덱스도 다시 만들어야 한다. 만든 직후에 알려주는 편이 효율적이다."""
    if not C.INDEX_META.exists():
        return
    try:
        meta = json.loads(C.INDEX_META.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if meta.get("source_sha256") != kb_hash:
        print(
            "\n⚠️  kb_index 가 낡았다 — KB 가 바뀌었는데 벡터는 예전 문장의 것이다.\n"
            "    python scripts/build_index.py 를 실행하라. 그대로 두면 검색이 local 모드로 내려간다.",
            file=sys.stderr,
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="재생성 없이 동기화 여부만 확인")
    args = ap.parse_args()

    md_text = C.KB_MD.read_text(encoding="utf-8")
    products, unsupported, chunks = parse(md_text)

    errs = validate(products, unsupported, chunks)
    if errs:
        print("❌ install_kb.md 검증 실패:", file=sys.stderr)
        for e in errs:
            print("   -", e, file=sys.stderr)
        return 1

    for c in chunks:
        c["embed_text"] = build_embed_text(c, products[c["product"]])

    kb_hash = md_source_hash(C.KB_MD)
    payload = {
        "source_sha256": kb_hash,
        "count": len(chunks),
        "products": products,
        "unsupported": unsupported,
        "chunks": chunks,
    }

    if args.check:
        if not C.KB_JSON.exists():
            print("❌ install_kb.json 이 없다. build_kb.py 를 실행하라.", file=sys.stderr)
            return 1
        old = json.loads(C.KB_JSON.read_text(encoding="utf-8"))
        if old.get("source_sha256") != kb_hash:
            print("❌ install_kb.md 가 바뀌었는데 json 이 갱신되지 않았다.", file=sys.stderr)
            return 1
        print(f"✅ 동기화 OK ({len(chunks)}개 청크)")
        return 0

    C.KB_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    by_product: dict[str, int] = {}
    for c in chunks:
        by_product[c["product"]] = by_product.get(c["product"], 0) + 1
    links = sorted({k for c in chunks for k in LINK_RE.findall(c["content"])})
    print(f"✅ {len(chunks)}개 청크 → {C.KB_JSON.relative_to(C.ROOT)}")
    for k, v in by_product.items():
        print(f"   {k:<11} {v}개")
    print(f"   예상 질문 {sum(len(c['questions']) for c in chunks)}개 · "
          f"미지원 별칭 {len(unsupported)}개 · 링크 자리표시자 {len(links)}개")

    warn_if_index_stale(kb_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
