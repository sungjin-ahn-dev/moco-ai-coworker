"""기존 Slack-based agent들이 받는 slack_data/message_data와 호환되는 가상 컨텍스트 생성.

agent들은 Slack event를 가정하고 만들어졌으므로, MCP 호출 시 같은 형태의 dict로 흉내냄.
- channel_id: "MCP_<user_name>" (Slack과 충돌 안 하는 prefix)
- user_name: 닉네임 (Operator의 set_clickup_requester 등이 매칭에 사용)
- user_id: 실제 Slack user ID 알면 그것, 모르면 user_name fallback
- is_dm: True (1:1 대화처럼 동작)
- is_mcp: True (필요 시 agent에서 분기 가능)
"""

from __future__ import annotations

import time
from typing import Any, Optional


def make_slack_data(user_name: str, slack_user_id: str = "", email: str = "") -> dict[str, Any]:
    """기존 agent들이 받는 slack_data 호환 dict.

    Args:
        user_name: 닉네임 (예: "관리자"). Operator가 매칭에 사용.
        slack_user_id: 실제 Slack ID (선택, 알면 넣음).
        email: 이메일 (선택).
    """
    member_id = slack_user_id or user_name
    return {
        "channel_id": f"MCP_{user_name}",
        "channel_name": "mcp_session",
        "is_dm": True,
        "is_mcp": True,
        "members": [{
            "id": member_id,
            "name": user_name,
            "real_name": user_name,
            "email": email,
        }],
        "messages": [],
    }


def make_message_data(
    user_name: str,
    text: str,
    slack_user_id: str = "",
    email: str = "",
) -> dict[str, Any]:
    """기존 agent들이 받는 message_data 호환 dict.

    user_name이 Operator의 set_clickup_requester 및 닉네임 의존 로직의 매칭 키입니다.
    """
    member_id = slack_user_id or user_name
    return {
        "user_id": member_id,
        "user_name": user_name,  # 닉네임 — Operator 매칭의 핵심
        "user_email": email,
        "text": text,
        "channel_id": f"MCP_{user_name}",
        "is_dm": True,
        "is_mcp": True,
        "ts": str(time.time()),
    }
