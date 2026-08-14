"""
표준 에이전트 템플릿 + 5-슬롯 채우기.

생성된 .py 는 `app/cc_agents/generated/<agent_id>/agent.py` 에 저장된다.
템플릿이 고정 보일러플레이트라서 LLM 이 슬롯만 채워도 구문 오류 불가능.

채우는 슬롯 (5개):
- agent_id        : 영문 소문자/숫자/_ (모듈명·라우팅 키)
- agent_name      : 사람이 읽는 이름 (UI 카드 타이틀)
- description     : 한 줄 설명 (UI 카드 description)
- system_prompt   : 시스템 프롬프트 본문
- model_tier      : SIMPLE | MODERATE | COMPLEX
- allowed_tools   : 도구 allow-list
- corpus_dir      : 참조 자료 경로 (없으면 빈 문자열)
"""

import re
from pathlib import Path
from typing import List, Optional


# 안전한 도구 화이트리스트 (generated 에이전트는 이 외 사용 불가)
SAFE_TOOL_WHITELIST = {
    "Read", "Glob", "Grep",
    "WebFetch", "WebSearch",
    "mcp__time__*",
}

# 금지 도구 — 메타시스템 변조 차단
FORBIDDEN_TOOLS = {"Bash", "Write", "Edit", "NotebookEdit"}


# id 검증: 영문 소문자, 숫자, 언더스코어만, 2~40자
_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class TemplateError(ValueError):
    """슬롯 검증 실패."""


def validate_agent_id(agent_id: str) -> None:
    if not _AGENT_ID_RE.match(agent_id):
        raise TemplateError(
            f"agent_id 형식 오류: '{agent_id}'. 영문 소문자 시작, 소문자/숫자/_ 만, 2~40자."
        )


def validate_allowed_tools(tools: List[str]) -> None:
    for t in tools:
        if t in FORBIDDEN_TOOLS:
            raise TemplateError(f"금지된 도구: '{t}' (Bash/Write/Edit 등 메타시스템 변조 도구는 사용 불가)")
        # SAFE_TOOL_WHITELIST 의 항목 또는 mcp__*__ 접두사만 허용
        if t in SAFE_TOOL_WHITELIST:
            continue
        if t.startswith("mcp__") and "__" in t[5:]:
            continue
        raise TemplateError(
            f"허용되지 않은 도구: '{t}'. "
            f"허용: {sorted(SAFE_TOOL_WHITELIST)} 또는 mcp__*__* 패턴."
        )


def validate_model_tier(tier: str) -> None:
    if tier not in ("SIMPLE", "MODERATE", "COMPLEX"):
        raise TemplateError(f"model_tier 는 SIMPLE/MODERATE/COMPLEX 중 하나여야 함: '{tier}'")


_TEMPLATE = '''"""
{agent_name} — 자동 생성된 에이전트.

{description}

생성일: {created_at}
생성자: {created_by}
모델 티어: {model_tier}

⚠️ 이 파일은 agent_factory 가 템플릿으로 생성한 코드입니다.
수동 편집해도 되지만, registry.json 의 version 도 함께 갱신하세요.
"""

import logging
import os
from typing import AsyncIterator, Dict, Any, Optional

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

from app.cc_agents.state_prompt import create_state_prompt
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.sdk_retry import RetryableSDKClient
from app.cc_utils.prompt_helper import prepare_options
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# 자동 생성 메타데이터 (registry.json 과 동기화 유지)
AGENT_ID = {agent_id_repr}
AGENT_NAME = {agent_name_repr}
DESCRIPTION = {description_repr}
CORPUS_DIR = {corpus_dir_repr}
MODEL_TIER = {model_tier_repr}
ALLOWED_TOOLS = {allowed_tools_repr}


def _build_system_prompt(user_name: str, state_prompt: str, retrieved_memory: str) -> str:
    memory_section = ""
    if retrieved_memory and retrieved_memory != "관련된 메모리가 없습니다.":
        memory_section = f"\\n\\n## 관련 메모리\\n<retrieved_memory>\\n{{retrieved_memory}}\\n</retrieved_memory>"

    corpus_section = ""
    if CORPUS_DIR:
        corpus_section = f"\\n\\n## 참조 자료\\n경로: `{{CORPUS_DIR}}`\\n관련 PDF/문서를 Read 도구로 직접 열람하세요."

    return f"""{{_SYSTEM_PROMPT_BODY}}

지금은 MOCO 웹 챗에서 {{user_name}}님과 1:1 대화 중입니다. 응답은 **이 채팅창의 마크다운 텍스트**로 작성하세요.

{{state_prompt}}{{corpus_section}}{{memory_section}}

## 응답 가이드
- 한국어로 응답
- 마크다운 활용 (제목·리스트·표·코드블록)
- 도구 호출 결과의 출처와 링크를 답변에 포함
- 정보가 부족하면 솔직히 말하고 추가 정보 요청

## 가드레일
- FILESYSTEM_BASE_DIR 외부 파일 접근 금지
- 다른 사용자의 메모리/DM/이메일 접근 금지
- 결정 사항이 큰 영역(법령·계약·인허가·재무)에서는 단정 어조 피하고 권고로 응답
"""


_SYSTEM_PROMPT_BODY = {system_prompt_repr}


async def stream_for_web(
    user_query: str,
    message_data: Dict[str, Any],
    retrieved_memory: str = "",
) -> AsyncIterator[Dict[str, Any]]:
    """웹 챗 SSE 스트림 (operator/atticus 와 동일 스키마)."""
    settings = get_settings()
    user_name = message_data.get("user_name", "사용자")

    state_prompt = create_state_prompt(slack_data=None, message_data=message_data)
    system_prompt = _build_system_prompt(user_name, state_prompt, retrieved_memory)

    mcp_servers = {{
        "time": local_mcp("@mcpcentral/mcp-time"),
    }}

    model_map = {{
        "SIMPLE": settings.MODEL_FOR_SIMPLE,
        "MODERATE": settings.MODEL_FOR_MODERATE,
        "COMPLEX": settings.MODEL_FOR_COMPLEX,
    }}
    model = model_map.get(MODEL_TIER, settings.MODEL_FOR_MODERATE)

    options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        model=model,
        permission_mode="bypassPermissions",
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=[
            "Bash", "Edit", "Write",
            "Read(./.env)", "Read(./credential.json)",
        ],
        setting_sources=["project"],
        cwd=os.getcwd(),
        max_buffer_size=10 * 1024 * 1024,
    )
    options = prepare_options(options)

    final_text = ""
    try:
        async with RetryableSDKClient(options, max_retries=3, agent_name=AGENT_ID.upper()) as client:
            await client.query(user_query)
            async for message in client.receive_response():
                content = getattr(message, "content", None)
                if content:
                    for block in content:
                        btype = type(block).__name__
                        if btype == "TextBlock":
                            text = getattr(block, "text", "") or ""
                            if text:
                                yield {{"type": "text", "delta": text}}
                        elif btype == "ToolUseBlock":
                            yield {{"type": "tool_use", "name": getattr(block, "name", "")}}
                        elif btype == "ToolResultBlock":
                            yield {{"type": "tool_result"}}
                if isinstance(message, ResultMessage):
                    final_text = message.result or ""
                    if "API Error" in final_text and "413" in final_text:
                        yield {{"type": "error", "message": "대화가 너무 길어졌어요. 새 대화를 시작해주세요."}}
                        return

        if not final_text:
            final_text = "응답을 생성하지 못했어요. 다시 시도해주세요."
        yield {{"type": "done", "final": final_text}}

    except Exception as e:
        logger.error(f"[{{AGENT_ID.upper()}}] error: {{e}}", exc_info=True)
        yield {{"type": "error", "message": f"처리 중 오류가 발생했어요: {{str(e)[:200]}}"}}
'''


def fill_template(
    *,
    agent_id: str,
    agent_name: str,
    description: str,
    system_prompt: str,
    model_tier: str,
    allowed_tools: List[str],
    corpus_dir: Optional[str] = None,
    created_at: str = "",
    created_by: str = "",
) -> str:
    """5개 슬롯을 채워서 완성된 agent.py 소스 반환.

    repr() 로 안전 이스케이프 — 따옴표/줄바꿈 모두 안전.
    """
    validate_agent_id(agent_id)
    validate_model_tier(model_tier)
    validate_allowed_tools(allowed_tools)

    return _TEMPLATE.format(
        agent_id=agent_id,
        agent_name=agent_name,
        description=description,
        created_at=created_at or "—",
        created_by=created_by or "—",
        model_tier=model_tier,
        # repr() 로 안전 직렬화 — 따옴표·줄바꿈·유니코드 처리
        agent_id_repr=repr(agent_id),
        agent_name_repr=repr(agent_name),
        description_repr=repr(description),
        corpus_dir_repr=repr(corpus_dir or ""),
        model_tier_repr=repr(model_tier),
        allowed_tools_repr=repr(allowed_tools),
        system_prompt_repr=repr(system_prompt),
    )


def write_agent_files(target_dir: Path, agent_source: str) -> None:
    """target_dir 에 __init__.py + agent.py 작성."""
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "__init__.py").write_text(
        "from .agent import stream_for_web\n__all__ = ['stream_for_web']\n",
        encoding="utf-8",
    )
    (target_dir / "agent.py").write_text(agent_source, encoding="utf-8")
