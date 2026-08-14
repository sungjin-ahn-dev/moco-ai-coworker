"""
자동화 워크플로우 엔진
트리거 평가 및 액션 실행
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.models import (
    Automation, AutomationExecution, AutomationStatus,
    TriggerType, Contact, Deal, Pipeline, CRMTask, Activity,
    ActivityType, TaskStatus, TaskPriority,
    now_kst,
)

logger = logging.getLogger(__name__)


async def evaluate_triggers(
    event_type: str,
    event_data: Dict[str, Any],
    db: AsyncSession,
) -> List[int]:
    """
    이벤트에 매칭되는 활성 자동화를 찾아 실행한다.

    Args:
        event_type: 이벤트 유형 (deal_stage_change, contact_created 등)
        event_data: 이벤트 컨텍스트 데이터
        db: 비동기 DB 세션

    Returns:
        실행된 자동화 ID 리스트
    """
    result = await db.execute(
        select(Automation)
        .where(Automation.status == AutomationStatus.active)
        .where(Automation.trigger_type == event_type)
    )
    automations = result.scalars().all()

    executed_ids = []
    for automation in automations:
        if _matches_trigger(automation, event_data):
            try:
                await execute_actions(automation.id, event_data, db)
                executed_ids.append(automation.id)
            except Exception as e:
                logger.error(
                    "[CRM Automation] 실행 실패 automation_id=%d: %s",
                    automation.id, e,
                )

    return executed_ids


def _matches_trigger(automation: Automation, event_data: Dict[str, Any]) -> bool:
    """트리거 설정과 이벤트 데이터 매칭 여부 확인"""
    config = automation.trigger_config or {}

    if automation.trigger_type == TriggerType.deal_stage_change:
        target_stage = config.get("stage")
        if target_stage and event_data.get("new_stage") != target_stage:
            return False

    elif automation.trigger_type == TriggerType.lead_score_threshold:
        threshold = config.get("threshold", 0)
        if event_data.get("lead_score", 0) < threshold:
            return False

    elif automation.trigger_type == TriggerType.form_submission:
        target_form = config.get("form_id")
        if target_form and event_data.get("form_id") != target_form:
            return False

    # contact_created, email_opened, manual 은 항상 매칭
    return True


async def execute_actions(
    automation_id: int,
    context: Dict[str, Any],
    db: AsyncSession,
) -> List[Dict[str, Any]]:
    """
    자동화의 액션들을 순차 실행한다.

    지원 액션:
    - send_email: 이메일 발송 (로그 기록)
    - create_task: CRM 태스크 생성
    - update_property: 연락처/거래 속성 업데이트
    - notify_slack: 슬랙 알림 (로그 기록)
    - enroll_sequence: 이메일 시퀀스 등록
    - change_stage: 거래 단계 변경

    Args:
        automation_id: 자동화 ID
        context: 실행 컨텍스트
        db: 비동기 DB 세션

    Returns:
        각 액션의 실행 결과 리스트
    """
    automation = await db.get(Automation, automation_id)
    if not automation:
        raise ValueError(f"자동화를 찾을 수 없습니다: {automation_id}")

    results = []
    actions = automation.actions or []

    for action in actions:
        action_type = action.get("type", "")
        action_config = action.get("config", {})
        try:
            result = await _execute_single_action(action_type, action_config, context, db)
            results.append({"type": action_type, "success": True, "result": result})
        except Exception as e:
            results.append({"type": action_type, "success": False, "error": str(e)})
            logger.error("[CRM Automation] 액션 실패 %s: %s", action_type, e)

    # 실행 이력 기록
    execution = AutomationExecution(
        automation_id=automation_id,
        trigger_data=context,
        results=results,
        success=all(r.get("success") for r in results),
    )
    db.add(execution)

    automation.execution_count = (automation.execution_count or 0) + 1
    automation.last_executed_at = now_kst()
    await db.flush()

    logger.info(
        "[CRM Automation] 실행 완료 automation_id=%d, actions=%d",
        automation_id, len(results),
    )
    return results


async def _execute_single_action(
    action_type: str,
    config: Dict[str, Any],
    context: Dict[str, Any],
    db: AsyncSession,
) -> Optional[str]:
    """단일 액션 실행"""

    if action_type == "create_task":
        task = CRMTask(
            title=config.get("title", "자동 생성 태스크"),
            description=config.get("description", ""),
            priority=TaskPriority(config.get("priority", "medium")),
            status=TaskStatus.todo,
            contact_id=context.get("contact_id"),
            deal_id=context.get("deal_id"),
            assigned_to_slack_id=config.get("assigned_to"),
        )
        if config.get("due_days"):
            from datetime import timedelta
            task.due_date = now_kst() + timedelta(days=config["due_days"])
        db.add(task)
        await db.flush()
        return f"태스크 생성: {task.id}"

    elif action_type == "update_property":
        entity = config.get("entity", "contact")
        prop = config.get("property", "")
        value = config.get("value")
        entity_id = context.get(f"{entity}_id")
        if entity == "contact" and entity_id:
            contact = await db.get(Contact, entity_id)
            if contact and hasattr(contact, prop):
                setattr(contact, prop, value)
                await db.flush()
                return f"업데이트: {entity}.{prop} = {value}"
        elif entity == "deal" and entity_id:
            deal = await db.get(Deal, entity_id)
            if deal and hasattr(deal, prop):
                setattr(deal, prop, value)
                await db.flush()
                return f"업데이트: {entity}.{prop} = {value}"

    elif action_type == "change_stage":
        deal_id = context.get("deal_id")
        new_stage = config.get("stage")
        if deal_id and new_stage:
            deal = await db.get(Deal, deal_id)
            if deal:
                # stage ID→name 변환
                pipeline = await db.get(Pipeline, deal.pipeline_id)
                if pipeline and pipeline.stages:
                    for stage_info in pipeline.stages:
                        if stage_info.get("id") == new_stage:
                            new_stage = stage_info.get("name", new_stage)
                            break
                deal.stage = new_stage
                await db.flush()
                return f"거래 단계 변경: {new_stage}"

    elif action_type == "send_email":
        # 실제 이메일 발송은 외부 연동 필요. 활동 기록만 남김
        contact_id = context.get("contact_id")
        activity = Activity(
            type=ActivityType.email,
            subject=config.get("subject", "자동 이메일"),
            body=config.get("body", ""),
            contact_id=contact_id,
            extra_data={"automation": True, "template": config.get("template")},
        )
        db.add(activity)
        await db.flush()
        return f"이메일 활동 기록: {activity.id}"

    elif action_type == "notify_slack":
        # 슬랙 알림 - 로깅으로 대체 (실제 연동은 추후 구현)
        message = config.get("message", "CRM 자동화 알림")
        channel = config.get("channel", "general")
        logger.info("[CRM Automation] 슬랙 알림: channel=%s, message=%s", channel, message)
        return f"슬랙 알림 전송 예약: {channel}"

    elif action_type == "enroll_sequence":
        from app.cc_web_interface.crm.services.sequences import enroll_contact
        seq_id = config.get("sequence_id")
        contact_id = context.get("contact_id")
        if seq_id and contact_id:
            enrollment = await enroll_contact(seq_id, contact_id, db)
            return f"시퀀스 등록: enrollment_id={enrollment.id}"

    elif action_type == "update_lead_score":
        contact_id = context.get("contact_id")
        adjustment = config.get("adjustment", 0)
        if contact_id:
            contact = await db.get(Contact, contact_id)
            if contact:
                new_score = max(0, (contact.lead_score or 0) + adjustment)
                contact.lead_score = new_score
                await db.flush()
                return f"리드 점수 변경: {adjustment:+d} → {new_score}"

    elif action_type == "add_tag":
        contact_id = context.get("contact_id")
        tag = config.get("tag", "")
        if contact_id and tag:
            contact = await db.get(Contact, contact_id)
            if contact:
                tags = list(contact.tags or [])
                if tag not in tags:
                    tags.append(tag)
                    contact.tags = tags
                    await db.flush()
                return f"태그 추가: {tag}"

    return None
