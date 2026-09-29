"""지식 베이스 · 제품 감지 · 검색 · 벡터 인덱스"""

from __future__ import annotations

import json
import sys

import pytest

from assistant import config as C
from assistant.kb import detect, load_kb, md_source_hash, render_links
from assistant.retriever import Retriever
from assistant.text import SEP

sys.path.insert(0, str(C.ROOT / "scripts"))
import build_index  # noqa: E402
import build_kb  # noqa: E402


def test_kb_json_is_in_sync_with_md(kb):
    """md 를 고치고 build_kb.py 를 안 돌린 채 커밋하는 사고를 막는다."""
    assert kb.source_sha256 == md_source_hash(C.KB_MD)
    assert len(kb.chunks) == 26


@pytest.mark.parametrize("q, products, unsupported", [
    ("레빗박스 설치하려면 레빗 먼저 깔아야 하나요?", ["RevitBOX", "Revit"], []),
    ("캐드박스 인증 방법", ["CADBOX"], []),                 # 캐드박스 ≠ 캐드
    ("지더블유캐드 설치", [], ["지더블유캐드"]),             # 미지원 제품이 '캐드'를 먹는다
    ("오토캐드 LT 설치해 주세요", [], ["오토캐드lt"]),
    ("Revit-Box 2024 버전", ["RevitBOX"], []),
])
def test_longest_alias_wins(kb, q, products, unsupported):
    d = detect(q, kb)
    assert d.products == products
    assert d.unsupported == unsupported


def test_versions_synonyms_and_residual(kb):
    d = detect("오토 캐드 2026 설치", kb)
    assert d.products == ["AutoCAD"] and d.versions == [2026]
    assert [w for w in d.residual.split(SEP) if w] == ["설치"]
    assert "라이선스" in detect("시빌 3D 라이센스 인증", kb).residual   # 표기 변이 통일


def test_render_links_without_local_file():
    text, used = render_links("설치파일 {{link:cadbox_setup}}", {})
    assert "콜센터" in text and used == []
    text, used = render_links("설치파일 {{link:cadbox_setup}}", {"cadbox_setup": "https://example.com/a.exe"})
    assert used == ["https://example.com/a.exe"]


def test_md_hash_ignores_line_endings(tmp_path):
    lf, crlf = tmp_path / "a.md", tmp_path / "b.md"
    lf.write_bytes("가\n나\n".encode())
    crlf.write_bytes("가\r\n나\r\n".encode())
    assert md_source_hash(lf) == md_source_hash(crlf)


def test_build_blocks_signed_urls_and_phone_numbers():
    md = ("## PRODUCT | AutoCAD | 오토캐드\n## KB-ACAD-01 | AutoCAD | 2026 | 설치\n**내용**\n"
          "1번 파일 https://efulfillment.autodesk.com/a.exe?authparam=0000000000_x\n"
          "콜센터: 02-1234-5678\n**예상 질문**\n- 질문 하나\n- 질문 둘\n")
    errs = build_kb.validate(*build_kb.parse(md))
    assert any("서명 URL" in e for e in errs)
    assert any("전화번호" in e for e in errs)


def test_gate_is_stricter_without_product(kb, retriever):
    res = retriever.search("프린터 드라이버 설치", detect("프린터 드라이버 설치", kb).residual, list(kb.chunks))
    assert not res.passes_gate(anchored=False)       # '드라이버↔드라이브' 부분 일치에 속지 않는다


def test_vector_index_roundtrip_and_stale_detection(kb, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from conftest import FakeEmbeddings

    from assistant.embed import make_embedder

    monkeypatch.setattr(C, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(C, "VECTORS_NPY", tmp_path / "vectors.npy")
    monkeypatch.setattr(C, "INDEX_META", tmp_path / "meta.json")
    emb = FakeEmbeddings()
    client = SimpleNamespace(embeddings=emb)
    meta = build_index.build(client=client)
    assert meta["count"] == 26 and meta["source_sha256"] == kb.source_sha256

    embedder = make_embedder("unused", client=client)
    r = Retriever(kb, embedder)
    assert r.mode == "hybrid"
    before = emb.calls
    r.search("오토캐드 라이선스", "라이선스", list(kb.chunks))
    r.search("오토캐드 라이선스", "라이선스", list(kb.chunks))
    assert emb.calls == before + 1                    # 같은 질문은 한 번만 임베딩한다

    stale = json.loads(C.INDEX_META.read_text(encoding="utf-8"))
    stale["source_sha256"] = "0" * 64
    C.INDEX_META.write_text(json.dumps(stale), encoding="utf-8")
    assert Retriever(kb, embedder).mode == "local"    # 낡은 인덱스는 쓰지 않는다
