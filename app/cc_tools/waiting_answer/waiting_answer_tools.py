"""
Waiting Answer Tools for Claude Code SDK
응답을 업데이트하고 취합하는 도구
SQLite로 모든 질의를 관리
"""

import json
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.cc_utils.waiting_answer_db import (
    update_response,
    get_request_by_id,
    get_all_responses_for_request,
    get_request_progress,
)


@tool(
    "update_request",
    "특정 질의의 응답을 업데이트합니다. 응답자가 답변했을 때 호출합니다. 응답과 함께 진행률 정보를 반환하므로, all_completed가 true일 때 원 질의자에게 알림을 보내세요.",
    {
        "type": "object",
        "properties": {
            "request_id": {
                "type": "string",
                "description": "질의 ID"
            },
            "user_id": {
                "type": "string",
                "description": "응답자의 Slack User ID"
            },
            "response": {
                "type": "string",
                "description": "응답 내용"
            }
        },
        "required": ["request_id", "user_id", "response"]
    }
)
async def waiting_answer_update_request(args: Dict[str, Any]) -> Dict[str, Any]:
    """SQLite에서 특정 질의 응답 업데이트 + 진행률 정보 반환"""
    request_id = args["request_id"]
    user_id = args["user_id"]
    response = args["response"]

    try:
        # 업데이트 전 질의 정보 조회
        request_info = get_request_by_id(request_id, user_id)

        if not request_info:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"질의 ID '{request_id}'를 찾을 수 없습니다."
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        # 응답 업데이트
        success = update_response(request_id, user_id, response)

        if not success:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"응답 업데이트 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        # 진행률 확인
        progress = get_request_progress(request_id)
        all_completed = progress["total"] == progress["completed"]

        # 결과 데이터 구성
        result = {
            "success": True,
            "message": "응답이 업데이트되었습니다.",
            "request_id": request_id,
            "progress": progress,
            "all_completed": all_completed,
            "requester_id": request_info["requester_id"],
            "channel_id": request_info["channel_id"],
            "request_content": request_info["request_content"]
        }

        # 모든 응답이 완료되었으면 전체 응답 포함
        if all_completed:
            result["all_responses"] = get_all_responses_for_request(request_id)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"질의 업데이트 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# MCP Server 생성
waiting_answer_tools = [
    waiting_answer_update_request,
]


def create_waiting_answer_mcp_server():
    """Claude Code SDK용 Waiting Answer MCP 서버"""
    return create_sdk_mcp_server(
        name="waiting_answer",
        version="1.0.0",
        tools=waiting_answer_tools
    )
