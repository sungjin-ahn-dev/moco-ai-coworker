"""
Google Drive API Authentication Helper
Service Account (Domain-Wide Delegation) 또는 OAuth 2.0 인증 지원
Slack 사용자별 자동 이메일 매핑으로 개인 드라이브 접근 지원
"""

import os
import json
import logging
from typing import Optional

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Drive API 스코프 (drive 스코프로 Sheets, Slides, Docs API 모두 접근 가능)
SCOPES = ['https://www.googleapis.com/auth/drive']

# 서비스 객체 캐시 (매 호출마다 재인증 방지)
_service_cache: dict = {}  # key: (service_type, user_email) → value: service object


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
        logger.warning(f"[GOOGLE_DRIVE] Failed to get Slack user email for {slack_user_id}: {e}")
    return None


def get_drive_service(slack_user_id: Optional[str] = None):
    """
    Google Drive API 서비스 객체 반환

    거버넌스: slack_user_id가 있으면 해당 사용자 권한으로만 접근 (Domain-Wide Delegation)
    slack_user_id가 없으면 서비스 계정 권한만 사용 (공유 드라이브만)

    Args:
        slack_user_id: Slack 사용자 ID (예: 'U12345678')
                       지정 시 해당 사용자의 권한으로 접근 (본인이 볼 수 있는 것만)
                       None이면 공유 드라이브만 접근 가능

    Returns:
        Resource: Google Drive API 서비스 객체

    Raises:
        ValueError: 인증 정보가 없거나 유효하지 않은 경우
    """
    settings = get_settings()

    # 1. Service Account 인증 시도
    service_account_path = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON
    if service_account_path and os.path.exists(service_account_path):
        try:
            # Slack 사용자 ID로 이메일 조회 → 해당 사용자 권한으로 접근
            user_email = None
            if slack_user_id:
                user_email = _get_slack_user_email(slack_user_id)
                if user_email:
                    logger.info(f"[GOOGLE_DRIVE] Auto-mapped Slack user {slack_user_id} to email: {user_email}")
                else:
                    # 거버넌스: Slack에서 이메일을 못 가져오면 fallback 하지 않음
                    logger.warning(f"[GOOGLE_DRIVE] Cannot resolve email for {slack_user_id}, using shared drive only")

            # 캐시 확인
            cache_key = ("drive", user_email)
            if cache_key in _service_cache:
                logger.debug(f"[GOOGLE_DRIVE] Using cached service (user={user_email})")
                return _service_cache[cache_key]

            if user_email:
                # 거버넌스: 요청자 권한으로 접근 — 본인이 볼 수 있는 파일만 보임
                logger.info(f"[GOOGLE_DRIVE] Delegating to user: {user_email} (요청자 권한으로 접근)")
                credentials = ServiceAccountCredentials.from_service_account_file(
                    service_account_path,
                    scopes=SCOPES,
                    subject=user_email
                )
            else:
                # 거버넌스: 서비스 계정 자체 권한만 (공유 드라이브만)
                logger.info("[GOOGLE_DRIVE] No user delegation (공유 드라이브만 접근)")
                credentials = ServiceAccountCredentials.from_service_account_file(
                    service_account_path,
                    scopes=SCOPES
                )

            service = build('drive', 'v3', credentials=credentials)
            _service_cache[cache_key] = service
            logger.info("[GOOGLE_DRIVE] Service Account authentication successful (cached)")
            return service
        except Exception as e:
            logger.error(f"[GOOGLE_DRIVE] Service Account authentication failed: {e}")
            raise ValueError(f"Service Account 인증 실패: {str(e)}")

    # 2. OAuth 2.0 인증 시도
    oauth_credentials_path = settings.GOOGLE_DRIVE_OAUTH_CREDENTIALS
    if oauth_credentials_path and os.path.exists(oauth_credentials_path):
        try:
            logger.info(f"[GOOGLE_DRIVE] Using OAuth 2.0 Credentials: {oauth_credentials_path}")

            # 토큰 파일 경로
            from app.cc_utils.path_helper import get_moco_file
            token_path = get_moco_file("google_drive_token.json")

            creds = None

            # 기존 토큰 로드
            if os.path.exists(token_path):
                try:
                    creds = OAuthCredentials.from_authorized_user_file(token_path, SCOPES)
                    logger.info("[GOOGLE_DRIVE] Loaded existing OAuth token")
                except Exception as e:
                    logger.warning(f"[GOOGLE_DRIVE] Failed to load token: {e}")

            # 토큰 갱신 또는 새로 발급
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    logger.info("[GOOGLE_DRIVE] Refreshing OAuth token")
                    creds.refresh(Request())
                else:
                    logger.info("[GOOGLE_DRIVE] Starting OAuth flow")
                    flow = InstalledAppFlow.from_client_secrets_file(
                        oauth_credentials_path,
                        SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                # 토큰 저장
                os.makedirs(os.path.dirname(token_path), exist_ok=True)
                with open(token_path, 'w', encoding='utf-8') as token:
                    token.write(creds.to_json())
                logger.info(f"[GOOGLE_DRIVE] Token saved to {token_path}")

            service = build('drive', 'v3', credentials=creds)
            logger.info("[GOOGLE_DRIVE] OAuth 2.0 authentication successful")
            return service

        except Exception as e:
            logger.error(f"[GOOGLE_DRIVE] OAuth 2.0 authentication failed: {e}")
            raise ValueError(f"OAuth 2.0 인증 실패: {str(e)}")

    # 인증 정보가 없는 경우
    raise ValueError(
        "Google Drive 인증 정보가 없습니다. "
        "Service Account JSON 또는 OAuth Credentials JSON 파일 경로를 설정해주세요."
    )


def get_docs_service(slack_user_id: Optional[str] = None):
    """
    Google Docs API 서비스 객체 반환
    Drive API와 동일한 인증 정보 사용

    Args:
        slack_user_id: Slack 사용자 ID (예: 'U12345678')
                       지정 시 해당 사용자의 개인 문서에 접근 (Domain-Wide Delegation)

    Returns:
        Resource: Google Docs API 서비스 객체
    """
    settings = get_settings()

    # 1. Service Account 인증 시도
    service_account_path = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON
    if service_account_path and os.path.exists(service_account_path):
        try:
            # Slack 사용자 ID로 이메일 조회 (개인 문서 접근용)
            user_email = None
            if slack_user_id:
                user_email = _get_slack_user_email(slack_user_id)
                if user_email:
                    logger.info(f"[GOOGLE_DOCS] Auto-mapped Slack user {slack_user_id} to email: {user_email}")

            if slack_user_id and not user_email:
                # 거버넌스: fallback 하지 않음
                logger.warning(f"[GOOGLE_DOCS] Cannot resolve email for {slack_user_id}, using shared access only")

            # 캐시 확인
            cache_key = ("docs", user_email)
            if cache_key in _service_cache:
                logger.debug(f"[GOOGLE_DOCS] Using cached service (user={user_email})")
                return _service_cache[cache_key]

            if user_email:
                # 거버넌스: 요청자 권한으로 접근
                logger.info(f"[GOOGLE_DOCS] Delegating to user: {user_email} (요청자 권한으로 접근)")
                credentials = ServiceAccountCredentials.from_service_account_file(
                    service_account_path,
                    scopes=SCOPES,
                    subject=user_email
                )
            else:
                credentials = ServiceAccountCredentials.from_service_account_file(
                    service_account_path,
                    scopes=SCOPES
                )

            service = build('docs', 'v1', credentials=credentials)
            _service_cache[cache_key] = service
            logger.info("[GOOGLE_DOCS] Service Account authentication successful (cached)")
            return service
        except Exception as e:
            logger.error(f"[GOOGLE_DOCS] Service Account authentication failed: {e}")
            raise ValueError(f"Service Account 인증 실패: {str(e)}")

    # 2. OAuth 2.0 인증 시도
    oauth_credentials_path = settings.GOOGLE_DRIVE_OAUTH_CREDENTIALS
    if oauth_credentials_path and os.path.exists(oauth_credentials_path):
        try:
            logger.info(f"[GOOGLE_DOCS] Using OAuth 2.0 Credentials")
            from app.cc_utils.path_helper import get_moco_file
            token_path = get_moco_file("google_drive_token.json")

            creds = None
            if os.path.exists(token_path):
                try:
                    creds = OAuthCredentials.from_authorized_user_file(token_path, SCOPES)
                except Exception as e:
                    logger.warning(f"[GOOGLE_DOCS] Failed to load token: {e}")

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        oauth_credentials_path,
                        SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                os.makedirs(os.path.dirname(token_path), exist_ok=True)
                with open(token_path, 'w', encoding='utf-8') as token:
                    token.write(creds.to_json())

            service = build('docs', 'v1', credentials=creds)
            logger.info("[GOOGLE_DOCS] OAuth 2.0 authentication successful")
            return service

        except Exception as e:
            logger.error(f"[GOOGLE_DOCS] OAuth 2.0 authentication failed: {e}")
            raise ValueError(f"OAuth 2.0 인증 실패: {str(e)}")

    raise ValueError(
        "Google Docs 인증 정보가 없습니다. "
        "Service Account JSON 또는 OAuth Credentials JSON 파일 경로를 설정해주세요."
    )


def _build_google_service(api_name: str, api_version: str, label: str, slack_user_id: Optional[str] = None):
    """
    Google API 서비스 객체를 생성하는 공통 헬퍼.
    Drive API와 동일한 인증 정보(Service Account 또는 OAuth)를 사용합니다.
    """
    settings = get_settings()
    service_account_path = settings.GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON

    if service_account_path and os.path.exists(service_account_path):
        user_email = None
        if slack_user_id:
            user_email = _get_slack_user_email(slack_user_id)
        if slack_user_id and not user_email:
            # 거버넌스: fallback 하지 않음
            logger.warning(f"[{label}] Cannot resolve email for {slack_user_id}, using shared access only")

        cache_key = (api_name, user_email)
        if cache_key in _service_cache:
            return _service_cache[cache_key]

        if user_email:
            credentials = ServiceAccountCredentials.from_service_account_file(
                service_account_path, scopes=SCOPES, subject=user_email
            )
        else:
            credentials = ServiceAccountCredentials.from_service_account_file(
                service_account_path, scopes=SCOPES
            )

        service = build(api_name, api_version, credentials=credentials)
        _service_cache[cache_key] = service
        logger.info(f"[{label}] Service Account auth successful (user={user_email})")
        return service

    oauth_credentials_path = settings.GOOGLE_DRIVE_OAUTH_CREDENTIALS
    if oauth_credentials_path and os.path.exists(oauth_credentials_path):
        from app.cc_utils.path_helper import get_moco_file
        token_path = get_moco_file("google_drive_token.json")
        creds = None
        if os.path.exists(token_path):
            try:
                creds = OAuthCredentials.from_authorized_user_file(token_path, SCOPES)
            except Exception:
                pass
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(oauth_credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, 'w', encoding='utf-8') as token:
                token.write(creds.to_json())
        service = build(api_name, api_version, credentials=creds)
        logger.info(f"[{label}] OAuth 2.0 auth successful")
        return service

    raise ValueError(f"{label} 인증 정보가 없습니다.")


def get_sheets_service(slack_user_id: Optional[str] = None):
    """Google Sheets API 서비스 객체 반환"""
    return _build_google_service('sheets', 'v4', 'GOOGLE_SHEETS', slack_user_id)


def get_slides_service(slack_user_id: Optional[str] = None):
    """Google Slides API 서비스 객체 반환"""
    return _build_google_service('slides', 'v1', 'GOOGLE_SLIDES', slack_user_id)
