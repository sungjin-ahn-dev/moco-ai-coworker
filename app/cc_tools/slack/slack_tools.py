"""
Slack Tools for Claude Code SDK
Claude가 직접 Slack API를 사용할 수 있는 도구
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from app.config.settings import get_settings


def get_slack_client() -> AsyncWebClient:
    """Slack AsyncWebClient 인스턴스 반환"""
    settings = get_settings()
    token = settings.SLACK_BOT_TOKEN
    if not token:
        raise ValueError("SLACK_BOT_TOKEN is not set in settings")
    return AsyncWebClient(token=token)


async def _ensure_bot_in_channel(client: AsyncWebClient, channel_id: str):
    """봇이 채널에 없으면 자동으로 join. 메시지가 반드시 MOCO 봇 이름으로 전송되도록 보장."""
    if not channel_id or not channel_id.startswith("C"):
        return  # DM(D), Group DM(G)은 스킵
    try:
        info = await client.conversations_info(channel=channel_id)
        if info["ok"] and not info["channel"].get("is_member", False):
            await client.conversations_join(channel=channel_id)
            import logging
            logging.info(f"[SLACK_AUTO_JOIN] Bot joined channel {channel_id} ({info['channel'].get('name', '')})")
    except Exception:
        pass  # private channel 등 join 불가능한 경우 무시


@tool(
    "add_reaction",
    "메시지에 이모지 리액션을 추가합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "메시지가 있는 채널 ID"
            },
            "timestamp": {
                "type": "string",
                "description": "메시지의 타임스탬프 (예: 1234567890.123456)"
            },
            "reaction": {
                "type": "string",
                "description": "리액션 이모지 이름 (콜론 없이, 예: 'thumbsup', 'heart', 'smile')"
            }
        },
        "required": ["channel_id", "timestamp", "reaction"]
    }
)
async def slack_add_reaction(args: Dict[str, Any]) -> Dict[str, Any]:
    """메시지에 리액션 추가"""
    channel_id = args["channel_id"]
    timestamp = args["timestamp"]
    reaction = args["reaction"]

    try:
        client = get_slack_client()
        response = await client.reactions_add(
            channel=channel_id,
            timestamp=timestamp,
            name=reaction
        )

        if response and response.get("ok"):
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message": f"리액션 '{reaction}' 추가 완료"
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "리액션 추가 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "answer_with_emoji",
    "원 요청자의 메시지에 이모지 리액션을 달아 간단히 응답합니다. 텍스트 답변 대신 '확인했어요' 표시용으로 사용하세요.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "state_data.current_message.channel_id"
            },
            "message_ts": {
                "type": "string",
                "description": "state_data.current_message.message_ts"
            },
            "reaction": {
                "type": "string",
                "description": "추가할 이모지 이름 (콜론 없이). 기본값: 'white_check_mark' (✅)"
            }
        },
        "required": ["channel_id", "message_ts"]
    }
)
async def slack_answer_with_emoji(args: Dict[str, Any]) -> Dict[str, Any]:
    """원 요청자의 메시지에 이모지 리액션 추가"""
    channel_id = args["channel_id"]
    message_ts = args["message_ts"]
    reaction = args.get("reaction", "white_check_mark")

    try:
        client = get_slack_client()
        response = await client.reactions_add(
            channel=channel_id,
            timestamp=message_ts,
            name=reaction
        )

        if response and response.get("ok"):
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message": f"이모지 리액션 '{reaction}' 추가 완료",
                        "channel": channel_id,
                        "timestamp": message_ts
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "리액션 추가 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
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
                    "message": f"오류: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "answer",
    "원 요청자에게 텍스트 답변을 보냅니다. 채널 타입에 따라 자동으로 적절한 위치(스레드/채널)를 결정합니다. 여러 번 호출 가능합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "state_data.current_message.channel_id"
            },
            "text": {
                "type": "string",
                "description": "보낼 텍스트 메시지 내용"
            },
            "channel_type": {
                "type": "string",
                "description": "state_data.slack_data.channel.channel_type (public_channel, private_channel, dm, group_dm)"
            },
            "message_ts": {
                "type": "string",
                "description": "state_data.current_message.message_ts"
            },
            "thread_ts": {
                "type": "string",
                "description": "state_data.current_message.thread_ts (있는 경우만)"
            }
        },
        "required": ["channel_id", "text", "channel_type", "message_ts"]
    }
)
async def slack_answer(args: Dict[str, Any]) -> Dict[str, Any]:
    """원 요청자에게 텍스트 답변 전송"""
    channel_id = args["channel_id"]
    text = args["text"]
    channel_type = args["channel_type"]
    message_ts = args["message_ts"]
    thread_ts = args.get("thread_ts")

    try:
        client = get_slack_client()

        # 봇이 채널에 없으면 자동 join (public channel만)
        await _ensure_bot_in_channel(client, channel_id)

        # channel_type에 따라 thread_ts 계산
        if channel_type in ["public_channel", "private_channel", "group_dm"]:
            # 그룹 채널: 무조건 스레드로 답변
            final_thread_ts = thread_ts or message_ts
        elif channel_type in ["dm"]:
            # DM: thread_ts가 있으면 스레드로, 없으면 일반 메시지 (flat)
            final_thread_ts = thread_ts if thread_ts else None
        else:
            final_thread_ts = None

        # 메시지 전송
        post_params = {
            "channel": channel_id,
            "text": text
        }

        if final_thread_ts:
            post_params["thread_ts"] = final_thread_ts

        response = await client.chat_postMessage(**post_params)

        if response and response.get("ok"):
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message": "답변 전송 완료",
                        "channel": channel_id,
                        "ts": response.get("ts"),
                        "thread_ts": final_thread_ts
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            error_msg = response.get("error", "Unknown error") if response else "Unknown error"
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"메시지 전송 실패: {error_msg}"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
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
                    "message": f"오류: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "forward_message",
    "다른 사람/채널에게 메시지를 전달합니다. 여러 명에게 같은 내용을 보낼 때는 respondents에 모두 포함해서 단 1회만 호출하세요. 중복 호출 절대 금지!",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "메시지를 보낼 Slack 채널 ID (예: C12345) 또는 사용자 DM ID (예: D12345)"
            },
            "text": {
                "type": "string",
                "description": "전송할 메시지 내용"
            },
            "request_answer": {
                "type": "boolean",
                "description": "답변을 받아야 하는 경우 True로 설정. False면 단순 메시지만 전송합니다. (기본값: False)"
            },
            "respondents": {
                "type": "array",
                "description": "답변을 받을 사람들의 목록. request_answer=True일 때 필수입니다. 여러 명에게 같은 질문을 할 때는 모든 사용자를 이 배열에 포함하여 단 1회만 호출하세요. 중복 호출 금지! 각 항목은 user_id와 name을 포함해야 합니다.",
                "items": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "응답자의 Slack User ID (예: U1234567890)"
                        },
                        "name": {
                            "type": "string",
                            "description": "응답자의 이름"
                        }
                    },
                    "required": ["user_id", "name"]
                }
            },
            "requester_id": {
                "type": "string",
                "description": "답변을 요청한 사람의 Slack User ID. request_answer=True일 때 필수입니다. state_data.current_message.user_id에서 가져오세요."
            },
            "requester_name": {
                "type": "string",
                "description": "답변을 요청한 사람의 이름. request_answer=True일 때 필수입니다. state_data.current_message.user_name에서 가져오세요."
            }
        },
        "required": ["channel_id", "text"]
    }
)
async def slack_forward_message(args: Dict[str, Any]) -> Dict[str, Any]:
    """다른 사람에게 메시지 전달 + 선택적 응답 대기 등록"""
    channel_id = args.get("channel_id")
    text = args["text"]
    request_answer = args.get("request_answer", False)
    respondents = args.get("respondents", [])
    requester_id = args.get("requester_id")
    requester_name = args.get("requester_name")

    try:
        client = get_slack_client()

        # 봇이 채널에 없으면 자동 join (채널에 직접 보낼 때)
        if not request_answer and channel_id and channel_id.startswith("C"):
            await _ensure_bot_in_channel(client, channel_id)

        # request_answer=True인 경우: respondents 각자에게 DM 전송
        if request_answer:
            if not respondents or not requester_id or not requester_name:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": True,
                            "message": "request_answer=True일 때는 respondents, requester_id, requester_name이 필수입니다."
                        }, ensure_ascii=False, indent=2)
                    }],
                    "error": True
                }

            # 하나의 request_id 생성
            import uuid
            from app.cc_utils.waiting_answer_db import add_request

            request_id = str(uuid.uuid4())[:8]

            # 각 respondent에게 DM 전송
            sent_channels = []
            for respondent in respondents:
                user_id = respondent.get("user_id")
                # DM 채널 열기
                dm_response = await client.conversations_open(users=user_id)
                dm_channel_id = dm_response["channel"]["id"]

                # 메시지 전송
                await client.chat_postMessage(
                    channel=dm_channel_id,
                    text=text
                )
                sent_channels.append(dm_channel_id)

            # waiting_answer에 등록 (모든 respondents를 하나의 request_id로)
            count = add_request(
                request_id=request_id,
                channel_id=sent_channels[0] if sent_channels else "unknown",
                requester_id=requester_id,
                requester_name=requester_name,
                request_content=text,
                respondents=respondents
            )

            result = {
                "success": True,
                "message": f"{len(respondents)}명에게 메시지 전송 및 응답 대기 등록 완료",
                "sent_to": [r.get("name") for r in respondents],
                "waiting_answer": {
                    "registered": True,
                    "request_id": request_id,
                    "respondent_count": count
                }
            }

        # request_answer=False인 경우: channel_id에 메시지만 전송
        else:
            if not channel_id:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": True,
                            "message": "request_answer=False일 때는 channel_id가 필수입니다."
                        }, ensure_ascii=False, indent=2)
                    }],
                    "error": True
                }

            response = await client.chat_postMessage(
                channel=channel_id,
                text=text
            )

            result = {
                "success": True,
                "message": "메시지 전송 완료",
                "channel": response["channel"],
                "ts": response["ts"]
            }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=2)
            }]
        }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
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
                    "message": f"오류: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "reply_to_thread",
    "Slack 스레드에 답글을 답니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "스레드가 있는 채널 ID"
            },
            "thread_ts": {
                "type": "string",
                "description": "스레드의 타임스탬프 (예: 1234567890.123456)"
            },
            "text": {
                "type": "string",
                "description": "답글 내용"
            }
        },
        "required": ["channel_id", "thread_ts", "text"]
    }
)
async def slack_reply_to_thread(args: Dict[str, Any]) -> Dict[str, Any]:
    """Slack 스레드에 답글"""
    channel_id = args["channel_id"]
    thread_ts = args["thread_ts"]
    text = args["text"]

    try:
        client = get_slack_client()

        # 봇이 채널에 없으면 자동 join
        await _ensure_bot_in_channel(client, channel_id)
        response = await client.chat_postMessage(
            channel=channel_id,
            text=text,
            thread_ts=thread_ts
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": "스레드 답글 전송 완료",
                    "channel": response["channel"],
                    "ts": response["ts"],
                    "thread_ts": response["thread_ts"]
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
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "delete_message",
    "Slack 채널에서 봇이 보낸 메시지를 삭제합니다. 봇 자신이 보낸 메시지만 삭제할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "메시지가 있는 채널 ID"
            },
            "message_ts": {
                "type": "string",
                "description": "삭제할 메시지의 타임스탬프 (예: 1234567890.123456)"
            }
        },
        "required": ["channel_id", "message_ts"]
    }
)
async def slack_delete_message(args: Dict[str, Any]) -> Dict[str, Any]:
    """봇이 보낸 메시지 삭제"""
    channel_id = args["channel_id"]
    message_ts = args["message_ts"]

    try:
        client = get_slack_client()
        response = await client.chat_delete(channel=channel_id, ts=message_ts)

        if response and response.get("ok"):
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message": "메시지 삭제 완료",
                        "channel": channel_id,
                        "ts": message_ts
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"메시지 삭제 실패: {response.get('error', 'Unknown error')}"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "upload_file",
    "로컬 파일을 원 요청자에게 업로드합니다. 채널 타입에 따라 자동으로 적절한 위치(스레드/채널)를 결정합니다. 여러 번 호출 가능합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "state_data.current_message.channel_id"
            },
            "file_path": {
                "type": "string",
                "description": "업로드할 로컬 파일의 절대 경로 (예: FILESYSTEM_BASE_DIR/tmp/report.pdf)"
            },
            "channel_type": {
                "type": "string",
                "description": "state_data.slack_data.channel.channel_type (public_channel, private_channel, dm, group_dm)"
            },
            "message_ts": {
                "type": "string",
                "description": "state_data.current_message.message_ts"
            },
            "thread_ts": {
                "type": "string",
                "description": "state_data.current_message.thread_ts (있는 경우만)"
            },
            "initial_comment": {
                "type": "string",
                "description": "파일과 함께 보낼 메시지 (선택사항)"
            }
        },
        "required": ["channel_id", "file_path", "channel_type", "message_ts"]
    }
)
async def slack_upload_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """Slack 파일 업로드"""
    channel_id = args["channel_id"]
    file_path = args["file_path"]
    channel_type = args["channel_type"]
    message_ts = args["message_ts"]
    thread_ts = args.get("thread_ts")
    initial_comment = args.get("initial_comment")

    try:
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"파일이 존재하지 않습니다: {file_path}"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        client = get_slack_client()

        # channel_type에 따라 thread_ts 계산
        if channel_type in ["public_channel", "private_channel", "group_dm"]:
            # 그룹 채널: 무조건 스레드로 업로드
            final_thread_ts = thread_ts or message_ts
        elif channel_type in ["dm"]:
            # DM: thread_ts가 있으면 스레드로, 없으면 일반 메시지 (flat)
            final_thread_ts = thread_ts if thread_ts else None
        else:
            final_thread_ts = None

        upload_params = {
            "channel": channel_id,
            "file": str(file_path_obj.absolute())
        }

        if final_thread_ts:
            upload_params["thread_ts"] = final_thread_ts

        if initial_comment:
            upload_params["initial_comment"] = initial_comment

        response = await client.files_upload_v2(**upload_params)

        if response and response.get("ok"):
            file_info = response.get("file", {})
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message": "파일 업로드 완료",
                        "file_id": file_info.get("id"),
                        "file_name": file_info.get("name"),
                        "permalink": file_info.get("permalink")
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            error_msg = response.get("error", "Unknown error") if response else "Unknown error"
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"파일 업로드 실패: {error_msg}"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
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
                    "message": f"오류: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "download_file_to_channel",
    "Slack 파일을 채널별 폴더에 다운로드합니다. 다운로드된 파일은 FILESYSTEM_BASE_DIR/files/{channel_id}/ 에 저장됩니다.",
    {
        "type": "object",
        "properties": {
            "url_private": {
                "type": "string",
                "description": "Slack 파일의 private URL"
            },
            "channel_id": {
                "type": "string",
                "description": "Slack 채널 ID"
            },
            "filename": {
                "type": "string",
                "description": "저장할 파일명 (선택, 기본값은 URL에서 추출)"
            }
        },
        "required": ["url_private", "channel_id"]
    }
)
async def slack_download_file_to_channel(args: Dict[str, Any]) -> Dict[str, Any]:
    """Slack 파일을 채널별 폴더에 다운로드"""
    url_private = args["url_private"]
    channel_id = args["channel_id"]
    filename = args.get("filename")

    try:
        # URL에서 파일명 추출
        if not filename:
            url_parts = url_private.split("/")
            for part in reversed(url_parts):
                if "." in part and not part.startswith("."):
                    filename = part
                    break

            if not filename:
                filename = "downloaded_file"

        # Slack 인증 헤더로 파일 다운로드
        settings = get_settings()
        headers = {"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"}
        chunk_size = 1_048_576  # 1MB chunks

        async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
            async with client.stream("GET", url_private, timeout=None) as resp:
                resp.raise_for_status()

                # FILESYSTEM_BASE_DIR/files/{channel_id} 디렉토리에 저장
                base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()
                channel_dir = Path(base_dir) / "files" / channel_id
                channel_dir.mkdir(parents=True, exist_ok=True)
                file_path = channel_dir / filename

                with open(file_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size):
                        if chunk:
                            f.write(chunk)

        # 파일 크기 확인
        file_size = file_path.stat().st_size
        file_size_mb = round(file_size / (1024 * 1024), 2)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": "파일 다운로드 완료",
                    "filename": filename,
                    "file_path": str(file_path),
                    "file_size_bytes": file_size,
                    "file_size_mb": file_size_mb
                }, ensure_ascii=False, indent=2)
            }]
        }

    except httpx.HTTPStatusError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"HTTP 오류: {e.response.status_code}"
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
                    "message": f"오류: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "transfer_file",
    "로컬 파일 또는 Slack 파일을 다른 채널이나 사용자에게 전송합니다.",
    {
        "type": "object",
        "properties": {
            "channel_or_user_id": {
                "type": "string",
                "description": "파일을 전송할 채널 ID (C로 시작) 또는 DM ID (D로 시작)"
            },
            "file_url": {
                "type": "string",
                "description": "로컬 파일 경로 (file:// 또는 절대 경로), Slack url_private, 또는 외부 파일 URL"
            },
            "filename": {
                "type": "string",
                "description": "전송할 파일명"
            },
            "thread_ts": {
                "type": "string",
                "description": "스레드 타임스탬프 (스레드 내 업로드 시)"
            },
            "initial_comment": {
                "type": "string",
                "description": "파일과 함께 보낼 메시지"
            }
        },
        "required": ["channel_or_user_id", "file_url", "filename"]
    }
)
async def slack_transfer_file(args: Dict[str, Any]) -> Dict[str, Any]:
    """로컬 파일 또는 Slack 파일 전송"""
    channel_or_user_id = args["channel_or_user_id"]
    file_url = args["file_url"]
    filename = args["filename"]
    thread_ts = args.get("thread_ts")
    initial_comment = args.get("initial_comment")

    try:
        # 로컬 파일 경로인지 확인
        is_local_file = False
        local_file_path = None

        # file:// 프로토콜 제거
        if file_url.startswith("file://"):
            local_file_path = Path(file_url.replace("file://", ""))
            is_local_file = True
        # 절대 경로인 경우 (/, ~/, ./ 등으로 시작)
        elif not file_url.startswith("http://") and not file_url.startswith("https://"):
            local_file_path = Path(file_url)
            is_local_file = True

        # 로컬 파일인 경우
        if is_local_file:
            if not local_file_path.exists():
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": True,
                            "message": f"파일이 존재하지 않습니다: {local_file_path}"
                        }, ensure_ascii=False, indent=2)
                    }],
                    "error": True
                }

            # 직접 업로드
            client = get_slack_client()
            upload_params = {
                "channel": channel_or_user_id,
                "file": str(local_file_path.absolute())
            }

            if thread_ts:
                upload_params["thread_ts"] = thread_ts
            if initial_comment:
                upload_params["initial_comment"] = initial_comment

            response = await client.files_upload_v2(**upload_params)

        # HTTP/HTTPS URL인 경우 (기존 로직)
        else:
            settings = get_settings()
            headers = {"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"}

            async with httpx.AsyncClient(follow_redirects=True, headers=headers) as http_client:
                async with http_client.stream("GET", file_url, timeout=None) as resp:
                    resp.raise_for_status()

                    # 임시 파일로 저장
                    temp_file = Path(tempfile.gettempdir()) / filename
                    with open(temp_file, "wb") as f:
                        async for chunk in resp.aiter_bytes(1_048_576):
                            if chunk:
                                f.write(chunk)

            # 파일 업로드
            client = get_slack_client()
            upload_params = {
                "channel": channel_or_user_id,
                "file": str(temp_file.absolute())
            }

            if thread_ts:
                upload_params["thread_ts"] = thread_ts
            if initial_comment:
                upload_params["initial_comment"] = initial_comment

            response = await client.files_upload_v2(**upload_params)

            # 임시 파일 삭제
            temp_file.unlink()

        if response and response.get("ok"):
            file_info = response.get("file", {})
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message": "파일 전송 완료",
                        "file_id": file_info.get("id"),
                        "file_name": file_info.get("name"),
                        "permalink": file_info.get("permalink")
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            error_msg = response.get("error", "Unknown error") if response else "Unknown error"
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"파일 전송 실패: {error_msg}"
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
                    "message": f"오류: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    

@tool(
    "get_user_profile",
    "Slack 사용자 프로필 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "Slack 사용자 ID (예: U1234567890)"
            }
        },
        "required": ["user_id"]
    }
)
async def slack_get_user_profile(args: Dict[str, Any]) -> Dict[str, Any]:
    """Slack 사용자 프로필 조회"""
    user_id = args["user_id"]

    try:
        client = get_slack_client()
        response = await client.users_info(user=user_id)

        if response and response.get("ok"):
            user = response.get("user", {})
            profile = user.get("profile", {})

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "user_id": user.get("id"),
                        "real_name": user.get("real_name"),
                        "display_name": profile.get("display_name"),
                        "email": profile.get("email"),
                        "is_bot": user.get("is_bot", False)
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "사용자 정보 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "get_thread_replies",
    "스레드의 모든 답글을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "스레드가 있는 채널 ID"
            },
            "thread_ts": {
                "type": "string",
                "description": "스레드의 타임스탬프 (예: 1234567890.123456)"
            }
        },
        "required": ["channel_id", "thread_ts"]
    }
)
async def slack_get_thread_replies(args: Dict[str, Any]) -> Dict[str, Any]:
    """스레드 답글 조회"""
    channel_id = args["channel_id"]
    thread_ts = args["thread_ts"]

    try:
        client = get_slack_client()
        response = await client.conversations_replies(
            channel=channel_id,
            ts=thread_ts
        )

        if response and response.get("ok"):
            messages = response.get("messages", [])
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message_count": len(messages),
                        "messages": messages
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "스레드 답글 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "get_channel_history",
    "채널의 메시지 히스토리를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "메시지를 조회할 채널 ID"
            },
            "limit": {
                "type": "integer",
                "description": "조회할 메시지 수 (기본값: 10, 최대: 100)"
            },
            "oldest": {
                "type": "string",
                "description": "이 타임스탬프 이후의 메시지만 조회 (예: 1234567890.123456)"
            },
            "latest": {
                "type": "string",
                "description": "이 타임스탬프 이전의 메시지만 조회 (예: 1234567890.123456)"
            }
        },
        "required": ["channel_id"]
    }
)
async def slack_get_channel_history(args: Dict[str, Any]) -> Dict[str, Any]:
    """채널 히스토리 조회"""
    channel_id = args["channel_id"]
    limit = args.get("limit", 10)
    oldest = args.get("oldest")
    latest = args.get("latest")

    try:
        client = get_slack_client()

        params = {
            "channel": channel_id,
            "limit": limit
        }

        if oldest:
            params["oldest"] = oldest
        if latest:
            params["latest"] = latest

        response = await client.conversations_history(**params)

        if response and response.get("ok"):
            messages = response.get("messages", [])
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message_count": len(messages),
                        "messages": messages,
                        "has_more": response.get("has_more", False)
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "채널 히스토리 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "get_usergroup_members",
    "유저그룹의 멤버 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "usergroup_id": {
                "type": "string",
                "description": "유저그룹 ID (예: S1234567890). 태그 형식에서 추출한 ID"
            }
        },
        "required": ["usergroup_id"]
    }
)
async def slack_get_usergroup_members(args: Dict[str, Any]) -> Dict[str, Any]:
    """유저그룹 멤버 조회"""
    usergroup_id = args["usergroup_id"]

    try:
        client = get_slack_client()
        response = await client.usergroups_users_list(usergroup=usergroup_id)

        if response and response.get("ok"):
            users = response.get("users", [])
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "usergroup_id": usergroup_id,
                        "user_count": len(users),
                        "users": users
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "유저그룹 멤버 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "get_permalink",
    "특정 메시지의 permalink를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "메시지가 있는 채널 ID"
            },
            "message_ts": {
                "type": "string",
                "description": "메시지의 타임스탬프 (예: 1234567890.123456)"
            }
        },
        "required": ["channel_id", "message_ts"]
    }
)
async def slack_get_permalink(args: Dict[str, Any]) -> Dict[str, Any]:
    """메시지 permalink 조회"""
    channel_id = args["channel_id"]
    message_ts = args["message_ts"]

    try:
        client = get_slack_client()
        response = await client.chat_getPermalink(
            channel=channel_id,
            message_ts=message_ts
        )

        if response and response.get("ok"):
            permalink = response.get("permalink")
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "channel_id": channel_id,
                        "message_ts": message_ts,
                        "permalink": permalink
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "Permalink 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "get_dm_channel_id",
    "특정 사용자와의 DM 채널 ID를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "DM을 보낼 Slack 사용자 ID (예: U1234567890)"
            }
        },
        "required": ["user_id"]
    }
)
async def slack_get_dm_channel_id(args: Dict[str, Any]) -> Dict[str, Any]:
    """사용자와의 DM 채널 ID 조회"""
    user_id = args["user_id"]

    try:
        client = get_slack_client()
        response = await client.conversations_open(users=[user_id])

        if response and response.get("ok"):
            channel = response.get("channel", {})
            channel_id = channel.get("id")

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "user_id": user_id,
                        "dm_channel_id": channel_id,
                        "message": f"사용자 {user_id}와의 DM 채널 ID: {channel_id}"
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "DM 채널 ID 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "find_user_by_name",
    "이름으로 Slack 사용자를 검색하여 user_id를 찾습니다.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "검색할 사용자 이름 (real_name 또는 display_name)"
            }
        },
        "required": ["name"]
    }
)
async def slack_find_user_by_name(args: Dict[str, Any]) -> Dict[str, Any]:
    """이름으로 사용자 검색"""
    search_name = args["name"].strip().lower()

    try:
        client = get_slack_client()
        response = await client.users_list()

        if response and response.get("ok"):
            members = response.get("members", [])
            matches = []

            for user in members:
                if user.get("deleted") or user.get("is_bot"):
                    continue

                real_name = user.get("real_name", "").lower()
                profile = user.get("profile", {})
                display_name = profile.get("display_name", "").lower()

                if search_name in real_name or search_name in display_name:
                    matches.append({
                        "user_id": user.get("id"),
                        "real_name": user.get("real_name"),
                        "display_name": profile.get("display_name"),
                        "email": profile.get("email")
                    })

            if matches:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "matches": matches,
                            "count": len(matches),
                            "message": f"{len(matches)}명의 사용자를 찾았습니다"
                        }, ensure_ascii=False, indent=2)
                    }]
                }
            else:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "matches": [],
                            "count": 0,
                            "message": f"'{args['name']}'과 일치하는 사용자를 찾을 수 없습니다"
                        }, ensure_ascii=False, indent=2)
                    }]
                }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "사용자 목록 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "get_channel_info",
    "채널 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "Slack 채널 ID (예: C1234567890)"
            }
        },
        "required": ["channel_id"]
    }
)
async def slack_get_channel_info(args: Dict[str, Any]) -> Dict[str, Any]:
    """채널 정보 조회"""
    channel_id = args["channel_id"]

    try:
        client = get_slack_client()
        response = await client.conversations_info(channel=channel_id)

        if response and response.get("ok"):
            channel = response.get("channel", {})

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "channel_id": channel.get("id"),
                        "channel_name": channel.get("name"),
                        "is_channel": channel.get("is_channel", False),
                        "is_group": channel.get("is_group", False),
                        "is_im": channel.get("is_im", False),
                        "is_private": channel.get("is_private", False)
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "채널 정보 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "create_canvas",
    "채널에 새로운 캔버스를 생성합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "캔버스를 생성할 채널 ID (예: C1234567890)"
            },
            "title": {
                "type": "string",
                "description": "캔버스 제목"
            },
            "content": {
                "type": "string",
                "description": "캔버스 내용 (Markdown 형식)"
            }
        },
        "required": ["channel_id", "title", "content"]
    }
)
async def slack_create_canvas(args: Dict[str, Any]) -> Dict[str, Any]:
    """채널에 캔버스 생성"""
    channel_id = args["channel_id"]
    title = args["title"]
    content = args["content"]

    try:
        client = get_slack_client()

        # 캔버스 생성
        response = await client.canvases_create(
            title=title,
            document_content={
                "type": "markdown",
                "markdown": content
            }
        )

        if response and response.get("ok"):
            canvas_id = response.get("canvas_id")

            # 채널에 캔버스 공유
            share_response = await client.chat_postMessage(
                channel=channel_id,
                text=f"📄 {title}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{title}*"
                        },
                        "accessory": {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "캔버스 열기"
                            },
                            "url": f"slack://canvas/{canvas_id}"
                        }
                    }
                ]
            )

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "canvas_id": canvas_id,
                        "message": f"캔버스 '{title}'가 생성되었습니다."
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "캔버스 생성 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "list_channel_canvases",
    "채널에 있는 캔버스 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "캔버스를 조회할 채널 ID (예: C1234567890)"
            }
        },
        "required": ["channel_id"]
    }
)
async def slack_list_channel_canvases(args: Dict[str, Any]) -> Dict[str, Any]:
    """채널의 캔버스 목록 조회"""
    channel_id = args["channel_id"]

    try:
        client = get_slack_client()

        # 채널의 메시지 히스토리에서 캔버스 찾기
        response = await client.conversations_history(
            channel=channel_id,
            limit=100
        )

        if response and response.get("ok"):
            messages = response.get("messages", [])
            canvases = []

            for msg in messages:
                # 캔버스가 첨부된 메시지 찾기
                files = msg.get("files", [])
                for file in files:
                    if file.get("filetype") == "canvas":
                        canvases.append({
                            "canvas_id": file.get("id"),
                            "title": file.get("title", "제목 없음"),
                            "created": file.get("created", 0),
                            "url": file.get("permalink", "")
                        })

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "channel_id": channel_id,
                        "canvases": canvases,
                        "count": len(canvases)
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "채널 히스토리 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "get_canvas",
    "캔버스의 내용을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "canvas_id": {
                "type": "string",
                "description": "조회할 캔버스 ID"
            }
        },
        "required": ["canvas_id"]
    }
)
async def slack_get_canvas(args: Dict[str, Any]) -> Dict[str, Any]:
    """캔버스 내용 조회"""
    canvas_id = args["canvas_id"]

    try:
        client = get_slack_client()
        response = await client.canvases_sections_lookup(canvas_id=canvas_id)

        if response and response.get("ok"):
            sections = response.get("sections", [])

            # 섹션들의 내용을 Markdown으로 결합
            content_parts = []
            for section in sections:
                if section.get("section_type") == "any_header_block":
                    content_parts.append(f"# {section.get('text', '')}")
                elif section.get("section_type") == "markdown":
                    content_parts.append(section.get("markdown", ""))

            content = "\n\n".join(content_parts)

            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "canvas_id": canvas_id,
                        "content": content
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "캔버스 조회 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "edit_canvas",
    "캔버스의 내용을 편집합니다.",
    {
        "type": "object",
        "properties": {
            "canvas_id": {
                "type": "string",
                "description": "편집할 캔버스 ID"
            },
            "content": {
                "type": "string",
                "description": "새로운 캔버스 내용 (Markdown 형식)"
            }
        },
        "required": ["canvas_id", "content"]
    }
)
async def slack_edit_canvas(args: Dict[str, Any]) -> Dict[str, Any]:
    """캔버스 내용 편집"""
    canvas_id = args["canvas_id"]
    content = args["content"]

    try:
        client = get_slack_client()

        # 캔버스 편집
        response = await client.canvases_edit(
            canvas_id=canvas_id,
            changes=[
                {
                    "operation": "replace",
                    "document_content": {
                        "type": "markdown",
                        "markdown": content
                    }
                }
            ]
        )

        if response and response.get("ok"):
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "canvas_id": canvas_id,
                        "message": "캔버스가 수정되었습니다."
                    }, ensure_ascii=False, indent=2)
                }]
            }
        else:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "캔버스 편집 실패"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

    except SlackApiError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"Slack API 오류: {e.response['error']}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# MCP Server 생성
slack_tools = [
    slack_add_reaction,
    slack_answer_with_emoji,
    slack_answer,
    slack_forward_message,
    slack_reply_to_thread,
    slack_upload_file,
    slack_download_file_to_channel,
    slack_transfer_file,
    slack_get_user_profile,
    slack_get_thread_replies,
    slack_get_channel_history,
    slack_get_usergroup_members,
    slack_get_permalink,
    slack_get_dm_channel_id,
    slack_find_user_by_name,
    slack_get_channel_info,
    slack_delete_message,
    slack_create_canvas,
    slack_list_channel_canvases,
    slack_get_canvas,
    slack_edit_canvas,
]


def create_slack_mcp_server():
    """Claude Code SDK용 Slack MCP 서버"""
    return create_sdk_mcp_server(
        name="slack",
        version="1.0.0",
        tools=slack_tools
    )
