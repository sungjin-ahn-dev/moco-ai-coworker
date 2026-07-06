"""
이메일 추적 API 라우트
열람 픽셀, 클릭 래핑, 추적 데이터 조회
"""

import base64
import logging
import uuid
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, RedirectResponse
from sqlalchemy import select, func, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import EmailTracking, now_kst
from app.cc_web_interface.crm.schemas import (
    EmailTrackingRead, EmailTrackingSummary,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/track", tags=["이메일 추적"])

# 1x1 투명 GIF 픽셀
TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


def generate_tracking_id() -> str:
    """추적 ID 생성"""
    return uuid.uuid4().hex


def inject_tracking(html: str, tracking_id: str, base_url: str) -> str:
    """
    HTML 이메일에 추적 픽셀과 클릭 래핑을 삽입한다.

    Args:
        html: 원본 HTML
        tracking_id: 추적 ID
        base_url: 서버 베이스 URL (예: https://192.168.1.100:8000)

    Returns:
        추적 코드가 삽입된 HTML
    """
    # 1. 열람 추적 픽셀 삽입 (</body> 앞 또는 끝에)
    pixel = f'<img src="{base_url}/api/crm/track/open/{tracking_id}" width="1" height="1" style="display:none" alt=""/>'
    if '</body>' in html.lower():
        html = html.replace('</body>', f'{pixel}</body>')
        html = html.replace('</BODY>', f'{pixel}</BODY>')
    else:
        html += pixel

    # 2. 클릭 추적 래핑 (href="..." 내 URL을 래핑)
    def wrap_link(match):
        original_url = match.group(1)
        # 추적 URL 자체는 래핑하지 않음
        if '/api/crm/track/' in original_url:
            return match.group(0)
        wrapped = f'{base_url}/api/crm/track/click/{tracking_id}?url={original_url}'
        return f'href="{wrapped}"'

    html = re.sub(r'href="(https?://[^"]+)"', wrap_link, html)

    return html


# ──────────────────────────────────────────────
# 추적 이벤트 수신 엔드포인트
# ──────────────────────────────────────────────

@router.get("/open/{tracking_id}")
async def track_open(tracking_id: str, db: AsyncSession = Depends(get_db)):
    """이메일 열람 추적 - 1px 투명 GIF 반환"""
    tracking = await db.execute(
        select(EmailTracking).where(EmailTracking.tracking_id == tracking_id)
    )
    record = tracking.scalar_one_or_none()

    if record:
        now = now_kst()
        record.open_count += 1
        if not record.first_opened_at:
            record.first_opened_at = now
        record.last_opened_at = now
        await db.commit()
        logger.info(f"[EMAIL_TRACK] Open: {tracking_id} (count: {record.open_count})")

    return Response(
        content=TRANSPARENT_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/click/{tracking_id}")
async def track_click(
    tracking_id: str,
    url: str = Query(..., description="원본 URL"),
    db: AsyncSession = Depends(get_db),
):
    """이메일 클릭 추적 - 원본 URL로 리다이렉트"""
    tracking = await db.execute(
        select(EmailTracking).where(EmailTracking.tracking_id == tracking_id)
    )
    record = tracking.scalar_one_or_none()

    if record:
        now = now_kst()
        record.click_count += 1
        if not record.first_clicked_at:
            record.first_clicked_at = now
        clicked_urls = record.clicked_urls or []
        clicked_urls.append({"url": url, "clicked_at": now.isoformat()})
        record.clicked_urls = clicked_urls
        await db.commit()
        logger.info(f"[EMAIL_TRACK] Click: {tracking_id} -> {url}")

    return RedirectResponse(url=url)


# ──────────────────────────────────────────────
# 추적 데이터 조회 엔드포인트
# ──────────────────────────────────────────────

@router.get("/contact/{contact_id}", response_model=SuccessResponse)
async def get_contact_tracking(
    contact_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """연락처의 이메일 추적 기록 조회"""
    query = select(EmailTracking).where(EmailTracking.contact_id == contact_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(EmailTracking.sent_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    trackings = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[EmailTrackingRead.model_validate(t) for t in trackings],
    ))


@router.get("/contact/{contact_id}/summary", response_model=SuccessResponse)
async def get_contact_tracking_summary(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
):
    """연락처의 이메일 추적 요약 (열람률, 클릭률, 답장률)"""
    result = await db.execute(
        select(
            func.count(EmailTracking.id),
            func.sum(func.min(EmailTracking.open_count, 1)),  # 열람한 이메일 수
            func.sum(func.min(EmailTracking.click_count, 1)),  # 클릭한 이메일 수
            func.sum(func.cast(EmailTracking.replied, Integer)),
        ).where(EmailTracking.contact_id == contact_id)
    )
    row = result.one_or_none()

    if not row or not row[0]:
        return SuccessResponse(data=EmailTrackingSummary())

    total = row[0]
    opened = row[1] or 0
    clicked = row[2] or 0
    replied = row[3] or 0

    return SuccessResponse(data=EmailTrackingSummary(
        total_sent=total,
        total_opened=opened,
        total_clicked=clicked,
        total_replied=replied,
        open_rate=round(opened / total * 100, 1) if total else 0,
        click_rate=round(clicked / total * 100, 1) if total else 0,
        reply_rate=round(replied / total * 100, 1) if total else 0,
    ))


@router.get("/sequence/{sequence_id}/stats", response_model=SuccessResponse)
async def get_sequence_tracking_stats(
    sequence_id: int,
    db: AsyncSession = Depends(get_db),
):
    """시퀀스별 이메일 추적 통계"""
    result = await db.execute(
        select(
            func.count(EmailTracking.id),
            func.sum(func.min(EmailTracking.open_count, 1)),
            func.sum(func.min(EmailTracking.click_count, 1)),
            func.sum(func.cast(EmailTracking.replied, Integer)),
        ).where(EmailTracking.sequence_id == sequence_id)
    )
    row = result.one_or_none()

    if not row or not row[0]:
        return SuccessResponse(data=EmailTrackingSummary())

    total = row[0]
    opened = row[1] or 0
    clicked = row[2] or 0
    replied = row[3] or 0

    return SuccessResponse(data=EmailTrackingSummary(
        total_sent=total,
        total_opened=opened,
        total_clicked=clicked,
        total_replied=replied,
        open_rate=round(opened / total * 100, 1) if total else 0,
        click_rate=round(clicked / total * 100, 1) if total else 0,
        reply_rate=round(replied / total * 100, 1) if total else 0,
    ))
