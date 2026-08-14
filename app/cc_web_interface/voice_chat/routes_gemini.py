"""
MOCO Voice Chat — Gemini Live API 버전
브라우저 마이크 → WebSocket → Gemini Live → WebSocket → 브라우저 스피커
단일 WebSocket으로 실시간 양방향 음성 대화
"""

import asyncio
import base64
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import HTMLResponse, JSONResponse

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice-chat-gemini", tags=["Voice Chat Gemini"])

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


def _get_gemini_key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    if not key:
        settings = get_settings()
        key = getattr(settings, "GEMINI_API_KEY", "") or getattr(settings, "GOOGLE_API_KEY", "") or ""
    if not key:
        key = GEMINI_API_KEY
    return key


@router.get("", response_class=HTMLResponse)
async def gemini_voice_chat_page():
    """Gemini Live 음성 대화 웹 페이지"""
    html_path = Path(__file__).parent / "static" / "voice_chat_gemini.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# FAQ 캐시 (모듈 레벨)
_faq_cache: str | None = None


def _load_faq() -> str:
    """FAQ 문서에서 QnA 쌍을 로드하여 문자열로 반환 (캐싱)"""
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
            logger.info(f"[GEMINI_VOICE] FAQ loaded: {len(faq_items)} QnA pairs")
        except Exception as e:
            logger.warning(f"[GEMINI_VOICE] FAQ 로드 실패: {e}")

    _faq_cache = "\n\n".join(faq_items) if faq_items else ""
    return _faq_cache


@router.websocket("/ws")
async def gemini_voice_ws(websocket: WebSocket):
    """
    브라우저 ↔ 서버 ↔ Gemini Live API 프록시 WebSocket

    프로토콜:
    - 브라우저 → 서버: {"type":"audio","data":"<base64 PCM 16kHz 16bit mono>"}
    - 서버 → 브라우저: {"type":"audio","data":"<base64 PCM 24kHz 16bit mono>"}
    - 서버 → 브라우저: {"type":"text","data":"<AI 응답 텍스트>"}
    - 서버 → 브라우저: {"type":"input_text","data":"<사용자 음성 인식 텍스트>"}
    - 서버 → 브라우저: {"type":"turn_complete","data":""}
    - 브라우저 → 서버: {"type":"end"} — 세션 종료
    """
    await websocket.accept()
    api_key = _get_gemini_key()
    if not api_key:
        await websocket.send_json({"type": "error", "data": "GEMINI_API_KEY not configured"})
        await websocket.close()
        return

    logger.info("[GEMINI_VOICE] WebSocket connected")

    # FAQ 로드
    faq_text = _load_faq()

    # 대화 기록 추적
    conversation_log = []

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        # 시스템 프롬프트 구성 (기존 voice-chat과 동일)
        system_text = (
            "제품A 고객 지원 상담원으로 한국어로 짧게 답변하세요. 2-3문장. "
            "마크다운 사용 금지. 고객센터 1588-0000.\n\n"
        )
        if faq_text:
            system_text += f"아래는 자주 묻는 질문(FAQ)입니다. 고객 질문에 관련된 내용이 있으면 참고하여 답변하세요.\n\n{faq_text}"

        # Gemini Live 세션 설정
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
            # 입출력 음성 텍스트 변환 활성화
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async with client.aio.live.connect(
            model="gemini-3.1-flash-live-preview",
            config=config,
        ) as session:
            logger.info("[GEMINI_VOICE] Gemini Live session connected")
            await websocket.send_json({"type": "connected", "data": "Gemini Live 연결됨"})

            stop_event = asyncio.Event()
            current_ai_text = []  # 현재 턴의 AI 텍스트 조각 수집

            async def browser_to_gemini():
                """브라우저 오디오 → Gemini"""
                try:
                    while not stop_event.is_set():
                        raw = await websocket.receive_text()
                        msg = json.loads(raw)

                        if msg.get("type") == "audio":
                            pcm_data = base64.b64decode(msg["data"])
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=pcm_data,
                                    mime_type="audio/pcm;rate=16000",
                                )
                            )
                        elif msg.get("type") == "end":
                            logger.info("[GEMINI_VOICE] Client requested end")
                            stop_event.set()
                            break
                except WebSocketDisconnect:
                    logger.info("[GEMINI_VOICE] Browser disconnected")
                    stop_event.set()
                except Exception as e:
                    logger.error(f"[GEMINI_VOICE] browser_to_gemini error: {e}")
                    stop_event.set()

            async def gemini_to_browser():
                """Gemini 응답 → 브라우저 (세션 끊기 전까지 계속 반복)"""
                nonlocal current_ai_text
                try:
                    async for response in session.receive():
                        if stop_event.is_set():
                            break

                        server_content = getattr(response, "server_content", None)
                        if not server_content:
                            continue

                        try:
                            # 입력 음성 트랜스크립션 (사용자가 말한 내용)
                            input_transcription = getattr(server_content, "input_transcription", None)
                            if input_transcription:
                                user_text = getattr(input_transcription, "text", "") or ""
                                if user_text.strip():
                                    conversation_log.append({"role": "user", "text": user_text.strip()})
                                    await websocket.send_json({
                                        "type": "input_text",
                                        "data": user_text.strip(),
                                    })
                                    logger.info(f"[GEMINI_VOICE] User said: {user_text.strip()}")

                            # 출력 음성 트랜스크립션 (AI가 말한 내용)
                            output_transcription = getattr(server_content, "output_transcription", None)
                            if output_transcription:
                                ai_text = getattr(output_transcription, "text", "") or ""
                                if ai_text.strip():
                                    current_ai_text.append(ai_text.strip())
                                    await websocket.send_json({
                                        "type": "text",
                                        "data": ai_text.strip(),
                                    })

                            # 모델 턴 (오디오 데이터)
                            model_turn = getattr(server_content, "model_turn", None)
                            if model_turn and hasattr(model_turn, "parts"):
                                for part in model_turn.parts:
                                    if hasattr(part, "inline_data") and part.inline_data:
                                        audio_b64 = base64.b64encode(
                                            part.inline_data.data
                                        ).decode()
                                        await websocket.send_json({
                                            "type": "audio",
                                            "data": audio_b64,
                                        })
                                    elif hasattr(part, "text") and part.text:
                                        current_ai_text.append(part.text)
                                        await websocket.send_json({
                                            "type": "text",
                                            "data": part.text,
                                        })

                            # turn_complete 신호
                            if getattr(server_content, "turn_complete", False):
                                if current_ai_text:
                                    full_ai = " ".join(current_ai_text)
                                    conversation_log.append({"role": "ai", "text": full_ai})
                                    logger.info(f"[GEMINI_VOICE] AI said: {full_ai}")
                                    current_ai_text = []

                                await websocket.send_json({
                                    "type": "turn_complete",
                                    "data": "",
                                })

                        except Exception as inner_e:
                            # 개별 응답 처리 에러는 로깅만 하고 계속 진행
                            logger.warning(f"[GEMINI_VOICE] Response processing error (continuing): {inner_e}")
                            continue

                except Exception as e:
                    if not stop_event.is_set():
                        logger.error(f"[GEMINI_VOICE] gemini_to_browser error: {e}")
                    stop_event.set()

            # 양방향 동시 실행
            tasks = [
                asyncio.create_task(browser_to_gemini()),
                asyncio.create_task(gemini_to_browser()),
            ]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

    except WebSocketDisconnect:
        logger.info("[GEMINI_VOICE] WebSocket disconnected")
    except Exception as e:
        logger.error(f"[GEMINI_VOICE] Session error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
        except Exception:
            pass
    finally:
        # 세션 종료 시 대화 기록이 있으면 Slack 전송
        if conversation_log:
            await _send_to_slack(conversation_log)
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info(f"[GEMINI_VOICE] Session closed. {len(conversation_log)} turns logged.")


async def _send_to_slack(conversation_log: list):
    """대화 기록을 Slack으로 전송"""
    try:
        from slack_sdk import WebClient
        settings = get_settings()
        if not settings.SLACK_BOT_TOKEN:
            logger.warning("[GEMINI_VOICE] SLACK_BOT_TOKEN not set, skipping Slack send")
            return

        slack_client = WebClient(token=settings.SLACK_BOT_TOKEN)
        summary_channel = "U0000000C3"  # admin DM

        # 대화 포맷팅
        lines = []
        for entry in conversation_log:
            role = "👤 고객" if entry["role"] == "user" else "🤖 AI"
            lines.append(f"{role}: {entry['text']}")

        conversation_text = "\n".join(lines)
        if len(conversation_text) > 3000:
            conversation_text = conversation_text[:3000] + "\n... (truncated)"

        summary_text = f"""📞 *Gemini Live Voice Chat 통화 종료*

*대화 내용 ({len(conversation_log)}턴):*
{conversation_text}

---
_Gemini 3.1 Flash Live · 자동 생성된 통화 로그_"""

        slack_client.chat_postMessage(
            channel=summary_channel,
            text=summary_text,
        )
        logger.info(f"[GEMINI_VOICE] Slack summary sent ({len(conversation_log)} turns)")

    except Exception as e:
        logger.error(f"[GEMINI_VOICE] Slack send failed: {e}")


@router.post("/end-session")
async def end_session(conversation: str = Body(..., embed=True)):
    """프론트엔드에서 별도로 대화 로그를 전송하는 폴백 엔드포인트"""
    if not conversation or len(conversation.strip()) < 10:
        return JSONResponse({"ok": True, "message": "대화 내용이 없어 요약을 건너뜁니다."})

    logger.info(f"[GEMINI_VOICE] End-session fallback. Conversation: {len(conversation)} chars")

    try:
        from slack_sdk import WebClient
        settings = get_settings()
        slack_client = WebClient(token=settings.SLACK_BOT_TOKEN)

        slack_client.chat_postMessage(
            channel="U0000000C3",
            text=f"""📞 *Gemini Live Voice Chat 통화 종료*

*통화 내용:*
{conversation[:3000]}

---
_Gemini 3.1 Flash Live · 자동 생성된 통화 로그_""",
        )
    except Exception as e:
        logger.error(f"[GEMINI_VOICE] Slack fallback send failed: {e}")

    return JSONResponse({"ok": True, "message": "통화 요약이 Slack에 전송되었습니다."})
