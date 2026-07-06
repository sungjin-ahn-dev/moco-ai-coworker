"""
PM Sub-agent (Project Management Sub-agent)

Jira, ClickUp, Atlassian 이슈 관리, 스프린트, 리포트를 담당합니다.
"""

import json
import logging
import os

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)
from app.cc_utils.sdk_retry import RetryableSDKClient

from app.cc_tools.clickup.clickup_tools import create_clickup_mcp_server
from app.config.settings import get_settings
from app.cc_agents.sub_agents.base import make_result, parse_result
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.prompt_helper import prepare_options


async def call_pm_agent(
    query: str,
    context: str = "",
    workspace_data: dict = None,
    message_data: dict = None,
) -> dict:
    """Jira/ClickUp 이슈·스프린트·리포트를 다루는 PM Sub-agent.

    Args:
        query: 수행할 PM 작업 설명
        context: 추가 컨텍스트 정보
        workspace_data: Orchestrator가 공유하는 작업 공간 데이터
        message_data: Slack 메시지 정보 (user_id, channel_id 등)

    Returns:
        dict: RESULT_SCHEMA 형태의 결과 딕셔너리
    """
    settings = get_settings()

    workspace_str = json.dumps(workspace_data or {}, ensure_ascii=False)
    message_str = json.dumps(message_data or {}, ensure_ascii=False)

    # 활성화된 PM 도구 안내 생성
    pm_tool_hints = []
    if settings.ATLASSIAN_ENABLED:
        pm_tool_hints.append(
            "- Atlassian(Confluence/Jira) 관련 작업은 mcp__atlassian__* 도구를 사용합니다."
        )
    if settings.CLICKUP_ENABLED:
        pm_tool_hints.append(
            "- ClickUp 작업 관리는 mcp__clickup__* 도구를 사용합니다. "
            "요청자 기준으로 동작하려면 username 파라미터에 요청자 이름을 전달하세요. "
            "워크스페이스 ID는 mcp__clickup__list_workspaces로 먼저 조회합니다."
        )

    pm_tools_str = "\n".join(pm_tool_hints) if pm_tool_hints else "- PM 도구 연동이 비활성화되어 있습니다."

    system_prompt = f"""당신은 프로젝트 관리(PM) 전문가입니다. Jira, ClickUp, Atlassian 이슈 관리, 스프린트 관리, 리포트 생성 등 프로젝트 관리 작업을 수행합니다.

## 역할
- 현재 시각 확인이 필요하면 mcp__time__get_current_time을 사용합니다.
{pm_tools_str}

## 출력 형식
반드시 다음 JSON 형식으로만 응답하세요. 마크다운 코드 블록 없이 순수 JSON만 출력합니다:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{
        "issues": [],
        "sprint": null,
        "report": null
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
        "time": local_mcp("@mcpcentral/mcp-time"),
    }

    if settings.ATLASSIAN_ENABLED:
        mcp_servers["atlassian"] = local_mcp(
            "mcp-remote",
            use_cache=True,
            extra_args=["https://mcp.atlassian.com/v1/sse"],
        )

    if settings.CLICKUP_ENABLED:
        mcp_servers["clickup"] = create_clickup_mcp_server()

    allowed_tools = [
        "mcp__time__*",
    ]

    if settings.ATLASSIAN_ENABLED:
        allowed_tools.append("mcp__atlassian__*")

    if settings.CLICKUP_ENABLED:
        allowed_tools.append("mcp__clickup__*")

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
        async with RetryableSDKClient(options, max_retries=3, agent_name="PM_AGENT") as client:
            await client.query(query)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    return parse_result(message.result)
    except Exception as e:
        logging.error(f"[PM_AGENT] Error: {e}")
        return make_result(
            status="failed",
            summary=f"PM 작업 실패: {str(e)}",
            error=str(e),
        )

    return make_result(
        status="failed",
        summary="응답 없음",
        error="no_response",
    )
