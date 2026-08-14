"""OAuth 2.1 + MCP authorization HTTP routes.

Mounted on FastAPI parent app:
    /.well-known/oauth-protected-resource
    /.well-known/oauth-authorization-server
    /oauth/register   (POST, RFC 7591 DCR)
    /oauth/authorize  (GET 페이지 / POST 승인)
    /oauth/token      (POST, code → access_token)
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .auth import verify_token as verify_static_token
from .oauth import (
    authorize_page_html,
    consume_authorization_code,
    get_client,
    issue_access_token,
    issue_authorization_code,
    register_client,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _issuer_from_request(request: Request) -> str:
    """Forwarded 헤더(Cloudflare 등) 우선, 없으면 request URL에서 base."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    return f"{proto}://{host}"


# ────────────────────── Resource Metadata (RFC 9728) ──────────────────────


@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource(request: Request):
    issuer = _issuer_from_request(request)
    return JSONResponse({
        "resource": f"{issuer}/mcp/mcp",
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
    })


# ──────────────────── Authorization Server Metadata (RFC 8414) ────────────────────


@router.get("/.well-known/oauth-authorization-server")
async def oauth_authorization_server(request: Request):
    issuer = _issuer_from_request(request)
    return JSONResponse({
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "scopes_supported": ["mcp"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "token_endpoint_auth_methods_supported": ["none"],
        "service_documentation": f"{issuer}/mcp/mcp",
    })


@router.get("/.well-known/openid-configuration")
async def openid_alias(request: Request):
    """일부 클라이언트는 OIDC discovery 경로를 시도 — 같은 metadata 반환."""
    return await oauth_authorization_server(request)


# ──────────────────── Dynamic Client Registration (RFC 7591) ────────────────────


@router.post("/oauth/register")
async def register(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request", "error_description": "JSON body required"}, status_code=400)

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse({"error": "invalid_redirect_uri", "error_description": "redirect_uris required"}, status_code=400)

    client_name = body.get("client_name", "")
    record = register_client(
        redirect_uris=redirect_uris,
        client_name=client_name,
        token_endpoint_auth_method=body.get("token_endpoint_auth_method", "none"),
        grant_types=body.get("grant_types", ["authorization_code"]),
        response_types=body.get("response_types", ["code"]),
        scope=body.get("scope", "mcp"),
    )
    return JSONResponse(record, status_code=201)


# ──────────────────── Authorization Endpoint ────────────────────


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize_get(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    state: str = Query(""),
    code_challenge: str = Query(""),
    code_challenge_method: str = Query("S256"),
    scope: str = Query("mcp"),
):
    if response_type != "code":
        return HTMLResponse(f"<h2>unsupported response_type: {response_type}</h2>", status_code=400)
    if not code_challenge:
        return HTMLResponse("<h2>code_challenge required (PKCE)</h2>", status_code=400)
    # client_id 검증 — 없으면 자동 등록 허용 (Claude.ai가 register 안 거치는 경우 대비)
    if not get_client(client_id):
        logger.info(f"[OAUTH] unregistered client_id used in /authorize: {client_id} (allowed)")

    return HTMLResponse(authorize_page_html(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
    ))


@router.post("/oauth/authorize")
async def authorize_post(
    request: Request,
    moco_token: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form("S256"),
    scope: str = Form("mcp"),
):
    """사용자가 토큰 붙여넣고 Approve."""
    # MCP_TOKEN_FILE 경로
    from app.config.settings import get_settings
    settings = get_settings()
    token_file = getattr(settings, "MCP_TOKEN_FILE", "~/.moco/mcp_tokens.json")

    # verify_static_token은 user_name(닉네임)을 반환 (v2 형식)
    user_name = verify_static_token(token_file, moco_token.strip())
    if not user_name:
        return HTMLResponse(authorize_page_html(
            client_id=client_id, redirect_uri=redirect_uri, state=state,
            code_challenge=code_challenge, code_challenge_method=code_challenge_method,
            scope=scope, error="❌ 토큰이 올바르지 않습니다. 관리자님께 다시 발급받아주세요.",
        ), status_code=401)

    code = issue_authorization_code(
        user_slack_id=user_name,  # 실제로는 닉네임 — OAuth 코드 → access_token에 그대로 저장됨
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )
    sep = "&" if "?" in redirect_uri else "?"
    params = {"code": code}
    if state:
        params["state"] = state
    location = f"{redirect_uri}{sep}{urlencode(params)}"
    logger.info(f"[OAUTH] authorize OK user={user_slack_id} → redirect {redirect_uri}")
    return RedirectResponse(url=location, status_code=302)


# ──────────────────── Token Endpoint ────────────────────


@router.post("/oauth/token")
async def token_endpoint(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    code_verifier: str = Form(""),
    client_id: str = Form(""),
):
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    if not code or not code_verifier or not redirect_uri:
        return JSONResponse({"error": "invalid_request", "error_description": "code/code_verifier/redirect_uri required"}, status_code=400)

    user_slack_id = consume_authorization_code(code=code, code_verifier=code_verifier, redirect_uri=redirect_uri)
    if not user_slack_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    access_token = issue_access_token(user_slack_id)
    logger.info(f"[OAUTH] token issued for user={user_slack_id}")
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "scope": "mcp",
        # 만료 없음(refresh 미구현). 필요 시 expires_in/refresh_token 추가.
    })
