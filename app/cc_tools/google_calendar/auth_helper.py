"""
Google Calendar API Authentication Helper
Service Account (Domain-Wide Delegation) 또는 OAuth 2.0 인증 지원
Slack 사용자별 자동 이메일 매핑 지원
"""

import os
import logging
from typing import Optional

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# 서비스 객체 캐시
_service_cache: dict = {}

# Calendar API 스코프
SCOPES = ['https://www.googleapis.com/auth/calendar']


def _get_slack_user_email(slack_user_id: str) -> Optional[str]:
    """
    Slack 사용자 ID로 이메일 주소를 조회합니다.

    Args:
        slack_user_id: Slack 사용자 ID (예: 'U12345678')

    Returns:
        str: 사용자 이메일 주소, 조회 실패 시 None
    """
    try:
        from app.cc_utils.slack_helper import get_user_info
        user_info = get_user_info(slack_user_id)
        if user_info and user_info.get("email"):
            return user_info["email"]
    except Exception as e:
        logger.warning(f"[GOOGLE_CALENDAR] Failed to get Slack user email for {slack_user_id}: {e}")
    return None


def get_calendar_service(slack_user_id: Optional[str] = None):
    """
    Google Calendar API 서비스 객체 반환

    Args:
        slack_user_id: Slack 사용자 ID (예: 'U12345678')
                       지정 시 해당 사용자의 이메일로 Calendar에 접근 (Service Account)
                       또는 해당 사용자별 토큰 파일 사용 (OAuth)

    우선순위:
    1. OAuth 2.0 Credentials (개인 계정용 - 사용자 인증)
    2. Service Account + Domain-Wide Delegation (조직 계정용)

    Returns:
        Resource: Google Calendar API 서비스 객체

    Raises:
        ValueError: 인증 정보가 없거나 유효하지 않은 경우
    """
    settings = get_settings()

    # 1. OAuth 2.0 인증 시도 (개인 계정용)
    oauth_credentials_path = settings.GOOGLE_DRIVE_OAUTH_CREDENTIALS
    if oauth_credentials_path and os.path.exists(oauth_credentials_path):
        try:
            logger.info(f"[GOOGLE_CALENDAR] Using OAuth 2.0 Credentials: {oauth_credentials_path}")

            # 토큰 파일 경로 (Slack 사용자별 분리)
            if slack_user_id:
                token_filename = f"google_calendar_token_{slack_user_id}.json"
                logger.info(f"[GOOGLE_CALENDAR] Using per-user token for Slack user: {slack_user_id}")
            else:
                token_filename = "google_calendar_token.json"
                logger.info("[GOOGLE_CALENDAR] Using default token (no slack_user_id provided)")

            from app.cc_utils.path_helper import get_moco_file
            token_path = get_moco_file(token_filename)

            creds = None

            # 기존 토큰 로드
            if os.path.exists(token_path):
                try:
                    creds = OAuthCredentials.from_authorized_user_file(token_path, SCOPES)
                    logger.info(f"[GOOGLE_CALENDAR] Loaded existing OAuth token from {token_filename}")
                except Exception as e:
                    logger.warning(f"[GOOGLE_CALENDAR] Failed to load token: {e}")

            # 토큰 갱신 또는 새로 발급
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    logger.info("[GOOGLE_CALENDAR] Refreshing OAuth token")
                    creds.refresh(Request())
                else:
                    user_msg = f" (Slack user: {slack_user_id})" if slack_user_id else ""
                    logger.info(f"[GOOGLE_CALENDAR] Starting OAuth flow{user_msg} - 브라우저에서 로그인해주세요")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        oauth_credentials_path,
                        SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                # 토큰 저장
                os.makedirs(os.path.dirname(token_path), exist_ok=True)
                with open(token_path, 'w', encoding='utf-8') as token:
                    token.write(creds.to_json())
                logger.info(f"[GOOGLE_CALENDAR] Token saved to {token_path}")

            service = build('calendar', 'v3', credentials=creds)
            logger.info("[GOOGLE_CALENDAR] OAuth 2.0 authentication successful")
            return service

        except Exception as e:
            logger.warning(f"[GOOGLE_CALENDAR] OAuth 2.0 authentication failed: {e}, trying Service Account...")

    # 2. Service Account 인증 시도 (조직 계정용 - Domain-Wide Delegation)
    service_account_path = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON

    if service_account_path and os.path.exists(service_account_path):
        try:
            # Slack 사용자 ID로 이메일 조회 (자동 매핑)
            user_email = None
            if slack_user_id:
                user_email = _get_slack_user_email(slack_user_id)
                if user_email:
                    logger.info(f"[GOOGLE_CALENDAR] Auto-mapped Slack user {slack_user_id} to email: {user_email}")

            # Slack에서 이메일을 못 가져오면 설정값 사용 (fallback)
            if not user_email:
                user_email = settings.GOOGLE_CALENDAR_USER_EMAIL
                if user_email:
                    logger.info(f"[GOOGLE_CALENDAR] Using fallback email from settings: {user_email}")

            if not user_email:
                raise ValueError(
                    "Google Calendar 사용자 이메일을 확인할 수 없습니다. "
                    "Slack 프로필에 이메일이 설정되어 있는지 확인하거나, "
                    "GOOGLE_CALENDAR_USER_EMAIL 설정값을 지정해주세요."
                )

            # 캐시 확인
            cache_key = ("calendar", user_email)
            if cache_key in _service_cache:
                logger.debug(f"[GOOGLE_CALENDAR] Using cached service (user={user_email})")
                return _service_cache[cache_key]

            logger.info(f"[GOOGLE_CALENDAR] Using Service Account: {service_account_path}")
            logger.info(f"[GOOGLE_CALENDAR] Delegating to user: {user_email}")

            # Domain-Wide Delegation: subject 파라미터로 접근할 사용자 지정
            credentials = ServiceAccountCredentials.from_service_account_file(
                service_account_path,
                scopes=SCOPES,
                subject=user_email
            )

            service = build('calendar', 'v3', credentials=credentials)
            _service_cache[cache_key] = service
            logger.info("[GOOGLE_CALENDAR] Service Account authentication successful (cached)")
            return service

        except Exception as e:
            logger.error(f"[GOOGLE_CALENDAR] Service Account authentication failed: {e}")
            raise ValueError(f"Google Calendar 인증 실패: {str(e)}")

    # 인증 정보가 없는 경우
    raise ValueError(
        "Google Calendar 인증 정보가 없습니다. "
        "개인 계정: OAuth Credentials JSON 파일 경로를 설정해주세요. "
        "조직 계정: Service Account JSON을 설정해주세요."
    )


def get_calendar_service_by_email(user_email: str):
    """이메일을 직접 지정해 Service Account(Domain-Wide Delegation)로 접근.

    Slack ID 매핑 우회용 (CRM Working Day 동기화 등에서 사용).
    """
    if not user_email:
        raise ValueError("user_email이 비어있습니다.")

    settings = get_settings()
    service_account_path = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON
    if not service_account_path or not os.path.exists(service_account_path):
        raise ValueError(
            "Service Account JSON이 없습니다. GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON 설정값을 확인하세요."
        )

    cache_key = ("calendar", user_email)
    if cache_key in _service_cache:
        return _service_cache[cache_key]

    credentials = ServiceAccountCredentials.from_service_account_file(
        service_account_path,
        scopes=SCOPES,
        subject=user_email,
    )
    service = build('calendar', 'v3', credentials=credentials)
    _service_cache[cache_key] = service
    logger.info(f"[GOOGLE_CALENDAR] Service Account auth (direct email): {user_email}")
    return service
