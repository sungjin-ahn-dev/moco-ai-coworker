"""
Bot Authentication Routes
봇이 외부 API를 사용하기 위한 인증 설정 라우트
(X/Twitter 등)
"""

import logging
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from app.cc_slack_handlers import is_authorized_user
from app.cc_utils import x_helper
from app.cc_web_interface.oauth_session_store import oauth_session_store
from app.cc_web_interface.utils import get_redirect_uri

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/bot/auth", tags=["bot-auth"])


def require_admin(request: Request) -> dict:
    """관리자 권한 확인"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not is_authorized_user(user.get("name", "")):
        raise HTTPException(status_code=403, detail="Not authorized")

    return user


# ========== X(Twitter) OAuth 2.0 ==========

@router.get("/x/start")
async def x_auth_start(request: Request):
    """X OAuth 2.0 인증 시작 (로그인 불필요 - 초기 세팅용)"""
    try:
        # PKCE 생성
        code_verifier = x_helper.generate_code_verifier()
        code_challenge = x_helper.generate_code_challenge(code_verifier)

        # State (CSRF 방지)
        state = x_helper.secrets.token_urlsafe(32)

        # 파일 기반 세션에 저장
        oauth_session_store.store(state, {
            "code_verifier": code_verifier,
        })

        # Redirect URI
        redirect_uri = get_redirect_uri(request, 'x_auth_callback')

        # X 인증 URL 생성
        auth_url = x_helper.get_authorization_url(redirect_uri, state, code_challenge)

        logger.info(f"[X_AUTH] Starting OAuth flow, state={state}")

        return RedirectResponse(url=auth_url)

    except Exception as e:
        logger.error(f"[X_AUTH] Start error: {e}")
        return HTMLResponse(
            content=f"<h1>X 인증 시작 실패</h1><p>{str(e)}</p>",
            status_code=500
        )


@router.get("/x/callback")
async def x_auth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None
):
    """X OAuth 2.0 Callback"""
    try:
        # 에러 처리
        if error:
            logger.error(f"[X_AUTH] OAuth error: {error} - {error_description}")
            return HTMLResponse(
                content=f"""
                <html>
                <head><title>X 인증 실패</title></head>
                <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1>❌ X (Twitter) 인증 실패</h1>
                    <p><strong>Error:</strong> {error}</p>
                    <p><strong>Description:</strong> {error_description or 'N/A'}</p>
                    <p><a href="/bot/auth/x/start">다시 시도</a></p>
                </body>
                </html>
                """,
                status_code=400
            )

        # 파라미터 검증
        if not code or not state:
            logger.error("[X_AUTH] Missing code or state")
            return HTMLResponse(
                content="<h1>잘못된 요청</h1><p>필수 파라미터가 누락되었습니다.</p>",
                status_code=400
            )

        # 세션 데이터 가져오기
        session_data = oauth_session_store.retrieve(state)
        if not session_data:
            logger.error(f"[X_AUTH] Invalid state: {state}")
            return HTMLResponse(
                content="<h1>유효하지 않은 요청</h1><p>세션이 만료되었거나 잘못된 요청입니다.</p>",
                status_code=400
            )

        code_verifier = session_data["code_verifier"]
        user_name = session_data.get("user_name", "Unknown")

        # 세션 삭제 (일회용)
        oauth_session_store.delete(state)

        # Redirect URI
        redirect_uri = get_redirect_uri(request, 'x_auth_callback')

        # Authorization Code를 Access Token으로 교환
        token_data = await x_helper.exchange_code_for_token(code, code_verifier, redirect_uri)

        if not token_data:
            return HTMLResponse(
                content="<h1>토큰 발급 실패</h1><p>X API에서 토큰을 받지 못했습니다.</p>",
                status_code=500
            )

        # 토큰 저장
        x_helper.save_token(token_data)

        logger.info(f"[X_AUTH] OAuth completed for {user_name}")

        return HTMLResponse(
            content="<body style='background:#000;color:#fff;font-family:monospace;padding:20px'>X OAuth authentication completed. You can close this window.</body>",
            status_code=200
        )

    except Exception as e:
        logger.error(f"[X_AUTH] Callback error: {e}")
        return HTMLResponse(
            content=f"<h1>X 인증 실패</h1><p>{str(e)}</p>",
            status_code=500
        )


@router.get("/x/status")
async def x_auth_status():
    """X OAuth 인증 상태 확인 (로그인 불필요)"""
    try:
        token_data = x_helper.load_token()

        if not token_data:
            return {
                "authenticated": False,
                "message": "X 인증이 필요합니다."
            }

        # 만료 시간 확인
        expires_at_str = token_data.get("expires_at")
        from datetime import datetime
        expires_at = datetime.fromisoformat(expires_at_str)
        time_remaining = expires_at - datetime.now()

        if time_remaining.total_seconds() <= 0:
            return {
                "authenticated": False,
                "message": "토큰이 만료되었습니다. 재인증이 필요합니다."
            }

        return {
            "authenticated": True,
            "scope": token_data.get("scope", ""),
            "expires_at": expires_at_str,
            "time_remaining_seconds": int(time_remaining.total_seconds()),
            "time_remaining_human": str(time_remaining).split('.')[0],
            "created_at": token_data.get("created_at"),
        }

    except Exception as e:
        logger.error(f"[X_AUTH] Status check error: {e}")
        return {
            "authenticated": False,
            "error": str(e)
        }


@router.post("/x/logout")
async def x_auth_logout():
    """X OAuth 토큰 삭제 (로그인 불필요)"""
    try:
        x_helper.delete_token()
        logger.info("[X_AUTH] Token deleted")
        return {
            "success": True,
            "message": "X 인증 토큰이 삭제되었습니다."
        }
    except Exception as e:
        logger.error(f"[X_AUTH] Logout error: {e}")
        return {
            "success": False,
            "error": str(e)
        }