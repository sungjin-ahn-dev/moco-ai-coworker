"""
Jira Tasks Tools for Claude Code SDK
Jira에서 추출한 할 일을 관리하는 MCP 도구
"""

import json
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.cc_utils.jira_tasks_db import add_task


@tool(
    "add_jira_task",
    "Jira 티켓에서 추출한 할 일을 추가합니다. 중요한 티켓이나 주의가 필요한 티켓이 있으면 이 도구를 사용하세요.",
    {
        "type": "object",
        "properties": {
            "issue_key": {
                "type": "string",
                "description": "Jira 이슈 키 (예: PROJ-123)",
            },
            "issue_url": {"type": "string", "description": "Jira 이슈 URL"},
            "summary": {"type": "string", "description": "이슈 제목"},
            "status": {
                "type": "string",
                "description": "이슈 상태 (예: In Progress, Blocked, To Do)",
            },
            "priority": {
                "type": "string",
                "description": "우선순위 (low/medium/high)",
                "enum": ["low", "medium", "high"],
            },
            "task_description": {
                "type": "string",
                "description": "할 일 설명 (구체적으로)",
            },
            "user_id": {"type": "string", "description": "알림을 받을 사용자 ID"},
            "user_name": {
                "type": "string",
                "description": "알림을 받을 사용자 이름 (권한 확인용)",
            },
            "text": {"type": "string", "description": "알림 메시지 내용"},
            "channel_id": {"type": "string", "description": "알림을 보낼 채널 ID"},
        },
        "required": [
            "issue_key",
            "issue_url",
            "summary",
            "status",
            "priority",
            "task_description",
            "user_id",
            "user_name",
            "text",
            "channel_id",
        ],
    },
)
async def jira_tasks_add_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """Jira에서 추출한 할 일 추가"""
    issue_key = args["issue_key"]
    issue_url = args["issue_url"]
    summary = args["summary"]
    status = args["status"]
    priority = args["priority"]
    task_description = args["task_description"]
    user_id = args["user_id"]
    user_name = args["user_name"]
    text = args["text"]
    channel_id = args["channel_id"]

    # 허용된 사용자 검증
    from app.cc_slack_handlers import is_authorized_user

    if not is_authorized_user(user_name):
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "success": False,
                            "error": True,
                            "message": f"사용자 '{user_name}'는 허용된 사용자가 아닙니다. 할 일을 추가할 수 없습니다.",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            ],
            "error": True,
        }

    try:
        task_id = add_task(
            issue_key=issue_key,
            issue_url=issue_url,
            summary=summary,
            status=status,
            priority=priority,
            task_description=task_description,
            user_id=user_id,
            text=text,
            channel_id=channel_id,
        )

        result = {
            "success": True,
            "task_id": task_id,
            "issue_key": issue_key,
            "message": f"할 일이 추가되었습니다 (ID: {task_id}, 이슈: {issue_key})",
        }

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2),
                }
            ]
        }
    except Exception as e:
        error_result = {"success": False, "error": str(e)}
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(error_result, ensure_ascii=False, indent=2),
                }
            ]
        }


# MCP 서버 생성
tools_list = [
    jira_tasks_add_task,
]


def create_jira_tasks_mcp_server():
    """Jira Tasks MCP 서버 생성"""
    return create_sdk_mcp_server(name="jira_tasks", version="1.0.0", tools=tools_list)
