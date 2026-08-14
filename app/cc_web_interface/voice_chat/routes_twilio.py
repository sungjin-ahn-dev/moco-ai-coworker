"""
MOCO Voice Chat — Twilio + Gemini Live 연동
전화 수신 → Twilio Media Stream → WebSocket → Gemini Live → 음성 응답
"""

import asyncio
import audioop
import base64
import json
import logging
import os
import struct
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/twilio", tags=["Twilio AICC"])

# Twilio 인증 정보
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")

# Gemini API 키 (routes_gemini.py와 공유)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def _get_gemini_key():
    import os
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    if not key:
        settings = get_settings()
        key = getattr(settings, "GEMINI_API_KEY", "") or getattr(settings, "GOOGLE_API_KEY", "") or ""
    if not key:
        key = GEMINI_API_KEY
    return key


# FAQ 로드 (routes_gemini.py와 동일)
_faq_cache: str | None = None


def _load_faq() -> str:
    global _faq_cache
    if _faq_cache is not None:
        return _faq_cache
    faq_items = []
    faq_path = Path(__file__).parent.parent.parent.parent / "AICC_인바운드_시나리오.docx"
    if faq_path.exists():
        try:
            from docx import Document as DocxDocument
            doc = DocxDocument(str(faq_path))
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if len(cells) >= 3 and cells[0].startswith("Q"):
                        faq_items.append(f"Q: {cells[1]}\nA: {cells[2]}")
            logger.info(f"[TWILIO] FAQ loaded: {len(faq_items)} QnA pairs")
        except Exception as e:
            logger.warning(f"[TWILIO] FAQ load failed: {e}")
    _faq_cache = "\n\n".join(faq_items) if faq_items else ""
    return _faq_cache


def _mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    """mulaw 8kHz → PCM 16bit 8kHz"""
    return audioop.ulaw2lin(mulaw_bytes, 2)


def _pcm16_8k_to_16k(pcm_8k: bytes) -> bytes:
    """PCM 16bit 8kHz → 16kHz (리샘플링)"""
    return audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)[0]


def _pcm16_24k_to_8k(pcm_24k: bytes) -> bytes:
    """PCM 16bit 24kHz → 8kHz (리샘플링)"""
    return audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, None)[0]


def _pcm16_to_mulaw(pcm_bytes: bytes) -> bytes:
    """PCM 16bit 8kHz → mulaw"""
    return audioop.lin2ulaw(pcm_bytes, 2)


@router.post("/voice")
async def twilio_voice_webhook(request: Request):
    """
    Twilio 전화 수신 webhook — TwiML로 Media Stream 연결
    전화가 오면 이 엔드포인트가 호출되고, WebSocket Media Stream을 시작
    """
    # 서버 호스트 결정 (ngrok/cloudflare 터널 등)
    host = request.headers.get("host", "localhost:8000")
    proto = "wss" if request.url.scheme == "https" else "ws"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="ko-KR">안녕하세요, 제품A 고객 지원 AI입니다. 궁금하신 내용을 말씀해 주세요.</Say>
    <Connect>
        <Stream url="{proto}://{host}/twilio/media-stream" />
    </Connect>
</Response>"""

    logger.info(f"[TWILIO] Incoming call, streaming to {proto}://{host}/twilio/media-stream")
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/media-stream")
async def twilio_media_stream(websocket: WebSocket):
    """
    Twilio Media Stream ↔ Gemini Live 프록시

    Twilio → mulaw 8kHz → PCM 16kHz → Gemini Live
    Gemini Live → PCM 24kHz → PCM 8kHz → mulaw → Twilio
    """
    await websocket.accept()
    logger.info("[TWILIO] Media stream WebSocket connected")

    api_key = _get_gemini_key()
    if not api_key:
        logger.error("[TWILIO] GEMINI_API_KEY not configured")
        await websocket.close()
        return

    faq_text = _load_faq()
    stream_sid = None
    conversation_log = []

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        system_text = (
            "제품A 고객 지원 상담원으로 한국어로 짧게 답변하세요. 2-3문장. "
            "마크다운 사용 금지. 고객센터 1588-0000.\n"
            "전화 통화 중이므로 짧고 명확하게 답변하세요.\n\n"
        )
        if faq_text:
            system_text += f"FAQ:\n{faq_text}"

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Kore"
                    )
                ),
                language_code="ko-KR",
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=system_text)]
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async with client.aio.live.connect(
            model="gemini-3.1-flash-live-preview",
            config=config,
        ) as session:
            logger.info("[TWILIO] Gemini Live session connected")

            stop_event = asyncio.Event()
            current_ai_text = []

            async def twilio_to_gemini():
                """Twilio 오디오 → Gemini"""
                nonlocal stream_sid
                try:
                    while not stop_event.is_set():
                        raw = await websocket.receive_text()
                        msg = json.loads(raw)
                        event = msg.get("event")

                        if event == "start":
                            stream_sid = msg.get("start", {}).get("streamSid")
                            logger.info(f"[TWILIO] Stream started: {stream_sid}")

                        elif event == "media":
                            # mulaw 8kHz base64 → PCM 16kHz
                            payload = msg["media"]["payload"]
                            mulaw_bytes = base64.b64decode(payload)
                            pcm_8k = _mulaw_to_pcm16(mulaw_bytes)
                            pcm_16k = _pcm16_8k_to_16k(pcm_8k)

                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=pcm_16k,
                                    mime_type="audio/pcm;rate=16000",
                                )
                            )

                        elif event == "stop":
                            logger.info("[TWILIO] Stream stopped")
                            stop_event.set()
                            break

                except WebSocketDisconnect:
                    logger.info("[TWILIO] Twilio disconnected")
                    stop_event.set()
                except Exception as e:
                    logger.error(f"[TWILIO] twilio_to_gemini error: {e}")
                    stop_event.set()

            async def gemini_to_twilio():
                """Gemini 응답 → Twilio"""
                nonlocal current_ai_text
                try:
                    async for response in session.receive():
                        if stop_event.is_set():
                            break

                        server_content = getattr(response, "server_content", None)
                        if not server_content:
                            continue

                        try:
                            # 입력 트랜스크립션
                            input_transcription = getattr(server_content, "input_transcription", None)
                            if input_transcription:
                                user_text = getattr(input_transcription, "text", "") or ""
                                if user_text.strip():
                                    conversation_log.append({"role": "user", "text": user_text.strip()})
                                    logger.info(f"[TWILIO] User said: {user_text.strip()}")

                            # 출력 트랜스크립션
                            output_transcription = getattr(server_content, "output_transcription", None)
                            if output_transcription:
                                ai_text = getattr(output_transcription, "text", "") or ""
                                if ai_text.strip():
                                    current_ai_text.append(ai_text.strip())

                            # 오디오 응답 → Twilio로 전송
                            model_turn = getattr(server_content, "model_turn", None)
                            if model_turn and hasattr(model_turn, "parts"):
                                for part in model_turn.parts:
                                    if hasattr(part, "inline_data") and part.inline_data:
                                        # PCM 24kHz → mulaw 8kHz → Twilio
                                        pcm_24k = part.inline_data.data
                                        pcm_8k = _pcm16_24k_to_8k(pcm_24k)
                                        mulaw = _pcm16_to_mulaw(pcm_8k)
                                        payload_b64 = base64.b64encode(mulaw).decode()

                                        if stream_sid:
                                            await websocket.send_json({
                                                "event": "media",
                                                "streamSid": stream_sid,
                                                "media": {
                                                    "payload": payload_b64,
                                                },
                                            })

                            # turn_complete
                            if getattr(server_content, "turn_complete", False):
                                if current_ai_text:
                                    full_ai = " ".join(current_ai_text)
                                    conversation_log.append({"role": "ai", "text": full_ai})
                                    logger.info(f"[TWILIO] AI said: {full_ai}")
                                    current_ai_text = []

                        except Exception as inner_e:
                            logger.warning(f"[TWILIO] Response processing error (continuing): {inner_e}")
                            continue

                except Exception as e:
                    if not stop_event.is_set():
                        logger.error(f"[TWILIO] gemini_to_twilio error: {e}")
                    stop_event.set()

            tasks = [
                asyncio.create_task(twilio_to_gemini()),
                asyncio.create_task(gemini_to_twilio()),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

    except Exception as e:
        logger.error(f"[TWILIO] Session error: {e}", exc_info=True)
    finally:
        # 통화 종료 → Slack 전송
        if conversation_log:
            await _send_to_slack(conversation_log)
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info(f"[TWILIO] Session closed. {len(conversation_log)} turns logged.")


async def _send_to_slack(conversation_log: list):
    """대화 기록을 Slack으로 전송"""
    try:
        from slack_sdk import WebClient
        settings = get_settings()
        if not settings.SLACK_BOT_TOKEN:
            return
        slack_client = WebClient(token=settings.SLACK_BOT_TOKEN)

        lines = []
        for entry in conversation_log:
            role = "👤 고객" if entry["role"] == "user" else "🤖 AI"
            lines.append(f"{role}: {entry['text']}")

        conversation_text = "\n".join(lines)
        if len(conversation_text) > 3000:
            conversation_text = conversation_text[:3000] + "\n... (truncated)"

        slack_client.chat_postMessage(
            channel="U0000000C3",  # admin DM
            text=f"""📞 *Twilio 전화 상담 종료*

*대화 내용 ({len(conversation_log)}턴):*
{conversation_text}

---
_Twilio + Gemini 3.1 Flash Live · 자동 생성된 통화 로그_""",
        )
        logger.info(f"[TWILIO] Slack summary sent ({len(conversation_log)} turns)")
    except Exception as e:
        logger.error(f"[TWILIO] Slack send failed: {e}")
