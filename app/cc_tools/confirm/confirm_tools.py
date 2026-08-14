"""
Confirm Tools for Claude Code SDK
사용자 확인 요청을 처리하는 도구
"""

import json
import uuid
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from app.config.settings import get_settings
from app.cc_utils.confirm_db import add_confirm_request


def get_slack_client() -> AsyncWebClient:
    """Slack AsyncWebClient 인스턴스 반환"""
    settings = get_settings()
    token = settings.SLACK_BOT_TOKEN
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is not set in settings")
    return AsyncWebClient(token=token)


@tool(
    "request_confirmation",
    "사용자에게 확인 메시지를 보내고 응답을 대기합니다. 봇이 명시적으로 호출되지 않았지만 관련 메모리가 있을 때 사용합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "메시지를 보낼 Slack 채널 ID"
            },
            "user_id": {
                "type": "string",
                "description": "확인을 받을 사용자 ID"
            },
            "user_name": {
                "type": "string",
                "description": "확인을 받을 사용자 이름"
            },
            "confirm_message": {
                "type": "string",
                "description": "확인 메시지 (반드시 사용자 이름으로 시작. 예: '철수님, 예전에 도와드린 적 있는데 도와드릴까요?')"
            },
            "original_request_text": {
                "type": "string",
                "description": "승인 시 실행할 완전한 명령문 (반드시 봇 이름으로 시작하는 전체 명령 포함. 예: '원하나님, 프로젝트 현황 정리해줘')"
            },
            "message_ts": {
                "type": "string",
                "description": "원본 메시지 타임스탬프 (스레드 생성용, 선택사항). state_data.current_message.message_ts 사용"
            },
            "thread_ts": {
                "type": "string",
                "description": "스레드 타임스탬프 (선택사항). state_data.current_message.thread_ts 사용"
            }
        },
        "required": ["channel_id", "user_id", "user_name", "confirm_message", "original_request_text"]
    }
)
async def confirm_request_confirmation(args: Dict[str, Any]) -> Dict[str, Any]:
    """사용자에게 confirm 메시지를 보내고 DB에 저장"""
    channel_id = args["channel_id"]
    user_id = args["user_id"]
    user_name = args["user_name"]
    confirm_message = args["confirm_message"]
    original_request_text = args["original_request_text"]

    # message_ts와 thread_ts는 선택 파라미터 ("null" 문자열 방어)
    message_ts = args.get("message_ts")
    thread_ts = args.get("thread_ts")
    if message_ts in (None, "null", "None", ""):
        message_ts = None
    if thread_ts in (None, "null", "None", ""):
        thread_ts = None

    # 허용된 사용자 검증
    from app.cc_slack_handlers import is_authorized_user

    if not is_authorized_user(user_name):
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"사용자 '{user_name}'는 허용된 사용자가 아닙니다. 확인 메시지를 보낼 수 없습니다."
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }

    try:
        # 고유 confirm_id 생성
        confirm_id = str(uuid.uuid4())

        # thread_ts 결정: thread_ts > message_ts > None (새 메시지)
        final_thread_ts = thread_ts or message_ts

        # DB에 저장 (thread_ts 포함)
        success = add_confirm_request(
            confirm_id=confirm_id,
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            confirm_message=confirm_message,
            original_request_text=original_request_text,
            thread_ts=final_thread_ts
        )

        if not success:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "confirm 요청 저장 실패 (중복된 confirm_id)"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        # Slack 메시지 전송
        client = get_slack_client()
        message_params = {
            "channel": channel_id,
            "text": confirm_message
        }

        # thread_ts가 있을 때만 추가 (없으면 새 메시지로 전송)
        if final_thread_ts:
            message_params["thread_ts"] = final_thread_ts

        response = await client.chat_postMessage(**message_params)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "confirm_id": confirm_id,
                    "message": "확인 메시지를 전송했습니다.",
                    "slack_ts": response.data.get("ts"),
                    "thread_ts": final_thread_ts
                }, ensure_ascii=False, indent=2)
            }]
        }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack 메시지 전송 실패: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"confirm 요청 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# MCP Server 생성
confirm_tools = [
    confirm_request_confirmation,
]


def create_confirm_mcp_server():
    """Claude Code SDK용 Confirm MCP 서버"""
    return create_sdk_mcp_server(
        name="confirm",
        version="1.0.0",
        tools=confirm_tools
    )
