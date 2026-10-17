"""
scripts/seal_check.py
봉인된 골든 데이터셋(test v2)이 "무엇이" 바뀌었는지 진단하고 봉인 상태를 판정한다.

실행:
    python scripts/seal_check.py                    # 현재 파일 vs Git HEAD 비교 + 봉인 판정
    python scripts/seal_check.py --against HEAD~3   # 다른 커밋과 비교
    python scripts/seal_check.py --against old.json # 파일과 비교 (Git 밖에서 받은 경우)

★ 봉인은 "평가 내용"에 건다 — 파일 바이트가 아니다 (TS-05)
  봉인 규칙은 "사람이 검수하면 reviewed_by 를 채운다"고 정해 두었다.
  그런데 파일 전체 해시로 봉인하면 그 검수 기록 한 줄이 "봉인 깨짐"이 된다. 규칙끼리 모순이다.
  → 채점에 쓰이지 않는 메타데이터(META_FIELDS)를 뺀 나머지로 내용 해시를 만든다.
  → 질문 · 정답 · 분할 · 유형처럼 점수를 바꾸는 필드는 한 글자만 바뀌어도 봉인이 깨진다.

★ 모르는 필드는 "평가 내용"으로 본다 (보수적 기본값)
  누군가 새 필드를 추가했는데 그게 채점에 쓰이는지 모른다면 봉인을 깨는 쪽이 안전하다.
  봉인을 지키며 추가하려면 META_FIELDS 에 등록하고 이유를 SEAL 문서에 남긴다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant import config as C
from assistant.console import safe_console

GOLDEN_V2 = C.ROOT / "eval" / "golden_rag_v2.json"
SEAL_MD = C.ROOT / "eval" / "SEAL_test2.md"

# 채점 · 집계에 쓰이지 않는 필드 — 바뀌어도 측정 결과가 같다 (scripts/eval_rag.py 에서 읽지 않음)
META_FIELDS = frozenset({"notes", "author", "created_at", "reviewed_by", "reviewed_at", "review_note"})

INTACT, METADATA_ONLY, BROKEN = "intact", "metadata_only", "broken"


def file_hash(raw: bytes) -> str:
    """파일 해시 — 줄바꿈만 LF 로 통일한다 (Windows CRLF 체크아웃 대응)."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _eval_view(items: list[dict]) -> list[dict]:
    return sorted(({k: v for k, v in it.items() if k not in META_FIELDS} for it in items), key=lambda x: x["id"])


def content_hash(items: list[dict]) -> str:
    """평가 내용 해시 — 메타데이터 제외 · 문항 id 순 정렬 · 키 정렬.

    서식(들여쓰기 · 줄바꿈 · BOM)이나 문항 순서가 바뀌어도 같은 값이다. 채점 결과도 같기 때문이다.
    """
    canon = json.dumps(_eval_view(items), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_items(raw: bytes) -> list[dict]:
    return json.loads(raw.decode("utf-8-sig"))


def sealed_hashes(seal_text: str) -> dict[str, str]:
    """SEAL 문서 표에서 봉인 해시를 읽는다. | sha256 | `...` | · | content_sha256 | `...` |"""
    out = {}
    for key in ("sha256", "content_sha256"):
        m = re.search(rf"^\|\s*{key}\s*\|\s*`([0-9a-f]{{64}})`", seal_text, re.M)
        if m:
            out[key] = m.group(1)
    return out


def seal_status(path: Path = GOLDEN_V2, seal_md: Path = SEAL_MD) -> dict:
    raw = path.read_bytes()
    items = load_items(raw)
    sealed = sealed_hashes(seal_md.read_text(encoding="utf-8"))
    fh, ch = file_hash(raw), content_hash(items)
    if fh == sealed.get("sha256"):
        status = INTACT
    elif ch == sealed.get("content_sha256"):
        status = METADATA_ONLY
    else:
        status = BROKEN
    return {"status": status, "file_sha256": fh, "content_sha256": ch, "sealed": sealed, "count": len(items)}


def diff_items(old: list[dict], new: list[dict]) -> dict:
    """문항 단위 차이. 필드를 평가 필드와 메타데이터로 나눠 돌려준다."""
    o, n = {i["id"]: i for i in old}, {i["id"]: i for i in new}
    changed = []
    for gid in sorted(o.keys() & n.keys()):
        keys = sorted(k for k in o[gid].keys() | n[gid].keys() if o[gid].get(k) != n[gid].get(k))
        if keys:
            changed.append({"id": gid,
                            "eval_fields": [k for k in keys if k not in META_FIELDS],
                            "meta_fields": [k for k in keys if k in META_FIELDS],
                            "before": {k: o[gid].get(k) for k in keys},
                            "after": {k: n[gid].get(k) for k in keys}})
    return {"added": sorted(n.keys() - o.keys()), "removed": sorted(o.keys() - n.keys()), "changed": changed}


def _read_reference(ref: str, path: Path) -> bytes | None:
    p = Path(ref)
    if p.exists():
        return p.read_bytes()
    rel = path.relative_to(C.ROOT).as_posix()
    try:
        return subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=C.ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    safe_console()
    ap = argparse.ArgumentParser(description="test v2 봉인 진단")
    ap.add_argument("--against", default="HEAD", help="비교 기준: Git 커밋(기본 HEAD) 또는 json 파일 경로")
    ap.add_argument("--show", type=int, default=10, help="출력할 변경 문항 수")
    args = ap.parse_args(argv)

    st = seal_status()
    label = {INTACT: "✅ 봉인 유지 (파일 동일)",
             METADATA_ONLY: "✅ 봉인 유지 (메타데이터만 변경 — 검수 기록 등)",
             BROKEN: "❌ 봉인 깨짐 (평가 내용 변경)"}[st["status"]]
    print(f"{label}\n   파일 해시   {st['file_sha256'][:16]}  (봉인 {st['sealed'].get('sha256', '?')[:16]})"
          f"\n   내용 해시   {st['content_sha256'][:16]}  (봉인 {st['sealed'].get('content_sha256', '?')[:16]})")

    ref_raw = _read_reference(args.against, GOLDEN_V2)
    if ref_raw is None:
        print(f"\n⚠️ 비교 기준({args.against})을 읽지 못해 문항 비교는 건너뛴다 — Git 저장소가 아니거나 커밋에 파일이 없다."
              "\n   --against <봉인 당시 json 경로> 로 다시 실행한다.")
        return 0 if st["status"] != BROKEN else 1
    old = load_items(ref_raw)
    d = diff_items(old, load_items(GOLDEN_V2.read_bytes()))
    ev = [c for c in d["changed"] if c["eval_fields"]]
    meta = [c for c in d["changed"] if not c["eval_fields"]]
    print(f"\n[{args.against} 대비] 추가 {len(d['added'])} · 삭제 {len(d['removed'])} · "
          f"평가 필드 변경 {len(ev)} · 메타데이터만 변경 {len(meta)}")
    for gid in d["added"] + d["removed"]:
        print(f"   {'+' if gid in d['added'] else '-'} {gid}")
    for c in (ev + meta)[:args.show]:
        kind = "평가" if c["eval_fields"] else "메타"
        print(f"   [{kind}] {c['id']}: {', '.join(c['eval_fields'] + c['meta_fields'])}")
        for k in c["eval_fields"]:
            print(f"        {k}: {json.dumps(c['before'][k], ensure_ascii=False)}"
                  f" → {json.dumps(c['after'][k], ensure_ascii=False)}")

    if not (ev or meta or d["added"] or d["removed"]) and st["status"] == BROKEN:
        print(f"\n   {args.against} 와는 같다 → 커밋된 파일 자체가 봉인 이후 바뀐 것이다. git log -p {GOLDEN_V2.name} 로 추적한다.")
    print("\n다음 단계: docs/troubleshooting.md (TS-05) 의 판정표를 따른다.")
    return 0 if st["status"] != BROKEN else 1


if __name__ == "__main__":
    raise SystemExit(main())
