"""평가 도구 — 사전 등록 규칙 · 생성 비교 · test2 실행 기록 (키 없이 가짜 클라이언트로 검증)"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from conftest import FakeClient, FakeEmbeddings, cite_first

from assistant import config as C

sys.path.insert(0, str(C.ROOT / "scripts"))
import build_index  # noqa: E402
import eval_rag as E  # noqa: E402


def _row(expected, vector, lexical=0.1, rank=1, anchored=True):
    return {"expected": expected, "vector": vector, "lexical": lexical, "rank": rank, "anchored": anchored,
            "needs_clarify": False, "reason": "" if expected == "answer" else "low_relevance",
            "got": "handoff"}


def test_vector_gate_enabled_only_above_handoff_max():
    rows = [_row("handoff", 0.52), _row("handoff", 0.48), _row("answer", 0.71), _row("answer", 0.64),
            _row("answer", 0.50)]                          # 0.50 은 넘길 문항(0.52)보다 낮아 구제하지 않는다
    v = E.recommend_vector(rows)
    assert v["status"] == "enable" and 0.52 < v["threshold"] < 0.64 and v["rescued"] == 2


def test_vector_gate_stays_off_without_separation():
    v = E.recommend_vector([_row("handoff", 0.70), _row("answer", 0.65)])
    assert v["status"] == "keep_off" and v["threshold"] == 1.01


def test_vector_gate_ignores_unanchored_rows():
    v = E.recommend_vector([_row("handoff", 0.9, anchored=False), _row("answer", 0.6), _row("handoff", 0.4)])
    assert v["status"] == "enable"                         # 무앵커 0.9 는 기준에 들어가지 않는다


def _g(**kw):
    base = {"under_handoff": 0, "wrong_cite": 1, "cited_correct_rate": 0.90, "latency_p95_ms": 1000}
    return {**base, **kw}


@pytest.mark.parametrize("resp, expected", [
    (_g(), "responses"),                                   # 동률이면 responses (표준 인터페이스)
    (_g(cited_correct_rate=0.885), "responses"),           # -0.02 이내
    (_g(cited_correct_rate=0.85), "chat"),
    (_g(wrong_cite=2), "chat"),
    (_g(under_handoff=1), "chat"),
    (_g(latency_p95_ms=1300), "chat"),                     # 20% 초과 지연
])
def test_backend_switch_rule(resp, expected):
    assert E.decide_backend(_g(), resp)[0] == expected


def test_generate_eval_both_backends(kb, retriever, monkeypatch, tmp_path):
    monkeypatch.setattr(E, "REPORT_DIR", tmp_path)
    items = [i for i in E.select(E.load_golden()[0], "dev") if i["expected"]["decision"] in ("answer", "handoff")][:8]
    client = FakeClient(cite_first)
    results = [E.generate_eval(items, kb, retriever, b, client, "fake-model") for b in ("chat", "responses")]
    for r in results:
        assert r["n"] == len(items) and r["errors"] == 0 and r["under_handoff"] == 0
        assert 0.0 <= r["cited_correct_rate"] <= 1.0 and r["tokens_total"] > 0
    assert client.chat.completions.calls and client.responses.calls
    path = E.write_generate_report("dev", results, E.decide_backend(*results))
    assert "기본 백엔드" in path.read_text(encoding="utf-8")


def test_vector_report_runs_end_to_end(kb, monkeypatch, tmp_path):
    from assistant.embed import make_embedder

    for name, value in (("INDEX_DIR", tmp_path), ("VECTORS_NPY", tmp_path / "vectors.npy"),
                        ("INDEX_META", tmp_path / "meta.json")):
        monkeypatch.setattr(C, name, value)
    monkeypatch.setattr(E, "REPORT_DIR", tmp_path)
    client = SimpleNamespace(embeddings=FakeEmbeddings())
    build_index.build(client=client)
    rep = E.vector_report(E.select(E.load_golden()[0], "dev"), kb, make_embedder("x", client=client), "dev")
    assert rep["vector_gate"]["status"] in ("enable", "keep_off", "rejected_unsafe")
    assert rep["path"].exists()


def test_test2_runs_are_counted(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(E, "TEST2_LOG", tmp_path / "test2_runs.log")
    cond = {"run_at": "t", "code": "c", "golden": {"v2": "h"}, "tokenizer": "kiwi", "mode": "local"}
    m = {"under_handoff": 0, "wrong_answer": 0, "hit_at_1": 1.0, "over_handoff_rate": 0.0}
    assert E.log_test2_run("baseline", cond, m) == 1
    assert E.log_test2_run("final", cond, m) == 2


def test_test2_is_never_part_of_tuning():
    items, _ = E.load_golden()
    assert all(i["split"] != "test2" for i in E.select(items, "tune") + E.select(items, "all"))


def test_test2_seal_is_intact():
    """봉인 기록의 해시와 파일이 다르면 누군가 봉인 후 문항을 고친 것이다."""
    seal = (C.ROOT / "eval" / "SEAL_test2.md").read_text(encoding="utf-8")
    assert E._hash(E.GOLDEN_FILES["v2"]) in seal
