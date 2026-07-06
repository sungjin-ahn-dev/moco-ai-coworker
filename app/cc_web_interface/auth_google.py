"""
Google OAuth Authentication
Google OAuth 2.0을 통한 사용자 인증
"""

import logging
from typing import Optional, Dict, Any

from authlib.integrations.starlette_client import OAuth
from starlette.config import Config

from app.config.settings import get_settings


class GoogleOAuth:
    """Google OAuth 2.0 인증 핸들러"""

    def __init__(self):
        settings = get_settings()

        # OAuth 설정
        config = Config(environ={
            "GOOGLE_CLIENT_ID": settings.WEB_GOOGLE_CLIENT_ID or "",
            "GOOGLE_CLIENT_SECRET": settings.WEB_GOOGLE_CLIENT_SECRET or "",
        })

        self.oauth = OAuth(config)

        # Google OAuth 등록
        self.oauth.register(
            name='google',
            client_id=config.get('GOOGLE_CLIENT_ID'),
            client_secret=config.get('GOOGLE_CLIENT_SECRET'),
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={
                'scope': 'openid email profile',
            },
        )

        self.client = self.oauth.google

    async def get_user_info_from_token(self, token: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        OAuth 토큰에서 사용자 정보 추출

        Args:
            token: OAuth 토큰

        Returns:
            사용자 정보 (email, name 등)
        """
        try:
            # Google userinfo endpoint로 사용자 정보 가져오기
            resp = await self.client.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                token=token
            )
            user_data = resp.json()

            return {
                'email': user_data.get('email'),
                'name': user_data.get('name'),
                'id': user_data.get('sub'),  # Google uses 'sub' for user ID
                'avatar': user_data.get('picture', ''),
            }
        except Exception as e:
            logging.error(f"[GOOGLE_AUTH] Error getting user info: {e}")
            return None
