"""
agent_factory 를 MCP 도구로 노출.

operator / orchestrator 가 사용자 요청 ("에이전트 만들어줘") 을 받았을 때
이 도구를 호출하면 자동 생성 파이프라인이 작동한다.

도구 2개:
  - propose_new_agent: 새 에이전트 생성 요청 (검증 → 승인요청 발송)
  - list_my_agents: 사용자가 만든/사용 가능한 에이전트 목록
"""

import json
import logging
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.cc_agents.agent_factory import propose_agent, registry, candidates_store

logger = logging.getLogger(__name__)


PROPOSE_AGENT_SCHEMA = {
    "type": "object",
    "properties": {
        "agent_id": {
            "type": "string",
            "description": "에이전트 ID — 영문 소문자 시작, 소문자/숫자/_, 2~40자. 예: 'mfds_tracker', 'contract_review_helper'",
        },
        "agent_name": {
            "type": "string",
            "description": "UI 카드 타이틀. 이모지 + 한국어 권장. 예: '🛰️ 식약처 동향 트래커'",
        },
        "description": {
            "type": "string",
            "description": "한 줄 설명. UI 카드 부제목. 예: '매주 식약처 가이드라인 변경 모니터링'",
        },
        "system_prompt": {
            "type": "string",
            "description": "에이전트의 시스템 프롬프트 본문. 페르소나·원칙·응답 포맷·가드레일 포함. 한국어 권장.",
        },
        "model_tier": {
            "type": "string",
            "enum": ["SIMPLE", "MODERATE", "COMPLEX"],
            "description": "모델 티어. SIMPLE=Haiku(빠른 분류), MODERATE=Sonnet(일반 분석), COMPLEX=Opus/Sonnet(복잡 추론). 기본 MODERATE.",
        },
        "allowed_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "도구 화이트리스트. 안전 도구만 가능: Read/Glob/Grep/WebFetch/WebSearch/mcp__time__* 또는 mcp__*__* 패턴. Bash/Write/Edit 금지.",
        },
        "corpus_dir": {
            "type": "string",
            "description": "참조 자료 디렉토리 경로 (없으면 빈 문자열). 예: '/home/user/MOCO_DATA/RA_규제자료'",
        },
        "examples": {
            "type": "array",
            "items": {"type": "string"},
            "description": "모달 예시 질문 3~5개. 사용자가 클릭하면 자동 전송.",
        },
        "created_by": {
            "type": "string",
            "description": "요청자 이메일 (예: 'user2@example.com'). 자동으로 채워주세요.",
        },
    },
    "required": ["agent_id", "agent_name", "description", "system_prompt"],
}


@tool("propose_new_agent", "새 에이전트를 자동 생성 시스템에 제안합니다. 검증 통과 시 승인자에게 Slack DM 발송 또는 자동 publish.", PROPOSE_AGENT_SCHEMA)
async def propose_new_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = await propose_agent(
            agent_id=args["agent_id"],
            agent_name=args["agent_name"],
            description=args["description"],
            system_prompt=args["system_prompt"],
            model_tier=args.get("model_tier", "MODERATE"),
            allowed_tools=args.get("allowed_tools") or None,
            corpus_dir=args.get("corpus_dir", ""),
            examples=args.get("examples") or [],
            created_by=args.get("created_by", "unknown"),
            skip_dry_run=False,  # 실제 dry-run 거침
        )
        payload = {
            "ok": result.ok,
            "agent_id": result.agent_id,
            "stage": result.stage,
            "message": result.message,
            "auto_approved": result.auto_approved,
        }
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}
    except Exception as e:
        logger.exception("[propose_new_agent] 예외")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"ok": False, "error": str(e)[:500]}, ensure_ascii=False),
                }
            ]
        }


@tool("list_my_agents", "현재 publish 된 에이전트 목록과 pending(승인 대기) 에이전트를 반환합니다.", {"type": "object", "properties": {}})
async def list_my_agents(args: Dict[str, Any]) -> Dict[str, Any]:
    approved = [
        {
            "agent_id": e["agent_id"],
            "agent_name": e["agent_name"],
            "description": e["description"],
            "usage_count": e.get("usage_count", 0),
            "created_by": e.get("created_by"),
        }
        for e in registry.list_active()
    ]
    pending = [
        {"agent_id": e["agent_id"], "agent_name": e["agent_name"], "created_by": e.get("created_by")}
        for e in registry.list_all(status="pending")
    ]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"approved": approved, "pending": pending},
                    ensure_ascii=False,
                    indent=2,
                ),
            }
        ]
    }


PROPOSE_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": "자동 감지된 후보의 ID (예: 'cand_20260609_a1b2'). 사용자 메시지에 포함된 [AGENT_CANDIDATE:<id>] 토큰에서 추출.",
        },
        "created_by": {
            "type": "string",
            "description": "요청자 이메일. 자동으로 채워주세요.",
        },
    },
    "required": ["candidate_id"],
}


@tool(
    "propose_candidate_agent",
    "자동 감지된 에이전트 후보 (candidate_id) 를 propose_agent 파이프라인에 전달합니다. 사용자가 자동 제안을 승인한 경우에만 호출하세요.",
    PROPOSE_CANDIDATE_SCHEMA,
)
async def propose_candidate_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    candidate_id = args["candidate_id"]
    candidate = candidates_store.get(candidate_id)
    if candidate is None:
        return {
            "content": [{"type": "text", "text": json.dumps(
                {"ok": False, "error": f"candidate_id '{candidate_id}' not found"},
                ensure_ascii=False)}]
        }
    if candidate.get("status") != "pending":
        return {
            "content": [{"type": "text", "text": json.dumps(
                {"ok": False, "error": f"candidate already consumed: status={candidate.get('status')}"},
                ensure_ascii=False)}]
        }

    spec = candidate["spec"]
    try:
        result = await propose_agent(
            agent_id=spec["agent_id"],
            agent_name=spec["agent_name"],
            description=spec["description"],
            system_prompt=spec["system_prompt"],
            model_tier=spec.get("model_tier", "MODERATE"),
            allowed_tools=spec.get("allowed_tools") or None,
            corpus_dir=spec.get("corpus_dir", ""),
            examples=spec.get("examples") or [],
            created_by=args.get("created_by") or spec.get("created_by", "auto_detect"),
            skip_dry_run=False,
        )
        candidates_store.mark_status(
            candidate_id,
            "accepted" if result.ok else "rejected",
            propose_stage=result.stage,
            propose_message=result.message,
        )
        payload = {
            "ok": result.ok,
            "candidate_id": candidate_id,
            "agent_id": result.agent_id,
            "stage": result.stage,
            "message": result.message,
            "auto_approved": result.auto_approved,
        }
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}
    except Exception as e:
        logger.exception("[propose_candidate_agent] 예외")
        candidates_store.mark_status(candidate_id, "rejected", propose_stage="exception", propose_message=str(e)[:200])
        return {"content": [{"type": "text", "text": json.dumps(
            {"ok": False, "error": str(e)[:500]}, ensure_ascii=False)}]}


def create_agent_factory_mcp_server():
    """operator/orchestrator 의 build_mcp_servers_dict() 에 등록할 MCP 서버."""
    return create_sdk_mcp_server(
        name="agent_factory",
        version="1.0.0",
        tools=[propose_new_agent, list_my_agents, propose_candidate_agent],
    )
