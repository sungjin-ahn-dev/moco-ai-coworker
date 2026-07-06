"""
문서 Sub-agent (Document Sub-agent)

문서 작성/편집, Google Drive, 번역을 담당합니다.
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
from app.cc_tools.files.files_tools import create_files_mcp_server
from app.cc_tools.deepl.deepl_tools import create_deepl_tools_server
from app.cc_tools.google_drive.google_drive_tools import create_google_drive_mcp_server
from app.cc_tools.google_docs.google_docs_tools import create_google_docs_mcp_server
from app.config.settings import get_settings
from app.cc_agents.sub_agents.base import make_result, parse_result
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.prompt_helper import prepare_options


async def call_document_agent(
    query: str,
    context: str = "",
    workspace_data: dict = None,
    message_data: dict = None,
) -> dict:
    """문서 작성/편집·Google Drive·번역을 담당하는 문서 Sub-agent.

    Args:
        query: 수행할 문서 작업 설명
        context: 추가 컨텍스트 정보
        workspace_data: Orchestrator가 공유하는 작업 공간 데이터
        message_data: Slack 메시지 정보 (user_id, channel_id 등)

    Returns:
        dict: RESULT_SCHEMA 형태의 결과 딕셔너리
    """
    settings = get_settings()

    workspace_str = json.dumps(workspace_data or {}, ensure_ascii=False)
    message_str = json.dumps(message_data or {}, ensure_ascii=False)

    # 활성화된 문서 도구 안내 생성
    doc_tool_hints = []
    if settings.GOOGLE_DRIVE_ENABLED:
        doc_tool_hints.append(
            "- Google Drive 파일 검색/관리는 mcp__google_drive__* 도구를 사용합니다. "
            "문서 내용 검색은 mcp__google_drive__semantic_search, 문서 Q&A는 mcp__google_drive__document_qa를 사용합니다. "
            "개인 드라이브 접근 시 slack_user_id 파라미터에 요청자의 Slack user_id를 전달하세요.\n"
            "- Google Docs 읽기/작성은 mcp__google_docs__* 도구를 사용합니다."
        )
    if settings.DEEPL_ENABLED:
        doc_tool_hints.append(
            "- 문서 번역 요청 시 mcp__deepl__* 도구를 사용합니다. 바이너리 파일은 Read 툴 사용하지 말고 파일 경로를 바로 전달하세요."
        )

    doc_tools_str = "\n".join(doc_tool_hints) if doc_tool_hints else ""

    system_prompt = f"""당신은 문서 전문가입니다. 문서 작성/편집, Google Drive 파일 관리, 번역 등 문서 관련 작업을 수행합니다.

## 역할
- 파일 읽기/쓰기는 mcp__files__* 도구를 사용합니다.
- 현재 시각 확인이 필요하면 mcp__time__get_current_time을 사용합니다.
- Slack 메시지 전송 및 파일 업로드는 mcp__slack__* 도구를 사용합니다.
{doc_tools_str}

## 출력 형식
반드시 다음 JSON 형식으로만 응답하세요. 마크다운 코드 블록 없이 순수 JSON만 출력합니다:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{
        "document_title": null,
        "document_url": null,
        "translated_text": null
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
        "slack": create_slack_mcp_server(),
    }

    if settings.GOOGLE_DRIVE_ENABLED:
        mcp_servers["google_drive"] = create_google_drive_mcp_server()
        mcp_servers["google_docs"] = create_google_docs_mcp_server()

    if settings.DEEPL_ENABLED:
        mcp_servers["deepl"] = create_deepl_tools_server()

    allowed_tools = [
        "mcp__files__*",
        "mcp__time__*",
        "mcp__slack__*",
    ]

    if settings.GOOGLE_DRIVE_ENABLED:
        allowed_tools.append("mcp__google_drive__*")
        allowed_tools.append("mcp__google_docs__*")

    if settings.DEEPL_ENABLED:
        allowed_tools.append("mcp__deepl__*")

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
        async with RetryableSDKClient(options, max_retries=3, agent_name="DOCUMENT_AGENT") as client:
            await client.query(query)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    return parse_result(message.result)
    except Exception as e:
        logging.error(f"[DOCUMENT_AGENT] Error: {e}")
        return make_result(
            status="failed",
            summary=f"문서 작업 실패: {str(e)}",
            error=str(e),
        )

    return make_result(
        status="failed",
        summary="응답 없음",
        error="no_response",
    )
