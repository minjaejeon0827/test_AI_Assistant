"""
scripts/eval_rag.py
골든 데이터셋(eval/golden_rag.json)으로 RAG 파이프라인을 평가한다.

실행:
    python scripts/eval_rag.py                   # test 분할 · 판정+검색 평가 (키 불필요)
    python scripts/eval_rag.py --split dev       # 튜닝용 분할
    python scripts/eval_rag.py --sweep           # dev 분할에서 근거 게이트 임계값 스윕 (sweep)
    python scripts/eval_rag.py --generate        # LLM 생성까지 평가 (OPENAI_API_KEY 필요)
    python scripts/eval_rag.py --strict          # 목표 미달 시 exit 1 (CI)

★ 평가 원칙
  1. 재현성 — 판정·검색 평가는 LLM 모델을 호출하지 않는다. 같은 입력이면 같은 점수다.
  2. 평가 오염 차단 — 골든 데이터셋 문항이 지식 베이스 '예상 질문'과 거의 같으면 검색이 문장을 외워서 맞힌다.
     겹침이 있으면 평가를 시작하지 않는다.
  3. dev / test 분리 — 임계값은 dev 에서만 고른다. 성능은 test 로만 보고한다.
     임계값을 test 에서 고르면 그 점수는 "외운 점수"다.

★ 지표를 두 층으로 나눈다
  검색 순위 — 게이트와 무관하게 정답 청크가 몇 위였나 (Hit@1 · Recall@3 · MRR)
  판정      — 답변 / 상담원 전환 / 되묻기 / 차단을 맞게 골랐나
  둘을 섞으면 "순위는 맞았는데 게이트가 막은 것"과 "순위부터 틀린 것"을 구분할 수 없다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import config as C
from assistant.kb import load_kb, md_source_hash
from assistant.pipeline import SYSTEM_PROMPT, ConversationState, decide
from assistant.retriever import Retriever
from assistant.text import bigrams, squeeze


GOLDEN = C.ROOT / "eval" / "golden_rag.json"
REPORT_DIR = C.ROOT / "eval" / "reports"

DECISIONS = {"answer", "handoff", "clarify", "smalltalk", "blocked", "invalid"}
CONTAMINATION_THRESHOLD = 0.6     # 예상 질문과 bigram Jaccard 가 이 이상이면 오염으로 본다

# ── 목표 (미달이면 --strict 에서 실패) ─────────────────────────────────────────
TARGETS = {
    "under_handoff": ("==", 0),       # 답하면 안 되는 질문에 답한 건수. 틀린 설치 안내 = 실제 피해
    "wrong_answer": ("==", 0),        # 답은 했는데 1위 문서가 틀린 건수. 엉뚱한 절차 안내 = 같은 피해
    "over_block": ("==", 0),          # 정상 질문을 프롬프트 인젝션으로 차단한 건수
    "block_recall": (">=", 1.0),
    "hit_at_1": (">=", 0.80),         # 검색 순위 — 정답 청크가 1위
    "over_handoff_rate": ("<=", 0.25),  # 답할 수 있는데 넘긴 비율. 안전한 실패라 느슨하게 둔다
}


# region 사전 점검


def preflight(kb, retriever) -> dict:
    kb_hash = md_source_hash(C.KB_MD)
    if kb.source_sha256 != kb_hash:
        raise SystemExit("❌ install_kb.md 가 json 보다 최신이다 → python scripts/build_kb.py 먼저 실행")
    return {"kb_sha256": kb_hash[:16], "mode": retriever.mode, "chunks": len(kb.chunks)}


def load_golden() -> tuple[list[dict], str]:
    raw = GOLDEN.read_bytes()
    items = json.loads(raw)
    return items, hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()[:16]


def validate_golden(items: list[dict], kb) -> list[str]:
    errs, seen = [], set()
    for it in items:
        gid = it.get("id", "?")
        if gid in seen:
            errs.append(f"{gid}: ID 중복")
        seen.add(gid)
        exp = it.get("expected", {})
        if exp.get("decision") not in DECISIONS:
            errs.append(f"{gid}: expected.decision 값 오류 {exp.get('decision')}")
        if it.get("split") not in ("dev", "test"):
            errs.append(f"{gid}: split 은 dev|test")
        for cid in exp.get("chunk_ids", []):
            if kb.get(cid) is None:
                errs.append(f"{gid}: 존재하지 않는 청크 {cid}")
        if exp.get("decision") == "answer" and not exp.get("chunk_ids"):
            errs.append(f"{gid}: answer 인데 chunk_ids 가 없다")
    return errs


def contamination(items: list[dict], kb) -> list[tuple[str, str, str, float]]:
    """골든 데이터셋 문항 ↔ 지식 베이스 예상 질문 겹침. (문항 ID, 문항, 가장 닮은 예상 질문, 유사도)"""
    examples = [(q, bigrams(squeeze(q))) for c in kb.chunks for q in c.questions]
    found = []
    for it in items:
        qg = bigrams(squeeze(it["question"]))
        if not qg:
            continue
        best = max(((q, len(qg & g) / len(qg | g)) for q, g in examples if g), key=lambda x: x[1])
        if best[1] >= CONTAMINATION_THRESHOLD:
            found.append((it["id"], it["question"], best[0], round(best[1], 3)))
    return found


def prompt_example_is_fake(kb) -> bool:
    """시스템 프롬프트의 형식 예시 ID 가 실제 청크 ID 가 아닌지 — 실제 ID 면 모델이 베껴도 인용 검사를 통과한다."""
    import re
    return all(kb.get(cid) is None for cid in re.findall(r"KB-[A-Z0-9]+-\d{2}", SYSTEM_PROMPT))


# endregion 사전 점검

# region 실행 · 지표


def run_items(items: list[dict], kb, retriever) -> list[dict]:
    rows = []
    for it in items:
        state = ConversationState()
        for turn in it.get("turns", []):
            _, state = decide(turn, state, kb, retriever)
        t0 = time.perf_counter()
        d, _ = decide(it["question"], state, kb, retriever)
        elapsed = time.perf_counter() - t0
        exp = it["expected"]
        accepted = set(exp.get("chunk_ids", []))
        rank = next((i + 1 for i, cid in enumerate(d.hit_ids) if cid in accepted), None)
        rows.append({
            "id": it["id"], "type": it["type"], "question": it["question"],
            "expected": exp["decision"], "got": d.kind,
            "expected_reason": exp.get("reason"), "reason": d.reason,
            "expected_product": exp.get("product"), "products": d.products,
            "accepted": sorted(accepted), "top": d.hit_ids[:3], "rank": rank,
            "lexical": d.gate_lexical, "anchored": d.anchored, "needs_clarify": d.needs_clarify,
            "latency_ms": round(elapsed * 1000, 2),
        })
    return rows


def _pct(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def metrics(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["expected"] == "answer"]
    ho = [r for r in rows if r["expected"] == "handoff"]
    ranked = [r for r in ans if r["top"]]

    under = [r for r in ho if r["got"] == "answer"]
    over = [r for r in ans if r["got"] in ("handoff", "clarify")]
    blocked_exp = [r for r in rows if r["expected"] == "blocked"]
    over_block = [r for r in rows if r["expected"] != "blocked" and r["got"] == "blocked"]
    with_reason = [r for r in rows if r["expected_reason"] and r["got"] == r["expected"]]
    with_prod = [r for r in rows if r["expected_product"]]

    return {
        "n": len(rows),
        "decision_accuracy": sum(r["got"] == r["expected"] for r in rows) / len(rows),
        "answer_success": sum(r["got"] == "answer" and r["rank"] == 1 for r in ans) / len(ans) if ans else 0.0,
        "hit_at_1": sum(r["rank"] == 1 for r in ranked) / len(ranked) if ranked else 0.0,
        "recall_at_3": sum(r["rank"] is not None and r["rank"] <= 3 for r in ranked) / len(ranked) if ranked else 0.0,
        "mrr": sum(1 / r["rank"] for r in ranked if r["rank"]) / len(ranked) if ranked else 0.0,
        "handoff_recall": sum(r["got"] == "handoff" for r in ho) / len(ho) if ho else 0.0,
        "under_handoff": len(under),
        # ★ test v1 결과를 보고 추가한 지표다(시스템은 그대로, 지표만 엄격하게).
        #   under_handoff 는 "넘겨야 할 질문에 답한 경우"만 센다. "답할 질문에 엉뚱한 문서로 답한 경우"는
        #   추출형 모드에서 틀린 절차가 그대로 나가는데도 어느 지표에도 잡히지 않았다.
        "wrong_answer": sum(r["got"] == "answer" and r["rank"] != 1 for r in ans),
        "over_handoff_rate": len(over) / len(ans) if ans else 0.0,
        "block_recall": sum(r["got"] == "blocked" for r in blocked_exp) / len(blocked_exp) if blocked_exp else 1.0,
        "over_block": len(over_block),
        "reason_accuracy": (sum(r["reason"] == r["expected_reason"] for r in with_reason) / len(with_reason)
                            if with_reason else 1.0),
        "product_accuracy": (sum(r["expected_product"] in r["products"] for r in with_prod) / len(with_prod)
                             if with_prod else 1.0),
        "latency_p50_ms": _pct([r["latency_ms"] for r in rows], 50),
        "latency_p95_ms": _pct([r["latency_ms"] for r in rows], 95),
    }


def judge(m: dict) -> dict[str, bool]:
    ops = {"==": lambda a, b: a == b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}
    return {k: ops[op](m[k], v) for k, (op, v) in TARGETS.items()}


# endregion 실행 · 지표

# region 스윕


def _gate_points(rows: list[dict], anchored: bool) -> tuple[list[float], list[float]]:
    """근거 게이트에서 판정이 갈린 문항의 어휘 점수. (OUT 점수, IN 점수)

    범위 게이트 · 입력 검증 · 되묻기에서 끝난 문항은 임계값과 무관하므로 제외한다.
    IN 은 1위 청크가 정답인 문항만 센다 — 순위가 틀린 문항을 통과시키면 틀린 안내가 된다.
    """
    outs, ins = [], []
    for r in rows:
        if r["anchored"] != anchored or r["needs_clarify"] or r["reason"] not in ("", "low_relevance"):
            continue
        if r["got"] not in ("answer", "handoff"):
            continue
        if r["expected"] == "handoff":
            outs.append(r["lexical"])
        elif r["expected"] == "answer" and r["rank"] == 1:
            ins.append(r["lexical"])
    return outs, sorted(ins)


def recommend(rows: list[dict]) -> dict[bool, dict]:
    """여유 최대화로 임계값을 고른다.

    ★ 규칙
      ① 틀린 안내 경계 L = 전환해야 할 문항(OUT)의 최고 점수. 임계값은 반드시 L 보다 커야 한다.
      ② L 위의 IN 점수들이 만드는 빈틈 중, 과잉 전환율이 목표 이내인 것만 후보로 둔다.
      ③ 후보 중 가장 넓은 빈틈의 한가운데를 고른다.
    ★ 왜 "가장 보수적인 값"이 아닌가
      dev 에서 성능이 같은 구간의 끝값은 바로 옆 문항과의 여유가 거의 없다.
      test 에서 처음 보는 질문의 점수가 조금만 흔들려도 판정이 뒤집힌다.
    """
    target_over = TARGETS["over_handoff_rate"][1]
    result = {}
    for anchored in (True, False):
        outs, ins = _gate_points(rows, anchored)
        low = max(outs, default=0.0)
        edges = sorted({low, *(v for v in ins if v > low), 1.0})
        best = None
        for lo, hi in zip(edges, edges[1:]):
            t = (lo + hi) / 2
            over = sum(v < t for v in ins) / len(ins) if ins else 0.0
            if over <= target_over and (best is None or hi - lo > best["gap"]):
                best = {"threshold": round(t, 2), "gap": hi - lo, "lo": lo, "hi": hi, "over": over}
        result[anchored] = {"best": best, "out_max": low, "outs": outs, "ins": ins}
    return result


def sweep(items: list[dict], kb, retriever) -> None:
    """dev 분할에서 임계값 조합별 결과를 보고, 여유 최대화 규칙으로 권장값을 낸다."""
    grid_a = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    grid_u = [0.50, 0.60, 0.70]
    orig = (C.HANDOFF_MIN_LEXICAL, C.HANDOFF_MIN_LEXICAL_UNANCHORED)
    print("\n  앵커  무앵커 │ under  over-rate  정확도")
    print("  ─────────────┼──────────────────────────")
    for a in grid_a:
        for u in grid_u:
            C.HANDOFF_MIN_LEXICAL, C.HANDOFF_MIN_LEXICAL_UNANCHORED = a, u
            m = metrics(run_items(items, kb, retriever))
            if u == 0.60:
                print(f"  {a:.2f}  {u:.2f}  │  {m['under_handoff']:>3}    {m['over_handoff_rate']:6.1%}"
                      f"    {m['decision_accuracy']:6.1%}")
    C.HANDOFF_MIN_LEXICAL, C.HANDOFF_MIN_LEXICAL_UNANCHORED = orig

    rec = recommend(run_items(items, kb, retriever))
    print("\n  [여유 최대화 권장]")
    for anchored, label in ((True, "앵커  "), (False, "무앵커")):
        r = rec[anchored]
        ins = ", ".join(f"{v:.3f}" for v in r["ins"])
        print(f"    {label} OUT 최고점 {r['out_max']:.3f} │ IN 점수 [{ins}]")
        b = r["best"]
        if b:
            print(f"           → 빈틈 ({b['lo']:.3f}, {b['hi']:.3f}) 한가운데 {b['threshold']:.2f}"
                  f"  · 양쪽 여유 ±{b['gap'] / 2:.3f} · 이 그룹 과잉 전환 {b['over']:.0%}")
        else:
            print("           → 과잉 전환 목표를 만족하는 빈틈이 없다")
    print(f"\n    현재 설정: 앵커 {orig[0]:.2f} / 무앵커 {orig[1]:.2f}  → config.py 또는 환경 변수로 반영")


# endregion 스윕

# region 생성 평가 (--generate)


def generate_eval(items: list[dict], kb, retriever) -> dict:
    """LLM 까지 돌려 최종 판정 · 인용 유효성을 본다. 키 필요 · 비결정적이므로 CI 에서 돌리지 않는다."""
    import os

    from assistant import llm
    from assistant.kb import load_links
    from assistant.pipeline import build_prompt, postcheck

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise SystemExit("❌ --generate 는 OPENAI_API_KEY 가 필요하다")
    client = llm.make_client(key)
    links = load_links()
    rows = []
    for it in items:
        state = ConversationState()
        for turn in it.get("turns", []):
            _, state = decide(turn, state, kb, retriever)
        d, _ = decide(it["question"], state, kb, retriever)
        final, cited, grounded, tokens, ms = d.kind, [], None, 0, 0
        if d.kind == "answer":
            p = build_prompt(it["question"], d, [], "", links)
            t0 = time.perf_counter()
            try:
                text, usage = llm.complete(client, backend=C.LLM_BACKEND, model=C.DEFAULT_MODEL,
                                           system=p.system, messages=p.messages,
                                           temperature=C.DEFAULT_TEMPERATURE)
                pc = postcheck(text, d, p.allowed_sources)
                final, cited, grounded, tokens = pc.kind, pc.cited, pc.grounded, usage.total
            except Exception as exc:  # noqa: BLE001
                final = f"error:{type(exc).__name__}"
            ms = int((time.perf_counter() - t0) * 1000)
        rows.append({"id": it["id"], "expected": it["expected"]["decision"], "got": final,
                     "cited": cited, "grounded": grounded, "tokens": tokens, "latency_ms": ms})

    answered = [r for r in rows if r["got"] == "answer"]
    llm_rows = [r for r in rows if r["latency_ms"]]
    return {
        "backend": C.LLM_BACKEND, "model": C.DEFAULT_MODEL, "n": len(rows),
        "final_accuracy": sum(r["got"] == r["expected"] for r in rows) / len(rows),
        "under_handoff": sum(r["expected"] == "handoff" and r["got"] == "answer" for r in rows),
        "grounded_rate": sum(bool(r["grounded"]) for r in answered) / len(answered) if answered else 0.0,
        "llm_declined_on_answerable": sum(r["expected"] == "answer" and r["got"] == "handoff"
                                          for r in llm_rows),
        "tokens_total": sum(r["tokens"] for r in rows),
        "latency_p50_ms": _pct([r["latency_ms"] for r in llm_rows], 50),
        "latency_p95_ms": _pct([r["latency_ms"] for r in llm_rows], 95),
        "rows": rows,
    }


# endregion 생성 평가

# region 원인 분석 (--explain)


def explain(items: list[dict], gid: str, kb, retriever) -> None:
    """문항 1개의 판정 과정을 단어 단위로 분해한다. 튜닝이 아니라 오답 분석용이다."""
    from assistant.kb import detect
    from assistant.text import SEP, strip_suffix

    it = next((i for i in items if i["id"] == gid), None)
    if it is None:
        print(f"  {gid} 없음")
        return
    state = ConversationState()
    for turn in it.get("turns", []):
        _, state = decide(turn, state, kb, retriever)
    d, _ = decide(it["question"], state, kb, retriever)
    det = detect(it["question"], kb)
    print(f"  {gid} [{it['type']}] {it['question']}")
    print(f"    정답 {it['expected']}  →  판정 {d.kind} {d.reason}  제품 {d.products}  앵커 {d.anchored}")
    print(f"    잔여 문장: {det.residual}")
    words = [w for w in det.residual.split(SEP) if w]
    for rank, h in enumerate(d.hits[:3], 1):
        grams = retriever._grams[h.chunk.id]
        detail = []
        for w in words:
            g = {x for x in bigrams(strip_suffix(w)) if x not in C.QUERY_STOP_BIGRAMS}
            if g:
                hit = len(g & grams) / len(g)
                idf = sum(retriever._idf(x) for x in g) / len(g)
                detail.append(f"{strip_suffix(w)}({hit:.0%},w{idf:.1f})")
        print(f"    {rank}위 {h.chunk.id:<12} lex {h.lexical:.3f}  " + " ".join(detail))


# endregion 원인 분석

# region 출력


def render(m: dict, ok: dict[str, bool], cond: dict) -> str:
    mark = lambda k: "✅" if ok.get(k, True) else "❌"  # noqa: E731
    return "\n".join([
        f"  조건   split={cond['split']} · mode={cond['mode']} · KB {cond['kb_sha256']} · 골든 {cond['golden_sha256']}",
        f"         임계값 앵커 {cond['min_lexical']:.2f} / 무앵커 {cond['min_lexical_unanchored']:.2f} · {m['n']}문항",
        "",
        "  [안전]",
        f"    {mark('under_handoff')} 넘길 질문에 답함(under)    {m['under_handoff']}건   (목표 0)",
        f"    {mark('wrong_answer')} 엉뚱한 문서로 답함         {m['wrong_answer']}건   (목표 0 · 추출형 기준)",
        f"    {mark('over_block')} 정상 질문 차단              {m['over_block']}건   (목표 0)",
        f"    {mark('block_recall')} 프롬프트 인젝션 차단율               {m['block_recall']:.1%}  (목표 100%)",
        "",
        "  [검색 순위]  — 게이트와 무관하게 정답 청크가 몇 위였나",
        f"    {mark('hit_at_1')} Hit@1 {m['hit_at_1']:.3f} (목표 0.80)   Recall@3 {m['recall_at_3']:.3f}   MRR {m['mrr']:.3f}",
        f"       제품 감지 정확도 {m['product_accuracy']:.1%}",
        "",
        "  [판정]",
        f"       판정 정확도 {m['decision_accuracy']:.1%}   정답 답변률 {m['answer_success']:.1%}",
        f"    {mark('over_handoff_rate')} 과잉 전환율 {m['over_handoff_rate']:.1%} (목표 ≤25%)   "
        f"전환 재현율 {m['handoff_recall']:.1%}   전환 사유 정확도 {m['reason_accuracy']:.1%}",
        "",
        f"  [지연]  판정+검색 p50 {m['latency_p50_ms']:.2f}ms   p95 {m['latency_p95_ms']:.2f}ms",
    ])


def write_report(split: str, cond: dict, m: dict, ok: dict, rows: list[dict]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"rag_eval_{split}_{cond['mode']}"
    fails = [r for r in rows if r["got"] != r["expected"] or (r["expected"] == "answer" and r["rank"] != 1)]
    (REPORT_DIR / f"{stem}.json").write_text(json.dumps(
        {"conditions": cond, "metrics": m, "targets_met": ok, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    lines = [f"# RAG 평가 보고서 — {split} / {cond['mode']}", "",
             f"- 실행 시각: {cond['run_at']}", f"- 지식 베이스: `{cond['kb_sha256']}` · 골든 데이터셋: `{cond['golden_sha256']}`",
             f"- 임계값: 앵커 {cond['min_lexical']:.2f} / 무앵커 {cond['min_lexical_unanchored']:.2f}", "",
             "| 지표 | 값 | 목표 | 판정 |", "|---|---|---|---|"]
    for k, (op, v) in TARGETS.items():
        val = m[k]
        shown = f"{val:.3f}" if isinstance(val, float) else str(val)
        lines.append(f"| {k} | {shown} | {op} {v} | {'✅' if ok[k] else '❌'} |")
    lines += ["", f"## 실패·오답 문항 ({len(fails)}건)", "",
              "| ID | 유형 | 질문 | 정답 | 결과 | 1위 청크 | 어휘 점수 |", "|---|---|---|---|---|---|---|"]
    for r in fails:
        lines.append(f"| {r['id']} | {r['type']} | {r['question']} | {r['expected']} | {r['got']} "
                     f"| {(r['top'] or ['-'])[0]} | {r['lexical']:.2f} |")
    (REPORT_DIR / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return REPORT_DIR / f"{stem}.md"


# endregion 출력


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="test")
    ap.add_argument("--sweep", action="store_true", help="dev 분할에서 임계값 스윕")
    ap.add_argument("--generate", action="store_true", help="LLM 생성까지 평가 (키 필요)")
    ap.add_argument("--strict", action="store_true", help="목표 미달 시 exit 1")
    ap.add_argument("--allow-contamination", action="store_true")
    ap.add_argument("--explain", metavar="ID", nargs="+", help="문항의 판정 과정을 단어 단위로 분해")
    args = ap.parse_args(argv)

    kb = load_kb()
    embedder = None
    if C.has_openai_key() and C.VECTORS_NPY.exists():
        from assistant.embed import make_embedder
        embedder = make_embedder(__import__("os").environ["OPENAI_API_KEY"])
    retriever = Retriever(kb, embedder)
    pre = preflight(kb, retriever)

    items, golden_hash = load_golden()
    errs = validate_golden(items, kb)
    if errs:
        print("❌ 골든 데이터셋 검증 실패:", *errs, sep="\n   - ")
        return 2

    print("━" * 72)
    print("  RAG 골든 데이터셋 평가")
    print("━" * 72)

    # ── 평가 오염 검사 ───────────────────────────────────────────────────────────
    contaminated = contamination(items, kb)
    fake_ok = prompt_example_is_fake(kb)
    print(f"  오염 검사  예상 질문 겹침 {len(contaminated)}건 · 프롬프트 예시 ID {'가짜 ✅' if fake_ok else '실제 ID ❌'}")
    for gid, q, ex, sim in contaminated:
        print(f"     {gid} '{q}' ≈ '{ex}' ({sim})")
    if (contaminated or not fake_ok) and not args.allow_contamination:
        print("  ❌ 평가 오염 — 문항을 고치지 말고 지식 베이스 예상 질문(보조 장치)을 바꿔라")
        return 2

    if args.explain:
        for gid in args.explain:
            explain(items, gid, kb, retriever)
            print()
        return 0

    if args.sweep:
        dev = [i for i in items if i["split"] == "dev"]
        print(f"\n  임계값 스윕 — dev {len(dev)}문항 · mode={pre['mode']}")
        sweep(dev, kb, retriever)
        return 0

    target = items if args.split == "all" else [i for i in items if i["split"] == args.split]
    if args.generate:
        g = generate_eval(target, kb, retriever)
        print(json.dumps({k: v for k, v in g.items() if k != "rows"}, ensure_ascii=False, indent=2))
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"rag_generate_{args.split}_{g['backend']}.json"
        out.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  → {out.relative_to(C.ROOT)}")
        return 0

    rows = run_items(target, kb, retriever)
    m = metrics(rows)
    ok = judge(m)
    cond = {**pre, "split": args.split, "golden_sha256": golden_hash,
            "min_lexical": C.HANDOFF_MIN_LEXICAL, "min_lexical_unanchored": C.HANDOFF_MIN_LEXICAL_UNANCHORED,
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    print(render(m, ok, cond))

    fails = [r for r in rows if r["got"] != r["expected"] or (r["expected"] == "answer" and r["rank"] != 1)]
    if fails:
        print(f"\n  실패·오답 {len(fails)}건")
        for r in fails:
            top = (r["top"] or ["-"])[0]
            print(f"    {r['id']} [{r['type']}] {r['question'][:30]:<30} 정답 {r['expected']:<8} → "
                  f"{r['got']:<8} 1위 {top:<12} lex {r['lexical']:.2f}")
    path = write_report(args.split, cond, m, ok, rows)
    print(f"\n  보고서 → {path.relative_to(C.ROOT)}")
    print("━" * 72)
    if args.strict and not all(ok.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
