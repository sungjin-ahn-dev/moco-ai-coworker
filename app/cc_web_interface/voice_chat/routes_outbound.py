"""
MOCO Voice Chat Outbound — 아웃바운드 재처방 안내
ElevenLabs STT/TTS + Claude API, 환자 정보 기반 대화
"""

import io
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, UploadFile, File, Form, Body
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice-chat-out", tags=["Voice Chat Outbound"])

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VOICE_ID = "hpp4J3VqNfWAUOO0d1Us"  # Bella


@router.get("", response_class=HTMLResponse)
async def outbound_page():
    html_path = Path(__file__).parent / "static" / "voice_chat_out.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@router.post("/greet")
async def outbound_greet(body: dict):
    """아웃바운드 첫 안내 멘트 TTS"""
    text = body.get("text", "")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{ELEVENLABS_BASE}/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_flash_v2_5",
                  "voice_settings": {"stability": 0.6, "similarity_boost": 0.8}},
        )
    if resp.status_code != 200:
        return JSONResponse({"error": "TTS failed"}, status_code=500)
    return StreamingResponse(io.BytesIO(resp.content), media_type="audio/mpeg")


@router.post("/process")
async def outbound_process(
    audio: UploadFile = File(...),
    context: str = Form(""),
    patient_name: str = Form(""),
    hospital: str = Form(""),
    doctor: str = Form(""),
    product: str = Form("제품A"),
    expiry_date: str = Form(""),
    call_purpose: str = Form(""),
):
    """아웃바운드 대화 처리 — 환자 정보 기반"""
    t0 = time.time()

    # 1. STT
    audio_bytes = await audio.read()
    logger.info(f"[OUTBOUND] Audio: {len(audio_bytes)} bytes")

    async with httpx.AsyncClient(timeout=30) as client:
        stt_resp = await client.post(
            f"{ELEVENLABS_BASE}/speech-to-text",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            files={"file": ("recording.wav", audio_bytes, "audio/wav")},
            data={"model_id": "scribe_v1", "language_code": "ko"},
        )

    if stt_resp.status_code != 200:
        return JSONResponse({"error": "음성 인식 실패"})

    user_text = stt_resp.json().get("text", "").strip()
    t1 = time.time()
    logger.info(f"[OUTBOUND] STT ({t1-t0:.1f}s): {user_text}")

    if not user_text:
        return JSONResponse({"error": "음성을 인식하지 못했습니다."})

    # 2. Claude API — 환자 정보 포함 프롬프트
    import anthropic

    system_prompt = f"""당신은 제품A 고객지원센터 상담원입니다. 지금 환자에게 전화를 건 상황입니다.

환자 정보:
- 이름: {patient_name}
- 처방 병원: {hospital}
- 담당의: {doctor}
- 처방 제품: {product}
- 처방 만료일: {expiry_date or '확인 필요'}
- 전화 목적: {call_purpose or '재처방 안내'}

대화 원칙:
- 한국어로 자연스럽고 친절하게 대화하세요.
- 음성 통화이므로 2-3문장으로 짧게 답하세요.
- 마크다운, 특수문자, 이모지를 사용하지 마세요.
- 고령 환자가 많으므로 쉬운 말로 천천히 설명하세요.
- 환자 이름을 자연스럽게 불러주세요.
- 재처방 절차를 안내하세요: 담당 병원 방문 → 재처방 코드 발급 → 앱에서 코드 등록.
- 환자가 질문하면 친절하게 답변하세요.
- 모르는 내용은 "확인 후 다시 연락드리겠습니다"로 안내하세요."""

    prompt = f"(이전 대화: {context})\n\n환자: {user_text}" if context else f"환자: {user_text}"

    try:
        claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        ai_text = resp.content[0].text.strip()
    except Exception as e:
        ai_text = f"{patient_name}님, 잠시 통화 상태가 좋지 않네요. 다시 말씀해주시겠어요?"
        logger.error(f"[OUTBOUND] Claude error: {e}")

    t2 = time.time()
    logger.info(f"[OUTBOUND] Claude ({t2-t1:.1f}s): {ai_text}")

    # 3. TTS
    async with httpx.AsyncClient(timeout=30) as client:
        tts_resp = await client.post(
            f"{ELEVENLABS_BASE}/text-to-speech/{VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": ai_text, "model_id": "eleven_flash_v2_5",
                  "voice_settings": {"stability": 0.6, "similarity_boost": 0.8}},
        )

    t3 = time.time()
    if tts_resp.status_code != 200:
        return JSONResponse({"error": "음성 생성 실패"})

    logger.info(f"[OUTBOUND] Total: {t3-t0:.1f}s (STT:{t1-t0:.1f} + Claude:{t2-t1:.1f} + TTS:{t3-t2:.1f})")

    return StreamingResponse(
        io.BytesIO(tts_resp.content),
        media_type="audio/mpeg",
        headers={
            "X-User-Text": user_text.encode("utf-8").decode("latin-1", errors="replace"),
            "X-AI-Text": ai_text.encode("utf-8").decode("latin-1", errors="replace"),
        },
    )


@router.post("/end-session")
async def outbound_end_session(body: dict):
    """통화 종료 → Slack 요약"""
    conversation = body.get("conversation", "")
    if not conversation or len(conversation.strip()) < 10:
        return JSONResponse({"ok": True})

    try:
        from slack_sdk import WebClient
        settings = get_settings()
        slack_client = WebClient(token=settings.SLACK_BOT_TOKEN)
        slack_client.chat_postMessage(
            channel="U0000000C3",
            text=f"📞 *아웃바운드 통화 종료*\n\n{conversation[:3000]}\n\n_자동 생성된 통화 로그_",
        )
    except Exception as e:
        logger.error(f"[OUTBOUND] Slack error: {e}")

    return JSONResponse({"ok": True})
