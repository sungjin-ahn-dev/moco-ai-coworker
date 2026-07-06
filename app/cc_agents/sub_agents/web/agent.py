"""
웹 Sub-agent (Web Sub-agent)

웹 브라우저 자동화, 사이트 탐색, 스크래핑을 담당합니다.
"""

import json
import logging
import os

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)
from app.cc_utils.sdk_retry import RetryableSDKClient

from app.config.settings import get_settings
from app.cc_agents.sub_agents.base import make_result, parse_result
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.prompt_helper import prepare_options


async def call_web_agent(
    query: str,
    context: str = "",
    workspace_data: dict = None,
    message_data: dict = None,
) -> dict:
    """Playwright 브라우저 자동화·사이트 탐색·스크래핑. RESULT_SCHEMA dict 반환."""
    settings = get_settings()

    workspace_str = json.dumps(workspace_data or {}, ensure_ascii=False)
    message_str = json.dumps(message_data or {}, ensure_ascii=False)

    # channel_id 추출 (스크린샷 저장 경로에 사용)
    channel_id = (message_data or {}).get("channel_id", "general")

    # 활성화된 웹 도구 안내 생성
    web_tool_hints = []
    if settings.CHROME_ENABLED:
        web_tool_hints.append(
            "- 웹 브라우저 자동화는 mcp__playwright__* 도구를 사용합니다. "
            f"스크린샷 저장 시 filename 파라미터를 '{channel_id}/파일명.png' 형태로 지정합니다."
        )
    web_tool_hints.append(
        "- 웹 전체에서 정보 검색/종합 시 WebSearch 도구를 사용합니다. "
        "검색 결과에 출처 링크가 포함된 경우 반드시 결과에 포함합니다."
    )

    web_tool_hints.append(
        "- 특정 URL의 내용을 직접 가져올 때는 WebFetch 도구를 사용합니다."
    )

    web_tools_str = "\n".join(web_tool_hints)

    system_prompt = f"""당신은 웹 자동화 전문가입니다. 웹 브라우저 자동화, 사이트 탐색, 스크래핑 등 웹 관련 작업을 수행합니다.

## 역할
- 현재 시각 확인이 필요하면 mcp__time__get_current_time을 사용합니다.
{web_tools_str}

## 중요 원칙
- 특정 사이트에서 여러 게시글이나 콘텐츠를 확인할 때 읽기 깊이가 불확실한 경우 절대 추론하지 마세요.
- 사용자에게 어떤 수준으로 읽을지 명확히 물어보거나, 작업 범위를 확정 후 진행합니다.

## 출력 형식
반드시 다음 JSON 형식으로만 응답하세요. 마크다운 코드 블록 없이 순수 JSON만 출력합니다:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{
        "url": null,
        "content": null,
        "scraped_items": [],
        "screenshot_path": null
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

    if settings.CHROME_ENABLED:
        mcp_servers["playwright"] = local_mcp(
            "@playwright/mcp",
            use_cache=True,
            extra_args=[
                "--browser",
                "chrome",
                "--user-data-dir",
                os.path.join(settings.FILESYSTEM_BASE_DIR, "chrome_profile"),
                "--caps",
                "vision",
                "--image-responses",
                "allow",
                "--output-dir",
                os.path.join(settings.FILESYSTEM_BASE_DIR, "files"),
            ],
        )

    allowed_tools = [
        "mcp__time__*",
        "WebFetch",
        "WebSearch",
    ]

    if settings.CHROME_ENABLED:
        allowed_tools.append("mcp__playwright__*")

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
        async with RetryableSDKClient(options, max_retries=3, agent_name="WEB_AGENT") as client:
            await client.query(query)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    return parse_result(message.result)
    except Exception as e:
        logging.error(f"[WEB_AGENT] Error: {e}")
        return make_result(
            status="failed",
            summary=f"웹 작업 실패: {str(e)}",
            error=str(e),
        )

    return make_result(
        status="failed",
        summary="응답 없음",
        error="no_response",
    )
