"""
X (Twitter) OAuth 2.0 PKCE Authentication
X API v2 사용자 인증
"""

import hashlib
import secrets
import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

import httpx

from app.config.settings import get_settings

settings = get_settings()

# X OAuth 2.0 endpoints
X_AUTH_URL = "https://twitter.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
X_REVOKE_URL = "https://api.twitter.com/2/oauth2/revoke"

# OAuth 2.0 Scopes
SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "follows.read",
    "follows.write",
    "offline.access",  # Refresh Token 발급
]

# 토큰 저장 경로
def get_token_cache_dir() -> Path:
    """X OAuth 토큰 캐시 디렉토리 경로 반환"""
    base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()
    cache_dir = Path(base_dir) / "data" / "bot_tokens"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir

def get_token_cache_file() -> Path:
    """X OAuth 토큰 캐시 파일 경로 반환"""
    return get_token_cache_dir() / "x_oauth_token.json"


def generate_code_verifier() -> str:
    """PKCE Code Verifier 생성 (43-128자 랜덤 문자열)"""
    return secrets.token_urlsafe(64)[:128]


def generate_code_challenge(code_verifier: str) -> str:
    """PKCE Code Challenge 생성 (SHA256 해시)"""
    sha256 = hashlib.sha256(code_verifier.encode('utf-8')).digest()
    # Base64 URL-safe encoding (패딩 제거)
    import base64
    return base64.urlsafe_b64encode(sha256).decode('utf-8').rstrip('=')


def get_authorization_url(redirect_uri: str, state: str, code_challenge: str) -> str:
    """
    X OAuth 2.0 인증 URL 생성

    Args:
        redirect_uri: 콜백 URL
        state: CSRF 방지 토큰
        code_challenge: PKCE code challenge

    Returns:
        인증 URL
    """
    params = {
        "response_type": "code",
        "client_id": settings.X_OAUTH2_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    # URL 생성
    from urllib.parse import urlencode
    return f"{X_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_token(
    code: str,
    code_verifier: str,
    redirect_uri: str
) -> Optional[Dict[str, Any]]:
    """
    Authorization Code를 Access Token으로 교환

    Args:
        code: Authorization code
        code_verifier: PKCE code verifier
        redirect_uri: 콜백 URL

    Returns:
        토큰 정보 (access_token, refresh_token, expires_in 등)
    """
    try:
        # Basic Authentication (Client ID:Client Secret)
        import base64
        credentials = f"{settings.X_OAUTH2_CLIENT_ID}:{settings.X_OAUTH2_CLIENT_SECRET}"
        b64_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

        headers = {
            "Authorization": f"Basic {b64_credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(X_TOKEN_URL, headers=headers, data=data)
            response.raise_for_status()
            token_data = response.json()

        logging.info(f"[X_OAUTH] Token exchanged successfully")
        return token_data

    except httpx.HTTPStatusError as e:
        error_detail = e.response.text
        logging.error(f"[X_OAUTH] Token exchange failed (HTTP {e.response.status_code}): {error_detail}")
        return None
    except Exception as e:
        logging.error(f"[X_OAUTH] Token exchange error: {e}")
        return None


async def refresh_access_token(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    Refresh Token으로 새 Access Token 발급

    Args:
        refresh_token: Refresh token

    Returns:
        새 토큰 정보
    """
    try:
        import base64
        credentials = f"{settings.X_OAUTH2_CLIENT_ID}:{settings.X_OAUTH2_CLIENT_SECRET}"
        b64_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')

        headers = {
            "Authorization": f"Basic {b64_credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(X_TOKEN_URL, headers=headers, data=data)
            response.raise_for_status()
            token_data = response.json()

        logging.info(f"[X_OAUTH] Token refreshed successfully")
        return token_data

    except Exception as e:
        logging.error(f"[X_OAUTH] Token refresh error: {e}")
        return None


def save_token(token_data: Dict[str, Any]) -> None:
    """
    토큰을 파일에 저장

    Args:
        token_data: 토큰 정보
    """
    try:
        token_cache_file = get_token_cache_file()

        # 만료 시간 계산
        expires_in = token_data.get("expires_in", 7200)  # 기본 2시간
        expires_at = datetime.now() + timedelta(seconds=expires_in)

        cache_data = {
            "access_token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "token_type": token_data.get("token_type", "bearer"),
            "scope": token_data.get("scope", " ".join(SCOPES)),
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.now().isoformat(),
        }

        # JSON 파일로 저장
        with open(token_cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)

        logging.info(f"[X_OAUTH] Token saved to {token_cache_file}")

    except Exception as e:
        logging.error(f"[X_OAUTH] Failed to save token: {e}")


def load_token() -> Optional[Dict[str, Any]]:
    """
    저장된 토큰 불러오기

    Returns:
        토큰 정보 (없으면 None)
    """
    try:
        token_cache_file = get_token_cache_file()

        if not token_cache_file.exists():
            return None

        with open(token_cache_file, 'r', encoding='utf-8') as f:
            token_data = json.load(f)

        # 만료 시간 확인 (만료되어도 토큰 데이터는 반환 - refresh token이 유효할 수 있음)
        expires_at_str = token_data.get("expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            if datetime.now() >= expires_at:
                logging.warning(f"[X_OAUTH] Access token expired at {expires_at}, but returning for potential refresh")

        return token_data

    except Exception as e:
        logging.error(f"[X_OAUTH] Failed to load token: {e}")
        return None


async def get_valid_access_token() -> Optional[str]:
    """
    유효한 Access Token 가져오기 (필요시 자동 갱신)

    Returns:
        Access Token (없으면 None)
    """
    token_data = load_token()

    if not token_data:
        logging.warning("[X_OAUTH] No cached token found")
        return None

    # 토큰 만료 시간 확인
    expires_at_str = token_data.get("expires_at")
    if expires_at_str:
        expires_at = datetime.fromisoformat(expires_at_str)
        time_remaining = expires_at - datetime.now()

        # 토큰이 이미 만료되었거나 곧 만료될 예정 (10분 이내)
        if time_remaining.total_seconds() < 600:
            if time_remaining.total_seconds() < 0:
                logging.info("[X_OAUTH] Token already expired, refreshing...")
            else:
                logging.info("[X_OAUTH] Token expiring soon, refreshing...")

            refresh_token = token_data.get("refresh_token")

            if refresh_token:
                new_token_data = await refresh_access_token(refresh_token)
                if new_token_data:
                    save_token(new_token_data)
                    return new_token_data.get("access_token")
                else:
                    logging.error("[X_OAUTH] Failed to refresh token")
                    return None
            else:
                logging.error("[X_OAUTH] No refresh token available")
                return None

    return token_data.get("access_token")


def delete_token() -> None:
    """저장된 토큰 삭제"""
    try:
        token_cache_file = get_token_cache_file()
        if token_cache_file.exists():
            token_cache_file.unlink()
            logging.info("[X_OAUTH] Token deleted")
    except Exception as e:
        logging.error(f"[X_OAUTH] Failed to delete token: {e}")
