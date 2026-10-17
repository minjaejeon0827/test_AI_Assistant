"""
assistant/store.py
대화 기록 · 사용량 로그 · 상담원 연결 요청을 SQLite 에 저장한다.

★ 왜 SQLite 인가
  · 표준 라이브러리라 의존성이 0 이다. PoC 단계에서 DB 서버를 띄울 이유가 없다.
  · 파일 하나라 백업·삭제·이관이 쉽다.
  · 이 클래스의 메서드만 같게 유지하면 Postgres 등으로 바꿔도 app.py 는 수정하지 않는다.

★ 연결을 호출마다 새로 연다
  Streamlit 은 세션마다 다른 스레드에서 스크립트를 실행한다.
  sqlite3 연결은 스레드 간 공유가 금지돼 있으므로(check_same_thread) 공유하지 않는다.
  SQLite 연결 생성은 파일 open 수준이라 비용이 거의 없다.

★ API 키는 절대 저장하지 않는다. 메시지 · 사용량 수치 · 판정 사유만 남긴다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

_CONV_ID = re.compile(r"^[0-9a-f]{32}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    summary          TEXT NOT NULL DEFAULT '',
    summarized_count INTEGER NOT NULL DEFAULT 0,
    state            TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id    TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    error      INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    conv_id           TEXT NOT NULL,
    kind              TEXT NOT NULL,          -- answer | summary | embed
    backend           TEXT NOT NULL,          -- chat | responses | extractive | none
    model             TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    estimated         INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    decision          TEXT NOT NULL DEFAULT '',  -- answer | handoff | blocked | invalid | smalltalk | clarify
    reason            TEXT NOT NULL DEFAULT '',
    retrieval_mode    TEXT NOT NULL DEFAULT '',
    top_score         REAL,
    grounded          INTEGER
);
CREATE TABLE IF NOT EXISTS handoffs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id    TEXT NOT NULL,
    reason     TEXT NOT NULL,
    question   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, id);
CREATE INDEX IF NOT EXISTS idx_usage_conv ON usage_log(conv_id, kind);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_valid_conv_id(conv_id: str | None) -> bool:
    """URL 로 들어온 값이므로 형식을 먼저 확인한다. 대화 ID 는 이 대화를 여는 유일한 열쇠다."""
    return bool(conv_id) and bool(_CONV_ID.match(conv_id))


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            con.execute("PRAGMA journal_mode=WAL")   # 읽기와 쓰기가 서로를 막지 않게 한다
            con.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # region 대화

    def create_conversation(self) -> str:
        conv_id = uuid.uuid4().hex     # 추측 불가능한 128비트 값 — URL 을 아는 사람만 대화를 연다
        ts = _now()
        with self._conn() as con:
            con.execute("INSERT INTO conversations(id, created_at, updated_at) VALUES (?, ?, ?)",
                        (conv_id, ts, ts))
        return conv_id

    def exists(self, conv_id: str) -> bool:
        if not is_valid_conv_id(conv_id):
            return False
        with self._conn() as con:
            return con.execute("SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone() is not None

    def append_message(self, conv_id: str, role: str, content: str, *,
                       error: bool = False, meta: dict | None = None) -> None:
        ts = _now()
        with self._conn() as con:
            con.execute(
                "INSERT INTO messages(conv_id, role, content, error, meta, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conv_id, role, content, int(error), json.dumps(meta or {}, ensure_ascii=False), ts),
            )
            con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (ts, conv_id))

    def load_messages(self, conv_id: str) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT role, content, error, meta FROM messages WHERE conv_id=? ORDER BY id", (conv_id,)
            ).fetchall()
        return [{"role": r["role"], "content": r["content"], "error": bool(r["error"]),
                 "meta": json.loads(r["meta"] or "{}")} for r in rows]

    def load_state(self, conv_id: str) -> dict:
        with self._conn() as con:
            row = con.execute("SELECT summary, summarized_count, state FROM conversations WHERE id=?",
                              (conv_id,)).fetchone()
        if row is None:
            return {}
        return {"summary": row["summary"], "summarized_count": row["summarized_count"],
                **json.loads(row["state"] or "{}")}

    def save_state(self, conv_id: str, *, summary: str, summarized_count: int, state: dict) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE conversations SET summary=?, summarized_count=?, state=?, updated_at=? WHERE id=?",
                (summary, summarized_count, json.dumps(state, ensure_ascii=False), _now(), conv_id),
            )

    def delete_conversation(self, conv_id: str) -> None:
        """사용자가 요청하면 대화를 지운다. 사용량 로그의 수치는 비용 추적용으로 남긴다(본문 없음)."""
        with self._conn() as con:
            con.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
            con.execute("DELETE FROM handoffs WHERE conv_id=?", (conv_id,))
            con.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    # endregion 대화

    # region 사용량 · 상담원 연결

    def log_usage(self, conv_id: str, *, kind: str, backend: str, model: str = "",
                  prompt_tokens: int = 0, completion_tokens: int = 0, estimated: bool = False,
                  latency_ms: int = 0, decision: str = "", reason: str = "",
                  retrieval_mode: str = "", top_score: float | None = None,
                  grounded: bool | None = None) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT INTO usage_log(ts, conv_id, kind, backend, model, prompt_tokens, completion_tokens,"
                " estimated, latency_ms, decision, reason, retrieval_mode, top_score, grounded)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), conv_id, kind, backend, model, prompt_tokens, completion_tokens, int(estimated),
                 latency_ms, decision, reason, retrieval_mode, top_score,
                 None if grounded is None else int(grounded)),
            )

    def usage_totals(self, conv_id: str) -> tuple[int, int]:
        """(LLM 호출 수, 토큰 합계). 추출형 답변(LLM 미사용)은 세지 않는다."""
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens"
                " FROM usage_log WHERE conv_id=? AND backend IN ('chat', 'responses')",
                (conv_id,),
            ).fetchone()
        return int(row["calls"]), int(row["tokens"])

    def daily_calls(self) -> int:
        """오늘(UTC) 전체 LLM 호출 수. 새 대화를 열어 대화별 상한을 우회하는 경로를 막는다."""
        today = datetime.now(timezone.utc).date().isoformat()
        with self._conn() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM usage_log WHERE ts >= ? AND backend IN ('chat', 'responses')",
                (today,),
            ).fetchone()
        return int(row["n"])

    def record_handoff(self, conv_id: str, reason: str, question: str) -> None:
        """상담원이 이어받을 수 있도록 대화 ID · 사유 · 질문을 남긴다."""
        with self._conn() as con:
            con.execute("INSERT INTO handoffs(conv_id, reason, question, created_at) VALUES (?, ?, ?, ?)",
                        (conv_id, reason, question, _now()))

    # endregion 사용량 · 상담원 연결
