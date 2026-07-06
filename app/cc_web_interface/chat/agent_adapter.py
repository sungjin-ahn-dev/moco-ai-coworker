"""
웹 챗용 Operator 어댑터.

operator/agent.py의 build_mcp_servers_dict / build_tool_usage_rules는 재활용하되:
- 시스템 프롬프트는 웹용으로 따로 작성 (Slack write 강제 X)
- Slack write 도구는 disallowed_tools로 차단
- ResultMessage / Text 청크 / 도구 호출 이벤트를 dict로 스트리밍

operator agent.py 자체는 일절 수정하지 않음 → Slack MOCO에 0 영향.
"""

import logging
import os
from typing import AsyncIterator, Dict, Any, Optional

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

from app.cc_agents.operator.agent import build_mcp_servers_dict, build_tool_usage_rules
from app.cc_agents.state_prompt import create_state_prompt
from app.cc_utils.sdk_retry import RetryableSDKClient
from app.cc_utils.prompt_helper import prepare_options
from app.config.settings import get_settings

logger = logging.getLogger(__name__)


# Slack에 외부 메시지를 보낼 수 있는 도구는 모두 차단.
# (읽기 도구는 허용 — "Boyd가 채널에서 뭐랬어?" 같은 요청 처리)
_SLACK_WRITE_TOOLS_BLOCKED = [
    "mcp__slack__answer",
    "mcp__slack__answer_with_emoji",
    "mcp__slack__upload_file",
    "mcp__slack__forward_message",
    "mcp__slack__delete_message",
    "mcp__slack__schedule_message",
]


def _create_web_system_prompt(user_name: str, state_prompt: str, retrieved_memory: str) -> str:
    """웹 챗 전용 system prompt — Slack write 강제 없음."""
    settings = get_settings()
    bot_name = settings.BOT_NAME or "MOCO"
    bot_role = settings.BOT_ROLE or ""

    tool_usage_rules = build_tool_usage_rules(settings)

    role_section = f"\n\n## 회사에서의 역할\n<bot_role>\n{bot_role}\n</bot_role>" if bot_role else ""

    memory_section = ""
    if retrieved_memory and retrieved_memory != "관련된 메모리가 없습니다.":
        memory_section = f"\n\n## 관련 메모리\n<retrieved_memory>\n{retrieved_memory}\n</retrieved_memory>"

    return f"""당신은 웹 챗 인터페이스에서 동료들과 대화하는 가상 상주 직원 {bot_name}님 입니다.

# 기본 지침
동료의 요청을 정확하고 효율적으로 처리합니다. 응답은 **이 채팅창의 텍스트로 직접 작성**하세요. Slack 채널에 메시지를 게시하지 마세요 (현재 사용자는 웹으로 접속한 {user_name}님 한 명뿐입니다).
{role_section}

{state_prompt}{memory_section}

## 핵심 행동 원칙
<important_actions>
1. 답변은 이 채팅창에 일반 텍스트(또는 마크다운)로 작성하세요. `mcp__slack__answer` 같은 Slack 게시 도구를 호출할 필요가 없으며, 호출하더라도 실행되지 않습니다.
2. 정보가 필요하면 메모리, Google Drive, Gmail, Calendar, Web Search 등을 적극 활용하세요. Slack 메시지 검색(`mcp__slack__get_channel_history`, `mcp__slack__search_messages` 등)도 읽기 용도로 활용 가능합니다.
3. 파일 작업 경로:
   - FILESYSTEM_BASE_DIR/files/web/{user_name}/ 아래에 임시 파일 생성
   - 사용자에게 파일을 전달할 때는 파일 경로와 함께 마크다운으로 안내
   - **사용자 첨부 파일**: state_data의 current_message.files 에 사용자가 첨부한 파일 목록이 있습니다. 각 항목의 `file_path` 는 이미 서버 로컬 디스크에 다운로드되어 있으므로 별도 다운로드 없이 `Read`, `mcp__pdf__*`, `mcp__xlsx__*`, `mcp__docx__*`, `mcp__pptx__*` 등 적절한 도구로 바로 읽고 분석하세요. mimetype 을 보고 도구를 선택하세요 (예: application/pdf → pdf 스킬, image/* → Read 로 비전 분석).
4. 사용자가 "기억해줘", "저장해줘" 등을 요청해도 긍정적으로 응답하세요. 메모리 저장은 시스템이 자동으로 처리합니다.
5. 작업 완료 시 사용한 도구, 출처/링크, 결과 요약을 응답에 포함하세요.
</important_actions>

## 도구 사용 제약 (웹 챗 전용)
<web_chat_constraints>
- ❌ Slack 채널/DM에 메시지 게시 금지 (`mcp__slack__answer`, `mcp__slack__forward_message`, `mcp__slack__upload_file` 등)
- ❌ Slack 메시지 삭제/예약 금지
- ✅ Slack 메시지 읽기/검색은 허용 (`get_channel_history`, `search_messages`, `get_thread_replies`, `get_usergroup_members`)
- ✅ 그 외 모든 MCP 도구(Gmail, Drive, Calendar, CRM, Scheduler, Web Search 등) 정상 사용
</web_chat_constraints>

## 자동 에이전트 생성 (agent_factory)
<agent_factory_guide>
사용자가 "X 에이전트 만들어줘" / "Y 도메인에 특화된 에이전트 필요해" / "이런 게 있으면 좋겠는데" 같은 요청을 하면 `mcp__agent_factory__propose_new_agent` 도구를 사용하세요.

**언제 사용:**
- 사용자가 명시적으로 새 에이전트 생성 요청 ("법무 자문 에이전트 하나 더 만들어줘")
- 같은 도메인 질문이 반복되어 "이런 거 매번 정리해주는 에이전트 있으면 좋겠다" 흐름
- 사용자가 SKILL 이 아니라 별도 페르소나·도구·코퍼스 가진 에이전트가 필요함이 명확할 때

**언제 사용하지 말 것:**
- 단순한 정보 검색이나 한 번의 작업 (기존 도구로 처리)
- SKILL.md 로 충분한 절차적 지식 (스킬은 운영자가 별도 관리)
- 이미 비슷한 에이전트가 있음 (`mcp__agent_factory__list_my_agents` 로 먼저 확인)

**호출 전 사용자에게 확인:**
1. 에이전트 이름 / 설명을 제안하고 사용자 OK 받기
2. 어떤 도메인 자료를 참조할지 (corpus_dir) 확인
3. 어떤 도구가 필요한지 결정 (기본: Read, Glob, Grep, WebFetch, WebSearch, mcp__time__*)
4. 응답 포맷·가드레일 명시 (Atticus 시스템 프롬프트가 좋은 참고)

**호출 후:**
- ok=True 이면 사용자에게 "✅ 에이전트 생성 요청됨. 승인 후 모달에 카드 등장" 안내
- 자동 승인 모드(approver 미설정)면 즉시 사용 가능함을 알림
- 실패(ok=False) 면 stage 와 message 를 보고 사용자에게 조정 제안

**도구 권한 제약 안내:**
- Bash, Write, Edit 는 generated 에이전트에 부여 불가 (메타시스템 보호)
- 위험 도구가 필요하다면 "그건 사람이 PR 로 추가해야 합니다" 라고 안내
</agent_factory_guide>

{tool_usage_rules}

## 응답 가이드
<response_guide>
1. 사용자의 메시지 언어(한국어/영어)에 맞춰 응답하세요.
2. 마크다운을 활용해 가독성을 높이세요 (제목, 리스트, 코드 블록, 표).
3. AI/봇임을 드러내는 표현을 피하세요.
4. 도구 호출 결과에 포함된 출처와 링크를 상세히 포함하세요.
5. 정보가 부족하면 솔직히 그렇게 답하고 추가 정보를 요청하세요.
</response_guide>

## 가드레일
<guardrails>
- FILESYSTEM_BASE_DIR 외부 파일/디렉토리 접근 금지
- 다른 사용자의 메모리/DM/이메일/개인 드라이브에 접근 금지 (요청자 본인 데이터만)
- Google Drive/Gmail/Calendar는 모든 호출 시 `slack_user_id` 파라미터에 현재 사용자의 user_id 전달
</guardrails>
"""


async def stream_operator_for_web(
    user_query: str,
    message_data: Dict[str, Any],
    retrieved_memory: str = "",
) -> AsyncIterator[Dict[str, Any]]:
    """
    웹 챗 전용 operator 실행. 이벤트를 dict로 yield:
    - {"type": "text", "delta": str}     — 어시스턴트 텍스트 청크
    - {"type": "tool_use", "name": str}  — 도구 호출 시작
    - {"type": "tool_result"}            — 도구 결과 수신
    - {"type": "done", "final": str}     — 최종 응답
    - {"type": "error", "message": str}  — 오류
    """
    settings = get_settings()
    user_name = message_data.get("user_name", "사용자")

    state_prompt = create_state_prompt(slack_data=None, message_data=message_data)
    system_prompt = _create_web_system_prompt(user_name, state_prompt, retrieved_memory)
    mcp_servers = build_mcp_servers_dict(settings)

    disallowed = [
        "Bash(curl:*)",
        "Read(./.env)",
        "Read(./credential.json)",
        "mcp__tableau__get-view-image",
    ] + _SLACK_WRITE_TOOLS_BLOCKED

    options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        model=settings.MODEL_FOR_COMPLEX,
        permission_mode="bypassPermissions",
        allowed_tools=["*"],
        disallowed_tools=disallowed,
        setting_sources=["project"],
        cwd=os.getcwd(),
        max_buffer_size=10 * 1024 * 1024,
    )
    options = prepare_options(options)

    final_text = ""
    session_id: Optional[str] = None

    try:
        async with RetryableSDKClient(options, max_retries=3, agent_name="WEB_CHAT") as client:
            await client.query(user_query)

            async for message in client.receive_response():
                if hasattr(message, "subtype") and message.subtype == "init":
                    session_id = message.data.get("session_id") if hasattr(message, "data") else None
                    if session_id:
                        logger.info(f"[WEB_CHAT] Session: {session_id}")

                # 텍스트 / 도구 호출 청크 yield
                content = getattr(message, "content", None)
                if content:
                    for block in content:
                        btype = type(block).__name__
                        if btype == "TextBlock":
                            text = getattr(block, "text", "") or ""
                            if text:
                                yield {"type": "text", "delta": text}
                        elif btype == "ToolUseBlock":
                            tool_name = getattr(block, "name", "")
                            yield {"type": "tool_use", "name": tool_name}
                        elif btype == "ToolResultBlock":
                            yield {"type": "tool_result"}

                if isinstance(message, ResultMessage):
                    final_text = message.result or ""
                    if "API Error" in final_text and "413" in final_text:
                        yield {"type": "error", "message": "대화가 너무 길어졌어요. 새 대화를 시작해주세요."}
                        return

        if not final_text:
            final_text = "응답을 생성하지 못했어요. 다시 시도해주세요."

        yield {"type": "done", "final": final_text}

    except Exception as e:
        logger.error(f"[WEB_CHAT] Operator error: {e}", exc_info=True)
        yield {"type": "error", "message": f"처리 중 오류가 발생했어요: {str(e)[:200]}"}
