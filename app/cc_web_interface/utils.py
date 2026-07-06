"""
Web Interface Utilities
공통 헬퍼 함수들
"""

import logging
from typing import Optional

from fastapi import Request

logger = logging.getLogger(__name__)


def get_redirect_uri(request: Request, endpoint: str) -> str:
    """
    OAuth redirect URI 생성
    WEB_INTERFACE_URL이 설정되어 있으면 사용, 없으면 현재 요청 기반으로 생성

    Args:
        request: FastAPI Request 객체
        endpoint: 엔드포인트 이름

    Returns:
        완전한 리다이렉트 URI
    """
    from app.config.settings import get_settings
    settings = get_settings()

    if settings.WEB_INTERFACE_URL:
        # 환경변수로 지정된 URL 사용
        return f"{settings.WEB_INTERFACE_URL}{request.url_for(endpoint).path}"
    else:
        # 현재 요청에서 URL 생성
        return str(request.url_for(endpoint))


async def get_slack_user_id(email: str, slack_client) -> Optional[str]:
    """
    이메일로 Slack 사용자 ID 찾기

    Args:
        email: 사용자 이메일
        slack_client: Slack API 클라이언트

    Returns:
        Slack User ID 또는 None
    """
    try:
        response = await slack_client.users_lookupByEmail(email=email)
        if response.get('ok'):
            user = response.get('user', {})
            return user.get('id')
        else:
            logger.error(f"Failed to find Slack user by email {email}: {response.get('error')}")
            return None
    except Exception as e:
        logger.error(f"Error looking up Slack user: {e}")
        return None


def is_development_mode() -> bool:
    """
    개발 모드 여부 확인
    WEB_INTERFACE_AUTH_PROVIDER가 'none'이면 개발 모드

    Returns:
        개발 모드 여부
    """
    from app.config.settings import get_settings
    settings = get_settings()
    return (settings.WEB_INTERFACE_AUTH_PROVIDER or "").lower() == "none"


def get_session_user(request: Request) -> Optional[dict]:
    """
    세션에서 사용자 정보 가져오기

    Args:
        request: FastAPI Request 객체

    Returns:
        사용자 정보 딕셔너리 또는 None
    """
    return request.session.get('user')


def require_auth(request: Request) -> bool:
    """
    인증이 필요한지 확인

    Args:
        request: FastAPI Request 객체

    Returns:
        인증 필요 여부
    """
    # 개발 모드면 인증 불필요
    if is_development_mode():
        return False

    # 세션에 사용자 정보가 있는지 확인
    user = get_session_user(request)
    return user is None