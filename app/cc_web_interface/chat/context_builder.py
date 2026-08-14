"""
웹 챗용 컨텍스트 빌더.

Google 로그인한 사용자의 email/name을 받아:
- Slack user_id로 매핑 (memories 경로 조회용)
- 메모리 retriever 호출 → 관련 메모리 취합
- 대화 히스토리를 사용자 쿼리에 합성
"""

import logging
from typing import Optional, Dict, Any, List

from app.cc_agents.memory_retriever.agent import call_memory_retriever

logger = logging.getLogger(__name__)


# 간단한 in-process 캐시 (email → slack_user_id)
_email_to_user_id_cache: Dict[str, Optional[str]] = {}


async def resolve_slack_user_id(email: str) -> Optional[str]:
    """이메일로 Slack user_id 조회 (메모리 경로 매칭용). 실패해도 무시."""
    if not email:
        return None
    if email in _email_to_user_id_cache:
        return _email_to_user_id_cache[email]

    try:
        from app.cc_tools.slack.slack_tools import get_slack_client
        slack_client = get_slack_client()
        resp = await slack_client.users_lookupByEmail(email=email)
        if resp.get("ok"):
            user_id = resp.get("user", {}).get("id")
            _email_to_user_id_cache[email] = user_id
            return user_id
    except Exception as e:
        logger.warning(f"[WEB_CHAT] Slack user lookup failed for {email}: {e}")

    _email_to_user_id_cache[email] = None
    return None


def build_message_data(
    user: Dict[str, Any],
    slack_user_id: Optional[str],
    user_text: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """operator/메모리 retriever에 넘길 message_data 모양 생성.
    Slack 핸들러가 만드는 구조와 호환되도록 같은 키를 사용하되 채널 관련 필드는 없음.

    attachments: 웹에서 업로드된 파일 메타데이터 리스트.
        각 항목: {"name", "mimetype", "size", "file_path"} — 파일은 이미 로컬 디스크에 있음.
    """
    data = {
        "user_id": slack_user_id or f"web:{user.get('email', 'unknown')}",
        "user_name": user.get("name", ""),
        "user_email": user.get("email", ""),
        "user_text": user_text,
        "channel_id": None,
        "thread_ts": None,
        "source": "web",
    }
    if attachments:
        # Slack 핸들러와 동일한 키 이름("files") 사용 — state_prompt가 그대로 인식
        data["files"] = attachments
    return data


async def retrieve_memory(user_text: str, message_data: Dict[str, Any]) -> str:
    """메모리 retriever 호출. 실패 시 빈 문자열."""
    try:
        return await call_memory_retriever(
            search_query=user_text,
            slack_data=None,
            message_data=message_data,
        )
    except Exception as e:
        logger.warning(f"[WEB_CHAT] Memory retrieval failed: {e}")
        return ""


def format_history(history: List[Dict[str, str]]) -> str:
    """이전 대화 히스토리를 프롬프트용 문자열로 변환."""
    if not history:
        return ""
    lines = []
    for msg in history:
        role = "사용자" if msg["role"] == "user" else "MOCO"
        lines.append(f"[{role}] {msg['content']}")
    return "\n".join(lines)


def compose_query_with_history(user_text: str, history: List[Dict[str, str]]) -> str:
    """현재 사용자 입력에 직전 대화 히스토리를 합성해 operator에 전달."""
    history_str = format_history(history)
    if not history_str:
        return user_text
    return f"""## 이전 대화 (참고용)
{history_str}

## 현재 사용자 입력
{user_text}"""
