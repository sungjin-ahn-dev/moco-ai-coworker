"""
오케스트레이터 에이전트 (Orchestrator Agent)

설정에 따라 활성화된 MCP를 직접 로드하여 작업을 수행합니다.
Community Skill Marketplace를 통해 사용자 정의 스킬도 지원합니다.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    create_sdk_mcp_server,
    tool,
)
from app.cc_utils.sdk_retry import RetryableSDKClient

from app.cc_tools.slack.slack_tools import get_slack_client
from app.cc_agents.operator.agent import (
    build_mcp_servers_dict,
    save_to_memory,
    create_system_prompt,
)
from app.cc_agents.state_prompt import create_state_prompt
from app.config.settings import get_settings
from app.cc_utils.prompt_helper import prepare_options


# ---------------------------------------------------------------------------
# Orchestrator hang 진단용 보조 로깅
# - CLI 서브프로세스 stderr → ~/.moco/cli_stderr.log
# - receive_response 루프에서 메시지 타입/툴콜/툴결과 트레이스
# 운영 중 hang 발생 시 어디서 멈췄는지 좁히기 위한 임시 진단 로그.
# ---------------------------------------------------------------------------

# 활동성(idle) 타임아웃 — 마지막 SDK 메시지로부터 N초 무응답 시 cancel하고 재시도.
# 절대시간 1200s 하드컷은 무거운 정상 작업까지 죽이므로 idle-timeout 으로 전환.
_IDLE_TIMEOUT_SECS = 300  # 5분 무응답이면 hang 으로 간주

_CLI_STDERR_LOG_PATH = os.path.expanduser("~/.moco/cli_stderr.log")
_cli_stderr_logger = logging.getLogger("orchestrator.cli_stderr")
if not _cli_stderr_logger.handlers:
    try:
        os.makedirs(os.path.dirname(_CLI_STDERR_LOG_PATH), exist_ok=True)
        _handler = logging.FileHandler(_CLI_STDERR_LOG_PATH, encoding="utf-8")
        _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _cli_stderr_logger.addHandler(_handler)
        _cli_stderr_logger.setLevel(logging.INFO)
        _cli_stderr_logger.propagate = False
    except Exception as _e:
        logging.warning(f"[ORCHESTRATOR] CLI stderr logger setup failed: {_e}")


def _make_stderr_callback(channel_id: str):
    def cb(line: str) -> None:
        try:
            _cli_stderr_logger.info(f"[ch={channel_id}] {line.rstrip()}")
        except Exception:
            pass
    return cb


def _trace_message(message) -> None:
    """receive_response 루프에서 받은 SDK 메시지를 한 줄로 요약 로깅."""
    try:
        msg_type = type(message).__name__
        if msg_type == "SystemMessage":
            sub = getattr(message, "subtype", "?")
            logging.info(f"[ORCHESTRATOR_TRACE] SystemMessage subtype={sub}")
            return
        if msg_type == "AssistantMessage":
            blocks = getattr(message, "content", []) or []
            for b in blocks:
                bt = type(b).__name__
                if bt == "TextBlock":
                    logging.info(
                        f"[ORCHESTRATOR_TRACE] AssistantText len={len(getattr(b,'text','') or '')}"
                    )
                elif bt == "ToolUseBlock":
                    name = getattr(b, "name", "?")
                    tid = getattr(b, "id", "?")
                    inp = getattr(b, "input", {}) or {}
                    try:
                        inp_size = len(json.dumps(inp, ensure_ascii=False))
                    except Exception:
                        inp_size = -1
                    logging.info(
                        f"[ORCHESTRATOR_TRACE] ToolUse name={name} id={tid} input_size={inp_size}"
                    )
                elif bt == "ThinkingBlock":
                    logging.info(
                        f"[ORCHESTRATOR_TRACE] Thinking len={len(getattr(b,'thinking','') or '')}"
                    )
                else:
                    logging.info(f"[ORCHESTRATOR_TRACE] AssistantBlock type={bt}")
            return
        if msg_type == "UserMessage":
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for b in content:
                    bt = type(b).__name__
                    if bt == "ToolResultBlock":
                        tid = getattr(b, "tool_use_id", "?")
                        is_err = getattr(b, "is_error", False)
                        c = getattr(b, "content", "")
                        try:
                            if isinstance(c, str):
                                c_size = len(c)
                            else:
                                c_size = len(json.dumps(c, ensure_ascii=False))
                        except Exception:
                            c_size = -1
                        logging.info(
                            f"[ORCHESTRATOR_TRACE] ToolResult tool_use_id={tid} "
                            f"is_error={is_err} content_size={c_size}"
                        )
                    else:
                        logging.info(f"[ORCHESTRATOR_TRACE] UserBlock type={bt}")
            else:
                logging.info(
                    f"[ORCHESTRATOR_TRACE] UserMessage content_type={type(content).__name__}"
                )
            return
        if msg_type == "ResultMessage":
            logging.info("[ORCHESTRATOR_TRACE] ResultMessage (final)")
            return
        logging.info(f"[ORCHESTRATOR_TRACE] Unknown message type={msg_type}")
    except Exception as e:
        logging.warning(f"[ORCHESTRATOR_TRACE] trace failed: {e}")


# ---------------------------------------------------------------------------
# Community Skills MCP 서버 (Google Drive 폴더 → SQLite 동기화된 스킬)
# ---------------------------------------------------------------------------

def create_skills_mcp_server():
    """Community Skill Registry를 MCP 도구로 노출합니다."""

    @tool(
        "list_skills",
        "Google Drive에서 동기화된 Community Skill 목록을 반환합니다. 사용자 요청과 관련된 스킬이 있는지 확인하세요.",
        {"type": "object", "properties": {}, "required": []},
    )
    async def list_skills_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        import json
        try:
            from app.cc_utils.skill_registry import SkillRegistry
            registry = SkillRegistry()
            skills = registry.get_all_active()
            result = [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "description": s["description"],
                    "trigger_keywords": json.loads(s.get("trigger_keywords", "[]")),
                }
                for s in skills
            ]
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except Exception as e:
            logging.error(f"[SKILLS_MCP] list_skills error: {e}")
            return {"content": [{"type": "text", "text": "[]"}]}

    # 프로젝트 레벨 skill은 Opus가 직접 실행 (SDK 콜드스타트 방지)
    _PROJECT_SKILLS = {
        "pptx", "docx", "pdf", "xlsx",
        "it-role-expert", "document-templates",
        "confluence-deep-reader", "designing-surveys",
        "scratch-pad", "web-navigation-strategies",
        "slack-memory-cleanup", "slack-memory-retrieval",
        "slack-memory-store", "slack-proactive-intervention-patterns",
        "email-action-extractor",
    }

    @tool(
        "execute_skill",
        "Community Skill을 실행합니다. skill_id와 수행할 query를 전달하세요.",
        {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "실행할 스킬 ID"},
                "query": {"type": "string", "description": "스킬에 전달할 작업 내용"},
                "context": {"type": "string", "description": "추가 컨텍스트 (선택)"},
            },
            "required": ["skill_id", "query"],
        },
    )
    async def execute_skill_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        import json
        try:
            skill_id = args.get("skill_id", "")
            query = args.get("query", "")
            context = args.get("context", "")

            # 프로젝트 skill은 별도 SDK 없이 직접 실행하도록 안내
            if skill_id in _PROJECT_SKILLS:
                logging.info(f"[SKILLS_MCP] Project skill '{skill_id}' → redirecting to direct execution")
                return {"content": [{"type": "text", "text": json.dumps({
                    "status": "redirect",
                    "summary": f"'{skill_id}'는 프로젝트 skill입니다. execute_skill을 사용하지 말고, '{skill_id}' skill의 지침을 직접 읽고 실행하세요.",
                    "skill_id": skill_id,
                }, ensure_ascii=False)}]}

            from app.cc_agents.skill_executor import call_skill_agent
            result = await call_skill_agent(skill_id, query, context)
            logging.info(f"[SKILLS_MCP] execute_skill '{skill_id}': {str(result)[:100]}")
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except Exception as e:
            logging.error(f"[SKILLS_MCP] execute_skill error: {e}")
            return {"content": [{"type": "text", "text": f'{{"status":"failed","error":"{e}"}}'}]}

    return create_sdk_mcp_server(
        name="skills",
        version="1.0.0",
        tools=[list_skills_tool, execute_skill_tool],
    )


# ---------------------------------------------------------------------------
# 오케스트레이터 메인 함수
# ---------------------------------------------------------------------------

async def call_orchestrator_agent(
    user_query: str,
    slack_data: dict,
    message_data: dict,
    retrieved_memory: str = "",
) -> str:
    """
    오케스트레이터 에이전트를 실행하여 사용자 요청을 처리합니다.

    설정에서 활성화된 MCP를 직접 로드하고 Community Skill도 실행할 수 있습니다.

    Args:
        user_query: 사용자 질의
        slack_data: Slack API 데이터 (채널, 멤버, 메시지 히스토리)
        message_data: 현재 메시지 정보 (user_id, text, channel_id 등)
        retrieved_memory: 검색된 관련 메모리 내용

    Returns:
        str: 최종 응답 텍스트
    """
    state_prompt = create_state_prompt(slack_data, message_data)

    if retrieved_memory and retrieved_memory != "관련된 메모리가 없습니다.":
        state_prompt += (
            f"\n\n## 관련 메모리\n<retrieved_memory>\n{retrieved_memory}\n</retrieved_memory>"
        )

    # operator와 동일한 풍부한 시스템 프롬프트 사용
    system_prompt = create_system_prompt(state_prompt)

    # Community Skills 사용 안내 추가
    skills_guide = """
## Community Skills 사용 원칙
<how_to_use_community_skills>
- **프로젝트 Skill (pptx, docx, pdf, xlsx, document-templates 등)**: `execute_skill`을 사용하지 마세요. 해당 skill의 지침을 직접 읽고 실행하세요. 이 skill들은 이미 프로젝트에 포함되어 있어 직접 접근 가능합니다.
- **Community Skill (Google Drive 동기화)**: `mcp__skills__list_skills`로 목록 확인 후 `mcp__skills__execute_skill`로 실행하세요.
</how_to_use_community_skills>
"""
    system_prompt += skills_guide

    settings = get_settings()

    # 설정에서 활성화된 MCP 로드 + Community Skills MCP 추가
    mcp_servers = build_mcp_servers_dict(settings)
    mcp_servers["skills"] = create_skills_mcp_server()

    _channel_id_for_log = message_data.get("channel_id", "?")
    options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        model=settings.MODEL_FOR_COMPLEX,
        permission_mode="bypassPermissions",
        allowed_tools=["*"],
        disallowed_tools=[
            "Bash(curl:*)",
            "Read(./.env)",
            "Read(./credential.json)",
            "mcp__tableau__get-view-image",
        ],
        setting_sources=["project"],
        cwd=os.getcwd(),
        max_buffer_size=10 * 1024 * 1024,
        stderr=_make_stderr_callback(_channel_id_for_log),
    )
    options = prepare_options(options)

    enhanced_query = user_query

    session_id = None
    final_message = ""
    max_retries = 2

    async with RetryableSDKClient(options, max_retries=3, agent_name="ORCHESTRATOR") as client:
        for attempt in range(max_retries + 1):
            try:
                if session_id:
                    await client.query(enhanced_query, session_id)
                else:
                    await client.query(enhanced_query)

                try:
                    # 활동성 타임아웃: 메시지 도착할 때마다 deadline 을 리스케줄.
                    # 정상 진행 중(메시지 흐름이 있음)이면 무한정 살림.
                    # _IDLE_TIMEOUT_SECS 동안 새 메시지가 안 오면 hang 으로 판단해 cancel.
                    async with asyncio.timeout(_IDLE_TIMEOUT_SECS) as _cm:
                        async for message in client.receive_response():
                            try:
                                _cm.reschedule(
                                    asyncio.get_event_loop().time() + _IDLE_TIMEOUT_SECS
                                )
                            except Exception:
                                pass
                            _trace_message(message)
                            if hasattr(message, "subtype") and message.subtype == "init":
                                session_id = message.data.get("session_id")
                                logging.info(f"[ORCHESTRATOR_AGENT] Session ID: {session_id}")
                            elif hasattr(message, "subtype") and message.subtype == "rate_limit_event":
                                logging.debug(
                                    "[ORCHESTRATOR_AGENT] Rate limit event, CLI retrying automatically..."
                                )
                                continue

                            if type(message) is ResultMessage:
                                if "API Error" in message.result and "413" in message.result:
                                    raise Exception(
                                        f"Context overflow in ResultMessage: {message.result}"
                                    )
                                final_message = message.result
                                logging.info(
                                    f"[ORCHESTRATOR_AGENT] Final message received: "
                                    f"{final_message[:100]}..."
                                )
                except asyncio.TimeoutError:
                    raise Exception(
                        f"Orchestrator idle timeout: no SDK message for {_IDLE_TIMEOUT_SECS}s"
                    )

                if not final_message:
                    final_message = "Unable to generate a response."
                    logging.warning("[ORCHESTRATOR_AGENT] No final message received, using default")

                break  # 성공 시 루프 종료

            except Exception as e:
                error_str = str(e)
                error_msg = error_str.lower()

                is_context_error = any(
                    [
                        "prompt is too long" in error_msg,
                        "context overflow" in error_msg,
                        "413" in error_msg,
                    ]
                )
                is_idle_timeout = "idle timeout" in error_msg

                if is_context_error and attempt < max_retries:
                    logging.warning(
                        f"[ORCHESTRATOR_AGENT] Context overflow (attempt {attempt + 1}/{max_retries}), /compact..."
                    )
                    await client.query("/compact", session_id)
                    async for msg in client.receive_response():
                        if isinstance(msg, ResultMessage):
                            logging.info("[ORCHESTRATOR_AGENT] /compact executed successfully")
                            break
                    continue
                elif is_idle_timeout and attempt < max_retries:
                    # hang 으로 추정 → fresh session 으로 재시도.
                    # session_id 를 비워두면 다음 iteration 의 client.query() 가
                    # 새 conversation 으로 시작.
                    logging.warning(
                        f"[ORCHESTRATOR_AGENT] Idle timeout (attempt {attempt + 1}/{max_retries}), "
                        f"retrying with fresh session..."
                    )
                    session_id = None
                    final_message = ""
                    continue
                else:
                    logging.error(f"[ORCHESTRATOR_AGENT] Error: {e}", exc_info=True)
                    if is_context_error:
                        final_message = (
                            "The context is too large to process. Please start a new conversation."
                        )
                    elif "maximum buffer size" in error_msg:
                        final_message = (
                            "The response data is too large. Please request a smaller scope."
                        )
                    elif not final_message:
                        final_message = "An error occurred while processing the task."

                    if settings.DEBUG_SLACK_MESSAGES_ENABLED:
                        try:
                            slack_client = get_slack_client()
                            channel_id = message_data.get("channel_id")
                            channel_type = (
                                slack_data.get("channel", {}).get("channel_type", "")
                                if slack_data
                                else ""
                            )
                            debug_thread_ts = message_data.get("thread_ts")
                            if channel_type in ["public_channel", "private_channel", "group_dm"]:
                                debug_thread_ts = debug_thread_ts or message_data.get("ts")
                            post_params = {"channel": channel_id, "text": f"⚠️ {final_message}"}
                            if debug_thread_ts:
                                post_params["thread_ts"] = debug_thread_ts
                            if channel_id:
                                await slack_client.chat_postMessage(**post_params)
                        except Exception as slack_error:
                            logging.error(
                                f"[ORCHESTRATOR_AGENT] Failed to send error to Slack: {slack_error}"
                            )

                    break

    # 메모리 저장
    await save_to_memory(user_query, final_message, slack_data, message_data, is_operator=True)

    return final_message
