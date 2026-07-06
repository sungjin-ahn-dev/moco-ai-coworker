"""
Jira Tasks Database Manager
Jira에서 추출한 할 일을 관리하는 SQLite 데이터베이스
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
    return db_dir / "jira_tasks.db"


def init_db():
    """데이터베이스 초기화 및 테이블 생성"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS jira_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_key TEXT NOT NULL,
            issue_url TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            task_description TEXT NOT NULL,
            user TEXT,
            text TEXT,
            channel TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            db_status TEXT DEFAULT 'pending'
        )
    """
    )

    # issue_key에 유니크 인덱스 추가 (중복 방지)
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_issue_key
        ON jira_tasks(issue_key)
    """
    )

    # 유저별 조회 성능을 위한 인덱스
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_jira_tasks_user_status
        ON jira_tasks(user, db_status)
    """
    )

    conn.commit()
    conn.close()
    logging.info(f"[JIRA_TASKS_DB] Database initialized at {db_path}")


def add_task(
    issue_key: str,
    issue_url: str,
    summary: str,
    status: str,
    priority: str,
    task_description: str,
    user_id: Optional[str] = None,
    text: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> int:
    """
    새 할 일 추가 (중복 시 업데이트)

    Args:
        issue_key: Jira 이슈 키 (예: PROJ-123)
        issue_url: Jira 이슈 URL
        summary: 이슈 제목
        status: 이슈 상태
        priority: 우선순위 (low/medium/high)
        task_description: 할 일 설명
        user_id: 알림을 받을 사용자 ID
        text: 알림 메시지 내용
        channel_id: 알림을 보낼 채널 ID

    Returns:
        생성된 task의 ID
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 이미 존재하면 업데이트, 없으면 삽입
    cursor.execute(
        """
        INSERT INTO jira_tasks
        (issue_key, issue_url, summary, status, priority, task_description, user, text, channel)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(issue_key) DO UPDATE SET
            issue_url = excluded.issue_url,
            summary = excluded.summary,
            status = excluded.status,
            priority = excluded.priority,
            task_description = excluded.task_description,
            user = excluded.user,
            text = excluded.text,
            channel = excluded.channel,
            updated_at = CURRENT_TIMESTAMP,
            db_status = 'pending'
    """,
        (
            issue_key,
            issue_url,
            summary,
            status,
            priority,
            task_description,
            user_id,
            text,
            channel_id,
        ),
    )

    # lastrowid는 INSERT 또는 UPDATE된 행의 ID
    task_id = cursor.lastrowid

    # UPDATE된 경우 실제 ID 조회
    if cursor.rowcount == 1:
        cursor.execute("SELECT id FROM jira_tasks WHERE issue_key = ?", (issue_key,))
        result = cursor.fetchone()
        if result:
            task_id = result[0]

    conn.commit()
    conn.close()

    logging.info(
        f"[JIRA_TASKS_DB] Added/Updated task {task_id}: {issue_key} - {task_description[:50]}..."
    )
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
        cursor.execute(
            """
            SELECT * FROM jira_tasks
            WHERE db_status = 'pending' AND user = ?
            ORDER BY
                CASE priority
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END,
                created_at ASC
            LIMIT ?
        """,
            (user_id, limit),
        )
    else:
        cursor.execute(
            """
            SELECT * FROM jira_tasks
            WHERE db_status = 'pending'
            ORDER BY
                CASE priority
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                END,
                created_at ASC
            LIMIT ?
        """,
            (limit,),
        )

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

    cursor.execute(
        """
        UPDATE jira_tasks
        SET db_status = 'completed'
        WHERE id = ?
    """,
        (task_id,),
    )

    affected = cursor.rowcount
    conn.commit()
    conn.close()

    if affected > 0:
        logging.info(f"[JIRA_TASKS_DB] Completed task {task_id}")
        return True
    else:
        logging.warning(f"[JIRA_TASKS_DB] Task {task_id} not found")
        return False


def get_existing_issue_keys() -> List[str]:
    """
    DB에 이미 존재하는 issue_key 리스트 반환

    Returns:
        issue_key 리스트
    """
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT issue_key FROM jira_tasks")
    rows = cursor.fetchall()
    conn.close()

    issue_keys = [row[0] for row in rows]
    return issue_keys
