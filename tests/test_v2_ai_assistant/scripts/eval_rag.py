"""
scripts/eval_rag.py
골든 데이터셋으로 RAG 파이프라인을 평가한다.

실행:
    python scripts/eval_rag.py                          # tune 분할(dev + 퇴역 test v1) · 키 불필요
    python scripts/eval_rag.py --split dev              # 1라운드 dev 만
    python scripts/eval_rag.py --sweep                  # tune 분할에서 근거 게이트 임계값 스윕
    python scripts/eval_rag.py --explain G-025 G-033    # 문항별 점수 분해
    python scripts/eval_rag.py --split test2 --label final   # 봉인된 test v2 (실행이 기록된다)
    python scripts/eval_rag.py --generate --backend both     # LLM 생성 평가 · Chat vs Responses (키 필요)
    python scripts/eval_rag.py --strict                 # 목표 미달 시 exit 1 (CI)

★ 평가 원칙
  1. 재현성 — 판정·검색 평가는 LLM 을 부르지 않는다. 같은 입력이면 같은 점수다.
  2. 평가 오염 차단 — 골든 데이터셋 문항이 지식 베이스 '예상 질문'이나 다른 분할 문항과 거의 같으면
     검색이 문장을 외워서 맞힌다. 겹침이 있으면 평가를 시작하지 않는다.
  3. 튜닝 / 측정 분리 — 임계값은 tune 분할에서만 고른다. 성능은 봉인된 test2 로만 보고한다.

★ 라운드별 분할
  1라운드: dev(23) 로 튜닝 → test(40) 로 측정. test v1 은 이 측정으로 소모됐다.
  2라운드: tune = dev + test v1 (63) 으로 튜닝 → test2(64, 봉인) 로 측정.

★ 지표를 두 층으로 나눈다
  검색 순위 — 게이트와 무관하게 정답 청크가 몇 위였나 (Hit@1 · Recall@3 · MRR)
  판정      — 답변 / 상담원 전환 / 되묻기 / 차단을 맞게 골랐나
  둘을 섞으면 "순위는 맞았는데 게이트가 막은 것"과 "순위부터 틀린 것"을 구분할 수 없다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import config as C
from assistant.kb import detect, load_kb, md_source_hash
from assistant.pipeline import SYSTEM_PROMPT, ConversationState, decide
from assistant.retriever import Retriever
from assistant.text import bigrams, squeeze

GOLDEN_FILES = {
    "v1": C.ROOT / "eval" / "golden_rag.json",       # dev · test
    "v2": C.ROOT / "eval" / "golden_rag_v2.json",    # test2 (봉인)
}
REPORT_DIR = C.ROOT / "eval" / "reports"
TEST2_LOG = Path(os.getenv("ASSISTANT_TEST2_LOG", REPORT_DIR / "test2_runs.log"))

DECISIONS = {"answer", "handoff", "clarify", "smalltalk", "blocked", "invalid"}
SPLITS = {"dev", "test", "test2"}
CONTAMINATION_THRESHOLD = 0.6     # bigram Jaccard 가 이 이상이면 오염으로 본다

# ── 목표 (미달이면 --strict 에서 실패) ─────────────────────────────────────────
TARGETS = {
    "under_handoff": ("==", 0),       # 넘겨야 할 질문에 답한 건수. 틀린 설치 안내 = 실제 피해
    "wrong_answer": ("==", 0),        # 답은 했는데 1위 문서가 틀린 건수. 엉뚱한 절차 안내 = 같은 피해
    "over_block": ("==", 0),          # 정상 질문을 인젝션으로 차단한 건수
    "block_recall": (">=", 1.0),
    "hit_at_1": (">=", 0.80),         # 검색 순위 — 정답 청크가 1위
    "over_handoff_rate": ("<=", 0.25),  # 답할 수 있는데 넘긴 비율. 안전한 실패라 느슨하게 둔다
}

# ── 사전 등록 판정 규칙 (측정 전에 고정한다 — 결과를 보고 바꾸지 않는다) ─────────
# 벡터 하이브리드 채택: tune 분할에서 local 대비 아래를 모두 만족하면 HYBRID_W_VECTOR 를 유지한다.
HYBRID_ADOPT_RULE = {"hit_at_1_not_lower": True, "wrong_answer_not_higher": True, "under_handoff_max": 0}
# Responses API 기본값 전환: tune 분할 생성 평가에서 chat 대비 아래를 모두 만족하면 responses 로 바꾼다.
#   동률이면 responses 를 고른다 — OpenAI 가 표준 인터페이스로 안내하는 쪽이 장기적으로 유지 비용이 낮다.
BACKEND_SWITCH_RULE = {
    "under_handoff_not_higher": True,
    "wrong_cite_not_higher": True,
    "cited_correct_rate_min_delta": -0.02,
    "latency_p95_max_ratio": 1.2,
}


# region 골든 데이터셋


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_golden() -> tuple[list[dict], dict[str, str]]:
    items: list[dict] = []
    hashes: dict[str, str] = {}
    for tag, path in GOLDEN_FILES.items():
        if path.exists():
            items += json.loads(path.read_text(encoding="utf-8"))
            hashes[tag] = _hash(path)[:16]
    return items, hashes


def select(items: list[dict], split: str) -> list[dict]:
    """tune = dev + 퇴역 test v1. all 은 test2 를 포함하지 않는다(실수로 봉인을 깨지 않도록)."""
    if split == "tune" or split == "all":
        return [i for i in items if i["split"] in ("dev", "test")]
    return [i for i in items if i["split"] == split]


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
        if it.get("split") not in SPLITS:
            errs.append(f"{gid}: split 은 {'|'.join(sorted(SPLITS))}")
        for cid in exp.get("chunk_ids", []):
            if kb.get(cid) is None:
                errs.append(f"{gid}: 존재하지 않는 청크 {cid}")
        if exp.get("decision") == "answer" and not exp.get("chunk_ids"):
            errs.append(f"{gid}: answer 인데 chunk_ids 가 없다")
    return errs


def contamination(items: list[dict], kb) -> list[tuple[str, str, str, float]]:
    """문항 ↔ (KB 예상 질문 · 다른 분할의 문항) 겹침. (문항 ID, 문항, 가장 닮은 문장, 유사도)

    ★ 다른 분할과도 비교한다 — test2 문항이 tune 문항과 거의 같으면, 튜닝한 문장을 다시 재는 셈이다.
    """
    refs = [("KB", q, bigrams(squeeze(q))) for c in kb.chunks for q in c.questions]
    refs += [(i["split"], i["question"], bigrams(squeeze(i["question"]))) for i in items]
    found = []
    for it in items:
        qg = bigrams(squeeze(it["question"]))
        if not qg:
            continue
        best = (0.0, "")
        for src, q, g in refs:
            if not g or q == it["question"] or src == it["split"]:
                continue
            best = max(best, (len(qg & g) / len(qg | g), q))
        if best[0] >= CONTAMINATION_THRESHOLD:
            found.append((it["id"], it["question"], best[1], round(best[0], 3)))
    return found


def prompt_example_is_fake(kb) -> bool:
    """시스템 프롬프트의 형식 예시 ID 가 실제 청크 ID 가 아닌지 — 실제 ID 면 모델이 베껴도 인용 검사를 통과한다."""
    return all(kb.get(cid) is None for cid in re.findall(r"KB-[A-Z0-9]+-\d{2}", SYSTEM_PROMPT))


def code_fingerprint() -> str:
    """판정에 영향을 주는 코드·설정의 해시 — test2 실행 기록에 남겨 '어떤 코드로 쟀는지'를 고정한다."""
    h = hashlib.sha256()
    for p in sorted((C.ROOT / "assistant").glob("*.py")):
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def log_test2_run(label: str, cond: dict, m: dict) -> int:
    """test2 실행을 기록하고 지금까지의 실행 횟수를 돌려준다."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    prev = TEST2_LOG.read_text(encoding="utf-8").splitlines() if TEST2_LOG.exists() else []
    line = json.dumps({
        "run_at": cond["run_at"], "label": label, "code": cond["code"], "golden_v2": cond["golden"].get("v2"),
        "tokenizer": cond["tokenizer"], "mode": cond["mode"],
        "under_handoff": m["under_handoff"], "wrong_answer": m["wrong_answer"],
        "hit_at_1": round(m["hit_at_1"], 3), "over_handoff_rate": round(m["over_handoff_rate"], 3),
    }, ensure_ascii=False)
    TEST2_LOG.write_text("\n".join([*prev, line]) + "\n", encoding="utf-8")
    return len(prev) + 1


# endregion 골든 데이터셋

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
            "id": it["id"], "type": it["type"], "style": it.get("style", ""), "question": it["question"],
            "expected": exp["decision"], "got": d.kind,
            "expected_reason": exp.get("reason"), "reason": d.reason,
            "expected_product": exp.get("product"), "products": d.products,
            "accepted": sorted(accepted), "top": d.hit_ids[:3], "rank": rank,
            "lexical": d.gate_lexical, "vector": d.gate_vector,
            "anchored": d.anchored, "needs_clarify": d.needs_clarify,
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
        "decision_accuracy": sum(r["got"] == r["expected"] for r in rows) / len(rows) if rows else 0.0,
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


def by_style(rows: list[dict]) -> dict[str, dict]:
    """문체별 정답 답변률 — 형태소 분석기가 겨냥한 구어체·공손체에서 실제로 나아졌는지 본다."""
    out: dict[str, dict] = {}
    for r in rows:
        if r["expected"] != "answer" or not r["style"]:
            continue
        s = out.setdefault(r["style"], {"n": 0, "ok": 0})
        s["n"] += 1
        s["ok"] += int(r["got"] == "answer" and r["rank"] == 1)
    return out


def judge(m: dict) -> dict[str, bool]:
    ops = {"==": lambda a, b: a == b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}
    return {k: ops[op](m[k], v) for k, (op, v) in TARGETS.items()}


# endregion 실행 · 지표

# region 임계값 스윕


def _largest_gap_midpoint(outs: list[float], ins: list[float], max_over: float | None = None) -> dict | None:
    """여유 최대화 규칙 — OUT 최고점 위에서, 가장 넓은 빈틈의 한가운데를 고른다.

    ★ "성능이 같으면 가장 보수적인 값"을 고르면 옆 문항과의 여유가 거의 없다(1라운드 실측 0.009).
      처음 보는 질문의 점수가 조금만 흔들려도 판정이 뒤집힌다.
    """
    low = max(outs, default=0.0)
    ins = sorted(ins)
    edges = sorted({low, *(v for v in ins if v > low), 1.0})
    best = None
    for lo, hi in zip(edges, edges[1:]):
        t = (lo + hi) / 2
        over = sum(v < t for v in ins) / len(ins) if ins else 0.0
        if max_over is not None and over > max_over:
            continue
        if best is None or hi - lo > best["gap"]:
            best = {"threshold": round(t, 2), "gap": hi - lo, "lo": lo, "hi": hi, "over": over}
    return best


def _first_gap_midpoint(outs: list[float], ins: list[float]) -> dict | None:
    """넘길 문항 최고점과, 그보다 높은 답할 문항 최저점 사이의 한가운데.

    ★ 목표 제약이 없을 때(벡터 게이트 · 무앵커 안전 우선)는 '가장 넓은 빈틈'을 쓰면 안 된다.
      가장 넓은 빈틈이 답할 문항들보다 위에 있으면 임계값이 아무 문항도 통과시키지 못한다
      (단위 테스트가 잡은 결함: 넘길 0.52 · 답할 0.64/0.71 에서 0.85 를 골랐다).
    """
    low = max(outs, default=0.0)
    above = sorted(v for v in ins if v > low)
    if not above:
        return None
    hi = above[0]
    return {"threshold": round((low + hi) / 2, 2), "gap": hi - low, "lo": low, "hi": hi,
            "over": sum(v < (low + hi) / 2 for v in ins) / len(ins) if ins else 0.0}


def _gate_rows(rows: list[dict], anchored: bool) -> list[dict]:
    """근거 게이트에서 판정이 갈린 문항만. 범위 게이트 · 입력 검증 · 되묻기에서 끝난 문항은 임계값과 무관하다."""
    return [r for r in rows
            if r["anchored"] == anchored and not r["needs_clarify"]
            and r["reason"] in ("", "low_relevance") and r["got"] in ("answer", "handoff")]


def recommend_lexical(rows: list[dict]) -> dict[bool, dict]:
    result = {}
    for anchored in (True, False):
        g = _gate_rows(rows, anchored)
        outs = [r["lexical"] for r in g if r["expected"] == "handoff"]
        # IN 은 1위가 정답인 문항만 — 순위가 틀린 문항을 통과시키면 틀린 안내가 된다
        ins = [r["lexical"] for r in g if r["expected"] == "answer" and r["rank"] == 1]
        best = _largest_gap_midpoint(outs, ins, TARGETS["over_handoff_rate"][1])
        fallback = best is None
        if fallback:
            # 과잉 전환 목표를 지키는 빈틈이 없으면(문항이 적은 그룹) 안전을 우선한다 —
            # 넘길 문항 최고점 바로 위. 넘길 질문에 답하지 않는 것이 목표보다 앞선다.
            best = _first_gap_midpoint(outs, ins)
        result[anchored] = {"best": best, "fallback": fallback,
                            "out_max": max(outs, default=0.0), "ins": sorted(ins)}
    return result


def recommend_vector(rows: list[dict]) -> dict:
    """벡터 게이트 권장값 — 사전 등록 규칙.

    대상: 앵커 질문 중 근거 게이트에서 갈린 문항.
      OUT = 넘겨야 할 문항의 1위 코사인 (전부)
      IN  = 1위가 정답인데 어휘 게이트를 못 넘은 문항의 1위 코사인 (벡터가 구제해야 할 문항)
    규칙: IN 중 OUT 최고점보다 큰 값이 있으면, 그 위 가장 넓은 빈틈의 한가운데. 없으면 켜지 않는다(1.01).
    """
    g = [r for r in _gate_rows(rows, True) if r["vector"] is not None]
    outs = [r["vector"] for r in g if r["expected"] == "handoff"]
    ins = [r["vector"] for r in g if r["expected"] == "answer" and r["rank"] == 1
           and r["lexical"] < C.HANDOFF_MIN_LEXICAL]
    out_max = max(outs, default=0.0)
    rescuable = [v for v in ins if v > out_max]
    if not g:
        return {"status": "no_vector", "threshold": None}
    if not rescuable:
        return {"status": "keep_off", "threshold": 1.01, "out_max": out_max, "ins": sorted(ins)}
    best = _first_gap_midpoint(outs, rescuable)
    return {"status": "enable", "threshold": best["threshold"], "out_max": out_max, "ins": sorted(ins),
            "rescued": len(rescuable), "gap": best["gap"]}


def sweep(items: list[dict], kb, retriever) -> None:
    grid_a = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    orig = (C.HANDOFF_MIN_LEXICAL, C.HANDOFF_MIN_LEXICAL_UNANCHORED)
    print("\n  앵커 임계값 │ under  wrong  over-rate  정확도   (무앵커 고정 %.2f)" % orig[1])
    print("  ──────────┼──────────────────────────────────")
    for a in grid_a:
        C.HANDOFF_MIN_LEXICAL = a
        m = metrics(run_items(items, kb, retriever))
        print(f"     {a:.2f}   │  {m['under_handoff']:>3}    {m['wrong_answer']:>3}    "
              f"{m['over_handoff_rate']:6.1%}    {m['decision_accuracy']:6.1%}")
    C.HANDOFF_MIN_LEXICAL = orig[0]

    rows = run_items(items, kb, retriever)
    rec = recommend_lexical(rows)
    print("\n  [어휘 게이트 — 여유 최대화 권장]")
    for anchored, label in ((True, "앵커  "), (False, "무앵커")):
        r = rec[anchored]
        print(f"    {label} OUT 최고점 {r['out_max']:.3f} │ IN {len(r['ins'])}건 "
              f"[{', '.join(f'{v:.2f}' for v in r['ins'][:12])}{' …' if len(r['ins']) > 12 else ''}]")
        b = r["best"]
        if b:
            note = "  (과잉 전환 목표를 지키는 빈틈이 없어 안전 우선 규칙 적용)" if r["fallback"] else ""
            print(f"           → 빈틈 ({b['lo']:.3f}, {b['hi']:.3f}) 한가운데 {b['threshold']:.2f}"
                  f" · 양쪽 여유 ±{b['gap'] / 2:.3f} · 이 그룹 과잉 전환 {b['over']:.0%}{note}")
    print(f"    현재 설정: 앵커 {orig[0]:.2f} / 무앵커 {orig[1]:.2f}")

    v = recommend_vector(rows)
    print("\n  [벡터 게이트 — 사전 등록 규칙]")
    if v["status"] == "no_vector":
        print("    벡터 점수가 없다(local 모드). build_index.py 를 실행하고 키를 넣은 뒤 다시 돌린다.")
    elif v["status"] == "keep_off":
        print(f"    OUT 최고 코사인 {v['out_max']:.3f} 보다 높은 구제 대상이 없다 → 켜지 않는다(1.01)")
    else:
        print(f"    OUT 최고 코사인 {v['out_max']:.3f} · 구제 가능 {v['rescued']}건 → "
              f"ASSISTANT_HANDOFF_MIN_VECTOR={v['threshold']:.2f} (빈틈 {v['gap']:.3f})")


def vector_report(items: list[dict], kb, embedder, split: str) -> dict:
    """local(어휘) vs hybrid(어휘+벡터)를 같은 분할에서 비교하고 사전 등록 규칙으로 결론을 낸다.

    ① 하이브리드 순위 채택 (HYBRID_ADOPT_RULE) — Hit@1 이 떨어지지 않고 wrong-answer 가 늘지 않아야 한다.
    ② 벡터 게이트 임계값 (recommend_vector) — 구제할 문항이 넘길 문항보다 확실히 높을 때만 켠다.
    ③ ②의 값을 적용했을 때 under-handoff 가 0 인지 시뮬레이션으로 확인한다.
    """
    local, hybrid = Retriever(kb, None), Retriever(kb, embedder)
    if hybrid.mode != "hybrid":
        raise SystemExit("❌ 벡터 인덱스를 읽지 못했다 — python scripts/build_index.py 를 먼저 실행하라")
    ml, rows_h = metrics(run_items(items, kb, local)), run_items(items, kb, hybrid)
    mh = metrics(rows_h)
    adopt = [
        (mh["hit_at_1"] >= ml["hit_at_1"], f"Hit@1 hybrid {mh['hit_at_1']:.3f} ≥ local {ml['hit_at_1']:.3f}"),
        (mh["wrong_answer"] <= ml["wrong_answer"], f"wrong-answer hybrid {mh['wrong_answer']} ≤ local {ml['wrong_answer']}"),
        (mh["under_handoff"] <= HYBRID_ADOPT_RULE["under_handoff_max"], f"under-handoff hybrid {mh['under_handoff']} = 0"),
    ]
    vec = recommend_vector(rows_h)
    sim = None
    if vec["status"] == "enable":
        orig = C.HANDOFF_MIN_VECTOR
        C.HANDOFF_MIN_VECTOR = vec["threshold"]
        try:
            sim = metrics(run_items(items, kb, hybrid))
        finally:
            C.HANDOFF_MIN_VECTOR = orig
        if sim["under_handoff"] > 0:   # 안전 확인에 실패하면 켜지 않는다
            vec = {**vec, "status": "rejected_unsafe"}

    adopt_ok = all(ok for ok, _ in adopt)
    lines = [f"# 벡터 검색 평가 — {split}", "", f"- 실행 시각: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             f"- 임베딩 모델: `{C.EMBED_MODEL}` · 문항 {ml['n']}", "",
             "| 지표 | local | hybrid | hybrid + 벡터 게이트 |", "|---|---|---|---|"]
    for k in ("under_handoff", "wrong_answer", "hit_at_1", "recall_at_3", "over_handoff_rate", "decision_accuracy"):
        fmt = (lambda v: f"{v:.3f}") if isinstance(ml[k], float) else str
        lines.append(f"| {k} | {fmt(ml[k])} | {fmt(mh[k])} | {fmt(sim[k]) if sim else '—'} |")
    lines += ["", f"## ① 하이브리드 순위 채택 → **{'채택' if adopt_ok else '기각 (ASSISTANT_W_VECTOR=0)'}**", ""]
    lines += [f"- {'✅' if ok else '❌'} {t}" for ok, t in adopt]
    lines += ["", "## ② 벡터 게이트", ""]
    if vec["status"] == "enable":
        lines.append(f"- 켠다: `ASSISTANT_HANDOFF_MIN_VECTOR={vec['threshold']:.2f}` "
                     f"(넘길 문항 최고 코사인 {vec['out_max']:.3f} · 구제 {vec['rescued']}건 · 빈틈 {vec['gap']:.3f})")
    elif vec["status"] == "rejected_unsafe":
        lines.append(f"- 켜지 않는다: 권장값 {vec['threshold']:.2f} 적용 시 under-handoff {sim['under_handoff']}건")
    else:
        lines.append(f"- 켜지 않는다: 넘길 문항 최고 코사인 {vec.get('out_max', 0):.3f} 보다 높은 구제 대상이 없다")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"rag_vector_{split}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"local": ml, "hybrid": mh, "gated": sim, "adopt": adopt_ok, "vector_gate": vec, "path": path}


# endregion 임계값 스윕

# region 원인 분석 (--explain)


def explain(items: list[dict], ids: list[str], kb, retriever) -> None:
    """문항의 판정 과정을 단위별로 분해한다. 튜닝이 아니라 오답 분석용이다."""
    for gid in ids:
        it = next((i for i in items if i["id"] == gid), None)
        if it is None:
            print(f"  {gid} 없음")
            continue
        if it["split"] == "test2":
            print(f"  {gid}: 봉인된 test2 문항은 분해하지 않는다 (최종 측정 후 --allow-test2-explain)")
            if "--allow-test2-explain" not in sys.argv:
                continue
        state = ConversationState()
        for turn in it.get("turns", []):
            _, state = decide(turn, state, kb, retriever)
        d, _ = decide(it["question"], state, kb, retriever)
        det = detect(it["question"], kb)
        q = retriever.analyze(det.residual)
        print(f"  {gid} [{it['type']}] {it['question']}")
        print(f"    정답 {it['expected']['decision']} {it['expected'].get('chunk_ids', '')}  →  "
              f"판정 {d.kind} {d.reason}  제품 {d.products}  앵커 {d.anchored}  지시어 {q.deictic}")
        print(f"    단위({retriever.tokenizer}): " + " ".join(f"{u.label}(w{retriever.weight(u):.1f})" for u in q.units))
        for rank, h in enumerate(d.hits[:3], 1):
            detail = " ".join(f"{u.label}:{retriever.match(u, h.chunk.id):.0%}" for u in q.units)
            print(f"    {rank}위 {h.chunk.id:<12} lex {h.lexical:.3f}  {detail}")
        print()


# endregion 원인 분석

# region 생성 평가 (--generate)


def generate_eval(items: list[dict], kb, retriever, backend: str, client, model: str) -> dict:
    """LLM 까지 돌려 최종 판정 · 인용 정확도를 본다. 비결정적이므로 CI 에서 돌리지 않는다.

    ★ 핵심 지표: cited_correct_rate
      추출형은 1위 문서만 보여주지만 생성형은 상위 3개를 근거로 받는다(Recall@3).
      1위가 틀린 문항(wrong-answer)을 생성 단계가 정답 문서를 골라 인용해 복구하는지 잰다.
    """
    from assistant import llm
    from assistant.kb import load_links
    from assistant.pipeline import build_prompt, postcheck

    links = load_links()
    rows = []
    for it in items:
        state = ConversationState()
        for turn in it.get("turns", []):
            _, state = decide(turn, state, kb, retriever)
        d, _ = decide(it["question"], state, kb, retriever)
        accepted = set(it["expected"].get("chunk_ids", []))
        row = {"id": it["id"], "expected": it["expected"]["decision"], "got": d.kind, "reason": d.reason,
               "cited": [], "grounded": None, "removed": 0, "tokens": 0, "latency_ms": 0,
               "top1_correct": bool(d.hit_ids) and d.hit_ids[0] in accepted, "accepted": sorted(accepted)}
        if d.kind == "answer":
            p = build_prompt(it["question"], d, [], "", links)
            t0 = time.perf_counter()
            try:
                text, usage = llm.complete(client, backend=backend, model=model, system=p.system,
                                           messages=p.messages, temperature=C.DEFAULT_TEMPERATURE)
                pc = postcheck(text, d, p.allowed_sources)
                row.update(got=pc.kind, reason=pc.reason, cited=pc.cited, grounded=pc.grounded,
                           removed=len(pc.removed), tokens=usage.total)
            except Exception as exc:  # noqa: BLE001
                row.update(got="error", reason=type(exc).__name__)
            row["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        rows.append(row)

    answered = [r for r in rows if r["got"] == "answer"]
    graded = [r for r in answered if r["accepted"]]
    llm_rows = [r for r in rows if r["latency_ms"]]
    return {
        "backend": backend, "model": model, "n": len(rows),
        "final_accuracy": sum(r["got"] == r["expected"] for r in rows) / len(rows) if rows else 0.0,
        "under_handoff": sum(r["expected"] == "handoff" and r["got"] == "answer" for r in rows),
        # 인용이 하나라도 정답 문서면 정답 근거로 답한 것이다
        "cited_correct_rate": (sum(bool(set(r["cited"]) & set(r["accepted"])) for r in graded) / len(graded)
                               if graded else 0.0),
        # 인용은 했는데 정답 문서가 하나도 없다 = 엉뚱한 근거로 답했다
        "wrong_cite": sum(bool(r["cited"]) and not set(r["cited"]) & set(r["accepted"]) for r in graded),
        "recovered_from_wrong_top1": sum(not r["top1_correct"] and bool(set(r["cited"]) & set(r["accepted"]))
                                         for r in graded),
        "grounded_rate": sum(bool(r["grounded"]) for r in answered) / len(answered) if answered else 0.0,
        "invalid_citation": sum(r["reason"] == "invalid_citation" for r in rows),
        "llm_declined_on_answerable": sum(r["expected"] == "answer" and r["reason"] == "llm_declined" for r in rows),
        "links_removed": sum(r["removed"] for r in rows),
        "errors": sum(r["got"] == "error" for r in rows),
        "tokens_total": sum(r["tokens"] for r in rows),
        "latency_p50_ms": _pct([r["latency_ms"] for r in llm_rows], 50),
        "latency_p95_ms": _pct([r["latency_ms"] for r in llm_rows], 95),
        "rows": rows,
    }


def decide_backend(chat: dict, resp: dict) -> tuple[str, list[str]]:
    """사전 등록 규칙(BACKEND_SWITCH_RULE)으로 기본 백엔드를 고른다."""
    R = BACKEND_SWITCH_RULE
    checks = [
        (resp["under_handoff"] <= chat["under_handoff"], f"under_handoff {resp['under_handoff']} ≤ {chat['under_handoff']}"),
        (resp["wrong_cite"] <= chat["wrong_cite"], f"wrong_cite {resp['wrong_cite']} ≤ {chat['wrong_cite']}"),
        (resp["cited_correct_rate"] >= chat["cited_correct_rate"] + R["cited_correct_rate_min_delta"],
         f"정답 인용률 {resp['cited_correct_rate']:.3f} ≥ {chat['cited_correct_rate']:.3f}{R['cited_correct_rate_min_delta']:+.2f}"),
        (resp["latency_p95_ms"] <= chat["latency_p95_ms"] * R["latency_p95_max_ratio"] or not chat["latency_p95_ms"],
         f"p95 {resp['latency_p95_ms']}ms ≤ {chat['latency_p95_ms']}ms × {R['latency_p95_max_ratio']}"),
    ]
    lines = [("✅ " if ok else "❌ ") + text for ok, text in checks]
    return ("responses" if all(ok for ok, _ in checks) else "chat"), lines


def write_generate_report(split: str, results: list[dict], decision: tuple[str, list[str]] | None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    keys = [("final_accuracy", "최종 판정 정확도", "{:.1%}"), ("under_handoff", "넘길 질문에 답함", "{}"),
            ("cited_correct_rate", "정답 문서 인용률", "{:.1%}"), ("wrong_cite", "엉뚱한 문서 인용", "{}"),
            ("recovered_from_wrong_top1", "1위 오답 복구", "{}"), ("grounded_rate", "인용 표시율", "{:.1%}"),
            ("invalid_citation", "검색 안 한 문서 인용", "{}"), ("llm_declined_on_answerable", "답할 질문 거절", "{}"),
            ("links_removed", "미확인 링크 삭제", "{}"), ("errors", "호출 오류", "{}"),
            ("tokens_total", "토큰 합계", "{:,}"), ("latency_p50_ms", "지연 p50 (ms)", "{}"),
            ("latency_p95_ms", "지연 p95 (ms)", "{}")]
    head = "| 지표 | " + " | ".join(f"{r['backend']} ({r['model']})" for r in results) + " |"
    lines = [f"# 생성 평가 — {split}", "", f"- 실행 시각: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
             f"- 문항 수: {results[0]['n']}", "", head, "|---|" + "---|" * len(results)]
    for k, label, fmt in keys:
        lines.append(f"| {label} | " + " | ".join(fmt.format(r[k]) for r in results) + " |")
    if decision:
        lines += ["", f"## 사전 등록 규칙 판정 → 기본 백엔드: **{decision[0]}**", ""]
        lines += [f"- {x}" for x in decision[1]]
    path = REPORT_DIR / f"rag_generate_{split}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (REPORT_DIR / f"rag_generate_{split}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
    return path


# endregion 생성 평가

# region 출력


def render(m: dict, ok: dict[str, bool], cond: dict) -> str:
    mark = lambda k: "✅" if ok.get(k, True) else "❌"  # noqa: E731
    return "\n".join([
        f"  조건   split={cond['split']} · tokenizer={cond['tokenizer']} · mode={cond['mode']} · code {cond['code']}",
        f"         KB {cond['kb_sha256']} · 골든 {cond['golden']} · {m['n']}문항",
        f"         임계값 앵커 {cond['min_lexical']:.2f} / 무앵커 {cond['min_lexical_unanchored']:.2f}"
        f" / 벡터 {cond['min_vector']:.2f}",
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


def write_report(cond: dict, m: dict, ok: dict, rows: list[dict], styles: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"rag_eval_{cond['split']}_{cond['tokenizer']}_{cond['mode']}"
    if cond.get("label"):
        stem += f"_{cond['label']}"
    fails = [r for r in rows if r["got"] != r["expected"] or (r["expected"] == "answer" and r["rank"] != 1)]
    (REPORT_DIR / f"{stem}.json").write_text(json.dumps(
        {"conditions": cond, "metrics": m, "targets_met": ok, "by_style": styles, "rows": rows},
        ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# RAG 평가 보고서 — {cond['split']} / {cond['tokenizer']} / {cond['mode']}"
             + (f" / {cond['label']}" if cond.get("label") else ""), "",
             f"- 실행 시각: {cond['run_at']}",
             f"- 코드: `{cond['code']}` · 지식 베이스: `{cond['kb_sha256']}` · 골든: `{cond['golden']}`",
             f"- 임계값: 앵커 {cond['min_lexical']:.2f} / 무앵커 {cond['min_lexical_unanchored']:.2f}"
             f" / 벡터 {cond['min_vector']:.2f}", "",
             "| 지표 | 값 | 목표 | 판정 |", "|---|---|---|---|"]
    for k, (op, v) in TARGETS.items():
        val = m[k]
        shown = f"{val:.3f}" if isinstance(val, float) else str(val)
        lines.append(f"| {k} | {shown} | {op} {v} | {'✅' if ok[k] else '❌'} |")
    if styles:
        lines += ["", "## 문체별 정답 답변률", "", "| 문체 | 정답 | 문항 | 비율 |", "|---|---|---|---|"]
        for s, v in sorted(styles.items()):
            lines.append(f"| {s} | {v['ok']} | {v['n']} | {v['ok'] / v['n']:.0%} |")
    lines += ["", f"## 실패·오답 문항 ({len(fails)}건)", "",
              "| ID | 유형 | 질문 | 정답 | 결과 | 1위 청크 | 어휘 점수 |", "|---|---|---|---|---|---|---|"]
    for r in fails:
        lines.append(f"| {r['id']} | {r['type']} | {r['question']} | {r['expected']} | {r['got']} "
                     f"| {(r['top'] or ['-'])[0]} | {r['lexical']:.2f} |")
    path = REPORT_DIR / f"{stem}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# endregion 출력


def build_retriever(kb) -> Retriever:
    embedder = None
    if C.has_openai_key() and C.VECTORS_NPY.exists():
        from assistant.embed import make_embedder
        embedder = make_embedder(os.environ["OPENAI_API_KEY"])
    return Retriever(kb, embedder)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "tune", "test2", "all"], default="tune",
                    help="tune = dev + 퇴역한 test v1 (2라운드 튜닝셋) · test2 = 봉인된 test v2")
    ap.add_argument("--label", default="", help="test2 실행 기록에 남길 이름 (예: baseline, final)")
    ap.add_argument("--sweep", action="store_true", help="tune 분할에서 임계값 스윕")
    ap.add_argument("--explain", metavar="ID", nargs="+", help="문항의 판정 과정을 단위별로 분해")
    ap.add_argument("--allow-test2-explain", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--vector", action="store_true", help="local vs hybrid 비교 · 벡터 게이트 규칙 적용 (키 필요)")
    ap.add_argument("--generate", action="store_true", help="LLM 생성까지 평가 (키 필요)")
    ap.add_argument("--backend", choices=["chat", "responses", "both"], default="both")
    ap.add_argument("--strict", action="store_true", help="목표 미달 시 exit 1")
    ap.add_argument("--allow-contamination", action="store_true")
    args = ap.parse_args(argv)

    kb = load_kb()
    if kb.source_sha256 != md_source_hash(C.KB_MD):
        raise SystemExit("❌ install_kb.md 가 json 보다 최신이다 → python scripts/build_kb.py 먼저 실행")
    retriever = build_retriever(kb)

    items, golden_hash = load_golden()
    errs = validate_golden(items, kb)
    if errs:
        print("❌ 골든 데이터셋 검증 실패:", *errs, sep="\n   - ")
        return 2

    print("━" * 72)
    print("  RAG 골든 데이터셋 평가")
    print("━" * 72)

    contaminated = contamination(items, kb)
    fake_ok = prompt_example_is_fake(kb)
    print(f"  오염 검사  겹침 {len(contaminated)}건 · 프롬프트 예시 ID {'가짜 ✅' if fake_ok else '실제 ID ❌'}")
    for gid, q, ex, sim in contaminated:
        print(f"     {gid} '{q}' ≈ '{ex}' ({sim})")
    if (contaminated or not fake_ok) and not args.allow_contamination:
        print("  ❌ 평가 오염 — 봉인된 문항은 고치지 말고 지식 베이스 예상 질문(보조 장치)을 바꿔라 (기준 문서 §5.4)")
        return 2

    if args.explain:
        explain(items, args.explain, kb, retriever)
        return 0

    target = select(items, args.split)
    if args.sweep:
        if args.split == "test2":
            print("  ❌ 봉인된 test2 로 임계값을 고르지 않는다")
            return 2
        print(f"\n  임계값 스윕 — {args.split} {len(target)}문항 · tokenizer={retriever.tokenizer} · mode={retriever.mode}")
        sweep(target, kb, retriever)
        return 0

    if args.vector:
        if args.split == "test2":
            print("  ❌ 봉인된 test2 로 벡터 게이트를 고르지 않는다")
            return 2
        if not C.has_openai_key():
            raise SystemExit("❌ --vector 는 OPENAI_API_KEY 가 필요하다 (질문 임베딩)")
        from assistant.embed import make_embedder
        rep = vector_report(target, kb, make_embedder(os.environ["OPENAI_API_KEY"]), args.split)
        print(f"  하이브리드 순위 {'채택' if rep['adopt'] else '기각'} · 벡터 게이트 {rep['vector_gate']['status']}"
              f" {rep['vector_gate'].get('threshold') or ''}")
        print(f"  보고서 → {rep['path'].relative_to(C.ROOT)}")
        return 0

    if args.generate:
        if not C.has_openai_key():
            raise SystemExit("❌ --generate 는 OPENAI_API_KEY 가 필요하다 (.env 또는 환경 변수)")
        from assistant import llm
        client = llm.make_client(os.environ["OPENAI_API_KEY"])
        backends = ["chat", "responses"] if args.backend == "both" else [args.backend]
        results = [generate_eval(target, kb, retriever, b, client, C.DEFAULT_MODEL) for b in backends]
        decision = decide_backend(results[0], results[1]) if len(results) == 2 else None
        for r in results:
            print(f"  {r['backend']:<10} 정답 인용률 {r['cited_correct_rate']:.1%} · 엉뚱한 인용 {r['wrong_cite']} · "
                  f"under {r['under_handoff']} · p95 {r['latency_p95_ms']}ms · 토큰 {r['tokens_total']:,}")
        if decision:
            print(f"\n  사전 등록 규칙 → 기본 백엔드 {decision[0]}")
            for line in decision[1]:
                print("    " + line)
        print(f"  보고서 → {write_generate_report(args.split, results, decision).relative_to(C.ROOT)}")
        if args.split == "test2":
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            with TEST2_LOG.open("a", encoding="utf-8") as f:
                for r in results:
                    f.write(json.dumps({"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                        "label": f"generate_{r['backend']}", "code": code_fingerprint(),
                                        "cited_correct_rate": round(r["cited_correct_rate"], 3),
                                        "under_handoff": r["under_handoff"]}, ensure_ascii=False) + "\n")
        return 0

    rows = run_items(target, kb, retriever)
    m = metrics(rows)
    ok = judge(m)
    tokenizer = getattr(retriever, "tokenizer", "bigram")
    cond = {"split": args.split, "label": args.label, "tokenizer": tokenizer, "mode": retriever.mode,
            "code": code_fingerprint(), "kb_sha256": kb.source_sha256[:16], "golden": golden_hash,
            "min_lexical": getattr(C, "HANDOFF_MIN_LEXICAL" if tokenizer == "kiwi" else "HANDOFF_MIN_LEXICAL_BIGRAM",
                                   C.HANDOFF_MIN_LEXICAL),
            "min_lexical_unanchored": getattr(C, "HANDOFF_MIN_LEXICAL_UNANCHORED" if tokenizer == "kiwi"
                                              else "HANDOFF_MIN_LEXICAL_UNANCHORED_BIGRAM",
                                              C.HANDOFF_MIN_LEXICAL_UNANCHORED),
            "min_vector": C.HANDOFF_MIN_VECTOR,
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    print(render(m, ok, cond))

    styles = by_style(rows)
    if styles:
        print("\n  [문체별 정답 답변률]  " + "   ".join(
            f"{s} {v['ok']}/{v['n']}" for s, v in sorted(styles.items())))

    fails = [r for r in rows if r["got"] != r["expected"] or (r["expected"] == "answer" and r["rank"] != 1)]
    if fails:
        print(f"\n  실패·오답 {len(fails)}건")
        for r in fails:
            top = (r["top"] or ["-"])[0]
            print(f"    {r['id']} [{r['type']}] {r['question'][:30]:<30} 정답 {r['expected']:<8} → "
                  f"{r['got']:<8} 1위 {top:<12} lex {r['lexical']:.2f}")
    path = write_report(cond, m, ok, rows, styles)
    print(f"\n  보고서 → {path.relative_to(C.ROOT)}")
    if args.split == "test2":
        n = log_test2_run(args.label or "unlabeled", cond, m)
        print(f"  test2 실행 기록 {n}회째 → {TEST2_LOG}"
              + ("   ⚠️ 2회를 넘었다 — 보고서에 그 사실을 적는다" if n > 2 else ""))
    print("━" * 72)
    if args.strict and not all(ok.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
