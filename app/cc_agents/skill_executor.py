import json
import logging
import os
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from app.cc_utils.sdk_retry import RetryableSDKClient
from app.cc_utils.skill_registry import SkillRegistry
from app.cc_agents.sub_agents.base import make_result, parse_result
from app.config.settings import get_settings
from app.cc_utils.mcp_helper import local_mcp
from app.cc_utils.prompt_helper import prepare_options

# 허용된 MCP 이름 화이트리스트 (보안)
ALLOWED_MCP_NAMES = {
    "slack", "atlassian", "jira", "confluence", "gitlab", "github",
    "google_drive", "google_docs", "gmail", "google_calendar",
    "ms365", "deepl", "context7", "arxiv", "clickup",
    "tableau", "files", "time", "airbnb", "x",
}

def build_mcp_for_skill(required_mcps: list) -> dict:
    """스킬에 필요한 MCP만 로드 (화이트리스트 기반)"""
    settings = get_settings()
    mcp_servers = {
        "time": local_mcp("@mcpcentral/mcp-time"),
    }

    for mcp_name in required_mcps:
        if mcp_name not in ALLOWED_MCP_NAMES:
            logging.warning(f"[SKILL_EXECUTOR] MCP '{mcp_name}' not in whitelist, skipping")
            continue

        if mcp_name == "slack":
            from app.cc_tools.slack.slack_tools import create_slack_mcp_server
            mcp_servers["slack"] = create_slack_mcp_server()
        elif mcp_name == "atlassian" and settings.ATLASSIAN_ENABLED:
            mcp_servers["atlassian"] = local_mcp("mcp-remote", use_cache=True, extra_args=["https://mcp.atlassian.com/v1/sse"])
        elif mcp_name == "google_drive" and settings.GOOGLE_DRIVE_ENABLED:
            from app.cc_tools.google_drive.google_drive_tools import create_google_drive_mcp_server
            mcp_servers["google_drive"] = create_google_drive_mcp_server()
        elif mcp_name == "gmail" and settings.GMAIL_ENABLED:
            from app.cc_tools.gmail.gmail_tools import create_gmail_mcp_server
            mcp_servers["gmail"] = create_gmail_mcp_server()
        elif mcp_name == "gitlab" and settings.GITLAB_ENABLED:
            mcp_servers["gitlab"] = local_mcp("@zereight/mcp-gitlab", use_cache=True, env={
                "GITLAB_PERSONAL_ACCESS_TOKEN": settings.GITLAB_PERSONAL_ACCESS_TOKEN, "GITLAB_API_URL": settings.GITLAB_API_URL, "GITLAB_READ_ONLY_MODE": "false",
            })
        elif mcp_name == "context7":
            mcp_servers["context7"] = local_mcp("@upstash/context7-mcp")
        elif mcp_name == "files":
            from app.cc_tools.files.files_tools import create_files_mcp_server
            mcp_servers["files"] = create_files_mcp_server()
        elif mcp_name == "clickup" and settings.CLICKUP_ENABLED:
            from app.cc_tools.clickup.clickup_tools import create_clickup_mcp_server
            mcp_servers["clickup"] = create_clickup_mcp_server()

    return mcp_servers


async def call_skill_agent(skill_id: str, query: str, context: str = "") -> dict:
    """
    Skill Registry에서 스킬을 불러와 동적으로 Sub-agent 생성 및 실행

    Returns: RESULT_SCHEMA 형태의 dict
    """
    registry = SkillRegistry()
    skill_data = registry.get(skill_id)

    if not skill_data:
        return make_result("failed", f"스킬을 찾을 수 없습니다: {skill_id}", error=f"skill_not_found:{skill_id}")

    settings = get_settings()
    required_mcps = json.loads(skill_data.get("required_mcps", "[]"))
    mcp_servers = build_mcp_for_skill(required_mcps)

    system_prompt = f"""{skill_data['system_prompt']}

반드시 다음 JSON 형식으로만 최종 응답하세요:
{{
    "status": "success" | "partial" | "failed",
    "summary": "한 줄 요약",
    "data": {{}},
    "artifacts": [],
    "next_suggestions": [],
    "error": null
}}

컨텍스트: {context}
"""

    options = ClaudeAgentOptions(
        mcp_servers=mcp_servers,
        system_prompt=system_prompt,
        model=skill_data.get("model", settings.MODEL_FOR_MODERATE),
        permission_mode="bypassPermissions",
        allowed_tools=["*"],
        disallowed_tools=["Bash(curl:*)", "Bash(rm:*)", "Bash(rm -rf*)", "Read(./.env)", "Read(./credential.json)"],
        setting_sources=["project"],
        cwd=os.getcwd(),
    )
    options = prepare_options(options)

    try:
        async with RetryableSDKClient(options, max_retries=3, agent_name="SKILL_EXECUTOR") as client:
            await client.query(f"{query}\n\n컨텍스트: {context}")
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    result = parse_result(msg.result)
                    logging.info(f"[SKILL_EXECUTOR] Skill '{skill_id}' completed: {result.get('summary', '')[:100]}")
                    return result
    except Exception as e:
        logging.error(f"[SKILL_EXECUTOR] Error running skill '{skill_id}': {e}")
        return make_result("failed", f"스킬 실행 실패: {str(e)}", error=str(e))

    return make_result("failed", "스킬 응답 없음", error="no_response")


def get_matching_skills(query: str) -> list:
    """쿼리와 매칭되는 스킬 목록 반환 (트리거 키워드 기반)"""
    registry = SkillRegistry()
    all_skills = registry.get_all_active()
    matching = []

    query_lower = query.lower()
    for skill in all_skills:
        keywords = json.loads(skill.get("trigger_keywords", "[]"))
        if any(kw.lower() in query_lower for kw in keywords):
            matching.append(skill)

    return matching
