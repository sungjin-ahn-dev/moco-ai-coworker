"""
Memory Auto-Save Heuristic
LLM 호출 없이 규칙 기반으로 메모리 저장 여부를 판단.
스몰토크/짧은 질답은 스킵, 중요한 작업만 저장.
"""

import re
import logging

logger = logging.getLogger(__name__)

# 저장 스킵 패턴 (스몰토크, 인사, 감사, 확인)
SKIP_PATTERNS = [
    r"^(안녕|하이|헬로|hi|hello|hey)\s*[!.?]*$",
    r"^(네|넵|응|ㅇㅇ|ㅇ|ok|okay|yes|yep|yeah)\s*[!.?]*$",
    r"^(고마워|감사|감사합니다|땡큐|thanks|thank you|thx)\s*[!.?]*$",
    r"^(알겠어|알겠습니다|확인|오키|ㅋㅋ|ㅎㅎ|ㅎ|ㅋ|좋아|굿|good|great|nice|cool)\s*[!.?]*$",
    r"^(아니|아뇨|놉|no|nope)\s*[!.?]*$",
    r"^(잠만|잠깐|wait|sec)\s*[!.?]*$",
    r"^(뭐|뭐야|뭐라고|what|huh)\s*[!.?\?]*$",
    r"^(ㅎㅎㅎ|ㅋㅋㅋ|ㅎ|ㅋ|lol|haha)\s*$",
    r"^.{0,5}$",  # 5자 이하
]

# 즉시 저장 패턴 (명시적 저장 요청)
SAVE_PATTERNS = [
    r"기억해|저장해|기록해|메모해|remember|save|record|memo",
    r"잊지\s*마|까먹지\s*마|don.?t forget",
    r"나중에\s*참고|참고로|for reference|fyi",
    r"이거\s*중요|important",
]

# 내용 기반 저장 시그널 (업무 관련 키워드)
DURABLE_SIGNALS = [
    r"결정|결론|합의|agreed|decided|concluded",
    r"일정|스케줄|마감|deadline|schedule|due",
    r"회의|미팅|meeting",
    r"처방|리스팅|매출|계약",
    r"버그|이슈|오류|에러|bug|issue|error",
    r"변경|업데이트|수정|update|change|modify",
    r"요청|부탁|해줘|해주세요",
]


def should_save_to_memory(
    user_query: str,
    response: str = "",
    tool_events: int = 0,
    is_operator: bool = False,
) -> str:
    """
    메모리 저장 여부를 규칙 기반으로 판단.

    Returns:
        "skip" — 저장 불필요 (LLM 호출 안 함)
        "save" — 즉시 저장 (LLM 호출 안 함)
        "ask_llm" — LLM에게 판단 위임 (기존 방식)
    """
    query_lower = user_query.strip().lower()
    combined = f"{user_query}\n{response}"
    combined_len = len(combined)

    # 1. 스킵 패턴 매칭 (스몰토크)
    for pattern in SKIP_PATTERNS:
        if re.match(pattern, query_lower, re.IGNORECASE):
            logger.info(f"[MEMORY_CLASSIFIER] SKIP (small talk): {query_lower[:30]}")
            return "skip"

    # 2. 짧은 대화 (질문+응답 합쳐서 100자 미만, 도구 사용 없음)
    if combined_len < 100 and tool_events == 0 and not is_operator:
        logger.info(f"[MEMORY_CLASSIFIER] SKIP (short, no tools): {combined_len}chars")
        return "skip"

    # 3. 즉시 저장 패턴 (명시적 저장 요청)
    for pattern in SAVE_PATTERNS:
        if re.search(pattern, query_lower, re.IGNORECASE):
            logger.info(f"[MEMORY_CLASSIFIER] SAVE (explicit): {query_lower[:30]}")
            return "save"

    # 4. Operator 사용 (복잡한 작업) → 저장
    if is_operator:
        logger.info(f"[MEMORY_CLASSIFIER] ASK_LLM (operator task)")
        return "ask_llm"

    # 5. 도구 사용 → 저장 가치 있음
    if tool_events >= 2:
        logger.info(f"[MEMORY_CLASSIFIER] SAVE (tools used: {tool_events})")
        return "save"

    # 6. 업무 키워드 + 충분한 길이
    if combined_len >= 150:
        for pattern in DURABLE_SIGNALS:
            if re.search(pattern, combined, re.IGNORECASE):
                logger.info(f"[MEMORY_CLASSIFIER] ASK_LLM (durable signal)")
                return "ask_llm"

    # 7. 긴 응답 (300자 이상) → LLM 판단
    if len(response) >= 300:
        logger.info(f"[MEMORY_CLASSIFIER] ASK_LLM (long response: {len(response)}chars)")
        return "ask_llm"

    # 8. 기본: 짧고 도구 안 쓴 대화는 스킵
    if combined_len < 200 and tool_events == 0:
        logger.info(f"[MEMORY_CLASSIFIER] SKIP (default short)")
        return "skip"

    # 9. 나머지는 LLM에게 위임
    logger.info(f"[MEMORY_CLASSIFIER] ASK_LLM (default)")
    return "ask_llm"
