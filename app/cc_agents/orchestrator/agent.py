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
from app.cc_tools.sub_agents.server import create_sub_agents_mcp_server
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

    # 프로젝트 레벨 skill은 오케스트레이터(Sonnet)가 직접 실행 (SDK 콜드스타트 방지)
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

async def _pmrv_query_collect(client, prompt, session_id, on_message):
    """PMRV 단계 하나를 실행하고 최종 텍스트와 session_id 를 반환한다."""
    if session_id:
        await client.query(prompt, session_id)
    else:
        await client.query(prompt)
    text = ""
    async with asyncio.timeout(_IDLE_TIMEOUT_SECS) as _cm:
        async for message in client.receive_response():
            try:
                _cm.reschedule(asyncio.get_event_loop().time() + _IDLE_TIMEOUT_SECS)
            except Exception:
                pass
            _trace_message(message)
            if on_message is not None:
                try:
                    on_message(message)
                except Exception:
                    pass
            if hasattr(message, "subtype") and message.subtype == "init":
                session_id = message.data.get("session_id")
            if type(message) is ResultMessage and message.result:
                text = message.result
    return text, session_id


async def _pmrv_map_dag(user_query, subtasks, message_data):
    """서브태스크 DAG 를 TaskExecutor.execute_dag 로 실행한다.

    독립 서브태스크는 병렬로, 선행(deps) 결과는 공유 TaskWorkspace 를 통해 후행
    서브에이전트에 전달된다. 결과를 원래 서브태스크 순서대로 텍스트 리스트로 반환한다
    (Reduce 입력).
    """
    from app.cc_agents.task_executor import TaskExecutor

    tasks = [
        {
            "id": st["id"],
            "agent": st["agent"],
            "query": f"{st['goal']}\n\n[원 요청]\n{user_query}",
            "description": st["goal"],
            "deps": st.get("deps", []),
        }
        for st in subtasks
    ]
    results = await TaskExecutor(get_slack_client(), message_data or {}).execute_dag(
        tasks, context=user_query)
    out = []
    for st in subtasks:
        r = results.get(st["id"]) or {}
        out.append(r.get("summary") or str(r.get("data") or ""))
    return out


async def _run_pmrv_pipeline(client, user_query, session_id, on_message, message_data):
    """복잡 요청을 Plan-Map-Reduce-Verify-Replan 으로 처리하고 최종 텍스트와 session_id 를 반환한다.

    Map 은 서브태스크 DAG 를 TaskExecutor.execute_dag 로 실행한다 — 독립 노드는 병렬,
    선행(deps) 결과는 공유 TaskWorkspace 로 후행 서브에이전트에 전달된다. DAG 실행이
    실패하거나 서브태스크가 하나뿐이면 같은 세션 순차 질의로 폴백한다. Verify 는 원 요청
    대조 test-time verification 이며 누락이 남으면 그 부분만 재계획(Replan)한다.
    """
    from app.cc_utils import pmrv

    plan_text, session_id = await _pmrv_query_collect(
        client, pmrv.build_plan_prompt(user_query), session_id, on_message)
    plan = pmrv.parse_plan(plan_text)
    subs = plan["subtasks"]

    # Map — DAG 실행: 독립 노드는 병렬, 선행 결과는 공유 TaskWorkspace 로 후행에 전달.
    #        서브태스크가 하나뿐이거나 DAG 실행이 실패하면 같은 세션 순차 질의로 폴백.
    results = []
    if len(subs) > 1:
        try:
            results = await _pmrv_map_dag(user_query, subs, message_data)
        except Exception as _pe:
            logging.warning(f"[PMRV] DAG map failed, sequential fallback: {_pe}")
            results = []
    if not results:
        for st in subs:
            sub_prompt = f"[서브태스크]\n{st['goal']}\n\n[원 요청]\n{user_query}"
            text, session_id = await _pmrv_query_collect(client, sub_prompt, session_id, on_message)
            results.append(text)

    reduced, session_id = await _pmrv_query_collect(
        client, pmrv.build_reduce_prompt(user_query, results), session_id, on_message)

    if pmrv.should_verify("completed", reduced, user_query):
        vtext, session_id = await _pmrv_query_collect(
            client, pmrv.build_verify_prompt(user_query, reduced), session_id, on_message)
        v = pmrv.parse_verify(vtext)
        reduced = v["revised"] or reduced
        replans = 0
        while not v["complete"] and replans < pmrv.MAX_REPLAN:
            rtext, session_id = await _pmrv_query_collect(
                client, pmrv.build_replan_prompt(user_query, reduced, v["gaps"]), session_id, on_message)
            reduced = rtext or reduced
            vtext, session_id = await _pmrv_query_collect(
                client, pmrv.build_verify_prompt(user_query, reduced), session_id, on_message)
            v = pmrv.parse_verify(vtext)
            reduced = v["revised"] or reduced
            replans += 1

    return reduced, session_id


async def call_orchestrator_agent(
    user_query: str,
    slack_data: dict,
    message_data: dict,
    retrieved_memory: str = "",
    on_message=None,   # [eval] 각 SDK 메시지 관찰 훅(도구 궤적 수집 등). 하위호환: 기본 None
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

    # 도메인 서브에이전트 위임 안내 (기본 경로: call_sub_agent 순차 위임)
    delegate_guide = """
## 도메인 서브에이전트 위임
<how_to_delegate>
리서치·문서·코드·응대 등 도메인 전문성이 필요한 작업은 한 컨텍스트에서 직접 처리하지 말고
`mcp__agents__call_sub_agent`로 해당 도메인 전문가에게 위임하세요:
- **research** 웹 검색·논문·정보 수집  ·  **communication** Slack·이메일 전달
- **code** 코드 리뷰·PR·GitHub/GitLab  ·  **pm** Jira·ClickUp·스프린트
- **document** 문서 작성·Drive·번역     ·  **data** 데이터 분석·시각화
- **web** 브라우저 자동화·스크래핑
각 서브에이전트는 자기 도메인 도구만 화이트리스트로 갖고 있어 도구 선택 정확도가 높습니다.
여러 도메인이 얽히면 순차로 위임하고, 각 결과(표준 스키마 status/summary/data/artifacts/error)를
종합해 원 요청 기준으로 최종 응답을 만드세요. `mcp__agents__list_sub_agents`로 목록을 볼 수 있습니다.
</how_to_delegate>
"""
    system_prompt += delegate_guide

    settings = get_settings()

    # 설정에서 활성화된 MCP 로드 + Community Skills MCP 추가
    mcp_servers = build_mcp_servers_dict(settings)
    mcp_servers["skills"] = create_skills_mcp_server()
    # 도메인 서브에이전트 위임 서버 — call_sub_agent / list_sub_agents (동시 실행 세마포어 20 내장)
    mcp_servers["agents"] = create_sub_agents_mcp_server()

    _channel_id_for_log = message_data.get("channel_id", "?")

    # [tool-RAG] opt-in: 관련 MCP 서버 네임스페이스만 노출(기본 off = 전량, 무회귀).
    # 221개 도구 스키마 전량 노출 → attention/오선택/토큰 부담을 의도 기반으로 축소.
    _allowed_tools = ["*"]
    try:
        if getattr(settings, "TOOL_RAG_ENABLED", False):
            from app.cc_utils.tool_selector import select_namespaces, to_allowed_tools
            _avail = {f"mcp__{k}__*" for k in mcp_servers}
            _allowed_tools = to_allowed_tools(
                select_namespaces(user_query, available=_avail), extra=["TodoWrite"])
            logging.info(f"[TOOL_RAG] allowed_tools → {_allowed_tools}")
    except Exception as _e:
        _allowed_tools = ["*"]

    options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        model=settings.MODEL_FOR_COMPLEX,
        permission_mode="bypassPermissions",
        allowed_tools=_allowed_tools,
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

    # [cost-cap] opt-in: 무한 도구루프/폭주 비용 방지. 기본 0=off. SDK에 필드 없으면 무시(안전).
    _max_turns = getattr(settings, "ORCH_MAX_TURNS", 0)
    if _max_turns and hasattr(options, "max_turns"):
        options.max_turns = _max_turns
        logging.info(f"[COST_CAP] max_turns={_max_turns}")

    enhanced_query = user_query

    session_id = None
    final_message = ""
    max_retries = 2

    async with RetryableSDKClient(options, max_retries=3, agent_name="ORCHESTRATOR") as client:
        for attempt in range(max_retries + 1):
            try:
                # [pmrv] opt-in: 복잡 요청은 Plan-Map-Reduce-Verify-Replan 으로 처리 (기본 off·실패 시 reactive 폴백)
                if getattr(settings, "PMRV_ENABLED", False):
                    try:
                        from app.cc_utils import pmrv as _pmrv
                        if _pmrv.classify_complexity(user_query) == "complex":
                            final_message, session_id = await _run_pmrv_pipeline(
                                client, user_query, session_id, on_message, message_data)
                            if final_message:
                                break
                    except Exception as _px:
                        logging.warning(f"[PMRV] failed, reactive fallback: {_px}")
                        final_message = ""

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
                            if on_message is not None:
                                try:
                                    on_message(message)
                                except Exception:
                                    pass
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

                # [reflexion] opt-in: 빈/기본 응답이면 직전 실패를 반성으로 감싸 재시도(기본 off·무회귀)
                if getattr(settings, "REFLEXION_ENABLED", False) and attempt < max_retries:
                    try:
                        from app.cc_utils.reflexion import should_reflect, build_reflection_prompt
                        if should_reflect("completed", final_message):
                            enhanced_query = build_reflection_prompt(user_query, prev_summary=final_message)
                            session_id = None
                            final_message = ""
                            logging.info(f"[REFLEXION] weak response → reflect & retry (attempt {attempt + 1})")
                            continue
                    except Exception as _rx:
                        logging.debug(f"[REFLEXION] skipped: {_rx}")

                # [spec-verify] opt-in: 다중요구 요청이면 응답 제출 전 요구사항 자기검증·보완 (기본 off·무회귀)
                #   근거: 오프라인 eval에서 지배 실패모드가 spec_violation(요청 미준수).
                if getattr(settings, "SPEC_VERIFY_ENABLED", False):
                    try:
                        from app.cc_utils.spec_verify import should_verify, build_verification_prompt
                        if should_verify("completed", final_message, user_query):
                            await client.query(
                                build_verification_prompt(user_query, final_message), session_id)
                            _sv_result = ""
                            async for _svm in client.receive_response():
                                if on_message is not None:
                                    try:
                                        on_message(_svm)
                                    except Exception:
                                        pass
                                if type(_svm) is ResultMessage and _svm.result:
                                    _sv_result = _svm.result
                            if _sv_result:
                                final_message = _sv_result
                                logging.info("[SPEC_VERIFY] response revised for requirement coverage")
                    except Exception as _sv:
                        logging.debug(f"[SPEC_VERIFY] skipped: {_sv}")

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
