"""
연락처 API 라우트
연락처 CRUD, 타임라인, 리드 스코어링
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Contact, Activity, EmailEnrollment, EmailSequence, LeadStatus, LifecycleStage, Prescription, Company
from app.cc_web_interface.crm.schemas import (
    ContactCreate, ContactUpdate, ContactRead, ContactDetail, ContactEnrollmentRead,
    ActivityRead, PrescriptionRead, PaginatedResponse, SuccessResponse, ErrorResponse,
)
from app.cc_web_interface.crm.services.scoring import calculate_lead_score
from app.cc_web_interface.crm.services.automation import evaluate_triggers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contacts", tags=["연락처"])

# Korean → English mapping for lead_status and lifecycle_stage
_LEAD_STATUS_KO = {
    "신규": "new", "연락중": "contacted", "적격": "qualified",
    "부적격": "unqualified", "전환됨": "customer",
}
_LIFECYCLE_KO = {
    "구독자": "subscriber", "리드": "lead", "MQL": "mql", "SQL": "sql",
    "기회": "opportunity", "고객": "customer", "에반젤리스트": "evangelist",
}


def _normalize_enums(data: dict) -> dict:
    """Convert Korean enum values to English if needed."""
    if "lead_status" in data and data["lead_status"]:
        data["lead_status"] = _LEAD_STATUS_KO.get(data["lead_status"], data["lead_status"])
    if "lifecycle_stage" in data and data["lifecycle_stage"]:
        data["lifecycle_stage"] = _LIFECYCLE_KO.get(data["lifecycle_stage"], data["lifecycle_stage"])
    return data


@router.get("", response_model=SuccessResponse)
async def list_contacts(
    search: Optional[str] = Query(None, description="이름/이메일 검색"),
    lead_status: Optional[str] = Query(None, description="리드 상태 필터"),
    lifecycle_stage: Optional[str] = Query(None, description="라이프사이클 단계 필터"),
    company_id: Optional[int] = Query(None, description="회사 ID 필터"),
    owner: Optional[str] = Query(None, description="담당자 Slack ID 필터"),
    tag: Optional[str] = Query(None, description="태그 필터"),
    department: Optional[str] = Query(None, description="진료과 필터"),
    sort_by: str = Query("created_at", description="정렬 필드"),
    sort_order: str = Query("desc", description="정렬 방향 (asc/desc)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """연락처 목록 조회 (필터, 검색, 페이지네이션)"""
    # Normalize Korean filter values
    if lead_status:
        lead_status = _LEAD_STATUS_KO.get(lead_status, lead_status)
    if lifecycle_stage:
        lifecycle_stage = _LIFECYCLE_KO.get(lifecycle_stage, lifecycle_stage)

    query = select(Contact)

    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                Contact.first_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.email.ilike(pattern),
            )
        )
    if lead_status:
        query = query.where(Contact.lead_status == lead_status)
    if lifecycle_stage:
        query = query.where(Contact.lifecycle_stage == lifecycle_stage)
    if company_id:
        query = query.where(Contact.company_id == company_id)
    if owner:
        query = query.where(Contact.owner_slack_id == owner)
    if department:
        query = query.where(Contact.department == department)
    if tag:
        # SQLite JSON array 검색 — json_each 사용
        from sqlalchemy import text
        tag_subquery = text(
            "SELECT id FROM contacts WHERE EXISTS(SELECT 1 FROM json_each(contacts.tags) WHERE json_each.value = :tag_val)"
        ).bindparams(tag_val=tag).columns(id=Contact.id.type)
        query = query.where(Contact.id.in_(select(tag_subquery.c.id)))

    # 전체 개수
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 정렬
    sort_col = getattr(Contact, sort_by, Contact.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    # 페이지네이션
    query = query.offset((page - 1) * page_size).limit(page_size)
    query = query.options(selectinload(Contact.company))
    result = await db.execute(query)
    contacts = result.scalars().all()

    items = []
    for c in contacts:
        cr = ContactRead.model_validate(c)
        if c.company:
            cr.company_name = c.company.name
        items.append(cr)

    return SuccessResponse(data=PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    ))


@router.get("/{contact_id}", response_model=SuccessResponse)
async def get_contact(contact_id: int, db: AsyncSession = Depends(get_db)):
    """연락처 상세 조회 (회사, 거래, 활동, 시퀀스 등록 포함)"""
    result = await db.execute(
        select(Contact)
        .options(
            selectinload(Contact.company),
            selectinload(Contact.deals),
            selectinload(Contact.activities),
            selectinload(Contact.enrollments).selectinload(EmailEnrollment.sequence),
        )
        .where(Contact.id == contact_id)
    )
    contact = result.scalar_one_or_none()
    if not contact:
        return ErrorResponse(message="연락처를 찾을 수 없습니다.")

    # Build contact detail with enrollment sequence names
    detail = ContactDetail.model_validate(contact)
    detail.enrollments = []
    for enrollment in contact.enrollments:
        er = ContactEnrollmentRead.model_validate(enrollment)
        if enrollment.sequence:
            er.sequence_name = enrollment.sequence.name
        detail.enrollments.append(er)

    return SuccessResponse(data=detail)


@router.post("", response_model=SuccessResponse)
async def create_contact(data: ContactCreate, db: AsyncSession = Depends(get_db)):
    """새 연락처 생성"""
    # 이메일 중복 확인
    if data.email:
        existing = await db.execute(
            select(Contact).where(Contact.email == data.email)
        )
        if existing.scalar_one_or_none():
            return ErrorResponse(message="이미 등록된 이메일입니다.")

    contact = Contact(**_normalize_enums(data.model_dump()))
    db.add(contact)
    await db.flush()

    # contact_created 자동화 트리거
    await evaluate_triggers("contact_created", {"contact_id": contact.id}, db)

    return SuccessResponse(data=ContactRead.model_validate(contact))


@router.put("/{contact_id}", response_model=SuccessResponse)
async def update_contact(
    contact_id: int,
    data: ContactUpdate,
    db: AsyncSession = Depends(get_db),
):
    """연락처 수정"""
    contact = await db.get(Contact, contact_id)
    if not contact:
        return ErrorResponse(message="연락처를 찾을 수 없습니다.")

    update_data = _normalize_enums(data.model_dump(exclude_unset=True))

    # 이메일 중복 체크
    if "email" in update_data and update_data["email"]:
        existing = await db.execute(
            select(Contact).where(
                Contact.email == update_data["email"],
                Contact.id != contact_id,
            )
        )
        if existing.scalar_one_or_none():
            return ErrorResponse(message=f"이미 사용 중인 이메일입니다: {update_data['email']}")

    for key, value in update_data.items():
        setattr(contact, key, value)
    await db.flush()

    return SuccessResponse(data=ContactRead.model_validate(contact))


@router.delete("/{contact_id}", response_model=SuccessResponse)
async def delete_contact(contact_id: int, db: AsyncSession = Depends(get_db)):
    """연락처 삭제"""
    contact = await db.get(Contact, contact_id)
    if not contact:
        return ErrorResponse(message="연락처를 찾을 수 없습니다.")

    await db.delete(contact)
    await db.flush()
    return SuccessResponse(data={"deleted": contact_id})


@router.get("/{contact_id}/timeline", response_model=SuccessResponse)
async def get_timeline(
    contact_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """연락처 활동 타임라인"""
    contact = await db.get(Contact, contact_id)
    if not contact:
        return ErrorResponse(message="연락처를 찾을 수 없습니다.")

    count_result = await db.execute(
        select(func.count(Activity.id)).where(Activity.contact_id == contact_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Activity)
        .where(Activity.contact_id == contact_id)
        .order_by(Activity.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    activities = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ActivityRead.model_validate(a) for a in activities],
    ))


@router.post("/{contact_id}/score", response_model=SuccessResponse)
async def recalculate_score(contact_id: int, db: AsyncSession = Depends(get_db)):
    """리드 점수 재계산"""
    contact = await db.get(Contact, contact_id)
    if not contact:
        return ErrorResponse(message="연락처를 찾을 수 없습니다.")

    score = await calculate_lead_score(contact_id, db)

    # 점수 임계값 자동화 트리거
    await evaluate_triggers(
        "lead_score_threshold",
        {"contact_id": contact_id, "lead_score": score},
        db,
    )

    return SuccessResponse(data={"contact_id": contact_id, "lead_score": score})


@router.get("/{contact_id}/prescriptions", response_model=SuccessResponse)
async def get_contact_prescriptions(
    contact_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """의사별 처방 이력"""
    contact = await db.get(Contact, contact_id)
    if not contact:
        return ErrorResponse(message="연락처를 찾을 수 없습니다.")

    count_result = await db.execute(
        select(func.count(Prescription.id)).where(Prescription.doctor_id == contact_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Prescription)
        .where(Prescription.doctor_id == contact_id)
        .order_by(Prescription.prescribed_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    prescriptions = result.scalars().all()

    items = []
    for rx in prescriptions:
        item = PrescriptionRead.model_validate(rx)
        item.doctor_name = contact.first_name
        if rx.hospital_id:
            h = await db.get(Company, rx.hospital_id)
            if h:
                item.hospital_name = h.name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))
