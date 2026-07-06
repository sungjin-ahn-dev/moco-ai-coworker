"""
커뮤니케이션 Sub-agent (Communication Sub-agent)

Slack 메시지 전송, 이메일, 메시지 포워딩을 담당합니다.
"""

import json
import logging
import os

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)
from app.cc_utils.sdk_retry import RetryableSDKClient

from app.cc_tools.slack.slack_tools import create_slack_mcp_server
from app.cc_tools.gmail.gmail_tools import create_gmail_mcp_server
from app.config.settings import get_settings
from app.cc_agents.sub_agents.base import make_result, parse_result
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.prompt_helper import prepare_options


async def call_communication_agent(
    query: str,
    context: str = "",
    workspace_data: dict = None,
    message_data: dict = None,
) -> dict:
    """Slack 메시지 전송·이메일·포워딩을 처리하는 커뮤니케이션 Sub-agent.

    Args:
        query: 수행할 커뮤니케이션 작업 설명
        context: 추가 컨텍스트 정보
        workspace_data: Orchestrator가 공유하는 작업 공간 데이터
        message_data: Slack 메시지 정보 (user_id, channel_id 등)

    Returns:
        dict: RESULT_SCHEMA 형태의 결과 딕셔너리
    """
    settings = get_settings()

    workspace_str = json.dumps(workspace_data or {}, ensure_ascii=False)
    message_str = json.dumps(message_data or {}, ensure_ascii=False)

    # 이메일 도구 안내 (활성화된 도구에 따라 달라짐)
    email_tool_hint = ""
    if settings.GMAIL_ENABLED:
        email_tool_hint = "- 이메일 발송/조회는 mcp__gmail__* 도구를 사용합니다. 반드시 slack_user_id 파라미터에 요청자의 Slack user_id를 전달하세요."
    elif settings.MS365_ENABLED:
        email_tool_hint = "- 이메일 발송/조회는 mcp__ms365__* 도구를 사용합니다."

    system_prompt = f"""당신은 커뮤니케이션 전문가입니다. Slack 메시지 전송, 이메일 발송, 메시지 포워딩 등 커뮤니케이션 작업을 수행합니다.

## 역할
- Slack 메시지 전송 및 포워딩은 mcp__slack__* 도구를 사용합니다.
- 현재 시각 확인이 필요하면 mcp__time__get_current_time을 사용합니다.
{email_tool_hint}

## 중요 원칙
- user_id를 알 수 없거나 불확실한 경우 절대 추론하지 마세요. 명확한 정보가 없으면 작업을 중단하고 오류로 반환합니다.
- 같은 내용의 메시지를 여러 명에게 보낼 때는 mcp__slack__forward_message를 단 한 번만 호출하고 respondents 리스트에 모든 수신자를 포함합니다.
- 개인화된 인사말을 추가하지 마세요.

## 출력 형식
반드시 다음 JSON 형식으로만 응답하세요. 마크다운 코드 블록 없이 순수 JSON만 출력합니다:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{
        "sent_to": [],
        "message_ts": null,
        "channel_id": null
    }},
    "artifacts": [],
    "next_suggestions": [],
    "error": null
}}

## 컨텍스트
{context}

## 공유 작업 공간
{workspace_str}

## 메시지 정보
{message_str}
"""

    mcp_servers = {
        "slack": create_slack_mcp_server(),
        "time": local_mcp("@mcpcentral/mcp-time"),
    }

    if settings.GMAIL_ENABLED:
        mcp_servers["gmail"] = create_gmail_mcp_server()

    if settings.MS365_ENABLED:
        mcp_servers["ms365"] = local_mcp(
            "@batteryho/lokka-cached",
            use_cache=True,
            env={
                "TENANT_ID": settings.MS365_TENANT_ID,
                "CLIENT_ID": settings.MS365_CLIENT_ID,
                "USE_INTERACTIVE": "true",
            },
        )

    allowed_tools = [
        "mcp__slack__*",
        "mcp__time__*",
    ]

    if settings.GMAIL_ENABLED:
        allowed_tools.append("mcp__gmail__*")

    if settings.MS365_ENABLED:
        allowed_tools.append("mcp__ms365__*")

    options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        model=settings.MODEL_FOR_MODERATE,
        permission_mode="bypassPermissions",
        allowed_tools=allowed_tools,
        disallowed_tools=[
            "Bash(curl:*)",
            "Bash(rm:*)",
            "Read(./.env)",
            "Read(./credential.json)",
        ],
        setting_sources=["project"],
        cwd=os.getcwd(),
    )
    options = prepare_options(options)

    try:
        async with RetryableSDKClient(options, max_retries=3, agent_name="COMMUNICATION_AGENT") as client:
            await client.query(query)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    return parse_result(message.result)
    except Exception as e:
        logging.error(f"[COMMUNICATION_AGENT] Error: {e}")
        return make_result(
            status="failed",
            summary=f"커뮤니케이션 작업 실패: {str(e)}",
            error=str(e),
        )

    return make_result(
        status="failed",
        summary="응답 없음",
        error="no_response",
    )
