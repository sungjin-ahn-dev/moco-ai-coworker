"""
Peer 소통 MCP 서버 (Peer Agents MCP Server)

Sub-agent가 다른 Sub-agent를 직접 호출할 수 있게 합니다.
순환 호출을 방지하기 위해 허용된 agent 목록(allowed_agents)만 호출 가능합니다.
"""

import json
import logging
from typing import Any, Dict, List

from claude_agent_sdk import create_sdk_mcp_server, tool


def create_peer_agents_mcp_server(allowed_agents: List[str]) -> object:
    """
    Peer agent MCP 서버 인스턴스를 생성하여 반환합니다.

    Args:
        allowed_agents: 허용된 peer agent 이름 목록
                        예: ["call_document_agent", "call_translation_agent"]
                        또는 short form: ["document", "web"]

    Returns:
        MCP 서버 인스턴스
    """

    # allowed_agents 정규화: "call_XXX_agent" 형식과 단순 이름 모두 지원
    def _normalize(name: str) -> str:
        """call_XXX_agent → XXX, XXX → XXX 형태로 정규화"""
        if name.startswith("call_") and name.endswith("_agent"):
            return name[len("call_"):-len("_agent")]
        return name

    normalized_allowed = [_normalize(a) for a in allowed_agents]

    async def _get_agent_func(agent_name: str):
        """agent_name으로 실제 call_XXX_agent 함수를 반환합니다."""
        from app.cc_agents.sub_agents.research.agent import call_research_agent
        from app.cc_agents.sub_agents.communication.agent import call_communication_agent
        from app.cc_agents.sub_agents.code.agent import call_code_agent
        from app.cc_agents.sub_agents.pm.agent import call_pm_agent
        from app.cc_agents.sub_agents.document.agent import call_document_agent
        from app.cc_agents.sub_agents.data.agent import call_data_agent
        from app.cc_agents.sub_agents.web.agent import call_web_agent

        registry = {
            "research": call_research_agent,
            "communication": call_communication_agent,
            "code": call_code_agent,
            "pm": call_pm_agent,
            "document": call_document_agent,
            "data": call_data_agent,
            "web": call_web_agent,
        }
        return registry.get(agent_name)

    @tool(
        "call_peer_agent",
        "다른 전문 Sub-agent에게 작업을 위임합니다 (순환 방지를 위해 허용된 agent만 가능)",
        {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "호출할 peer agent 이름 (예: 'document', 'web', 'call_document_agent')",
                },
                "query": {
                    "type": "string",
                    "description": "위임할 작업 내용",
                },
                "context": {
                    "type": "string",
                    "description": "현재 작업 컨텍스트",
                },
            },
            "required": ["agent_name", "query", "context"],
        },
    )
    async def call_peer_agent_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        """허용된 peer agent를 호출하고 결과를 반환합니다."""
        raw_agent_name = args.get("agent_name", "")
        query = args.get("query", "")
        context = args.get("context", "")

        normalized_name = _normalize(raw_agent_name)

        # 허용 여부 검사
        if normalized_name not in normalized_allowed:
            error_result = {
                "status": "failed",
                "summary": f"허용되지 않은 peer agent: {raw_agent_name}",
                "data": {},
                "artifacts": [],
                "next_suggestions": [],
                "error": (
                    f"peer_agent_not_allowed:{raw_agent_name}. "
                    f"Allowed agents: {normalized_allowed}"
                ),
            }
            return {
                "content": [
                    {"type": "text", "text": json.dumps(error_result, ensure_ascii=False)}
                ]
            }

        agent_func = await _get_agent_func(normalized_name)
        if not agent_func:
            error_result = {
                "status": "failed",
                "summary": f"알 수 없는 agent: {normalized_name}",
                "data": {},
                "artifacts": [],
                "next_suggestions": [],
                "error": f"unknown_agent:{normalized_name}",
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
            result = await agent_func(
                query=query,
                context=context,
                workspace_data={},
                message_data=message_data if message_data else None,
            )
        except Exception as e:
            logging.error(
                f"[PEER_AGENTS_SERVER] call_peer_agent error ({normalized_name}): {e}"
            )
            result = {
                "status": "failed",
                "summary": f"{normalized_name} 실행 중 오류 발생: {str(e)}",
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

    return create_sdk_mcp_server(
        name="peer_agents",
        version="1.0.0",
        tools=[call_peer_agent_tool],
    )
