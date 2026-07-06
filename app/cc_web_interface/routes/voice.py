"""
Voice Routes
음성 입력 WebSocket 및 관련 라우트
"""

import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from slack_sdk.web.async_client import AsyncWebClient

from app.queueing_extended import enqueue_message
from app.cc_web_interface.utils import get_slack_user_id
from app.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ws", tags=["voice"])


@router.websocket("/voice")
async def websocket_voice(websocket: WebSocket):
    """음성 입력 WebSocket 엔드포인트"""
    await websocket.accept()

    settings = get_settings()
    slack_client = AsyncWebClient(token=settings.SLACK_BOT_TOKEN)

    try:
        while True:
            data = await websocket.receive_json()

            # 프론트엔드에서 voice_text 타입으로 전송
            if data.get("type") == "voice_text":
                message = data.get("text", "").strip()
                user_info = data.get("user", {})

                if message and user_info:
                    # 사용자 이메일로 Slack user_id 조회
                    user_email = user_info.get("email")
                    user_name = user_info.get("name", "Unknown")

                    if not user_email:
                        logger.warning(f"[VOICE] No email in user_info: {user_info}")
                        await websocket.send_json({
                            "type": "error",
                            "message": "사용자 이메일 정보가 없습니다. 다시 로그인해주세요."
                        })
                        continue

                    # 이메일로 Slack user_id 조회
                    slack_user_id = await get_slack_user_id(user_email, slack_client)

                    if not slack_user_id:
                        logger.error(f"[VOICE] Failed to get Slack user ID for email: {user_email}")
                        await websocket.send_json({
                            "type": "error",
                            "message": "Slack 사용자를 찾을 수 없습니다."
                        })
                        continue

                    # DM 채널 ID 가져오기
                    try:
                        dm_response = await slack_client.conversations_open(users=[slack_user_id])
                        dm_channel_id = dm_response["channel"]["id"]
                    except Exception as e:
                        logger.error(f"[VOICE] Failed to get DM channel: {e}")
                        await websocket.send_json({
                            "type": "error",
                            "message": "Slack DM 채널을 열 수 없습니다."
                        })
                        continue

                    logger.info(f"[VOICE] Received from {user_name}({slack_user_id}): {message[:50]}...")

                    # 메시지 큐에 추가 (실제 DM 채널 사용)
                    await enqueue_message({
                        "text": message,
                        "channel": dm_channel_id,
                        "ts": "",
                        "user": slack_user_id,
                        "thread_ts": None,
                    })

                    await websocket.send_json({
                        "type": "processed",
                        "message": f"'{message[:30]}...' 처리 중"
                    })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()