"""
MOCO Voice Chat — ElevenLabs STT/TTS + Claude 연동
브라우저 마이크 → STT → Claude → TTS → 브라우저 스피커
"""

import io
import logging
import os
import httpx
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Form, Body
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice-chat", tags=["Voice Chat"])

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"


def _get_api_key():
    import os
    # 환경변수 → settings 순서로 확인
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        settings = get_settings()
        key = getattr(settings, "ELEVENLABS_API_KEY", "") or ""
    return key


@router.get("", response_class=HTMLResponse)
async def voice_chat_page():
    """음성 대화 웹 페이지"""
    html_path = Path(__file__).parent / "static" / "voice_chat.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


from fastapi.responses import FileResponse


@router.post("/greet")
async def greet():
    """통화 시작 안내 멘트 TTS"""
    api_key = _get_api_key()
    greet_text = "안녕하세요, 제품A 고객 지원센터입니다. 궁금하신 내용을 말씀해 주시면 도와드리겠습니다."

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{ELEVENLABS_BASE}/text-to-speech/hpp4J3VqNfWAUOO0d1Us",
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={"text": greet_text, "model_id": "eleven_flash_v2_5",
                  "voice_settings": {"stability": 0.6, "similarity_boost": 0.8}},
        )
    if resp.status_code != 200:
        return JSONResponse({"error": "TTS failed"}, status_code=500)
    return StreamingResponse(io.BytesIO(resp.content), media_type="audio/mpeg")


@router.get("/static/{filename}")
async def voice_chat_static(filename: str):
    """정적 파일 서빙 (홀드 뮤직 등)"""
    file_path = Path(__file__).parent / "static" / filename
    if file_path.exists():
        return FileResponse(file_path)
    return JSONResponse({"error": "not found"}, status_code=404)


@router.post("/process")
async def process_voice(audio: UploadFile = File(...), context: str = Form("")):
    """
    음성 입력 → STT → Claude → TTS → 음성 응답

    1. ElevenLabs Scribe로 음성을 텍스트로 변환
    2. Claude API로 응답 생성
    3. ElevenLabs TTS로 응답을 음성으로 변환
    4. 음성 + 텍스트 반환
    """
    api_key = _get_api_key()
    if not api_key:
        return {"error": "ELEVENLABS_API_KEY not configured"}

    # 타이밍 측정
    import time as _time
    _t0 = _time.time()

    # 1. STT: ElevenLabs Scribe
    audio_bytes = await audio.read()
    logger.info(f"[VOICE_CHAT] Received audio: {len(audio_bytes)} bytes (WAV)")

    async with httpx.AsyncClient(timeout=30) as client:
        stt_resp = await client.post(
            f"{ELEVENLABS_BASE}/speech-to-text",
            headers={"xi-api-key": api_key},
            files={"file": ("recording.wav", audio_bytes, "audio/wav")},
            data={"model_id": "scribe_v1", "language_code": "ko"},
        )

    if stt_resp.status_code != 200:
        logger.error(f"[VOICE_CHAT] STT error: {stt_resp.status_code} {stt_resp.text}")
        return {"error": f"STT failed: {stt_resp.text}"}

    user_text = stt_resp.json().get("text", "").strip()
    _t1 = _time.time()
    logger.info(f"[VOICE_CHAT] STT result: {user_text} ({_t1-_t0:.1f}s)")

    if not user_text:
        return {"error": "음성을 인식하지 못했습니다. 다시 말씀해주세요."}

    # 2. Claude: Anthropic API 직접 호출
    import anthropic

    # FAQ 문서 로드 (캐싱 - QnA 쌍으로 파싱)
    global _faq_items
    if "_faq_items" not in globals() or _faq_items is None:
        _faq_items = []
        faq_path = Path(__file__).parent.parent.parent.parent / "AICC_인바운드_시나리오.docx"
        if faq_path.exists():
            try:
                from docx import Document as DocxDocument
                doc = DocxDocument(str(faq_path))
                for table in doc.tables:
                    for row in table.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        if len(cells) >= 3 and cells[0].startswith("Q"):
                            _faq_items.append({"q": cells[1], "a": cells[2]})
                logger.info(f"[VOICE_CHAT] FAQ loaded: {len(_faq_items)} QnA pairs")
            except Exception as e:
                logger.warning(f"[VOICE_CHAT] FAQ 로드 실패: {e}")

    # 질문에 관련된 FAQ만 추출 (키워드 매칭)
    keywords = [w for w in user_text.lower().split() if len(w) > 1]
    relevant_faq = []
    for item in _faq_items:
        q_lower = item["q"].lower()
        a_lower = item["a"].lower()
        if any(kw in q_lower or kw in a_lower for kw in keywords):
            relevant_faq.append(f"Q: {item['q']}\nA: {item['a']}")
    if not relevant_faq:
        relevant_faq = [f"Q: {item['q']}\nA: {item['a']}" for item in _faq_items[:5]]
    faq_snippet = "\n\n".join(relevant_faq[:5])

    prompt = user_text
    if context:
        prompt = f"(이전 맥락: {context})\n\n{user_text}"

    system_prompt = f"""제품A 고객 지원 상담원으로 한국어로 짧게 답변하세요. 2-3문장. 마크다운 사용 금지. 고객센터 1588-0000.

관련 FAQ:
{faq_snippet}"""

    try:
        claude_client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )
        claude_resp = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        ai_text = claude_resp.content[0].text.strip()
    except Exception as e:
        ai_text = ""
        logger.error(f"[VOICE_CHAT] Claude API error: {e}")

    _t2 = _time.time()
    if not ai_text:
        ai_text = "죄송합니다, 잠시 후 다시 시도해주세요. 상담원 연결은 0번을 눌러주세요."
    logger.info(f"[VOICE_CHAT] Claude response ({_t2-_t1:.1f}s): {ai_text}")
    logger.info(f"[VOICE_CHAT] Claude response: {ai_text}")

    # 3. TTS: ElevenLabs
    voice_id = "hpp4J3VqNfWAUOO0d1Us"  # Bella (Professional, Bright, Warm, female)

    async with httpx.AsyncClient(timeout=30) as client:
        tts_resp = await client.post(
            f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": ai_text,
                "model_id": "eleven_flash_v2_5",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75,
                },
            },
        )

    if tts_resp.status_code != 200:
        logger.error(f"[VOICE_CHAT] TTS error: {tts_resp.status_code} {tts_resp.text}")
        return {"user_text": user_text, "ai_text": ai_text, "error": "TTS failed"}

    _t3 = _time.time()
    logger.info(f"[VOICE_CHAT] TTS audio: {len(tts_resp.content)} bytes ({_t3-_t2:.1f}s)")
    logger.info(f"[VOICE_CHAT] Total: {_t3-_t0:.1f}s (STT:{_t1-_t0:.1f} + Claude:{_t2-_t1:.1f} + TTS:{_t3-_t2:.1f})")

    # 4. 응답: 음성 + 텍스트
    return StreamingResponse(
        io.BytesIO(tts_resp.content),
        media_type="audio/mpeg",
        headers={
            "X-User-Text": user_text.encode("utf-8").decode("latin-1", errors="replace"),
            "X-AI-Text": ai_text.encode("utf-8").decode("latin-1", errors="replace"),
        },
    )


@router.post("/end-session")
async def end_session(conversation: str = Body(..., embed=True)):
    """통화 종료 시 대화 로그를 MOCO에 전달 → Slack 요약 + 메모리 저장

    Claude API 사용 안 함. MOCO Slack 봇으로 요약 전송.
    """
    if not conversation or len(conversation.strip()) < 10:
        return JSONResponse({"ok": True, "message": "대화 내용이 없어 요약을 건너뜁니다."})

    logger.info(f"[VOICE_CHAT] Session ended. Conversation length: {len(conversation)} chars")

    # Slack에 통화 요약 전송 (MOCO 봇 토큰 사용)
    try:
        from slack_sdk import WebClient
        settings = get_settings()
        slack_client = WebClient(token=settings.SLACK_BOT_TOKEN)

        # 요약 채널 (기본: admin DM)
        summary_channel = "U0000000C3"

        # 간단한 요약 포맷
        summary_text = f"""📞 *Voice Chat 통화 종료*

*통화 내용:*
{conversation[:3000]}

---
_자동 생성된 통화 로그입니다._"""

        slack_client.chat_postMessage(
            channel=summary_channel,
            text=summary_text,
        )
        logger.info(f"[VOICE_CHAT] Summary sent to Slack channel {summary_channel}")

    except Exception as e:
        logger.error(f"[VOICE_CHAT] Failed to send summary to Slack: {e}")

    return JSONResponse({"ok": True, "message": "통화 요약이 Slack에 전송되었습니다."})
