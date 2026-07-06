"""MCP 토큰 인증 — `~/.moco/mcp_tokens.json` 기반 닉네임 ↔ Bearer token 매핑.

파일 형식 (JSON, v2 — 권장):
    {
      "관리자": {
        "token": "tok_xxx",
        "slack_user_id": "U01ABC...",   // 선택
        "email": "user@example.com"      // 선택
      },
      "사용자A": { "token": "tok_yyy" }
    }

key는 **닉네임(user_name)** 으로 사용 — Slack 봇과 같이 Operator의 user_name 매칭에 사용됩니다.

Backward compat (v1):
    { "U01ABC...": "tok_xxx" }  // 기존 형식도 인식. 닉네임 = key 그대로 사용.

새 멤버 추가:
    from app.cc_mcp.auth import add_user_token
    add_user_token("~/.moco/mcp_tokens.json", "관리자", slack_user_id="U01ABC...", email="user@example.com")
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _resolve(path: str) -> Path:
    return Path(os.path.expanduser(path))


def _read_raw(path: str) -> dict[str, Any]:
    p = _resolve(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"[MCP_AUTH] {p} 파싱 실패: {e}")
        return {}


def _write_raw(path: str, data: dict[str, Any]) -> None:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _normalize_entry(value: Any) -> dict[str, Any]:
    """v1 (단순 string) 또는 v2 (dict) 둘 다 받아서 dict로 정규화."""
    if isinstance(value, str):
        return {"token": value}
    if isinstance(value, dict):
        return {k: v for k, v in value.items()}
    return {}


def load_user_to_token(path: str) -> dict[str, str]:
    """user_name(=key) → token 정방향 매핑 (UI/디버깅용)."""
    raw = _read_raw(path)
    out = {}
    for k, v in raw.items():
        entry = _normalize_entry(v)
        token = entry.get("token")
        if token:
            out[str(k)] = str(token)
    return out


def load_token_to_meta(path: str) -> dict[str, dict[str, Any]]:
    """token → {user_name, slack_user_id, email, ...} 메타 매핑."""
    raw = _read_raw(path)
    out = {}
    for k, v in raw.items():
        entry = _normalize_entry(v)
        token = entry.get("token")
        if not token:
            continue
        meta = {
            "user_name": str(k),
            "slack_user_id": entry.get("slack_user_id") or "",
            "email": entry.get("email") or "",
        }
        # 추가 필드도 보존
        for ek, ev in entry.items():
            if ek not in ("token",) and ek not in meta:
                meta[ek] = ev
        out[str(token)] = meta
    return out


def load_token_to_user(path: str) -> dict[str, str]:
    """token → user_name (=key, 닉네임) 매핑. backward compat용."""
    return {tok: meta["user_name"] for tok, meta in load_token_to_meta(path).items()}


def issue_token() -> str:
    """새 토큰 발급."""
    return f"tok_{secrets.token_urlsafe(24)}"


_SLACK_ID_RE = __import__("re").compile(r"^[UW][A-Z0-9]{6,}$")


def add_user_token(
    path: str,
    user_or_slack_id: str,
    token: Optional[str] = None,
    slack_user_id: str = "",
    email: str = "",
) -> str:
    """사용자에게 토큰을 발급/저장하고 토큰 문자열 반환.

    Args:
        user_or_slack_id:
            - 진짜 Slack ID (예: "U03ABCDEF")를 주면 Slack API로 닉네임/이메일 자동 조회 → 닉네임을 key로 저장.
            - 이미 닉네임 형태(예: "관리자")이면 그대로 key로 사용.
        token: None이면 새로 발급. 이미 있으면 덮어씀.
        slack_user_id: 명시적으로 Slack ID 지정 (자동 조회 결과를 override).
        email: 이메일 (override).
    """
    arg = user_or_slack_id.strip()
    resolved_name = arg
    resolved_slack_id = slack_user_id
    resolved_email = email

    # 인자가 Slack ID 형태면 Slack API로 닉네임/이메일 자동 조회 시도
    if _SLACK_ID_RE.match(arg):
        resolved_slack_id = resolved_slack_id or arg
        # bash 세션에서 직접 호출 시 SLACK_BOT_TOKEN 환경변수가 없을 수 있어 settings에서 보강
        if not os.environ.get("SLACK_BOT_TOKEN"):
            try:
                from app.config.settings import get_settings
                _s = get_settings()
                if getattr(_s, "SLACK_BOT_TOKEN", ""):
                    os.environ["SLACK_BOT_TOKEN"] = _s.SLACK_BOT_TOKEN
            except Exception:
                pass
        try:
            from app.cc_utils.slack_helper import get_user_info
            info = get_user_info(arg)
            if info:
                # 닉네임 우선순위: profile.display_name > real_name > name > Slack ID 그대로
                profile = info.get("profile", {}) if isinstance(info.get("profile"), dict) else {}
                resolved_name = (
                    profile.get("display_name")
                    or info.get("real_name")
                    or info.get("name")
                    or arg
                )
                resolved_email = resolved_email or info.get("email", "") or profile.get("email", "")
                logger.info(f"[MCP_AUTH] Slack 조회 성공: {arg} → name={resolved_name!r} email={resolved_email!r}")
            else:
                logger.warning(f"[MCP_AUTH] Slack 조회 실패: {arg} (닉네임 = ID 그대로 사용)")
        except Exception as e:
            logger.warning(f"[MCP_AUTH] Slack 조회 중 예외(무시): {e}")

    raw = _read_raw(path)
    tok = token or issue_token()
    entry: dict[str, Any] = {"token": tok}
    if resolved_slack_id:
        entry["slack_user_id"] = resolved_slack_id
    if resolved_email:
        entry["email"] = resolved_email
    raw[resolved_name] = entry
    _write_raw(path, raw)
    return tok


def remove_user_token(path: str, user_name: str) -> bool:
    """사용자 토큰 제거. 성공하면 True."""
    raw = _read_raw(path)
    if user_name not in raw:
        return False
    del raw[user_name]
    _write_raw(path, raw)
    return True


def verify_token(path: str, token: str) -> Optional[str]:
    """토큰 검증. 유효하면 user_name 반환, 아니면 None.

    backward compat: 기존 v1 형식 (단순 string token)에서도 동작.
    """
    if not token:
        return None
    return load_token_to_user(path).get(token)


def verify_token_with_meta(path: str, token: str) -> Optional[dict[str, Any]]:
    """토큰 검증 + 메타데이터 반환. {user_name, slack_user_id, email, ...} 또는 None."""
    if not token:
        return None
    return load_token_to_meta(path).get(token)
