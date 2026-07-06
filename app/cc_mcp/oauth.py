"""MOCO MCP OAuth 2.1 — Claude.ai 웹 Custom Connector 호환 인증.

흐름:
    Claude.ai 웹 → /.well-known/oauth-protected-resource (resource metadata)
                 → /.well-known/oauth-authorization-server (auth server metadata)
                 → POST /oauth/register (Dynamic Client Registration, RFC 7591)
                 → 브라우저 redirect → GET /oauth/authorize (PKCE, code_challenge)
                 → 사용자가 본인 토큰 붙여넣고 Approve
                 → POST 처리 → code 발급 → callback URL로 redirect
                 → POST /oauth/token (code + code_verifier)
                 → access_token 발급
                 → MCP 호출 시 Authorization: Bearer <access_token>

저장소:
    ~/.moco/mcp_oauth_tokens.json: { "<access_token>": "<user_slack_id>", ... }
    ~/.moco/mcp_oauth_clients.json: { "<client_id>": {redirect_uris, client_name, ...} }
    in-memory: pending authorization codes (5분 단명)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────── 토큰 prefix 구분 ───────────────────
# 직접 발급한 Bearer 토큰: tok_xxx (mcp_tokens.json)
# OAuth access_token:      mcp_at_xxx (mcp_oauth_tokens.json)
# OAuth authorization code: mcp_code_xxx (in-memory)
# OAuth client_id:         mcp_cli_xxx (mcp_oauth_clients.json)


def _resolve(path: str) -> Path:
    return Path(os.path.expanduser(path))


# ─────────────────── 인증 코드 (in-memory, 5분 단명) ───────────────────


@dataclass
class PendingCode:
    code: str
    user_slack_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    issued_at: float
    expires_at: float


_pending_codes: dict[str, PendingCode] = {}


def issue_authorization_code(
    user_slack_id: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    ttl_seconds: int = 300,
) -> str:
    """authorization code 발급 (5분 단명)."""
    code = f"mcp_code_{secrets.token_urlsafe(32)}"
    now = time.time()
    _pending_codes[code] = PendingCode(
        code=code,
        user_slack_id=user_slack_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        issued_at=now,
        expires_at=now + ttl_seconds,
    )
    _gc_expired_codes()
    return code


def _gc_expired_codes() -> None:
    now = time.time()
    expired = [c for c, info in _pending_codes.items() if info.expires_at < now]
    for c in expired:
        _pending_codes.pop(c, None)


def consume_authorization_code(code: str, code_verifier: str, redirect_uri: str) -> Optional[str]:
    """code + code_verifier 검증, 성공 시 user_slack_id 반환 후 code 삭제."""
    info = _pending_codes.pop(code, None)
    if info is None:
        logger.warning(f"[OAUTH] code not found or expired: {code[:16]}...")
        return None
    if info.expires_at < time.time():
        logger.warning(f"[OAUTH] code expired")
        return None
    if info.redirect_uri != redirect_uri:
        logger.warning(f"[OAUTH] redirect_uri mismatch: stored={info.redirect_uri} got={redirect_uri}")
        return None
    if not _verify_pkce(info.code_challenge, info.code_challenge_method, code_verifier):
        logger.warning(f"[OAUTH] PKCE verification failed")
        return None
    return info.user_slack_id


def _verify_pkce(code_challenge: str, method: str, code_verifier: str) -> bool:
    """PKCE: code_verifier → code_challenge 검증."""
    if method == "plain":
        return code_challenge == code_verifier
    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_challenge == computed
    logger.warning(f"[OAUTH] unsupported code_challenge_method: {method}")
    return False


# ─────────────────── Access Token 영구 저장 ───────────────────


def _tokens_path() -> Path:
    return _resolve("~/.moco/mcp_oauth_tokens.json")


def _load_oauth_tokens() -> dict[str, str]:
    p = _tokens_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[OAUTH] load tokens failed: {e}")
        return {}


def _save_oauth_tokens(data: dict[str, str]) -> None:
    p = _tokens_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def issue_access_token(user_slack_id: str) -> str:
    token = f"mcp_at_{secrets.token_urlsafe(32)}"
    data = _load_oauth_tokens()
    data[token] = user_slack_id
    _save_oauth_tokens(data)
    return token


def verify_access_token(token: str) -> Optional[str]:
    """access token → user_slack_id 또는 None."""
    if not token:
        return None
    return _load_oauth_tokens().get(token)


def revoke_access_token(token: str) -> bool:
    data = _load_oauth_tokens()
    if token not in data:
        return False
    del data[token]
    _save_oauth_tokens(data)
    return True


# ─────────────────── 정적 토큰 (직접 발급한 tok_xxx) ───────────────────


def verify_any_token(token_file: str, token: str) -> Optional[str]:
    """OAuth access_token 또는 정적 Bearer 토큰 둘 다 검증.

    우선순위:
        1. mcp_at_*  → OAuth access_token 저장소
        2. tok_*     → mcp_tokens.json (직접 발급)
    """
    if not token:
        return None
    if token.startswith("mcp_at_"):
        return verify_access_token(token)
    # 정적 토큰 fallback (기존 호환)
    from .auth import verify_token as _static_verify
    return _static_verify(token_file, token)


# ─────────────────── Dynamic Client Registration (RFC 7591) ───────────────────


def _clients_path() -> Path:
    return _resolve("~/.moco/mcp_oauth_clients.json")


def _load_clients() -> dict[str, dict]:
    p = _clients_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_clients(data: dict[str, dict]) -> None:
    p = _clients_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def register_client(redirect_uris: list[str], client_name: str = "", **extra) -> dict:
    """RFC 7591 Dynamic Client Registration.

    공개 클라이언트로 등록 (token_endpoint_auth_method=none).
    PKCE 필수.
    """
    client_id = f"mcp_cli_{secrets.token_urlsafe(16)}"
    record = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": redirect_uris,
        "client_name": client_name or "Unnamed MCP Client",
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        # extra의 metadata는 그대로 보존
        **{k: v for k, v in extra.items() if k not in ("client_id", "client_secret")},
    }
    data = _load_clients()
    data[client_id] = record
    _save_clients(data)
    logger.info(f"[OAUTH] client registered: {client_id} ({client_name})")
    return record


def get_client(client_id: str) -> Optional[dict]:
    return _load_clients().get(client_id)


# ─────────────────── 페이지 ───────────────────


def authorize_page_html(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    error: str = "",
) -> str:
    """사용자가 본인 토큰(tok_xxx)을 붙여넣고 Approve하는 HTML 페이지."""
    error_html = (
        f'<div class="err">{error}</div>' if error else ""
    )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MOCO MCP 인증</title>
<style>
  body {{ font-family: -apple-system,Segoe UI,sans-serif; background:#0f1116; color:#e6e9ef; margin:0; padding:48px 24px; min-height:100vh; box-sizing:border-box; }}
  .card {{ max-width:480px; margin:0 auto; background:#1a1d26; border:1px solid #2a2e3a; border-radius:16px; padding:32px; }}
  h1 {{ margin:0 0 8px; font-size:20px; }}
  .sub {{ color:#8a92a4; font-size:13px; margin-bottom:24px; }}
  label {{ display:block; font-size:12px; color:#8a92a4; margin-bottom:6px; text-transform:uppercase; letter-spacing:0.5px; }}
  input[type=text],input[type=password] {{
    width:100%; box-sizing:border-box; padding:10px 12px; background:#0f1116; border:1px solid #2a2e3a;
    border-radius:8px; color:#e6e9ef; font-size:14px; font-family:ui-monospace,monospace;
  }}
  .btn {{ width:100%; padding:11px; margin-top:20px; background:#5b5fc7; border:none; border-radius:8px;
    color:#fff; font-size:14px; font-weight:600; cursor:pointer; }}
  .btn:hover {{ background:#6f73d1; }}
  .meta {{ margin-top:24px; padding-top:16px; border-top:1px solid #2a2e3a; font-size:11px; color:#5d6478; line-height:1.6; }}
  .err {{ background:#3a1d22; color:#ff6b6b; padding:10px 12px; border-radius:8px; font-size:13px; margin-bottom:16px; }}
  code {{ background:#0f1116; padding:1px 6px; border-radius:4px; font-size:12px; }}
</style></head><body>
<div class="card">
  <h1>🔐 MOCO MCP 인증</h1>
  <div class="sub">Claude가 MOCO 봇 도구에 접근하려고 합니다. 본인 토큰을 입력하고 승인해주세요.</div>
  {error_html}
  <form method="post" action="/oauth/authorize">
    <label for="moco_token">MOCO MCP 토큰</label>
    <input type="password" id="moco_token" name="moco_token" placeholder="tok_..." autocomplete="off" autofocus required>

    <input type="hidden" name="client_id" value="{client_id}">
    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <input type="hidden" name="state" value="{state}">
    <input type="hidden" name="code_challenge" value="{code_challenge}">
    <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
    <input type="hidden" name="scope" value="{scope}">

    <button type="submit" class="btn">승인하고 연결</button>
  </form>
  <div class="meta">
    승인 시 Claude는 본인 권한으로 MOCO에 메모리/태스크/Slack 등을 조회·생성할 수 있습니다.<br>
    토큰이 없으면 관리자님께 발급 요청 (<code>U_본인슬랙ID</code>로 매핑됨).
  </div>
</div>
</body></html>"""
