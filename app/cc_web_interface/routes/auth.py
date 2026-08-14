"""
Authentication Routes
사용자 인증 관련 라우트 (/auth/login, /auth/callback, /auth/logout)
"""

import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.cc_web_interface.auth_handler import auth_handler

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    """로그인 시작"""
    return await auth_handler.handle_login(request)


@router.get("/callback")
async def auth_callback(request: Request):
    """OAuth 콜백 처리"""
    return await auth_handler.handle_callback(request)


@router.get("/logout")
async def logout(request: Request):
    """로그아웃"""
    provider = request.session.get('user', {}).get('provider', 'unknown')
    request.session.clear()

    return HTMLResponse(
        content=f"""
        <h1>로그아웃 완료</h1>
        <p>{provider.title()} 계정에서 로그아웃되었습니다.</p>
        <p><a href="/">다시 로그인</a></p>
        """,
        status_code=200
    )


@router.get("/status")
async def auth_status(request: Request):
    """현재 인증 상태"""
    user = request.session.get('user')

    if not user:
        return {
            "logged_in": False,
            "user": None
        }

    return {
        "logged_in": True,
        "user": {
            "name": user.get('name', ''),
            "email": user.get('email', ''),
            "id": user.get('id', ''),
            "provider": user.get('provider', 'unknown')
        }
    }