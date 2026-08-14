"""
웹 챗 대화 히스토리 SQLite 저장소.

FILESYSTEM_BASE_DIR/web_chat_history.db에 conversations / messages 테이블 생성.
Slack MOCO 데이터(memories, ~/.moco/*.db)와 완전 분리.
"""

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import List, Optional, Dict, Any

from app.config.settings import get_settings


def _db_path() -> str:
    settings = get_settings()
    base = settings.FILESYSTEM_BASE_DIR or os.getcwd()
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "web_chat_history.db")


@contextmanager
def _conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_email TEXT NOT NULL,
                user_name TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '새 대화',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user_updated
                ON conversations(user_email, updated_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_msg_conv
                ON messages(conversation_id, id);
            """
        )
        # 기존 DB에 attachments 컬럼 없으면 추가 (JSON 직렬화된 리스트, NULL 허용)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)").fetchall()}
        if "attachments" not in cols:
            c.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")


def create_conversation(user_email: str, user_name: str, title: str = "새 대화") -> str:
    conv_id = uuid.uuid4().hex
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO conversations (id, user_email, user_name, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, user_email, user_name, title, now, now),
        )
    return conv_id


def list_conversations(user_email: str, limit: int = 50) -> List[Dict[str, Any]]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE user_email = ? ORDER BY updated_at DESC LIMIT ?",
            (user_email, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conv_id: str, user_email: str) -> Optional[Dict[str, Any]]:
    with _conn() as c:
        row = c.execute(
            "SELECT id, user_email, user_name, title, created_at, updated_at FROM conversations WHERE id = ? AND user_email = ?",
            (conv_id, user_email),
        ).fetchone()
    return dict(row) if row else None


def delete_conversation(conv_id: str, user_email: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM conversations WHERE id = ? AND user_email = ?",
            (conv_id, user_email),
        )
        c.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        return cur.rowcount > 0


def rename_conversation(conv_id: str, user_email: str, title: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND user_email = ?",
            (title[:200], time.time(), conv_id, user_email),
        )
        return cur.rowcount > 0


def add_message(
    conv_id: str,
    role: str,
    content: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> None:
    now = time.time()
    att_json = json.dumps(attachments, ensure_ascii=False) if attachments else None
    with _conn() as c:
        c.execute(
            "INSERT INTO messages (conversation_id, role, content, attachments, created_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, role, content, att_json, now),
        )
        c.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conv_id),
        )


def _row_to_message(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    raw = d.pop("attachments", None)
    if raw:
        try:
            d["attachments"] = json.loads(raw)
        except Exception:
            d["attachments"] = []
    else:
        d["attachments"] = []
    return d


def get_messages(conv_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    with _conn() as c:
        query = "SELECT id, role, content, attachments, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC"
        params: tuple = (conv_id,)
        if limit:
            query += " LIMIT ?"
            params = (conv_id, limit)
        rows = c.execute(query, params).fetchall()
    return [_row_to_message(r) for r in rows]


def get_recent_messages_for_context(conv_id: str, max_turns: int = 12) -> List[Dict[str, str]]:
    """최근 N개 메시지를 시간순으로 반환 (어댑터에 컨텍스트로 전달용)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conv_id, max_turns),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


init_db()
