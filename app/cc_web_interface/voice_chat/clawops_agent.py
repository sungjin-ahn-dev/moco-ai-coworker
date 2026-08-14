"""
MOCO AICC — CLAW OPS + Gemini Live 음성 에이전트.
인바운드 전화를 받아 Gemini Live로 응답한다.
"""

import asyncio
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ~/.moco/config.env에서 환경변수 로드 (Electron 없이 standalone 실행 시)
_eco_config = os.path.join(os.path.expanduser("~"), ".eco", "config.env")
if os.path.exists(_eco_config):
    from dotenv import load_dotenv
    load_dotenv(_eco_config, override=False)

if not os.environ.get("CLAWOPS_API_KEY"):
    logger.warning("[CLAWOPS_AGENT] CLAWOPS_API_KEY 환경변���가 설정되지 않았습니���")
if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
    logger.warning("[CLAWOPS_AGENT] GOOGLE_API_KEY/GEMINI_API_KEY 환경변수가 설정되지 않았습니다")


def _load_faq() -> str:
    """FAQ 문서에서 QnA 쌍을 로드"""
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
            logger.info(f"FAQ loaded: {len(faq_items)} QnA pairs")
        except Exception as e:
            logger.warning(f"FAQ load failed: {e}")
    return "\n\n".join(faq_items) if faq_items else ""


def main():
    from clawops.agent import ClawOpsAgent, GeminiRealtime, BuiltinTool

    faq_text = _load_faq()

    system_prompt = (
        "제품A 고객 지원 상담원으로 한국어로만 답변하세요. 2-3문장. "
        "마크다운 사용 금지. 고객센터 1588-0000.\n"
        "전화 통화 중이므로 짧고 명확하게 답변하세요.\n"
        "고객은 항상 한국어로 말합니다. 모든 음성 입력을 한국어로 해석하세요. 절대 일본어, 영어, 중국어로 해석하지 마세요.\n"
        "고객이 '상담원 연결', '사람과 통화', '0번' 등을 요청하면 transfer_call 도구를 사용하여 07012345678 번호로 전환하세요.\n\n"
    )
    if faq_text:
        system_prompt += f"아래는 자주 묻는 질문(FAQ)입니다. 고객 질문에 관련된 내용이 있으면 참고하여 답변하세요.\n\n{faq_text}"

    # 대화 기록 저장용 (call_id → list)
    conversations = {}

    # 발신번호 → Slack 채널 매핑
    CALLER_SLACK_MAP = {
        "01000000001": "U0000000A1",   # member1
        "01000000002": "U0000000B2",   # member2
        "01000000003": "U0000000C3",   # admin
        "01000000004": "U0000000D4",   # member3
        "01000000005": "U0000000E5",   # member4
    }

    agent = ClawOpsAgent(
        from_="07012345678",
        session=GeminiRealtime(
            system_prompt=system_prompt,
            language="ko",
        ),
        recording=True,
        recording_path="/home/user/MOCO_DATA/aicc_recordings",
    )

    @agent.on("call_start")
    async def on_call_start(call):
        conversations[call.call_id] = {"from": call.from_number, "to": call.to_number, "log": []}
        logger.info(f"📞 전화 수신: from={call.from_number}, to={call.to_number}, id={call.call_id}")

    @agent.on("call_end")
    async def on_call_end(call):
        logger.info(f"📞 통화 종료: id={call.call_id}")

        # Slack 전송
        conv = conversations.pop(call.call_id, None)
        try:
            from slack_sdk import WebClient
            slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
            if not slack_token:
                try:
                    from app.config.settings import get_settings
                    slack_token = get_settings().SLACK_BOT_TOKEN or ""
                except Exception:
                    pass

            if slack_token:
                slack_client = WebClient(token=slack_token)

                if conv and conv["log"]:
                    lines = [f"{'👤 고객' if r == 'user' else '🤖 AI'}: {t}" for r, t in conv["log"]]
                    conv_text = "\n".join(lines)

                    # Gemini API로 트랜스크립트 정제 (오인식 한국어 교정)
                    try:
                        from google import genai
                        gemini_client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
                        refine_resp = gemini_client.models.generate_content(
                            model="gemini-3-flash-preview",
                            contents=f"""아래는 전화 통화 트랜스크립트입니다. 음성 인식 오류로 한국어가 일본어, 영어 등 다른 언어로 잘못 변환된 부분이 있습니다.
모든 내용을 자연스러운 한국어로 교정해주세요. 원래 의미를 유추하여 자연스러운 한국어 문장으로 바꿔주세요.
형식(👤 고객: / 🤖 AI:)은 그대로 유지하세요. 교정된 대화만 출력하세요.

{conv_text}""",
                        )
                        refined = refine_resp.text.strip()
                        if refined:
                            conv_text = refined
                            logger.info("✅ 트랜스크립트 정제 완료")
                    except Exception as refine_err:
                        logger.warning(f"트랜스크립트 정제 실패 (원본 사용): {refine_err}")

                    if len(conv_text) > 3000:
                        conv_text = conv_text[:3000] + "\n... (truncated)"
                    msg = f"""📞 *070 AI 전화 상담 종료*

*발신자:* {conv.get('from', '알 수 없음')}
*수신번호:* 070-1234-5678
*대화 ({len(conv['log'])}턴):*
{conv_text}

---
_CLAW OPS + Gemini 3.1 Flash Live_"""
                else:
                    from_num = conv.get('from', '알 수 없음') if conv else '알 수 없음'
                    msg = f"""📞 *070 AI 전화 상담 종료*

*발신자:* {from_num}
*수신번호:* 070-1234-5678
*대화 내용:* 트랜스크립트 없음 (짧은 통화)

---
_CLAW OPS + Gemini 3.1 Flash Live_"""

                # 발신번호에 따라 Slack 채널 결정
                from_num = conv.get('from', '') if conv else ''
                # 번호 정규화 (010-xxxx-xxxx → 010xxxxxxxx)
                clean_num = from_num.replace("-", "").replace("+82", "0").lstrip("82")
                slack_channel = CALLER_SLACK_MAP.get(clean_num, "U0000000C3")  # 기본: admin

                slack_client.chat_postMessage(channel=slack_channel, text=msg)
                logger.info(f"✅ Slack 전송 완료 → {slack_channel} (from: {clean_num})")

                # 녹음 파일 Slack 전송
                import glob
                rec_dir = "/home/user/MOCO_DATA/aicc_recordings"
                # {call_id}/mix.wav 구조
                mix_files = glob.glob(f"{rec_dir}/{call.call_id}/mix.wav")
                if not mix_files:
                    mix_files = glob.glob(f"{rec_dir}/{call.call_id}/*.wav")
                for wav_path in mix_files:
                    try:
                        slack_client.files_upload_v2(
                            channel=slack_channel,
                            file=wav_path,
                            title=f"통화 녹음 ({call.call_id})",
                            initial_comment="🎙️ 통화 녹음 파일",
                        )
                        logger.info(f"✅ 녹음 파일 전송 완료: {wav_path}")
                    except Exception as wav_err:
                        logger.warning(f"녹음 파일 전송 실패: {wav_err}")
            else:
                logger.warning("SLACK_BOT_TOKEN 없음 — Slack 전송 건너뜀")
        except Exception as e:
            logger.error(f"Slack 전송 실패: {e}")

    @agent.on("call_failed")
    async def on_call_failed(call, reason):
        logger.error(f"📞 통화 실패: {reason}")

    @agent.on("transcript")
    async def on_transcript(call, role, text):
        logger.info(f"💬 [{role}] {text}")
        if call.call_id in conversations:
            log = conversations[call.call_id]["log"]
            # 같은 role이 연속이면 이어붙이기 (단어 단위 스트리밍 합치기)
            if log and log[-1][0] == role:
                log[-1] = (role, log[-1][1] + text)
            else:
                log.append((role, text))

    logger.info("MOCO AICC Agent 시작 — 070-1234-5678 대기 중...")
    logger.info("   전화하면 Gemini AI가 응답합니다. Ctrl+C로 종료.")
    asyncio.run(agent.serve())


if __name__ == "__main__":
    main()
