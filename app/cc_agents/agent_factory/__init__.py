"""
Agent Factory — MOCO 자동 에이전트 생성 시스템 (Phase 1).

핵심 흐름:
  사용자 요청 → propose_agent() → 검증 → registry pending →
  approval.send_approval_request() → 승인 → approval.publish() → 즉시 사용 가능

사용 예시 (operator 또는 직접):

    from app.cc_agents.agent_factory import propose_agent

    spec = {
        "agent_id": "mfds_tracker",
        "agent_name": "🛰️ 식약처 동향 트래커",
        "description": "매주 식약처 가이드라인 변경 모니터링 + 우리 회사 제품 영향 분석",
        "system_prompt": "당신은 ...",
        "model_tier": "MODERATE",
        "allowed_tools": ["Read", "Glob", "Grep", "WebFetch", "WebSearch", "mcp__time__*"],
        "corpus_dir": "/home/user/MOCO_DATA/RA_규제자료",
        "examples": ["이번 주 변경된 가이드라인 알려줘", ...],
        "created_by": "user2@example.com",
    }
    result = await propose_agent(**spec)
    # result.ok 이면 pending 상태로 등록되고 승인 DM 발송됨

Phase 2/3 hooks:
- 자동 감지: proactive_dynamic_suggester 가 propose_agent 호출
- 사용량 추적: registry.record_usage() 를 routes 스트림에서 호출
- archive: lifecycle.py 의 archive_unused_agents() 를 스케줄러에 등록
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from app.cc_agents.agent_factory import (
    approval,
    installer,
    registry,
    template,
    validator,
)

logger = logging.getLogger(__name__)


@dataclass
class ProposeResult:
    ok: bool
    agent_id: str
    stage: str  # 어디까지 진행됐는지
    message: str
    auto_approved: bool = False


async def propose_agent(
    *,
    agent_id: str,
    agent_name: str,
    description: str,
    system_prompt: str,
    model_tier: str = "MODERATE",
    allowed_tools: Optional[List[str]] = None,
    corpus_dir: str = "",
    examples: Optional[List[str]] = None,
    created_by: str = "unknown",
    skip_dry_run: bool = False,
) -> ProposeResult:
    """
    새 에이전트 생성 파이프라인 (6단계).

    1. 슬롯 검증 + 템플릿 채우기
    2. /tmp 임시 저장
    3. py_compile + 격리 import + dry_run
    4. generated/ 원자적 이동
    5. registry 에 pending 등록 + 승인 요청 발송
    6. (auto_approve 모드면 즉시 publish)
    """
    if allowed_tools is None:
        allowed_tools = ["Read", "Glob", "Grep", "WebFetch", "WebSearch", "mcp__time__*"]

    from app.config.settings import get_settings
    settings = get_settings()
    if not settings.AGENT_FACTORY_ENABLED:
        return ProposeResult(False, agent_id, "disabled", "AGENT_FACTORY_ENABLED=False")

    # 중복 ID 차단
    if registry.get(agent_id) is not None:
        return ProposeResult(
            False, agent_id, "duplicate",
            f"agent_id '{agent_id}' 이미 존재. 다른 이름 사용."
        )

    try:
        # 1) 템플릿 채우기
        from app.cc_agents.agent_factory.registry import _now_iso
        agent_source = template.fill_template(
            agent_id=agent_id,
            agent_name=agent_name,
            description=description,
            system_prompt=system_prompt,
            model_tier=model_tier,
            allowed_tools=allowed_tools,
            corpus_dir=corpus_dir or None,
            created_at=_now_iso(),
            created_by=created_by,
        )
    except template.TemplateError as e:
        return ProposeResult(False, agent_id, "template", f"템플릿 검증 실패: {e}")

    # 2) /tmp 에 stage
    try:
        stage_dir = installer.stage_to_temp(agent_id=agent_id, agent_source=agent_source)
    except Exception as e:
        return ProposeResult(False, agent_id, "stage", f"임시 저장 실패: {e}")

    # 3) 검증
    val = await validator.validate_agent_dir(stage_dir, skip_dry_run=skip_dry_run)
    if not val.ok:
        return ProposeResult(False, agent_id, val.stage, f"검증 실패: {val.message}")

    # 4) generated/ 로 promote
    try:
        installer.promote_to_generated(stage_dir, agent_id)
    except Exception as e:
        return ProposeResult(False, agent_id, "promote", f"이동 실패: {e}")

    # 5) registry pending
    registry.create_pending(
        agent_id=agent_id,
        agent_name=agent_name,
        description=description,
        system_prompt=system_prompt,
        model_tier=model_tier,
        allowed_tools=allowed_tools,
        corpus_dir=corpus_dir,
        created_by=created_by,
        examples=examples,
        approver_slack_id=settings.AGENT_APPROVER_SLACK_ID,
    )

    # 6) 승인 요청 (또는 auto-approve)
    if not settings.AGENT_APPROVER_SLACK_ID:
        await approval.auto_approve(agent_id, reason="no_approver_configured")
        return ProposeResult(True, agent_id, "auto_approved",
                             f"승인자 미설정 → 자동 publish 완료. 웹 챗 카드는 1분 내 자동 등장 (열린 페이지는 새로고침 시 즉시).",
                             auto_approved=True)

    ts = await approval.send_approval_request(agent_id)
    if ts is None:
        return ProposeResult(True, agent_id, "pending_no_dm",
                             "Slack DM 발송 실패 — pending 상태 유지. 수동 publish 가능.")

    return ProposeResult(True, agent_id, "pending",
                         f"Slack DM 발송 완료. 승인 대기 중.")


__all__ = [
    "propose_agent",
    "ProposeResult",
    "approval",
    "installer",
    "registry",
    "template",
    "validator",
]
