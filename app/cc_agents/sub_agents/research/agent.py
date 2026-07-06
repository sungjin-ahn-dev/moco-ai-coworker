"""
리서치 Sub-agent (Research Sub-agent)

웹 검색, arXiv 논문, 정보 수집 및 분석을 담당합니다.
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


async def call_research_agent(
    query: str,
    context: str = "",
    workspace_data: dict = None,
    message_data: dict = None,
) -> dict:
    """웹 검색·arXiv·context7로 리서치하고 RESULT_SCHEMA dict를 돌려준다."""
    settings = get_settings()

    workspace_str = json.dumps(workspace_data or {}, ensure_ascii=False)
    message_str = json.dumps(message_data or {}, ensure_ascii=False)

    system_prompt = f"""당신은 리서치 전문가입니다. 웹 검색, arXiv 논문 조회, 정보 수집 및 분석 작업을 수행합니다.

## 역할
- 웹에서 최신 정보를 검색하고 종합하여 신뢰할 수 있는 리서치 결과를 제공합니다.
- arXiv 논문 링크나 학술 정보 요청 시 mcp__arxiv__* 도구를 사용합니다.
- 코드 관련 문서를 찾을 때는 mcp__context7__* 도구를 사용합니다.
- 웹 전체에서 정보 검색/종합 시 WebSearch 도구를 사용합니다. 검색 결과에 출처 링크가 포함된 경우 반드시 결과에 포함합니다.
- 현재 시각 확인이 필요하면 mcp__time__get_current_time을 사용합니다.

## 출력 형식
반드시 다음 JSON 형식으로만 응답하세요. 마크다운 코드 블록 없이 순수 JSON만 출력합니다:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{
        "findings": [],
        "sources": [],
        "key_points": []
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
        "arxiv": local_mcp("@langgpt/arxiv-paper-mcp"),
        "context7": local_mcp("@upstash/context7-mcp"),
    }

    allowed_tools = [
        "mcp__time__*",
        "mcp__arxiv__*",
        "mcp__context7__*",
        "WebFetch",
        "WebSearch",
    ]

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
        async with RetryableSDKClient(options, max_retries=3, agent_name="RESEARCH_AGENT") as client:
            await client.query(query)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    return parse_result(message.result)
    except Exception as e:
        logging.error(f"[RESEARCH_AGENT] Error: {e}")
        return make_result(
            status="failed",
            summary=f"리서치 작업 실패: {str(e)}",
            error=str(e),
        )

    return make_result(
        status="failed",
        summary="응답 없음",
        error="no_response",
    )
