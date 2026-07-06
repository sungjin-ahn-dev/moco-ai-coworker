"""MOCO MCP 도구 — acme 멤버가 Claude(Code/Desktop)에서 호출.

CRM 페이지·AICC(제품A) 제외, MOCO 봇이 Slack에서 처리하던 모든 기능을 자연어로 노출.

주력:
    moco_ask(message)    — Operator wrapper (모든 MCP 도구 사용 가능, 90% 커버)

보조 (자주 쓰는 작업 직접):
    moco_chat            — Simple Chat (Haiku, 가벼운 답변)
    moco_search_memory   — 메모리 검색
    moco_save_memory     — 메모리 저장
    moco_list_email_tasks
    moco_list_jira_tasks
    moco_list_pending_answers
    moco_schedule_message
    moco_status
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .context import make_message_data, make_slack_data
from .mcp_app import get_current_user, get_current_user_meta

logger = logging.getLogger(__name__)


def register(mcp) -> None:
    """FastMCP 인스턴스에 도구 함수 등록."""

    @mcp.tool()
    async def moco_ask(message: str) -> str:
        """[자동 호출 규칙] 사용자가 'moco', '모코', 'MOCO'로 호명하거나, acme 회사 업무 관련 요청(일정·회의·이메일·Slack·Confluence·Jira·CRM·병원·처방·계획·메모리·태스크 등)을 하면 즉시 이 도구를 호출하세요. 원본 메시지를 그대로 message 인자에 전달합니다.

        Trigger 예시:
            "moco야 일정 알려줘"          → moco_ask("일정 알려줘")
            "안녕 모코"                    → moco_ask("안녕")
            "어제 회의록 정리해줘"         → moco_ask("어제 회의록 정리해줘")
            "미답신 메일 알려줘"           → moco_ask("미답신 24시간 넘은 메일 알려줘")
            "내 이번주 일정 확인"          → moco_ask("이번주 일정 확인")
            "Confluence에서 OKR 검색"     → moco_ask("Confluence에서 OKR 검색해서 요약")
            "다음주 월요일 9시에 #general에 알림" → moco_ask("...")

        이 도구는 MOCO Operator(Opus)가 Slack/Outlook/Atlassian/Memory/Scheduler 등 모든 MCP 도구를 통합 사용해 처리합니다. Claude의 자체 캘린더/이메일/메모리 도구는 사용하지 말고 이 도구를 우선 호출하세요 — acme 사용자의 통합 컨텍스트(메모리, 권한, 채널 멤버십)가 MOCO 측에 있습니다.
        """
        user = get_current_user()
        return await _run_operator(user, message)

    @mcp.tool()
    async def moco_chat(message: str) -> str:
        """MOCO Simple Chat (빠른 답변). 도구 사용 없는 가벼운 대화.

        주의: 1차 구현에서는 moco_ask로 폴백합니다 (Slack 응답 캡처 wrapper 미완성).
        """
        user = get_current_user()
        return await _run_operator(user, message)  # 1차: ask로 폴백

    @mcp.tool()
    async def moco_search_memory(query: str) -> str:
        """MOCO 로컬 메모리에서 query와 관련된 내용 검색.

        FILESYSTEM_BASE_DIR/memories/ 안의 채널/프로젝트/사용자/결정 메모리를 인덱스 기반으로 조회.
        """
        user = get_current_user()
        return await _run_memory_retriever(user, query)

    @mcp.tool()
    async def moco_save_memory(content: str, category: str = "general") -> str:
        """MOCO 메모리에 새 정보 저장.

        category: general | channels | projects | users | decisions
        Memory queue(1 worker)에 위임되어 순차 저장됩니다.
        """
        user = get_current_user()
        return await _run_memory_manager(user, content, category)

    @mcp.tool()
    async def moco_list_email_tasks(status: str = "pending") -> str:
        """이메일에서 추출된 태스크 조회. status: pending | completed | all"""
        user = get_current_user()
        return await _list_email_tasks(user, status)

    @mcp.tool()
    async def moco_list_jira_tasks(status: str = "pending") -> str:
        """Jira에서 추출된 todo 태스크 조회. status: pending | completed | all"""
        user = get_current_user()
        return await _list_jira_tasks(user, status)

    @mcp.tool()
    async def moco_list_pending_answers() -> str:
        """답변을 기다리는 질문 목록 (waiting_answer DB)."""
        user = get_current_user()
        return await _list_pending_answers(user)

    @mcp.tool()
    async def moco_schedule_message(channel: str, message: str, when: str) -> str:
        """Slack 채널에 메시지 예약 발송.

        channel: Slack channel ID (예: C01ABC...)
        message: 발송할 메시지 본문
        when: ISO 8601 시각 (예: "2026-05-10T09:00:00+09:00")
        """
        user = get_current_user()
        return await _schedule_message(user, channel, message, when)

    @mcp.tool()
    async def moco_status() -> str:
        """MOCO 시스템 상태 (활성 체커, 메모리 디렉토리, 모델 설정 등)."""
        return await _status()


# ─────────────────────── 내부 구현 ───────────────────────


async def _run_operator(user: str, message: str) -> str:
    """Operator wrapper.

    기존 call_operator_agent는 Slack으로 응답을 직접 쏘는 구조라 None 반환.
    MCP에선 응답 텍스트를 받아야 하므로 ClaudeSDKClient를 직접 띄우고
    `mcp__slack__answer`/`send_message`를 비활성화 + system_prompt에
    "MCP 호출이므로 텍스트로 응답" 지침을 추가해서 ResultMessage.result에서 회수.
    """
    try:
        from claude_agent_sdk import (  # type: ignore
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
        )

        from app.cc_agents.operator.agent import (
            build_mcp_servers_dict,
            create_state_prompt,
            create_system_prompt,
            prepare_options,
        )
        from app.config.settings import get_settings
    except Exception as e:
        logger.exception("[MCP] Operator 의존 모듈 import 실패")
        return f"❌ Operator 호출 준비 실패: {e}"

    settings = get_settings()
    meta = get_current_user_meta()
    slack_user_id = meta.get("slack_user_id", "")
    email = meta.get("email", "")
    slack_data = make_slack_data(user, slack_user_id=slack_user_id, email=email)
    message_data = make_message_data(user, message, slack_user_id=slack_user_id, email=email)

    state_prompt = create_state_prompt(slack_data, message_data)
    state_prompt += (
        "\n\n## MCP 호출 모드\n"
        "이 요청은 Slack이 아니라 MCP를 통해 외부 Claude 클라이언트에서 직접 호출됐습니다.\n"
        "응답을 mcp__slack__answer / mcp__slack__send_message 로 보내지 말고 "
        "최종 답변을 일반 텍스트로 직접 출력하세요. 도구는 자유롭게 사용 가능합니다."
    )
    system_prompt = create_system_prompt(state_prompt)

    try:
        mcp_servers = build_mcp_servers_dict(settings)
    except Exception as e:
        logger.exception("[MCP] mcp_servers dict 빌드 실패")
        return f"❌ MCP 서버 구성 실패: {e}"

    options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        model=settings.MODEL_FOR_COMPLEX,
        permission_mode="bypassPermissions",
        allowed_tools=["*"],
        disallowed_tools=[
            "mcp__slack__answer",
            "mcp__slack__send_message",
            "Bash(curl:*)",
            "Read(./.env)",
            "Read(./credential.json)",
        ],
        setting_sources=["project"],
        cwd=os.getcwd(),
        max_buffer_size=10 * 1024 * 1024,
    )
    options = prepare_options(options)

    final_text = ""
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(message)
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    final_text = msg.result or ""
                    break
    except Exception as e:
        logger.exception("[MCP] Operator 실행 오류")
        return f"❌ MOCO 처리 중 오류: {e}"

    return final_text or "(빈 응답 — Operator가 텍스트 답변을 만들지 않았을 수 있음)"


async def _run_memory_retriever(user: str, query: str) -> str:
    """기존 call_memory_retriever 호출."""
    try:
        from app.cc_agents.memory_retriever.agent import call_memory_retriever
    except Exception as e:
        return f"❌ memory_retriever import 실패: {e}"
    meta = get_current_user_meta()
    slack_data = make_slack_data(user, slack_user_id=meta.get("slack_user_id", ""), email=meta.get("email", ""))
    message_data = make_message_data(user, query, slack_user_id=meta.get("slack_user_id", ""), email=meta.get("email", ""))
    try:
        result = await call_memory_retriever(query, slack_data, message_data)
        return str(result) if result is not None else "(메모리 없음)"
    except TypeError:
        # 시그니처 불일치 가능성 — 한 인자만 받는 형태도 시도
        try:
            result = await call_memory_retriever(query)  # type: ignore[arg-type]
            return str(result) if result is not None else "(메모리 없음)"
        except Exception as e:
            return f"❌ memory_retriever 호출 실패: {e}"
    except Exception as e:
        return f"❌ memory_retriever 호출 실패: {e}"


async def _run_memory_manager(user: str, content: str, category: str) -> str:
    """Memory Manager에 저장 요청. 실제 저장은 memory queue 워커가 처리."""
    try:
        from app.cc_agents.memory_manager.agent import call_memory_manager
    except Exception as e:
        return f"❌ memory_manager import 실패: {e}"

    meta = get_current_user_meta()
    slack_data = make_slack_data(user, slack_user_id=meta.get("slack_user_id", ""), email=meta.get("email", ""))
    message_data = make_message_data(user, content, slack_user_id=meta.get("slack_user_id", ""), email=meta.get("email", ""))
    try:
        await call_memory_manager(content, slack_data, message_data, "")
        return f"✅ 메모리 저장 요청됨 (category={category}). 큐 워커가 처리합니다."
    except TypeError as e:
        return (
            f"⚠️ memory_manager 시그니처 불일치: {e}\n"
            "→ 1차 골격이라 호출 시그니처 미세조정 필요. CHANGELOG 참고."
        )
    except Exception as e:
        return f"❌ memory_manager 호출 실패: {e}"


async def _list_email_tasks(user: str, status: str) -> str:
    return await _call_cc_tool(
        module="app.cc_tools.email_tasks.email_tasks_tools",
        candidates=[
            "email_tasks_get_pending_tasks",
            "email_tasks_list",
            "email_tasks_query",
        ],
        args={"user_slack_id": user, "status": status},
        label="email_tasks",
    )


async def _list_jira_tasks(user: str, status: str) -> str:
    return await _call_cc_tool(
        module="app.cc_tools.jira_tasks.jira_tasks_tools",
        candidates=[
            "jira_tasks_get_pending_tasks",
            "jira_tasks_list",
            "jira_tasks_query",
        ],
        args={"user_slack_id": user, "status": status},
        label="jira_tasks",
    )


async def _list_pending_answers(user: str) -> str:
    return await _call_cc_tool(
        module="app.cc_tools.waiting_answer.waiting_answer_tools",
        candidates=[
            "waiting_answer_get_pending",
            "waiting_answer_list",
            "waiting_answer_query",
        ],
        args={"user_slack_id": user},
        label="waiting_answer",
    )


async def _schedule_message(user: str, channel: str, message: str, when: str) -> str:
    return await _call_cc_tool(
        module="app.cc_tools.scheduler.scheduler_tools",
        candidates=[
            "scheduler_create_schedule",
            "scheduler_create",
            "scheduler_add",
        ],
        args={
            "user_slack_id": user,
            "channel_id": channel,
            "message": message,
            "scheduled_time": when,
        },
        label="scheduler",
    )


async def _call_cc_tool(module: str, candidates: list[str], args: dict[str, Any], label: str) -> str:
    """cc_tools 함수의 시그니처를 정확히 모를 때 후보 이름들을 차례로 시도.

    1차 골격이라 함수명·인자명이 실제와 다를 수 있음. 동작 안 하면 시그니처 맞춰
    수정 필요 (CHANGELOG 참고).
    """
    try:
        import importlib

        mod = importlib.import_module(module)
    except Exception as e:
        return f"❌ {label} 모듈 import 실패: {e}"

    last_err: str = ""
    for name in candidates:
        fn = getattr(mod, name, None)
        if fn is None:
            continue
        try:
            result = await fn(args)
            # claude_agent_sdk @tool 스타일은 {"content":[{"type":"text","text":...}]}
            if isinstance(result, dict) and "content" in result:
                texts = []
                for block in result["content"]:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
                return "\n".join(texts) or json.dumps(result, ensure_ascii=False)
            return str(result)
        except Exception as e:
            last_err = f"{name}: {e}"
            continue
    return f"⚠️ {label} 호출 실패 (1차 골격, 시그니처 조정 필요). 마지막 에러: {last_err}"


async def _status() -> str:
    try:
        from app.config.settings import get_settings

        s = get_settings()
        info = {
            "filesystem_base": s.FILESYSTEM_BASE_DIR,
            "model": {
                "simple": s.MODEL_FOR_SIMPLE,
                "moderate": s.MODEL_FOR_MODERATE,
                "complex": s.MODEL_FOR_COMPLEX,
            },
            "checkers": {
                "outlook": s.OUTLOOK_CHECK_ENABLED,
                "confluence": s.CONFLUENCE_CHECK_ENABLED,
                "jira": s.JIRA_CHECK_ENABLED,
            },
            "proactive_suggester": s.DYNAMIC_SUGGESTER_ENABLED,
            "web_interface": s.WEB_INTERFACE_ENABLED,
            "mcp": {
                "enabled": s.MCP_ENABLED,
                "path": s.MCP_PATH,
                "token_file": s.MCP_TOKEN_FILE,
            },
        }
        return json.dumps(info, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ status 조회 실패: {e}"
