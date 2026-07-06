"""MCP HTTP 서버 — FastAPI에 `/mcp` 엔드포인트 마운트.

settings.MCP_ENABLED=true일 때만 활성. false면 마운트 자체를 안 해서 기존 시스템 무영향.

흐름:
    [Claude Code/Desktop] --HTTPS--> [FastAPI :8000/mcp]
                                          |
                            MCPAuthMiddleware (Bearer 검증)
                                          |
                                set_current_user(user_slack_id)
                                          |
                                  FastMCP streamable-http
                                          |
                                  도구 함수 (tools.py)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .auth import verify_token, verify_token_with_meta
from .mcp_app import build_mcp_app, set_current_user
from .oauth import verify_access_token, verify_any_token

logger = logging.getLogger(__name__)


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """`Authorization: Bearer <token>` 검증 + user_slack_id를 ContextVar에 주입."""

    def __init__(self, app, token_file: str):
        super().__init__(app)
        self.token_file = token_file

    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        if not auth or not auth.lower().startswith("bearer "):
            # Claude.ai가 OAuth flow를 시작하도록 WWW-Authenticate에 resource metadata 위치를 알림.
            issuer_proto = request.headers.get("x-forwarded-proto") or request.url.scheme
            issuer_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
            resource_meta = f"{issuer_proto}://{issuer_host}/.well-known/oauth-protected-resource"
            return JSONResponse(
                {"error": "missing bearer token"},
                status_code=401,
                headers={
                    "WWW-Authenticate": f'Bearer realm="moco-mcp", resource_metadata="{resource_meta}"'
                },
            )
        token = auth[7:].strip()
        # 토큰 → user_name + 메타 매핑
        user_name: str = ""
        meta: dict = {}
        if token.startswith("mcp_at_"):
            # OAuth access_token: 자체 저장소에서 user_name(닉네임) 반환
            user_name = verify_access_token(token) or ""
            if user_name:
                # 정적 토큰 매핑에서 메타 보완 (같은 닉네임 항목 있으면 가져옴)
                meta = {"user_name": user_name}
                static_meta = None
                try:
                    from .auth import _read_raw, _normalize_entry
                    raw = _read_raw(self.token_file)
                    if user_name in raw:
                        static_meta = _normalize_entry(raw[user_name])
                except Exception:
                    pass
                if static_meta:
                    meta["slack_user_id"] = static_meta.get("slack_user_id", "")
                    meta["email"] = static_meta.get("email", "")
        else:
            # 정적 토큰 (tok_*): 메타까지 한 번에
            full_meta = verify_token_with_meta(self.token_file, token)
            if full_meta:
                user_name = full_meta.get("user_name", "")
                meta = full_meta
        if not user_name:
            return JSONResponse({"error": "invalid token"}, status_code=401)
        set_current_user(user_name, meta)
        try:
            return await call_next(request)
        except Exception as e:
            logger.exception(f"[MCP] 도구 실행 오류 (user={user})")
            return JSONResponse(
                {"error": f"internal error: {e}"},
                status_code=500,
            )


def attach_mcp(parent_app: FastAPI, settings: Any) -> bool:
    """parent FastAPI에 `/mcp` 마운트.

    Returns:
        bool: 마운트 성공 여부 (False면 기존 시스템과 동일)
    """
    if not getattr(settings, "MCP_ENABLED", False):
        logger.info("[MCP] MCP_ENABLED=false → /mcp 마운트 안 함 (기존 시스템 그대로)")
        return False

    # OAuth 2.1 + MCP authorization 라우터 등록 (Claude.ai 웹 Custom Connector 호환)
    try:
        from .oauth_routes import router as oauth_router
        parent_app.include_router(oauth_router)
        logger.info("[MCP] OAuth 2.1 라우터 등록 완료 (/oauth/*, /.well-known/*)")
    except Exception as e:
        logger.exception(f"[MCP] OAuth 라우터 등록 실패: {e}")

    try:
        mcp = build_mcp_app()
    except ImportError as e:
        logger.error(
            f"[MCP] mcp 패키지 import 실패: {e}. "
            "uv add mcp 또는 pip install mcp 필요. 마운트 건너뜀."
        )
        return False
    except Exception as e:
        logger.exception(f"[MCP] FastMCP 빌드 실패: {e}")
        return False

    try:
        http_app = mcp.streamable_http_app()
    except Exception as e:
        logger.exception(f"[MCP] streamable_http_app 생성 실패: {e}")
        return False

    token_file = getattr(settings, "MCP_TOKEN_FILE", "~/.moco/mcp_tokens.json")
    http_app.add_middleware(MCPAuthMiddleware, token_file=token_file)

    path = getattr(settings, "MCP_PATH", "/mcp") or "/mcp"
    if not path.startswith("/"):
        path = "/" + path

    # FastAPI mount 시 sub-app lifespan이 자동 호출되지 않아 FastMCP의 task group이 초기화되지 않음.
    # parent의 startup/shutdown 이벤트에서 명시적으로 시작/종료시켜 task group을 활성화.
    @parent_app.on_event("startup")
    async def _start_mcp_lifespan():
        try:
            cm = http_app.router.lifespan_context(http_app)
            await cm.__aenter__()
            parent_app.state.__mcp_lifespan_cm = cm
            logger.info("[MCP] sub-app lifespan 시작 — task group 초기화 완료")
        except Exception as _e:
            logger.exception(f"[MCP] sub-app lifespan 시작 실패: {_e}")

    @parent_app.on_event("shutdown")
    async def _stop_mcp_lifespan():
        cm = getattr(parent_app.state, "__mcp_lifespan_cm", None)
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
                logger.info("[MCP] sub-app lifespan 종료")
            except Exception as _e:
                logger.warning(f"[MCP] sub-app lifespan 종료 실패: {_e}")

    parent_app.mount(path, http_app)

    logger.info(f"[MCP] 활성화됨 — {path} 엔드포인트 마운트 (token_file={token_file})")
    return True
