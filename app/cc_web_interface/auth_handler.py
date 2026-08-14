"""
Authentication Handler
웹 인터페이스 인증 방식 선택 및 처리
"""

import os
import logging
from typing import Optional, Dict, Any
from enum import Enum

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

from app.cc_web_interface.auth_azure import AzureOAuth
from app.cc_web_interface.auth_slack import SlackOAuth
from app.cc_web_interface.auth_google import GoogleOAuth
from app.cc_slack_handlers import is_authorized_user
from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class AuthProvider(str, Enum):
    """지원하는 인증 제공자"""
    MICROSOFT = "microsoft"
    SLACK = "slack"
    GOOGLE = "google"
    NONE = "none"  # 개발/테스트용


class AuthHandler:
    """통합 인증 핸들러"""

    def __init__(self):
        self.provider = self._get_provider()
        logger.info(f"Web interface auth provider: {self.provider}")

        # OAuth 클라이언트 초기화
        if self.provider == AuthProvider.MICROSOFT:
            self.azure_oauth = AzureOAuth()
        elif self.provider == AuthProvider.SLACK:
            self.slack_oauth = SlackOAuth()
        elif self.provider == AuthProvider.GOOGLE:
            self.google_oauth = GoogleOAuth()

    def _get_provider(self) -> AuthProvider:
        """설정된 인증 제공자 확인"""
        settings = get_settings()
        provider = (settings.WEB_INTERFACE_AUTH_PROVIDER or "microsoft").lower()

        logger.info(f"[AUTH_PROVIDER] Read from settings: {provider}")

        try:
            return AuthProvider(provider)
        except ValueError:
            logger.warning(f"Unknown auth provider: {provider}, falling back to microsoft")
            return AuthProvider.MICROSOFT

    def get_redirect_uri(self, request: Request) -> str:
        """OAuth 리디렉트 URI 생성"""
        settings = get_settings()

        if settings.WEB_INTERFACE_URL:
            base_url = settings.WEB_INTERFACE_URL
        else:
            base_url = f"{request.url.scheme}://{request.url.netloc}"

        return f"{base_url}/auth/callback"

    async def handle_login(self, request: Request):
        """로그인 시작"""
        if self.provider == AuthProvider.NONE:
            # 개발 모드 - 바로 세션 생성
            request.session['user'] = {
                'email': 'dev@localhost',
                'name': 'Developer',
                'id': 'dev_user'
            }
            return RedirectResponse(url="/", status_code=302)

        elif self.provider == AuthProvider.SLACK:
            # Slack OAuth
            redirect_uri = self.get_redirect_uri(request)
            auth_url = self.slack_oauth.get_authorize_url(redirect_uri, state="random_state")
            return RedirectResponse(url=auth_url)

        elif self.provider == AuthProvider.GOOGLE:
            # Google OAuth
            redirect_uri = self.get_redirect_uri(request)
            return await self.google_oauth.client.authorize_redirect(request, redirect_uri)

        else:  # MICROSOFT
            # Microsoft OAuth
            redirect_uri = self.get_redirect_uri(request)
            return await self.azure_oauth.client.authorize_redirect(request, redirect_uri)

    async def handle_callback(self, request: Request):
        """OAuth 콜백 처리"""
        if self.provider == AuthProvider.NONE:
            return RedirectResponse(url="/", status_code=302)

        elif self.provider == AuthProvider.SLACK:
            # Slack OAuth 콜백
            code = request.query_params.get("code")
            if not code:
                raise HTTPException(status_code=400, detail="No authorization code")

            redirect_uri = self.get_redirect_uri(request)
            token_data = await self.slack_oauth.get_access_token(code, redirect_uri)

            if not token_data:
                raise HTTPException(status_code=400, detail="Failed to get access token")

            # OIDC 사용자 정보 가져오기
            access_token = token_data.get("access_token")
            if not access_token:
                raise HTTPException(status_code=400, detail="No access token in response")

            user_info = await self.slack_oauth.get_user_info(access_token)

            if not user_info:
                raise HTTPException(status_code=400, detail="Failed to get user info")

            # OIDC userInfo 응답 형식: {"ok": true, "sub": "U...", "name": "...", "email": "..."}
            user_name = user_info.get("name", "")
            user_email = user_info.get("email", "")
            user_id = user_info.get("sub", "")  # OIDC uses 'sub' for user ID

            if not is_authorized_user(user_name):
                logger.warning(f"Unauthorized Slack user: {user_name} ({user_email})")
                raise HTTPException(status_code=403, detail=f"Not authorized: {user_name}")

            # 세션에 저장
            request.session['user'] = {
                'email': user_email,
                'name': user_name,
                'id': user_id,
                'provider': 'slack',
                'avatar': user_info.get("picture", "")  # OIDC uses 'picture' for profile image
            }

            return RedirectResponse(url="/", status_code=302)

        elif self.provider == AuthProvider.GOOGLE:
            # Google OAuth 콜백
            token = await self.google_oauth.client.authorize_access_token(request)
            user_info = await self.google_oauth.get_user_info_from_token(token)

            if not user_info:
                raise HTTPException(status_code=400, detail="Failed to get user info")

            # 권한 확인
            if not is_authorized_user(user_info.get('name', '')):
                logger.warning(f"Unauthorized Google user: {user_info.get('name')} ({user_info.get('email')})")
                raise HTTPException(status_code=403, detail=f"Not authorized: {user_info.get('name')}")

            # 세션에 저장
            request.session['user'] = {
                'email': user_info.get('email'),
                'name': user_info.get('name'),
                'id': user_info.get('id'),
                'provider': 'google',
                'avatar': user_info.get('avatar', '')
            }

            return RedirectResponse(url="/", status_code=302)

        else:  # MICROSOFT
            # Microsoft OAuth 콜백
            token = await self.azure_oauth.client.authorize_access_token(request)
            user_info = await self.azure_oauth.get_user_info_from_token(token)

            if not user_info:
                raise HTTPException(status_code=400, detail="Failed to get user info")

            # 권한 확인
            if not is_authorized_user(user_info.get('name', '')):
                logger.warning(f"Unauthorized MS user: {user_info.get('name')} ({user_info.get('email')})")
                raise HTTPException(status_code=403, detail=f"Not authorized: {user_info.get('name')}")

            # 세션에 저장
            request.session['user'] = {
                'email': user_info.get('email'),
                'name': user_info.get('name'),
                'id': user_info.get('id'),
                'provider': 'microsoft'
            }

            return RedirectResponse(url="/", status_code=302)

    def get_provider_name(self) -> str:
        """현재 인증 제공자 이름"""
        return self.provider.value


# 싱글톤 인스턴스
auth_handler = AuthHandler()