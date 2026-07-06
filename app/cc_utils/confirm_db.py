"""
Confirm SQLite Database Manager
사용자 확인 요청을 관리하는 SQLite 데이터베이스
"""

import sqlite3
import json
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
    return db_dir / "confirms.db"


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
        CREATE TABLE IF NOT EXISTS confirms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confirm_id TEXT NOT NULL UNIQUE,
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT,
            confirm_message TEXT NOT NULL,
            original_request_text TEXT NOT NULL,
            thread_ts TEXT,
            confirmed INTEGER DEFAULT 0,
            response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
    """)

    # 인덱스 생성
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_confirm_id
        ON confirms(confirm_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_channel_user_pending
        ON confirms(channel_id, user_id, confirmed)
    """)

    conn.commit()
    conn.close()


def cancel_user_pending_confirms(user_id: str, channel_id: str, thread_ts: str = None) -> int:
    """
    특정 채널/스레드에서 특정 사용자의 pending confirm들을 expired 상태로 변경
    새 제안이 나갈 때 이전 제안들을 취소하기 위해 사용

    Args:
        user_id: 사용자 ID
        channel_id: 채널 ID
        thread_ts: 스레드 타임스탬프 (None이면 메인 채널만)

    Returns:
        취소된 confirm 수
    """
    conn = get_connection()
    cursor = conn.cursor()

    updated_at = datetime.now().isoformat()

    if thread_ts:
        # 특정 스레드의 pending만 취소
        cursor.execute("""
            UPDATE confirms
            SET confirmed = -2,
                status = 'expired',
                updated_at = ?
            WHERE user_id = ? AND channel_id = ? AND thread_ts = ? AND confirmed = 0
        """, (updated_at, user_id, channel_id, thread_ts))
    else:
        # 메인 채널(thread_ts=NULL)의 pending만 취소
        cursor.execute("""
            UPDATE confirms
            SET confirmed = -2,
                status = 'expired',
                updated_at = ?
            WHERE user_id = ? AND channel_id = ? AND thread_ts IS NULL AND confirmed = 0
        """, (updated_at, user_id, channel_id))

    conn.commit()
    cancelled_count = cursor.rowcount
    conn.close()

    return cancelled_count


def add_confirm_request(
    confirm_id: str,
    channel_id: str,
    user_id: str,
    user_name: str,
    confirm_message: str,
    original_request_text: str,
    thread_ts: str = None
) -> bool:
    """
    새로운 confirm 요청 추가
    추가 전에 해당 채널에서 해당 사용자의 이전 pending confirm들을 자동으로 취소함

    Args:
        confirm_id: confirm 고유 ID
        channel_id: 채널 ID
        user_id: 확인을 받아야 하는 사용자 ID
        user_name: 사용자 이름
        confirm_message: 확인 메시지 ("예전에 도와드린 적 있는데 도와드릴까요?")
        original_request_text: 원본 요청 텍스트
        thread_ts: 스레드 타임스탬프 (선택, 스레드 격리용)

    Returns:
        추가 성공 여부
    """
    import logging

    # 먼저 해당 채널/스레드에서 해당 사용자의 이전 pending confirm들을 취소
    cancelled = cancel_user_pending_confirms(user_id, channel_id, thread_ts)
    if cancelled > 0:
        logging.info(f"[CONFIRM_DB] Cancelled {cancelled} previous pending confirms for user {user_id} in channel {channel_id} (thread_ts={thread_ts})")

    conn = get_connection()
    cursor = conn.cursor()

    created_at = datetime.now().isoformat()

    try:
        cursor.execute("""
            INSERT INTO confirms (
                confirm_id, channel_id, user_id, user_name, confirm_message,
                original_request_text, thread_ts, confirmed, response, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, 'pending')
        """, (confirm_id, channel_id, user_id, user_name, confirm_message,
              original_request_text, thread_ts, created_at))

        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        # confirm_id 중복 시
        success = False
    finally:
        conn.close()

    return success


def update_confirm_response(
    confirm_id: str,
    user_id: str,
    approved: bool,
    response: str
) -> bool:
    """
    confirm 응답 업데이트

    Args:
        confirm_id: confirm ID
        user_id: 사용자 ID
        approved: 승인 여부 (True: 승인, False: 거부)
        response: 사용자의 실제 응답 텍스트

    Returns:
        업데이트 성공 여부
    """
    conn = get_connection()
    cursor = conn.cursor()

    updated_at = datetime.now().isoformat()
    confirmed = 1 if approved else -1
    status = 'approved' if approved else 'rejected'

    cursor.execute("""
        UPDATE confirms
        SET confirmed = ?,
            response = ?,
            updated_at = ?,
            status = ?
        WHERE confirm_id = ? AND user_id = ?
    """, (confirmed, response, updated_at, status, confirm_id, user_id))

    conn.commit()
    success = cursor.rowcount > 0
    conn.close()

    return success


def get_confirm_by_id(confirm_id: str) -> Optional[Dict[str, Any]]:
    """
    특정 confirm 조회

    Args:
        confirm_id: confirm ID

    Returns:
        confirm 정보 또는 None
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id, confirm_id, channel_id, user_id, user_name, confirm_message,
            original_request_text, thread_ts, confirmed, response, created_at, updated_at, status
        FROM confirms
        WHERE confirm_id = ?
    """, (confirm_id,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_channel_pending_confirms(channel_id: str, user_id: str, thread_ts: str = None) -> List[Dict[str, Any]]:
    """
    특정 채널에서 특정 사용자의 대기 중인 confirm 조회 (최근 1일 이내)
    thread_ts로 스레드 격리:
    - Dynamic suggester confirm (thread_ts=NULL): 어디서든 응답 가능
    - Proactive suggester confirm (thread_ts=값): 해당 스레드에서만 응답 가능

    Args:
        channel_id: 채널 ID
        user_id: 사용자 ID
        thread_ts: 스레드 타임스탬프 (None이면 메인 채널만, 값이 있으면 해당 스레드 또는 NULL confirm)

    Returns:
        대기 중인 confirm 리스트 (최근 1일 이내)
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id, confirm_id, channel_id, user_id, user_name, confirm_message,
            original_request_text, thread_ts, confirmed, response, created_at, updated_at, status
        FROM confirms
        WHERE channel_id = ? AND user_id = ? AND confirmed = 0
          AND created_at >= datetime('now', '-12 hours')
          AND (thread_ts = ? OR thread_ts IS NULL)
        ORDER BY created_at DESC
    """, (channel_id, user_id, thread_ts))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]
