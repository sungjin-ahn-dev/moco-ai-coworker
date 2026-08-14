"""
Waiting Answer SQLite Database Manager
응답 대기 질의를 관리하는 SQLite 데이터베이스
"""

import sqlite3
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config.settings import get_settings


def get_db_path() -> Path:
    """SQLite 데이터베이스 파일 경로 반환"""
    settings = get_settings()
    base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()
    db_dir = Path(base_dir) / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "waiting_answers.db"


def get_connection() -> sqlite3.Connection:
    """SQLite 연결 반환 (Row factory 설정)"""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """데이터베이스 초기화 및 테이블 생성"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS waiting_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            requester_name TEXT,
            request_content TEXT NOT NULL,
            respondent_user_id TEXT NOT NULL,
            respondent_name TEXT,
            responded INTEGER DEFAULT 0,
            response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'in_progress'
        )
    """)

    # 기존 테이블에 컬럼 추가 (마이그레이션)
    try:
        cursor.execute("ALTER TABLE waiting_answers ADD COLUMN requester_name TEXT")
    except:
        pass  # 이미 존재하면 무시

    try:
        cursor.execute("ALTER TABLE waiting_answers ADD COLUMN respondent_name TEXT")
    except:
        pass  # 이미 존재하면 무시

    # 인덱스 생성
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_request_id
        ON waiting_answers(request_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_respondent
        ON waiting_answers(respondent_user_id, responded)
    """)

    conn.commit()
    conn.close()


def add_request(
    request_id: str,
    channel_id: str,
    requester_id: str,
    requester_name: str,
    request_content: str,
    respondents: List[Dict[str, str]]
) -> int:
    """
    새로운 질의 추가 (여러 응답자에게)

    Args:
        request_id: 질의 고유 ID
        channel_id: 질의 생성 채널 ID
        requester_id: 요청자 Slack User ID
        requester_name: 요청자 이름
        request_content: 질의 내용
        respondents: 응답자 정보 리스트 [{"user_id": "U123", "name": "홍길동"}, ...]

    Returns:
        추가된 레코드 수
    """
    conn = get_connection()
    cursor = conn.cursor()

    created_at = datetime.now().isoformat()

    for respondent in respondents:
        respondent_user_id = respondent.get("user_id")
        respondent_name = respondent.get("name", "")

        cursor.execute("""
            INSERT INTO waiting_answers (
                request_id, channel_id, requester_id, requester_name, request_content,
                respondent_user_id, respondent_name, responded, response, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, 'in_progress')
        """, (request_id, channel_id, requester_id, requester_name, request_content,
              respondent_user_id, respondent_name, created_at))

    conn.commit()
    count = len(respondents)
    conn.close()

    return count


def get_user_pending_requests(user_id: str) -> List[Dict[str, Any]]:
    """
    특정 사용자의 응답 대기 중인 질의 목록 조회 (최근 1일 이내)

    Args:
        user_id: 응답자 Slack User ID

    Returns:
        응답 대기 질의 리스트 (최근 1일 이내)
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id, request_id, channel_id, requester_id, requester_name, request_content,
            respondent_user_id, respondent_name, responded, response, created_at, updated_at, status
        FROM waiting_answers
        WHERE respondent_user_id = ? AND responded = 0
          AND created_at >= datetime('now', '-1 day')
        ORDER BY created_at DESC
    """, (user_id,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def update_response(
    request_id: str,
    user_id: str,
    response: str
) -> bool:
    """
    특정 질의에 대한 응답 업데이트

    Args:
        request_id: 질의 ID
        user_id: 응답자 User ID
        response: 응답 내용

    Returns:
        업데이트 성공 여부
    """
    conn = get_connection()
    cursor = conn.cursor()

    updated_at = datetime.now().isoformat()

    cursor.execute("""
        UPDATE waiting_answers
        SET responded = 1,
            response = ?,
            updated_at = ?,
            status = 'completed'
        WHERE request_id = ? AND respondent_user_id = ?
    """, (response, updated_at, request_id, user_id))

    conn.commit()
    success = cursor.rowcount > 0
    conn.close()

    return success


def get_request_by_id(request_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    특정 질의 조회 (특정 사용자 기준)

    Args:
        request_id: 질의 ID
        user_id: 응답자 User ID

    Returns:
        질의 정보 또는 None
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id, request_id, channel_id, requester_id, requester_name, request_content,
            respondent_user_id, respondent_name, responded, response, created_at, updated_at, status
        FROM waiting_answers
        WHERE request_id = ? AND respondent_user_id = ?
    """, (request_id, user_id))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_all_responses_for_request(request_id: str) -> List[Dict[str, Any]]:
    """
    특정 질의에 대한 모든 응답자의 응답 조회

    Args:
        request_id: 질의 ID

    Returns:
        모든 응답자의 응답 리스트
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id, request_id, channel_id, requester_id, requester_name, request_content,
            respondent_user_id, respondent_name, responded, response, created_at, updated_at, status
        FROM waiting_answers
        WHERE request_id = ?
        ORDER BY created_at ASC
    """, (request_id,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_request_progress(request_id: str) -> Dict[str, int]:
    """
    특정 질의의 진행률 조회

    Args:
        request_id: 질의 ID

    Returns:
        {"total": 전체 응답자 수, "completed": 완료된 응답 수}
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(responded) as completed
        FROM waiting_answers
        WHERE request_id = ?
    """, (request_id,))

    row = cursor.fetchone()
    conn.close()

    return {
        "total": row["total"] if row else 0,
        "completed": row["completed"] if row and row["completed"] else 0
    }
