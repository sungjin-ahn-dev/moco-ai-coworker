"""
봇 호출 감지 에이전트 (Bot Call Detector Agent)

이 모듈은 메시지가 봇을 직접 호출하는 것인지 판단합니다.
빠른 키워드 매칭 후, 애매한 경우 Claude로 판단합니다.
"""

import asyncio
import logging
import os

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)
from app.cc_utils.sdk_retry import RetryableSDKClient

from app.config.settings import get_settings
from app.cc_utils.language_helper import detect_language
from app.cc_utils.prompt_helper import prepare_options


def create_system_prompt(bot_name: str) -> str:
    """봇 호출 감지를 위한 system prompt 생성

    Args:
        bot_name: 봇의 이름

    Returns:
        str: 봇 호출 감지를 위한 system prompt
    """
    # 한글 이름인 경우만 줄임말 생성
    is_korean_name = detect_language(bot_name) == "Korean" if bot_name else False
    bot_short_name = bot_name[1:] if is_korean_name and len(bot_name) > 2 else None

    # 줄임말 설명
    short_name_desc = f' 혹은 "{bot_short_name}"' if bot_short_name else ''

    # 한글 패턴
    korean_patterns = f'"{bot_name}", "{bot_name}야", "{bot_name}아", "{bot_name}씨", "{bot_name}님"'
    if bot_short_name:
        korean_patterns += f'\n  - "{bot_short_name}", "{bot_short_name}야", "{bot_short_name}아", "{bot_short_name}씨", "{bot_short_name}님"'

    system_prompt = f"""You are an agent that determines whether the user's message is directly calling the target "{bot_name}"{short_name_desc}.

## Core Behavior Rules
<important_actions>
1. If the message calls the target by name to talk or request a task, respond with true.
2. The following patterns indicate a direct call (respond with true):
  Korean patterns:
  - {korean_patterns}
  English patterns:
  - "{bot_name}", "Hey {bot_name}", "Hi {bot_name}", "{bot_name}," (case-insensitive)
  Slack mention patterns (IMPORTANT - these are direct calls):
  - "{bot_name} (@U...)" or "({bot_name}) (@U...)" followed by "님", "씨", etc.
  - Any message containing the target name with a Slack user ID mention (@U...) is a direct call
3. If the target name is mentioned but NOT directly addressed, respond with false.
4. If the target name is not present at all, respond with false.
</important_actions>

## Output Format
<output_format>
Target called: output "true"
Target not called: output "false"
</output_format>"""

    return system_prompt


async def call_bot_call_detector(
    message_text: str,
    bot_name: str = None
) -> bool:
    """
    봇 호출 감지 에이전트를 실행합니다.
    Gemini Flash를 사용하여 비용을 절감합니다.

    Args:
        message_text: 사용자가 보낸 메시지 텍스트
        bot_name: 봇의 이름 (기본값: settings에서 가져옴)

    Returns:
        bool: 봇이 호출되었는지 여부
    """
    settings = get_settings()
    if not bot_name:
        bot_name = settings.BOT_NAME or "MOCO"

    # 빠른 키워드 매칭 (명확한 경우 CLI 호출 없이 처리)
    text_lower = message_text.strip().lower()
    bot_lower = bot_name.lower()
    # 봇 이름이 포함되어 있으면 바로 true
    if bot_lower in text_lower:
        logging.info(f"[BOT_CALL_DETECTOR] Fast keyword match: bot name '{bot_name}' found in message")
        return True
    # 한글 줄임말 체크 (예: "모코" → "코")
    is_korean_name = detect_language(bot_name) == "Korean" if bot_name else False
    if is_korean_name and len(bot_name) > 2:
        short_name = bot_name[1:].lower()
        short_patterns = [short_name, f"{short_name}야", f"{short_name}아", f"{short_name}님"]
        if any(p in text_lower for p in short_patterns):
            logging.info(f"[BOT_CALL_DETECTOR] Fast keyword match: short name found in message")
            return True
    # Slack mention으로 봇을 직접 호출한 경우 (<@BOT_USER_ID>)
    from app.cc_slack_handlers import get_bot_user_id
    _bot_uid = get_bot_user_id()
    if _bot_uid and f"<@{_bot_uid.lower()}>" in text_lower:
        logging.info(f"[BOT_CALL_DETECTOR] Fast keyword match: bot mention <@{_bot_uid}> found")
        return True
    # 봇 이름이 전혀 없으면 바로 false (Slack mention 제외)
    if bot_lower not in text_lower and "@u" not in text_lower:
        logging.info(f"[BOT_CALL_DETECTOR] Fast keyword match: no bot name or mention found")
        return False

    system_prompt = create_system_prompt(bot_name)

    # Claude 사용
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=settings.MODEL_FOR_SIMPLE,
        permission_mode="bypassPermissions",
        allowed_tools=["*"],
        disallowed_tools=[
            "Bash(curl:*)",
            "Bash(rm:*)",
            "Bash(rm -r*)",
            "Bash(rm -rf*)",
            "Read(./.env)",
            "Read(./credential.json)",
            "WebFetch",
        ],
        setting_sources=['project'],
        cwd=os.getcwd()
    )
    options = prepare_options(options)

    try:
        async with RetryableSDKClient(options, max_retries=3, agent_name="BOT_CALL_DETECTOR") as client:
            query = f"""Determine if the following message is directly calling the target "{bot_name}".

Message: {message_text}"""

            await client.query(query)

            try:
                async with asyncio.timeout(30):  # 30초 타임아웃
                    async for message in client.receive_response():
                        if isinstance(message, ResultMessage):
                            result_text = message.result.strip().lower()
                            logging.info(f"[BOT_CALL_DETECTOR] Claude response: {result_text}")
                            return "true" in result_text
            except asyncio.TimeoutError:
                logging.warning("[BOT_CALL_DETECTOR] Timeout after 30s")
                return False
    except Exception as e:
        logging.error(f"[BOT_CALL_DETECTOR] Claude error: {e}")

    return False
