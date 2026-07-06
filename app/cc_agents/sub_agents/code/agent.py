"""
코드 Sub-agent (Code Sub-agent)

GitLab/GitHub 코드 작업, 파일 관리를 담당합니다.
"""

import json
import logging
import os

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)
from app.cc_utils.sdk_retry import RetryableSDKClient

from app.cc_tools.files.files_tools import create_files_mcp_server
from app.config.settings import get_settings
from app.cc_agents.sub_agents.base import make_result, parse_result
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.prompt_helper import prepare_options


async def call_code_agent(
    query: str,
    context: str = "",
    workspace_data: dict = None,
    message_data: dict = None,
) -> dict:
    """GitLab/GitHub 코드 작업·PR 리뷰·파일 관리를 담당하는 Sub-agent.

    Args:
        query: 수행할 코드 작업 설명
        context: 추가 컨텍스트 정보
        workspace_data: Orchestrator가 공유하는 작업 공간 데이터
        message_data: Slack 메시지 정보 (user_id, channel_id 등)

    Returns:
        dict: RESULT_SCHEMA 형태의 결과 딕셔너리
    """
    settings = get_settings()

    workspace_str = json.dumps(workspace_data or {}, ensure_ascii=False)
    message_str = json.dumps(message_data or {}, ensure_ascii=False)

    # 활성화된 코드 도구 안내 생성
    code_tool_hints = []
    if settings.GITLAB_ENABLED:
        code_tool_hints.append(
            "- GitLab 링크나 GitLab 관련 작업은 mcp__gitlab__* 도구를 사용합니다."
        )
    if settings.GITHUB_ENABLED:
        code_tool_hints.append(
            "- GitHub 저장소 작업(이슈, PR, 파일 관리 등)은 mcp__github__* 도구를 사용합니다."
        )

    code_tools_str = "\n".join(code_tool_hints) if code_tool_hints else "- 코드 저장소 연동이 비활성화되어 있습니다."

    system_prompt = f"""당신은 코드 전문가입니다. GitLab/GitHub 코드 작업, PR 리뷰, 파일 관리 등 코드 관련 작업을 수행합니다.

## 역할
- 파일 읽기/쓰기는 mcp__files__* 도구를 사용합니다.
- 현재 시각 확인이 필요하면 mcp__time__get_current_time을 사용합니다.
{code_tools_str}

## 출력 형식
반드시 다음 JSON 형식으로만 응답하세요. 마크다운 코드 블록 없이 순수 JSON만 출력합니다:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{
        "repository": null,
        "branch": null,
        "changes": [],
        "review_comments": []
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

    if settings.GITLAB_ENABLED:
        mcp_servers["gitlab"] = local_mcp(
            "@zereight/mcp-gitlab",
            use_cache=True,
            env={
                "GITLAB_PERSONAL_ACCESS_TOKEN": settings.GITLAB_PERSONAL_ACCESS_TOKEN,
                "GITLAB_API_URL": settings.GITLAB_API_URL,
                "GITLAB_READ_ONLY_MODE": "false",
                "USE_GITLAB_WIKI": "false",
                "USE_MILESTONE": "false",
                "USE_PIPELINE": "false",
            },
        )

    if settings.GITHUB_ENABLED:
        mcp_servers["github"] = {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {
                "Authorization": f"Bearer {settings.GITHUB_PERSONAL_ACCESS_TOKEN}"
            },
        }

    allowed_tools = [
        "mcp__files__*",
        "mcp__time__*",
    ]

    if settings.GITLAB_ENABLED:
        allowed_tools.append("mcp__gitlab__*")

    if settings.GITHUB_ENABLED:
        allowed_tools.append("mcp__github__*")

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
        async with RetryableSDKClient(options, max_retries=3, agent_name="CODE_AGENT") as client:
            await client.query(query)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    return parse_result(message.result)
    except Exception as e:
        logging.error(f"[CODE_AGENT] Error: {e}")
        return make_result(
            status="failed",
            summary=f"코드 작업 실패: {str(e)}",
            error=str(e),
        )

    return make_result(
        status="failed",
        summary="응답 없음",
        error="no_response",
    )
