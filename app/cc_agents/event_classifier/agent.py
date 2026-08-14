"""
캘린더 이벤트 분류 에이전트 (Event Classifier Agent)

Google Calendar에서 가져온 이벤트의 제목·설명을 working_day 카테고리로 분류한다.
MOCO의 표준 ClaudeAgentSDK 패턴을 따른다.
"""

import asyncio
import json
import logging
import os
import re
from typing import Tuple

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)
from app.cc_utils.sdk_retry import RetryableSDKClient

from app.config.settings import get_settings
from app.cc_utils.prompt_helper import prepare_options


VALID_TYPES = {"vacation", "conference", "training", "sales_activity", "other"}


def create_system_prompt() -> str:
    return """You are a calendar event classifier for a CRM working-day system.

Classify each calendar event into EXACTLY one category:

- vacation: 휴가·연차·반차·휴무·월차·PTO·off·annual leave
- conference: 학회·학술대회·세미나·심포지엄·포럼·conference·symposium
- training: 교육·연수·워크샵·워크숍·training·workshop
- sales_activity: 병원 방문·KOL 미팅·고객 디너·제품 발표·외근·약국 방문·영업 미팅 등 대외 영업 활동
- other: 위 4개에 해당하지 않는 사내 회의·1:1·개인 일정·일반 미팅 등

Also detect half-day status from the title or description (look for "반차", "half day", "half-day", "오전반차", "오후반차").

## Output Format
Output a SINGLE LINE of JSON with exactly two fields:
{"event_type":"vacation|conference|training|sales_activity|other","is_half_day":true|false}

Do NOT include any other text, explanation, or markdown. JSON only."""


def _parse_result(text: str) -> Tuple[str, bool]:
    """JSON 응답에서 (event_type, is_half_day) 추출. 실패 시 ('other', False)."""
    if not text:
        return "other", False

    match = re.search(r'\{[^{}]*"event_type"[^{}]*\}', text, re.DOTALL)
    if not match:
        return "other", False

    try:
        data = json.loads(match.group(0))
    except Exception:
        return "other", False

    et = data.get("event_type", "other")
    if et not in VALID_TYPES:
        et = "other"
    return et, bool(data.get("is_half_day", False))


async def call_event_classifier(title: str, description: str = "") -> Tuple[str, bool]:
    """캘린더 이벤트 제목·설명으로 분류.

    Args:
        title: 이벤트 제목
        description: 이벤트 설명 (선택)

    Returns:
        (event_type, is_half_day) — 실패 시 ('other', False)
    """
    settings = get_settings()

    options = ClaudeAgentOptions(
        system_prompt=create_system_prompt(),
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
            "WebSearch",
        ],
        setting_sources=['project'],
        cwd=os.getcwd(),
    )
    options = prepare_options(options)

    user_query = f"""Title: {title or '(none)'}
Description: {(description or '(none)')[:500]}"""

    try:
        async with RetryableSDKClient(options, max_retries=2, agent_name="EVENT_CLASSIFIER") as client:
            await client.query(user_query)
            try:
                async with asyncio.timeout(20):
                    async for message in client.receive_response():
                        if isinstance(message, ResultMessage):
                            et, is_half = _parse_result(message.result)
                            logging.info(
                                f"[EVENT_CLASSIFIER] title={title!r} → {et} half={is_half}"
                            )
                            return et, is_half
            except asyncio.TimeoutError:
                logging.warning(f"[EVENT_CLASSIFIER] Timeout — title={title!r}")
    except Exception as e:
        logging.error(f"[EVENT_CLASSIFIER] Error: {e} — title={title!r}")

    return "other", False
