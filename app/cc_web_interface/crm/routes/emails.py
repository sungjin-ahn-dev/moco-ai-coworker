"""
이메일 시퀀스 API 라우트
시퀀스 CRUD, 등록, 일시정지, 통계, 대시보드, 벌크 등록
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from sqlalchemy.orm import selectinload

from app.cc_web_interface.crm.models import (
    EmailSequence, EmailEnrollment, EnrollmentStatus, Contact, Segment,
)
from app.cc_web_interface.crm.schemas import (
    EmailSequenceCreate, EmailSequenceUpdate, EmailSequenceRead,
    EmailEnrollmentRead, EnrollmentWithContactRead, EnrollRequest, SequenceStats,
    SequenceDashboardItem, BulkEnrollRequest,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)
from app.cc_web_interface.crm.services.sequences import (
    enroll_contact, pause_enrollment,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/emails/sequences", tags=["이메일 시퀀스"])


@router.get("", response_model=SuccessResponse)
async def list_sequences(
    status: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """시퀀스 목록 조회"""
    query = select(EmailSequence)
    if status:
        query = query.where(EmailSequence.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(EmailSequence.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    sequences = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[EmailSequenceRead.model_validate(s) for s in sequences],
    ))


# ──────────────────────────────────────────────
# 시퀀스 대시보드 (전체 시퀀스 일괄 현황)
# ── /{sequence_id} 보다 먼저 선언해야 라우트 충돌 방지
# ──────────────────────────────────────────────
@router.get("/dashboard/overview", response_model=SuccessResponse)
async def sequence_dashboard(
    status: str = Query(None, description="시퀀스 상태 필터 (active, paused, archived)"),
    db: AsyncSession = Depends(get_db),
):
    """모든 시퀀스의 등록 현황을 한 번에 조회합니다."""
    # 시퀀스 목록 조회
    seq_query = select(EmailSequence)
    if status:
        seq_query = seq_query.where(EmailSequence.status == status)
    seq_query = seq_query.order_by(EmailSequence.created_at.desc())
    result = await db.execute(seq_query)
    sequences = result.scalars().all()

    # 전체 시퀀스의 enrollment 상태별 카운트를 한 번의 쿼리로 가져오기
    stats_query = (
        select(
            EmailEnrollment.sequence_id,
            EmailEnrollment.status,
            func.count(EmailEnrollment.id),
        )
        .group_by(EmailEnrollment.sequence_id, EmailEnrollment.status)
    )
    stats_result = await db.execute(stats_query)
    # {sequence_id: {status: count}}
    stats_map = {}
    for seq_id, enroll_status, count in stats_result:
        if seq_id not in stats_map:
            stats_map[seq_id] = {}
        stats_map[seq_id][enroll_status] = count

    items = []
    total_all = 0
    for seq in sequences:
        sc = stats_map.get(seq.id, {})
        total = sum(sc.values())
        total_all += total
        items.append(SequenceDashboardItem(
            id=seq.id,
            name=seq.name,
            description=seq.description,
            status=seq.status if isinstance(seq.status, str) else seq.status.value,
            step_count=len(seq.steps) if seq.steps else 0,
            total_enrolled=total,
            active=sc.get(EnrollmentStatus.active, 0),
            completed=sc.get(EnrollmentStatus.completed, 0),
            paused=sc.get(EnrollmentStatus.paused, 0),
            bounced=sc.get(EnrollmentStatus.bounced, 0),
            created_at=seq.created_at,
        ))

    return SuccessResponse(data={
        "total_sequences": len(items),
        "total_enrolled_all": total_all,
        "sequences": [item.model_dump() for item in items],
    })


@router.get("/{sequence_id}", response_model=SuccessResponse)
async def get_sequence(sequence_id: int, db: AsyncSession = Depends(get_db)):
    """시퀀스 상세 조회 (등록된 연락처 포함)"""
    result = await db.execute(
        select(EmailSequence)
        .options(selectinload(EmailSequence.enrollments).selectinload(EmailEnrollment.contact))
        .where(EmailSequence.id == sequence_id)
    )
    seq = result.scalar_one_or_none()
    if not seq:
        return ErrorResponse(message="시퀀스를 찾을 수 없습니다.")

    seq_data = EmailSequenceRead.model_validate(seq).model_dump()
    # 등록된 연락처 정보 포함
    enrollments = []
    for e in seq.enrollments:
        er = EnrollmentWithContactRead.model_validate(e)
        if e.contact:
            er.contact_name = f"{e.contact.first_name} {e.contact.last_name or ''}".strip()
            er.contact_email = e.contact.email
        enrollments.append(er)
    seq_data["enrollments"] = [e.model_dump() for e in enrollments]

    return SuccessResponse(data=seq_data)


@router.post("", response_model=SuccessResponse)
async def create_sequence(
    data: EmailSequenceCreate, db: AsyncSession = Depends(get_db),
):
    """새 시퀀스 생성"""
    seq = EmailSequence(
        name=data.name,
        description=data.description,
        steps=[s.model_dump() for s in data.steps],
        status=data.status,
    )
    db.add(seq)
    await db.flush()
    return SuccessResponse(data=EmailSequenceRead.model_validate(seq))


@router.put("/{sequence_id}", response_model=SuccessResponse)
async def update_sequence(
    sequence_id: int,
    data: EmailSequenceUpdate,
    db: AsyncSession = Depends(get_db),
):
    """시퀀스 수정"""
    seq = await db.get(EmailSequence, sequence_id)
    if not seq:
        return ErrorResponse(message="시퀀스를 찾을 수 없습니다.")

    update_data = data.model_dump(exclude_unset=True)
    if "steps" in update_data and update_data["steps"] is not None:
        update_data["steps"] = [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in update_data["steps"]
        ]
    for key, value in update_data.items():
        setattr(seq, key, value)
    await db.flush()
    return SuccessResponse(data=EmailSequenceRead.model_validate(seq))


@router.delete("/{sequence_id}", response_model=SuccessResponse)
async def delete_sequence(sequence_id: int, db: AsyncSession = Depends(get_db)):
    """시퀀스 삭제"""
    seq = await db.get(EmailSequence, sequence_id)
    if not seq:
        return ErrorResponse(message="시퀀스를 찾을 수 없습니다.")
    await db.delete(seq)
    await db.flush()
    return SuccessResponse(data={"deleted": sequence_id})


@router.post("/{sequence_id}/enroll", response_model=SuccessResponse)
async def enroll_contact_route(
    sequence_id: int,
    data: EnrollRequest,
    db: AsyncSession = Depends(get_db),
):
    """연락처를 시퀀스에 등록"""
    try:
        enrollment = await enroll_contact(sequence_id, data.contact_id, db)
        return SuccessResponse(data=EmailEnrollmentRead.model_validate(enrollment))
    except ValueError as e:
        return ErrorResponse(message=str(e))


# ──────────────────────────────────────────────
# 벌크 등록 (여러 연락처 또는 세그먼트 기반)
# ──────────────────────────────────────────────
@router.post("/{sequence_id}/enroll-bulk", response_model=SuccessResponse)
async def bulk_enroll_contacts(
    sequence_id: int,
    data: BulkEnrollRequest,
    db: AsyncSession = Depends(get_db),
):
    """여러 연락처를 시퀀스에 일괄 등록합니다. contact_ids 직접 지정 또는 segment_id 기반."""
    # contact_ids 또는 segment_id 중 하나 필수
    if not data.contact_ids and not data.segment_id:
        return ErrorResponse(message="contact_ids 또는 segment_id 중 하나를 지정해주세요.")

    contact_ids = data.contact_ids or []

    # segment_id가 있으면 세그먼트에 해당하는 연락처 ID 조회
    if data.segment_id:
        segment = await db.get(Segment, data.segment_id)
        if not segment:
            return ErrorResponse(message="세그먼트를 찾을 수 없습니다.")

        from app.cc_web_interface.crm.routes.segments import _build_segment_query
        seg_query = _build_segment_query(segment.filters)
        seg_result = await db.execute(seg_query)
        segment_contacts = seg_result.scalars().all()
        segment_ids = [c.id for c in segment_contacts]
        # contact_ids와 합치기 (중복 제거)
        contact_ids = list(set(contact_ids + segment_ids))

    if not contact_ids:
        return ErrorResponse(message="등록할 연락처가 없습니다.")

    succeeded = []
    failed = []
    for cid in contact_ids:
        try:
            enrollment = await enroll_contact(sequence_id, cid, db)
            succeeded.append({"contact_id": cid, "enrollment_id": enrollment.id})
        except ValueError as e:
            failed.append({"contact_id": cid, "reason": str(e)})

    return SuccessResponse(data={
        "total_requested": len(contact_ids),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "enrolled": succeeded,
        "errors": failed,
    })


@router.post("/{sequence_id}/pause", response_model=SuccessResponse)
async def pause_enrollment_route(
    sequence_id: int,
    enrollment_id: int = Query(..., description="등록 ID"),
    db: AsyncSession = Depends(get_db),
):
    """시퀀스 등록 일시정지"""
    try:
        enrollment = await pause_enrollment(enrollment_id, db)
        return SuccessResponse(data=EmailEnrollmentRead.model_validate(enrollment))
    except ValueError as e:
        return ErrorResponse(message=str(e))


@router.get("/{sequence_id}/enrollments", response_model=SuccessResponse)
async def list_enrollments(
    sequence_id: int,
    status: str = Query(None, description="등록 상태 필터 (active, completed, paused, bounced)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """시퀀스에 등록된 연락처 목록 조회 (이름, 이메일 포함)"""
    seq = await db.get(EmailSequence, sequence_id)
    if not seq:
        return ErrorResponse(message="시퀀스를 찾을 수 없습니다.")

    query = (
        select(EmailEnrollment)
        .options(selectinload(EmailEnrollment.contact))
        .where(EmailEnrollment.sequence_id == sequence_id)
    )
    if status:
        query = query.where(EmailEnrollment.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(EmailEnrollment.started_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    enrollments_raw = result.scalars().all()

    items = []
    for e in enrollments_raw:
        er = EnrollmentWithContactRead.model_validate(e)
        if e.contact:
            er.contact_name = f"{e.contact.first_name} {e.contact.last_name or ''}".strip()
            er.contact_email = e.contact.email
        items.append(er)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[i.model_dump() for i in items],
    ))


@router.get("/{sequence_id}/stats", response_model=SuccessResponse)
async def sequence_stats(sequence_id: int, db: AsyncSession = Depends(get_db)):
    """시퀀스 통계 (등록 현황)"""
    seq = await db.get(EmailSequence, sequence_id)
    if not seq:
        return ErrorResponse(message="시퀀스를 찾을 수 없습니다.")

    # 상태별 카운트
    result = await db.execute(
        select(EmailEnrollment.status, func.count(EmailEnrollment.id))
        .where(EmailEnrollment.sequence_id == sequence_id)
        .group_by(EmailEnrollment.status)
    )
    status_counts = {status: count for status, count in result}

    total = sum(status_counts.values())
    stats = SequenceStats(
        total_enrolled=total,
        active=status_counts.get(EnrollmentStatus.active, 0),
        completed=status_counts.get(EnrollmentStatus.completed, 0),
        paused=status_counts.get(EnrollmentStatus.paused, 0),
        bounced=status_counts.get(EnrollmentStatus.bounced, 0),
    )
    return SuccessResponse(data=stats)
