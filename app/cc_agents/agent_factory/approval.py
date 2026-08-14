"""
승인 게이트.

AGENT_APPROVER_SLACK_ID 가 비어있으면 자동 publish (개발/MVP 모드).
채워두면 Slack DM 으로 승인 요청 → 승인 시 publish, 반려 시 폐기.

승인 응답 수신은 두 가지 경로 (둘 다 작동):
1. Block Kit 인터랙티브 버튼 (기본) — `agent_factory_approve` / `agent_factory_reject` action_id.
   cc_slack_handlers.register_handlers() 가 @app.action 으로 처리.
2. 텍스트 회신 폴백 — "approve <agent_id>" / "reject <agent_id> <사유>" DM.

본 모듈은 발송과 publish/reject 함수만 제공.
"""

import logging
from typing import Optional

from app.cc_agents.agent_factory import installer, registry
from app.cc_tools.slack.slack_tools import get_slack_client
from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


async def send_approval_request(agent_id: str) -> Optional[str]:
    """
    관리자(또는 설정된 승인자) 에게 Slack DM 발송.

    Returns:
        발송한 메시지의 ts (있으면) 또는 None.
    """
    settings = get_settings()
    approver = (settings.AGENT_APPROVER_SLACK_ID or "").strip()
    entry = registry.get(agent_id)
    if entry is None:
        logger.error(f"[AGENT_APPROVAL] {agent_id} not in registry")
        return None
    if not approver:
        logger.info(f"[AGENT_APPROVAL] AGENT_APPROVER_SLACK_ID 비어있음 — 자동 승인 모드")
        await auto_approve(agent_id, reason="approver_unset")
        return None

    client = get_slack_client()
    if client is None:
        logger.error("[AGENT_APPROVAL] Slack client 없음 — 승인 요청 보낼 수 없음")
        return None

    # 1) DM 채널 열기
    try:
        dm = await client.conversations_open(users=approver)
        channel_id = dm["channel"]["id"]
    except Exception as e:
        logger.error(f"[AGENT_APPROVAL] DM open 실패: {e}")
        return None

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🆕 새 에이전트 승인 요청", "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*이름*\n{entry['agent_name']}"},
                {"type": "mrkdwn", "text": f"*ID*\n`{entry['agent_id']}`"},
                {"type": "mrkdwn", "text": f"*요청자*\n{entry['created_by']}"},
                {"type": "mrkdwn", "text": f"*모델*\n{entry['model_tier']}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*설명*\n{entry['description']}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*시스템 프롬프트 미리보기*\n```{_truncate(entry['system_prompt_preview'], 1500)}```",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*도구 권한*\n`{', '.join(entry['allowed_tools'])}`\n"
                        f"*참조 자료*: `{entry.get('corpus_dir') or '없음'}`",
            },
        },
        {
            "type": "actions",
            "block_id": f"agent_factory_actions_{agent_id}",
            "elements": [
                {
                    "type": "button",
                    "action_id": "agent_factory_approve",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "✅ 승인", "emoji": True},
                    "value": agent_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "에이전트 publish"},
                        "text": {"type": "mrkdwn", "text": f"`{entry['agent_id']}` 를 즉시 publish 합니다. 진행할까요?"},
                        "confirm": {"type": "plain_text", "text": "publish"},
                        "deny": {"type": "plain_text", "text": "취소"},
                    },
                },
                {
                    "type": "button",
                    "action_id": "agent_factory_reject",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "❌ 반려", "emoji": True},
                    "value": agent_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "에이전트 반려"},
                        "text": {"type": "mrkdwn", "text": f"`{entry['agent_id']}` 를 반려하고 폐기합니다. 진행할까요?"},
                        "confirm": {"type": "plain_text", "text": "반려"},
                        "deny": {"type": "plain_text", "text": "취소"},
                    },
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "버튼이 안 보이면 DM 으로 "
                        f"`approve {agent_id}` 또는 `reject {agent_id} <사유>` 회신해도 됩니다."
                    ),
                }
            ],
        },
    ]

    try:
        result = await client.chat_postMessage(
            channel=channel_id,
            text=f"새 에이전트 승인 요청: {entry['agent_name']}",
            blocks=blocks,
        )
        return result.get("ts")
    except Exception as e:
        logger.error(f"[AGENT_APPROVAL] DM 발송 실패: {e}")
        return None


def publish(agent_id: str, approver_id: str = "") -> None:
    """
    승인 완료 처리 — registry 상태 변경 + hot reload + routes 매핑 갱신.
    """
    entry = registry.get(agent_id)
    if entry is None:
        raise KeyError(agent_id)
    if entry["status"] in ("approved",):
        logger.info(f"[AGENT_APPROVAL] {agent_id} 이미 approved — 노옵")
        return

    # hot reload
    installer.hot_reload(agent_id)

    # routes 의 _AGENT_STREAMERS 갱신
    try:
        from app.cc_web_interface.chat import routes as chat_routes
        streamer = installer.get_streamer(agent_id)
        chat_routes._AGENT_STREAMERS[agent_id] = streamer
        logger.info(f"[AGENT_APPROVAL] routes._AGENT_STREAMERS['{agent_id}'] 등록")
    except Exception as e:
        logger.error(f"[AGENT_APPROVAL] routes 등록 실패 (publish는 계속): {e}")

    from app.cc_agents.agent_factory.registry import _now_iso
    registry.set_status(
        agent_id,
        "approved",
        approved_at=_now_iso(),
        approver_slack_id=approver_id or entry.get("approver_slack_id", ""),
    )


def reject(agent_id: str, reason: str = "") -> None:
    """반려 처리 — generated/<id> 삭제 + registry status='rejected'."""
    installer.rollback(agent_id, reason=reason or "rejected_by_approver")


async def auto_approve(agent_id: str, reason: str = "") -> None:
    """approver 미설정 시 자동 publish."""
    logger.warning(f"[AGENT_APPROVAL] 자동 승인 모드 ({reason}) → publish {agent_id}")
    publish(agent_id, approver_id="auto")
