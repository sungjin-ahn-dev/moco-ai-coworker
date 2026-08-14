"""
리드 스코어링 서비스
연락처의 행동 기반 점수 계산
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.models import (
    Contact, Activity, ActivityType, Deal, FormSubmission,
    now_kst,
)

logger = logging.getLogger(__name__)

# 스코어링 가중치
SCORE_WEIGHTS = {
    "email_opened": 5,
    "email_clicked": 10,
    "form_submitted": 20,
    "meeting_booked": 15,
    "deal_created": 25,
    "website_visit": 3,
    "call_made": 10,
    "note_added": 2,
    "inactive_30_days": -10,
}


async def calculate_lead_score(contact_id: int, db: AsyncSession) -> int:
    """
    연락처의 리드 점수를 계산한다.

    활동 이력, 거래, 폼 제출 등을 기반으로 점수를 산정하며,
    30일 이상 비활성 시 감점을 적용한다.

    Args:
        contact_id: 연락처 ID
        db: 비동기 DB 세션

    Returns:
        계산된 리드 점수 (최소 0)
    """
    score = 0
    now = now_kst()
    thirty_days_ago = now - timedelta(days=30)

    # 활동별 점수 계산
    activity_counts = await db.execute(
        select(Activity.type, func.count(Activity.id))
        .where(Activity.contact_id == contact_id)
        .group_by(Activity.type)
    )
    for activity_type, count in activity_counts:
        if activity_type == ActivityType.email:
            score += count * SCORE_WEIGHTS["email_opened"]
        elif activity_type == ActivityType.meeting:
            score += count * SCORE_WEIGHTS["meeting_booked"]
        elif activity_type == ActivityType.call:
            score += count * SCORE_WEIGHTS["call_made"]
        elif activity_type == ActivityType.note:
            score += count * SCORE_WEIGHTS["note_added"]

    # 거래 생성 점수
    deal_count_result = await db.execute(
        select(func.count(Deal.id)).where(Deal.contact_id == contact_id)
    )
    deal_count = deal_count_result.scalar() or 0
    score += deal_count * SCORE_WEIGHTS["deal_created"]

    # 폼 제출 점수
    form_count_result = await db.execute(
        select(func.count(FormSubmission.id)).where(FormSubmission.contact_id == contact_id)
    )
    form_count = form_count_result.scalar() or 0
    score += form_count * SCORE_WEIGHTS["form_submitted"]

    # 비활성 감점: 최근 30일간 활동 없음
    recent_activity_result = await db.execute(
        select(func.count(Activity.id))
        .where(Activity.contact_id == contact_id)
        .where(Activity.timestamp >= thirty_days_ago)
    )
    recent_count = recent_activity_result.scalar() or 0
    if recent_count == 0:
        score += SCORE_WEIGHTS["inactive_30_days"]

    # 최소 0점
    final_score = max(0, score)

    # DB 업데이트
    contact = await db.get(Contact, contact_id)
    if contact:
        contact.lead_score = final_score
        await db.flush()

    logger.info("[CRM Scoring] contact_id=%d, score=%d", contact_id, final_score)
    return final_score
