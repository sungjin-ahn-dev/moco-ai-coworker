"""
OAuth Session Store
OAuth 플로우 중 임시 데이터를 파일 기반으로 저장
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class OAuthSessionStore:
    """
    OAuth 세션 데이터를 파일 시스템에 저장
    서버 재시작해도 OAuth 플로우를 계속할 수 있음
    """

    def __init__(self, store_path: Optional[Path] = None):
        """
        Args:
            store_path: 세션 데이터 저장 경로
        """
        if store_path is None:
            settings = get_settings()
            base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()
            store_path = Path(base_dir) / "data" / "oauth_sessions"

        self.store_path = store_path
        self.store_path.mkdir(parents=True, exist_ok=True)
        self.session_file = self.store_path / "sessions.json"

        # 초기화 및 만료된 세션 정리
        self._load_sessions()
        self._cleanup_expired()

    def _load_sessions(self) -> Dict[str, Any]:
        """세션 데이터 로드"""
        if self.session_file.exists():
            try:
                with open(self.session_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load OAuth sessions: {e}")
                return {}
        return {}

    def _save_sessions(self, sessions: Dict[str, Any]):
        """세션 데이터 저장"""
        try:
            with open(self.session_file, 'w', encoding='utf-8') as f:
                json.dump(sessions, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save OAuth sessions: {e}")

    def _cleanup_expired(self):
        """만료된 세션 정리 (24시간 이상 된 세션)"""
        sessions = self._load_sessions()
        now = datetime.now()
        cleaned = {}

        for state, data in sessions.items():
            try:
                created_at = datetime.fromisoformat(data.get("created_at"))
                if now - created_at < timedelta(hours=24):
                    cleaned[state] = data
                else:
                    logger.info(f"Cleaned expired OAuth session: {state}")
            except Exception as e:
                logger.error(f"Error processing session {state}: {e}")

        if len(cleaned) < len(sessions):
            self._save_sessions(cleaned)

    def store(self, state: str, data: Dict[str, Any]) -> bool:
        """
        OAuth 세션 데이터 저장

        Args:
            state: OAuth state 파라미터
            data: 저장할 데이터 (code_verifier 등)

        Returns:
            저장 성공 여부
        """
        try:
            sessions = self._load_sessions()
            sessions[state] = {
                **data,
                "created_at": datetime.now().isoformat()
            }
            self._save_sessions(sessions)
            logger.info(f"Stored OAuth session: {state}")
            return True
        except Exception as e:
            logger.error(f"Failed to store OAuth session: {e}")
            return False

    def retrieve(self, state: str) -> Optional[Dict[str, Any]]:
        """
        OAuth 세션 데이터 가져오기

        Args:
            state: OAuth state 파라미터

        Returns:
            세션 데이터 또는 None
        """
        sessions = self._load_sessions()
        data = sessions.get(state)

        if data:
            logger.info(f"Retrieved OAuth session: {state}")
        else:
            logger.warning(f"OAuth session not found: {state}")

        return data

    def delete(self, state: str) -> bool:
        """
        OAuth 세션 데이터 삭제

        Args:
            state: OAuth state 파라미터

        Returns:
            삭제 성공 여부
        """
        try:
            sessions = self._load_sessions()
            if state in sessions:
                del sessions[state]
                self._save_sessions(sessions)
                logger.info(f"Deleted OAuth session: {state}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete OAuth session: {e}")
            return False

    def clear_all(self) -> bool:
        """
        모든 세션 데이터 삭제

        Returns:
            삭제 성공 여부
        """
        try:
            self._save_sessions({})
            logger.info("Cleared all OAuth sessions")
            return True
        except Exception as e:
            logger.error(f"Failed to clear OAuth sessions: {e}")
            return False


# 싱글톤 인스턴스
oauth_session_store = OAuthSessionStore()