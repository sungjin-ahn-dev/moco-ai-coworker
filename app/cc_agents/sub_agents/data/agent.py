"""
데이터 Sub-agent (Data Sub-agent)

데이터 분석, Tableau, 시각화를 담당합니다.
"""

import json
import logging
import os

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)
from app.cc_utils.sdk_retry import RetryableSDKClient
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.prompt_helper import prepare_options

from app.cc_tools.files.files_tools import create_files_mcp_server
from app.cc_tools.google_drive.google_drive_tools import create_google_drive_mcp_server
from app.config.settings import get_settings
from app.cc_agents.sub_agents.base import make_result, parse_result, log_cache_usage


async def call_data_agent(
    query: str,
    context: str = "",
    workspace_data: dict = None,
    message_data: dict = None,
) -> dict:
    """데이터 분석·Tableau·시각화 작업. RESULT_SCHEMA dict 반환."""
    settings = get_settings()

    workspace_str = json.dumps(workspace_data or {}, ensure_ascii=False)
    message_str = json.dumps(message_data or {}, ensure_ascii=False)

    # 활성화된 데이터 도구 안내 생성
    data_tool_hints = []
    if settings.TABLEAU_ENABLED:
        data_tool_hints.append(
            "- Tableau 데이터 조회는 mcp__tableau__* 도구를 사용합니다. "
            "사용자가 정확한 대시보드를 명시하지 않으면 가장 많이 사용하는 대시보드 1개를 선택해서 보여줍니다."
        )
    if settings.GOOGLE_DRIVE_ENABLED:
        data_tool_hints.append(
            "- Google Drive에서 데이터 파일 검색은 mcp__google_drive__* 도구를 사용합니다. "
            "개인 드라이브 접근 시 slack_user_id 파라미터에 요청자의 Slack user_id를 전달하세요."
        )

    data_tools_str = "\n".join(data_tool_hints) if data_tool_hints else ""

    system_prompt = f"""당신은 데이터 분석 전문가입니다. 데이터 분석, Tableau 대시보드 조회, 통계 및 시각화 작업을 수행합니다.

## 역할
- 파일 읽기/쓰기는 mcp__files__* 도구를 사용합니다.
- 현재 시각 확인이 필요하면 mcp__time__get_current_time을 사용합니다.
{data_tools_str}

## 출력 형식
반드시 다음 JSON 형식으로만 응답하세요. 마크다운 코드 블록 없이 순수 JSON만 출력합니다:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{
        "analysis_result": null,
        "chart_data": null,
        "statistics": {{}}
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
        "files": create_files_mcp_server(),
        "time": local_mcp("@mcpcentral/mcp-time"),
    }

    if settings.TABLEAU_ENABLED:
        mcp_servers["tableau"] = local_mcp("@tableau/mcp-server", use_cache=True, env={
            "SERVER": settings.TABLEAU_SERVER,
            "SITE_NAME": settings.TABLEAU_SITE_NAME,
            "PAT_NAME": settings.TABLEAU_PAT_NAME,
            "PAT_VALUE": settings.TABLEAU_PAT_VALUE,
        })

    if settings.GOOGLE_DRIVE_ENABLED:
        mcp_servers["google_drive"] = create_google_drive_mcp_server()

    allowed_tools = [
        "mcp__files__*",
        "mcp__time__*",
    ]

    if settings.TABLEAU_ENABLED:
        allowed_tools.append("mcp__tableau__*")

    if settings.GOOGLE_DRIVE_ENABLED:
        allowed_tools.append("mcp__google_drive__*")

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
            "mcp__tableau__get-view-image",
        ],
        setting_sources=["project"],
        cwd=os.getcwd(),
    )
    options = prepare_options(options)

    try:
        async with RetryableSDKClient(options, max_retries=3, agent_name="DATA_AGENT") as client:
            await client.query(query)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    log_cache_usage(message, "data")
                    return parse_result(message.result)
    except Exception as e:
        logging.error(f"[DATA_AGENT] Error: {e}")
        return make_result(
            status="failed",
            summary=f"데이터 작업 실패: {str(e)}",
            error=str(e),
        )

    return make_result(
        status="failed",
        summary="응답 없음",
        error="no_response",
    )
