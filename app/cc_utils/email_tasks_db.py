"""
Email Tasks Database Manager
이메일에서 추출한 할 일을 관리하는 SQLite 데이터베이스
"""
import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config.settings import get_settings

settings = get_settings()


def get_db_path() -> Path:
    """데이터베이스 파일 경로 반환"""
    base_dir = settings.FILESYSTEM_BASE_DIR or "."
    db_dir = Path(base_dir) / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "email_tasks.db"


def init_db():
    """데이터베이스 초기화 및 테이블 생성"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            subject TEXT NOT NULL,
            task_description TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            user TEXT,
            text TEXT,
            channel TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
    """)

    # 유저별 조회 성능을 위한 인덱스
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_email_tasks_user_status
        ON email_tasks(user, status)
    """)

    conn.commit()
    conn.close()
    logging.info(f"[EMAIL_TASKS_DB] Database initialized at {db_path}")


def add_task(
    email_id: str,
    sender: str,
    subject: str,
    task_description: str,
    priority: str = "medium",
    user_id: Optional[str] = None,
    text: Optional[str] = None,
    channel_id: Optional[str] = None
) -> int:
    """
    새 할 일 추가

    Args:
        email_id: 이메일 ID
        sender: 발신자
        subject: 이메일 제목
        task_description: 할 일 설명
        priority: 우선순위 (low/medium/high)
        user_id: 알림을 받을 사용자 ID
        text: 알림 메시지 내용
        channel_id: 알림을 보낼 채널 ID

    Returns:
        생성된 task의 ID
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO email_tasks
        (email_id, sender, subject, task_description, priority, user, text, channel)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (email_id, sender, subject, task_description, priority, user_id, text, channel_id))

    task_id = cursor.lastrowid
    conn.commit()
    conn.close()

    logging.info(f"[EMAIL_TASKS_DB] Added task {task_id}: {task_description[:50]}...")
    return task_id


def get_pending_tasks(user_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """
    대기 중인 할 일 목록 조회

    Args:
        user_id: 특정 사용자의 태스크만 조회 (None이면 전체)
        limit: 최대 조회 개수

    Returns:
        할 일 목록 (딕셔너리 리스트)
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if user_id:
        cursor.execute("""
            SELECT * FROM email_tasks
            WHERE status = 'pending' AND user = ?
            ORDER BY
                CASE priority
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END,
                created_at ASC
            LIMIT ?
        """, (user_id, limit))
    else:
        cursor.execute("""
            SELECT * FROM email_tasks
            WHERE status = 'pending'
            ORDER BY
                CASE priority
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END,
                created_at ASC
            LIMIT ?
        """, (limit,))

    rows = cursor.fetchall()
    conn.close()

    tasks = [dict(row) for row in rows]
    return tasks


def complete_task(task_id: int) -> bool:
    """
    할 일 완료 처리 (큐에 들어간 후)

    Args:
        task_id: 할 일 ID

    Returns:
        성공 여부
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE email_tasks
        SET status = 'completed'
        WHERE id = ?
    """, (task_id,))

    affected = cursor.rowcount
    conn.commit()
    conn.close()

    if affected > 0:
        logging.info(f"[EMAIL_TASKS_DB] Completed task {task_id}")
        return True
    else:
        logging.warning(f"[EMAIL_TASKS_DB] Task {task_id} not found")
        return False
