"""
Email Tasks Tools for Claude Code SDK
이메일에서 추출한 할 일을 관리하는 MCP 도구
"""

import json
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.cc_utils.email_tasks_db import add_task


@tool(
    "add_email_task",
    "이메일에서 추출한 할 일을 추가합니다. 이메일을 분석하여 해야 할 작업이 있으면 이 도구를 사용하세요.",
    {
        "type": "object",
        "properties": {
            "email_id": {
                "type": "string",
                "description": "이메일 ID"
            },
            "sender": {
                "type": "string",
                "description": "발신자 (이름 <이메일> 형식)"
            },
            "subject": {
                "type": "string",
                "description": "이메일 제목"
            },
            "task_description": {
                "type": "string",
                "description": "할 일 설명 (구체적으로)"
            },
            "priority": {
                "type": "string",
                "description": "우선순위 (low/medium/high)",
                "enum": ["low", "medium", "high"]
            },
            "user_id": {
                "type": "string",
                "description": "알림을 받을 사용자 ID"
            },
            "user_name": {
                "type": "string",
                "description": "알림을 받을 사용자 이름 (권한 확인용)"
            },
            "text": {
                "type": "string",
                "description": "알림 메시지 내용"
            },
            "channel_id": {
                "type": "string",
                "description": "알림을 보낼 채널 ID"
            }
        },
        "required": ["email_id", "sender", "subject", "task_description", "user_id", "user_name", "text", "channel_id"]
    }
)
async def email_tasks_add_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """이메일에서 추출한 할 일 추가"""
    email_id = args["email_id"]
    sender = args["sender"]
    subject = args["subject"]
    task_description = args["task_description"]
    priority = args.get("priority", "medium")
    user_id = args["user_id"]
    user_name = args["user_name"]
    text = args["text"]
    channel_id = args["channel_id"]

    # 허용된 사용자 검증
    from app.cc_slack_handlers import is_authorized_user

    if not is_authorized_user(user_name):
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"사용자 '{user_name}'는 허용된 사용자가 아닙니다. 할 일을 추가할 수 없습니다."
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }

    try:
        task_id = add_task(
            email_id=email_id,
            sender=sender,
            subject=subject,
            task_description=task_description,
            priority=priority,
            user_id=user_id,
            text=text,
            channel_id=channel_id
        )

        result = {
            "success": True,
            "task_id": task_id,
            "message": f"할 일이 추가되었습니다 (ID: {task_id})"
        }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=2)
            }]
        }
    except Exception as e:
        error_result = {
            "success": False,
            "error": str(e)
        }
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(error_result, ensure_ascii=False, indent=2)
            }]
        }


# MCP 서버 생성
tools_list = [
    email_tasks_add_task,
]


def create_email_tasks_mcp_server():
    """Email Tasks MCP 서버 생성"""
    return create_sdk_mcp_server(
        name="email_tasks",
        version="1.0.0",
        tools=tools_list
    )
