"""
Sub-agent MCP 서버 (Sub-agent MCP Server)

Orchestrator가 이 MCP를 통해 Sub-agent를 호출합니다.
"""

import asyncio
import json
import logging
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.cc_agents.sub_agents.base import SUB_AGENT_REGISTRY

# Global semaphore: limit concurrent sub-agent Claude CLI processes
# (prevents thundering-herd rate-limit events when many orchestrators run at once)
# 15 orchestrators × avg 1 sub-agent each ≈ 15 concurrent, cap at 20 to give headroom
_SUB_AGENT_SEMAPHORE = asyncio.Semaphore(20)


# ---------------------------------------------------------------------------
# Agent 함수 조회 헬퍼
# ---------------------------------------------------------------------------

async def _get_agent_func(agent_name: str):
    """agent_name으로 실제 call_XXX_agent 함수를 반환합니다."""
    from app.cc_agents.sub_agents.research.agent import call_research_agent
    from app.cc_agents.sub_agents.communication.agent import call_communication_agent
    from app.cc_agents.sub_agents.code.agent import call_code_agent
    from app.cc_agents.sub_agents.pm.agent import call_pm_agent
    from app.cc_agents.sub_agents.document.agent import call_document_agent
    from app.cc_agents.sub_agents.data.agent import call_data_agent
    from app.cc_agents.sub_agents.web.agent import call_web_agent

    AGENT_FUNCTIONS = {
        "research": call_research_agent,
        "communication": call_communication_agent,
        "code": call_code_agent,
        "pm": call_pm_agent,
        "document": call_document_agent,
        "data": call_data_agent,
        "web": call_web_agent,
    }
    return AGENT_FUNCTIONS.get(agent_name)


# ---------------------------------------------------------------------------
# 도구 1: call_sub_agent
# ---------------------------------------------------------------------------

@tool(
    "call_sub_agent",
    "특정 Sub-agent를 호출해 전문 작업을 위임합니다. research/communication/code/pm/document/data/web 중 선택.",
    {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "enum": ["research", "communication", "code", "pm", "document", "data", "web"],
                "description": "호출할 sub-agent 이름",
            },
            "query": {
                "type": "string",
                "description": "sub-agent에게 전달할 작업 내용",
            },
            "context": {
                "type": "string",
                "description": "대화 맥락 및 사용자 정보",
            },
            "workspace_data": {
                "type": "object",
                "description": "다른 sub-agent가 생성한 공유 데이터 (optional)",
            },
        },
        "required": ["agent_name", "query", "context"],
    },
)
async def call_sub_agent_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """지정된 Sub-agent를 호출하고 결과를 반환합니다."""
    agent_name = args.get("agent_name", "")
    query = args.get("query", "")
    context = args.get("context", "")
    workspace_data = args.get("workspace_data") or {}

    available_agents = list(SUB_AGENT_REGISTRY.keys())

    agent_func = await _get_agent_func(agent_name)
    if not agent_func:
        error_result = {
            "status": "failed",
            "summary": f"알 수 없는 agent: {agent_name}",
            "data": {},
            "artifacts": [],
            "next_suggestions": [],
            "error": f"unknown_agent:{agent_name}. Available agents: {available_agents}",
        }
        return {
            "content": [
                {"type": "text", "text": json.dumps(error_result, ensure_ascii=False)}
            ]
        }

    # context에서 message_data 파싱 시도
    message_data: dict = {}
    if context:
        try:
            parsed = json.loads(context)
            if isinstance(parsed, dict):
                message_data = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        logging.info(f"[SUB_AGENTS_SERVER] Acquiring semaphore for {agent_name} (current: {8 - _SUB_AGENT_SEMAPHORE._value} active)")
        async with _SUB_AGENT_SEMAPHORE:
            logging.info(f"[SUB_AGENTS_SERVER] Semaphore acquired for {agent_name}")
            result = await agent_func(
                query=query,
                context=context,
                workspace_data=workspace_data,
                message_data=message_data if message_data else None,
            )
        logging.info(f"[SUB_AGENTS_SERVER] Semaphore released for {agent_name}")
    except Exception as e:
        logging.error(f"[SUB_AGENTS_SERVER] call_sub_agent error ({agent_name}): {e}")
        result = {
            "status": "failed",
            "summary": f"{agent_name} 실행 중 오류 발생: {str(e)}",
            "data": {},
            "artifacts": [],
            "next_suggestions": [],
            "error": str(e),
        }

    return {
        "content": [
            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
        ]
    }


# ---------------------------------------------------------------------------
# 도구 2: list_sub_agents
# ---------------------------------------------------------------------------

@tool(
    "list_sub_agents",
    "사용 가능한 Sub-agent 목록과 각 agent의 전문 분야를 반환합니다.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def list_sub_agents_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """사용 가능한 Sub-agent 목록을 반환합니다."""
    agents_info = []
    for name, info in SUB_AGENT_REGISTRY.items():
        agents_info.append(
            {
                "name": name,
                "description": info.get("description", ""),
                "keywords": info.get("keywords", []),
            }
        )

    result = {
        "available_agents": agents_info,
        "total": len(agents_info),
    }

    return {
        "content": [
            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
        ]
    }


# ---------------------------------------------------------------------------
# MCP 서버 팩토리
# ---------------------------------------------------------------------------

def create_sub_agents_mcp_server():
    """Sub-agent MCP 서버 인스턴스를 생성하여 반환합니다."""
    return create_sdk_mcp_server(
        name="agents",
        version="1.0.0",
        tools=[call_sub_agent_tool, list_sub_agents_tool],
    )
