"""
scripts/build_index.py
data/install_kb.json  →  data/kb_index/{vectors.npy, meta.json}

실행:
    python scripts/build_index.py        # OPENAI_API_KEY 필요 (.env 또는 환경 변수)

★ 산출물은 Git 에 커밋한다
  · 26청크 × 1536차원 × float32 ≈ 160KB — 저장소 부담이 없다.
  · 임베딩 호출이 한 번으로 끝난다. 비용은 약 $0.0002 다.
  · 누가 돌려도 같은 벡터를 쓰므로 골든 데이터셋 점수가 재현된다.
    각자 임베딩하면 같은 코드인데 점수가 달라져 원인을 추적할 수 없다.

★ meta.json 에 지식 베이스 해시와 모델명을 넣는다
  md 를 고치고 인덱스를 다시 만들지 않으면 검색기가 감지해 local 모드로 내려간다.
  모델을 바꿨는데 예전 벡터를 쓰면 질문 벡터와 문서 벡터가 서로 다른 공간이라 점수가 무의미해진다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import config as C
from assistant.kb import md_source_hash

BATCH = 64


def assert_kb_is_current() -> dict:
    """install_kb.json 이 install_kb.md 의 최신 상태인지 확인하고 json 을 돌려준다.

    ★ 이 검사가 없으면 이전 버전 벡터가 만들어진다
      md 를 고치고 build_kb.py 를 안 돌린 채 여기를 실행하면 이전 버전 청크로 임베딩하고,
      meta 에도 이전 버전 해시가 들어가 검색기의 해시 비교를 통과해 버린다.
      어느 단계에서도 드러나지 않는 유일한 경로라 여기서 막는다.

    ★ 해시는 kb.md_source_hash() 하나로만 계산한다
      빌드 스크립트는 줄바꿈을 변환한 텍스트로, 인덱스 스크립트는 원본 바이트로 해시하면
      Windows(CRLF)에서 내용이 같아도 "인덱스가 낡았다"는 거짓 오류가 난다.
    """
    if not C.KB_JSON.exists():
        raise SystemExit("❌ install_kb.json 이 없다 → python scripts/build_kb.py 를 먼저 실행하라")
    kb = json.loads(C.KB_JSON.read_text(encoding="utf-8"))
    md_hash = md_source_hash(C.KB_MD)
    if kb.get("source_sha256") != md_hash:
        raise SystemExit(
            "❌ install_kb.md 가 install_kb.json 보다 최신이다.\n"
            f"   md   : {md_hash[:16]}\n"
            f"   json : {kb.get('source_sha256', '')[:16]}\n"
            "   → python scripts/build_kb.py 를 먼저 실행하라."
        )
    return kb


def build(client=None) -> dict:
    """임베딩을 만들어 저장하고 meta 를 돌려준다. client 는 테스트용 주입 지점이다."""
    import numpy as np

    kb = assert_kb_is_current()
    if client is None:
        if not C.has_openai_key():
            raise SystemExit("❌ OPENAI_API_KEY 가 없다 (.env 또는 환경 변수)")
        from openai import OpenAI

        client = OpenAI(timeout=60, max_retries=2)

    chunks = kb["chunks"]
    texts = [c["embed_text"] for c in chunks]
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        resp = client.embeddings.create(model=C.EMBED_MODEL, input=texts[i:i + BATCH])
        vectors.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        print(f"  임베딩 {min(i + BATCH, len(texts))}/{len(texts)}")

    mat = np.asarray(vectors, dtype="float32")
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9   # 정규화 → 내적 = 코사인

    C.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(C.VECTORS_NPY, mat)
    meta = {
        "ids": [c["id"] for c in chunks],
        "model": C.EMBED_MODEL,
        "dim": int(mat.shape[1]),
        "count": len(chunks),
        "source_sha256": kb["source_sha256"],
    }
    C.INDEX_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    meta = build()
    size_kb = C.VECTORS_NPY.stat().st_size / 1024
    print(f"✅ {meta['count']}개 × {meta['dim']}차원 → {C.INDEX_DIR.relative_to(C.ROOT)} ({size_kb:.0f}KB)")
    print("   다음 단계: python scripts/eval_rag.py --sweep  (벡터 게이트 임계값을 측정해 켠다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
