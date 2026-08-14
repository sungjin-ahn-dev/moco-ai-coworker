"""
KOL 관리 API 라우트
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import KOLPlan, Company, Contact
from app.cc_web_interface.crm.schemas import (
    KOLPlanCreate, KOLPlanUpdate, KOLPlanRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kol-plans", tags=["KOL 관리"])


@router.get("", response_model=SuccessResponse)
async def list_kol_plans(
    company_id: Optional[int] = Query(None),
    doctor_id: Optional[int] = Query(None),
    plan_type: Optional[str] = Query(None),
    engagement_status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """KOL 계획 목록"""
    query = select(KOLPlan)
    if company_id:
        query = query.where(KOLPlan.company_id == company_id)
    if doctor_id:
        query = query.where(KOLPlan.doctor_id == doctor_id)
    if plan_type:
        query = query.where(KOLPlan.plan_type == plan_type)
    if engagement_status:
        query = query.where(KOLPlan.engagement_status == engagement_status)
    if search:
        from sqlalchemy import or_
        query = query.outerjoin(Contact, KOLPlan.doctor_id == Contact.id).outerjoin(
            Company, KOLPlan.company_id == Company.id
        ).where(or_(
            Contact.first_name.ilike(f"%{search}%"),
            Company.name.ilike(f"%{search}%"),
        ))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(KOLPlan.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    plans = result.scalars().all()

    items = []
    for p in plans:
        item = KOLPlanRead.model_validate(p)
        if p.company_id:
            c = await db.get(Company, p.company_id)
            if c:
                item.company_name = c.name
        if p.doctor_id:
            d = await db.get(Contact, p.doctor_id)
            if d:
                item.doctor_name = d.first_name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.post("", response_model=SuccessResponse)
async def create_kol_plan(data: KOLPlanCreate, db: AsyncSession = Depends(get_db)):
    """KOL 계획 생성"""
    plan = KOLPlan(**data.model_dump())
    db.add(plan)
    await db.flush()
    return SuccessResponse(data=KOLPlanRead.model_validate(plan))


@router.get("/{plan_id}", response_model=SuccessResponse)
async def get_kol_plan(plan_id: int, db: AsyncSession = Depends(get_db)):
    """KOL 계획 상세"""
    plan = await db.get(KOLPlan, plan_id)
    if not plan:
        return ErrorResponse(message="KOL 계획을 찾을 수 없습니다.")
    item = KOLPlanRead.model_validate(plan)
    if plan.company_id:
        c = await db.get(Company, plan.company_id)
        if c:
            item.company_name = c.name
    if plan.doctor_id:
        d = await db.get(Contact, plan.doctor_id)
        if d:
            item.doctor_name = d.first_name
    return SuccessResponse(data=item)


@router.put("/{plan_id}", response_model=SuccessResponse)
async def update_kol_plan(plan_id: int, data: KOLPlanUpdate, db: AsyncSession = Depends(get_db)):
    """KOL 계획 수정"""
    plan = await db.get(KOLPlan, plan_id)
    if not plan:
        return ErrorResponse(message="KOL 계획을 찾을 수 없습니다.")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(plan, key, value)
    await db.flush()
    return SuccessResponse(data=KOLPlanRead.model_validate(plan))


@router.delete("/{plan_id}", response_model=SuccessResponse)
async def delete_kol_plan(plan_id: int, db: AsyncSession = Depends(get_db)):
    """KOL 계획 삭제"""
    plan = await db.get(KOLPlan, plan_id)
    if not plan:
        return ErrorResponse(message="KOL 계획을 찾을 수 없습니다.")
    await db.delete(plan)
    await db.flush()
    return SuccessResponse(data={"deleted": plan_id})
