"""FastMCP 인스턴스 빌더 + 현재 요청의 사용자 추적 (ContextVar).

MCP 호출 흐름:
    HTTP 요청 → MCPAuthMiddleware (Bearer 검증) → set_current_user(user_slack_id)
    → FastMCP가 도구 함수 호출 → 도구 함수에서 get_current_user() → user 확인
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Optional

logger = logging.getLogger(__name__)

# 현재 MCP 요청의 인증된 사용자 메타 (도구 함수 안에서 접근)
# 형식: {"user_name": "관리자", "slack_user_id": "U01ABC...", "email": "..."}
_current_user_meta: ContextVar[Optional[dict]] = ContextVar("moco_mcp_current_user_meta", default=None)


def set_current_user(user_name: str, meta: Optional[dict] = None) -> None:
    """인증된 사용자 정보를 ContextVar에 저장. meta가 dict면 그대로 사용."""
    if meta is None:
        meta = {"user_name": user_name}
    elif "user_name" not in meta:
        meta = {**meta, "user_name": user_name}
    _current_user_meta.set(meta)


def get_current_user() -> str:
    """현재 인증된 user_name(닉네임) 반환."""
    meta = _current_user_meta.get()
    if not meta or not meta.get("user_name"):
        raise RuntimeError(
            "MCP 컨텍스트에 사용자 정보가 없습니다 (인증 미들웨어가 set_current_user를 호출하지 않은 것 같음)."
        )
    return meta["user_name"]


def get_current_user_meta() -> dict:
    """현재 인증된 사용자 전체 메타 반환 ({user_name, slack_user_id, email, ...})."""
    meta = _current_user_meta.get()
    if not meta:
        raise RuntimeError("MCP 컨텍스트에 사용자 정보가 없습니다.")
    return meta


def build_mcp_app():
    """FastMCP 인스턴스 생성 + 도구 등록. mcp 패키지 import 시점은 여기로 미룸."""
    from mcp.server.fastmcp import FastMCP  # noqa: WPS433 (지연 import)

    from . import tools  # noqa: WPS433

    mcp = FastMCP(
        name="moco",
        instructions=(
            "MOCO 봇 — acme 회사의 자체 AI 코워커.\n"
            "Slack에서 운영 중인 MOCO 봇과 같은 인스턴스이며 메모리/태스크/CRM/스케줄러를 공유합니다.\n\n"
            "자동 호출 규칙 (반드시 따르세요):\n"
            "사용자 메시지에 다음 패턴이 있으면 **무조건 moco_ask(message)를 즉시 호출**하세요:\n"
            "  • 'moco', '모코', 'MOCO' 호명 (예: 'moco야 ~', '모코 ~해줘')\n"
            "  • acme 회사 업무 관련 요청 (일정·회의·이메일·Slack·Confluence·Jira·CRM·병원·처방·계획·메모리)\n"
            "  • 본인 업무 컨텍스트 조회 ('내 일정', '미답신 메일', '오늘 할 일', '최근 결정사항' 등)\n\n"
            "원본 사용자 메시지를 그대로 message 인자로 전달.\n"
            "Claude의 자체 캘린더/이메일/Drive 도구를 사용하지 말고 moco_ask를 우선 호출하세요 — "
            "acme 멤버의 통합 컨텍스트와 권한이 MOCO 측에 있습니다.\n\n"
            "도구 분류:\n"
            "  - 메인: moco_ask(message)  ← 90% 이걸로 처리\n"
            "  - 검색: moco_search_memory, moco_list_email_tasks, moco_list_jira_tasks, moco_list_pending_answers\n"
            "  - 저장: moco_save_memory, moco_schedule_message\n"
            "  - 운영: moco_status"
        ),
    )
    tools.register(mcp)
    logger.info("[MCP] FastMCP 인스턴스 생성 — 도구 등록 완료")
    return mcp
