"""
회사 API 라우트
회사 CRUD 및 연관 데이터 조회
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import (
    Company, Contact, Deal, Prescription, SalesTransaction,
    ProductListing, KOLPlan, HospitalContract,
)
from app.cc_web_interface.crm.schemas import (
    CompanyCreate, CompanyUpdate, CompanyRead, CompanyDetailRead,
    ContactRead, DealRead, PrescriptionRead, SalesTransactionRead,
    ProductListingRead, KOLPlanRead, HospitalContractRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["회사"])


@router.get("", response_model=SuccessResponse)
async def list_companies(
    search: Optional[str] = Query(None, description="이름/도메인 검색"),
    industry: Optional[str] = Query(None, description="업종 필터"),
    hospital_type: Optional[str] = Query(None, description="병원 구분 필터"),
    territory_owner: Optional[str] = Query(None, description="담당자 필터"),
    region_1: Optional[str] = Query(None, description="지역 필터"),
    is_target: Optional[bool] = Query(None, description="타겟 여부"),
    exclude_hospital_type: Optional[str] = Query(None, description="제외할 병원 구분"),
    sort_by: str = Query("created_at", description="정렬 필드"),
    sort_order: str = Query("desc", description="정렬 방향"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """회사 목록 조회"""
    query = select(Company).where(~Company.name.like("__REFERENCE_%"))

    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(Company.name.ilike(pattern), Company.domain.ilike(pattern))
        )
    if industry:
        query = query.where(Company.industry == industry)
    if hospital_type:
        query = query.where(Company.hospital_type == hospital_type)
    if territory_owner:
        query = query.where(Company.territory_owner == territory_owner)
    if region_1:
        query = query.where(Company.region_1 == region_1)
    if is_target is not None:
        query = query.where(Company.is_target == is_target)
    if exclude_hospital_type:
        query = query.where(or_(Company.hospital_type.is_(None), Company.hospital_type != exclude_hospital_type))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    sort_col = getattr(Company, sort_by, Company.created_at)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    companies = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[CompanyRead.model_validate(c) for c in companies],
    ))


@router.get("/{company_id}", response_model=SuccessResponse)
async def get_company(company_id: int, db: AsyncSession = Depends(get_db)):
    """회사 상세 조회 (연락처/딜 수, 딜 총액 포함)"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    # 연락처 수
    contact_count_result = await db.execute(
        select(func.count(Contact.id)).where(Contact.company_id == company_id)
    )
    contact_count = contact_count_result.scalar() or 0

    # 딜 수 + 총액
    deal_stats = await db.execute(
        select(func.count(Deal.id), func.coalesce(func.sum(Deal.amount), 0.0))
        .where(Deal.company_id == company_id)
    )
    deal_row = deal_stats.one()
    deal_count = deal_row[0] or 0
    total_deal_value = deal_row[1] or 0.0

    detail = CompanyDetailRead.model_validate(company)
    detail.contact_count = contact_count
    detail.deal_count = deal_count
    detail.total_deal_value = total_deal_value

    return SuccessResponse(data=detail)


@router.post("", response_model=SuccessResponse)
async def create_company(data: CompanyCreate, db: AsyncSession = Depends(get_db)):
    """새 회사 생성"""
    company = Company(**data.model_dump())
    db.add(company)
    await db.flush()
    return SuccessResponse(data=CompanyRead.model_validate(company))


@router.put("/{company_id}", response_model=SuccessResponse)
async def update_company(
    company_id: int,
    data: CompanyUpdate,
    db: AsyncSession = Depends(get_db),
):
    """회사 수정"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    await db.flush()
    return SuccessResponse(data=CompanyRead.model_validate(company))


@router.delete("/{company_id}", response_model=SuccessResponse)
async def delete_company(company_id: int, db: AsyncSession = Depends(get_db)):
    """회사 삭제 (연관 연락처, 거래도 삭제됨)"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    await db.delete(company)
    await db.flush()
    return SuccessResponse(data={"deleted": company_id})


@router.get("/{company_id}/contacts", response_model=SuccessResponse)
async def get_company_contacts(
    company_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """회사 소속 연락처 목록"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    count_result = await db.execute(
        select(func.count(Contact.id)).where(Contact.company_id == company_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Contact)
        .where(Contact.company_id == company_id)
        .order_by(Contact.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    contacts = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[ContactRead.model_validate(c) for c in contacts],
    ))


@router.get("/{company_id}/deals", response_model=SuccessResponse)
async def get_company_deals(
    company_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """회사 관련 거래 목록"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    count_result = await db.execute(
        select(func.count(Deal.id)).where(Deal.company_id == company_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Deal)
        .where(Deal.company_id == company_id)
        .order_by(Deal.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    deals = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[DealRead.model_validate(d) for d in deals],
    ))


@router.get("/{company_id}/prescriptions", response_model=SuccessResponse)
async def get_company_prescriptions(
    company_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """병원 처방 이력"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    count_result = await db.execute(
        select(func.count(Prescription.id)).where(Prescription.hospital_id == company_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(Prescription)
        .where(Prescription.hospital_id == company_id)
        .order_by(Prescription.prescribed_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    prescriptions = result.scalars().all()

    items = []
    for rx in prescriptions:
        item = PrescriptionRead.model_validate(rx)
        item.hospital_name = company.name
        if rx.doctor_id:
            d = await db.get(Contact, rx.doctor_id)
            if d:
                item.doctor_name = d.first_name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.get("/{company_id}/sales", response_model=SuccessResponse)
async def get_company_sales(
    company_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """병원 매출 이력"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    count_result = await db.execute(
        select(func.count(SalesTransaction.id)).where(SalesTransaction.company_id == company_id)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(SalesTransaction)
        .where(SalesTransaction.company_id == company_id)
        .order_by(SalesTransaction.year.desc(), SalesTransaction.month.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    sales = result.scalars().all()

    items = []
    for s in sales:
        item = SalesTransactionRead.model_validate(s)
        item.company_name = company.name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.get("/{company_id}/360", response_model=SuccessResponse)
async def get_company_360(company_id: int, db: AsyncSession = Depends(get_db)):
    """Hospital 360 - 병원 종합 데이터"""
    company = await db.get(Company, company_id)
    if not company:
        return ErrorResponse(message="회사를 찾을 수 없습니다.")

    # 기본 정보
    detail = CompanyDetailRead.model_validate(company)

    # 연락처 수
    contact_count = (await db.execute(
        select(func.count(Contact.id)).where(Contact.company_id == company_id)
    )).scalar() or 0
    detail.contact_count = contact_count

    # 딜 수/총액
    deal_stats = await db.execute(
        select(func.count(Deal.id), func.coalesce(func.sum(Deal.amount), 0.0))
        .where(Deal.company_id == company_id)
    )
    deal_row = deal_stats.one()
    detail.deal_count = deal_row[0] or 0
    detail.total_deal_value = deal_row[1] or 0.0

    # 의사(HCP) 목록
    contacts_q = await db.execute(
        select(Contact).where(Contact.company_id == company_id).limit(50)
    )
    doctors = []
    for c in contacts_q.scalars().all():
        try:
            doctors.append(ContactRead.model_validate(c))
        except Exception:
            # fallback: 기본 필드만
            doctors.append(ContactRead(
                id=c.id, first_name=c.first_name, last_name=c.last_name or "",
                email=c.email, phone=c.phone, company_id=c.company_id,
                department=getattr(c, 'department', None),
                title_position=getattr(c, 'title_position', None),
            ))

    # 처방 현황
    rx_count = (await db.execute(
        select(func.count(Prescription.id)).where(Prescription.hospital_id == company_id)
    )).scalar() or 0

    rx_recent = await db.execute(
        select(Prescription)
        .where(Prescription.hospital_id == company_id)
        .order_by(Prescription.prescribed_date.desc())
        .limit(10)
    )
    recent_prescriptions = []
    for rx in rx_recent.scalars().all():
        item = PrescriptionRead.model_validate(rx)
        if rx.doctor_id:
            d = await db.get(Contact, rx.doctor_id)
            if d:
                item.doctor_name = d.first_name
        recent_prescriptions.append(item)

    # 매출 현황
    sales_total = (await db.execute(
        select(func.coalesce(func.sum(SalesTransaction.revenue), 0.0))
        .where(SalesTransaction.company_id == company_id)
    )).scalar() or 0.0

    sales_recent = await db.execute(
        select(SalesTransaction)
        .where(SalesTransaction.company_id == company_id)
        .order_by(SalesTransaction.year.desc(), SalesTransaction.month.desc())
        .limit(10)
    )
    recent_sales = [SalesTransactionRead.model_validate(s) for s in sales_recent.scalars().all()]

    # 제품 리스팅
    listings_q = await db.execute(
        select(ProductListing).where(ProductListing.company_id == company_id)
    )
    listings = [ProductListingRead.model_validate(l) for l in listings_q.scalars().all()]

    # KOL 계획
    kol_q = await db.execute(
        select(KOLPlan).where(KOLPlan.company_id == company_id)
    )
    kol_plans = []
    for k in kol_q.scalars().all():
        item = KOLPlanRead.model_validate(k)
        if k.doctor_id:
            d = await db.get(Contact, k.doctor_id)
            if d:
                item.doctor_name = d.first_name
        kol_plans.append(item)

    # 계약
    contracts_q = await db.execute(
        select(HospitalContract).where(HospitalContract.company_id == company_id)
    )
    contracts = [HospitalContractRead.model_validate(c) for c in contracts_q.scalars().all()]

    return SuccessResponse(data={
        "company": detail,
        "doctors": doctors,
        "prescriptions": {
            "total": rx_count,
            "recent": recent_prescriptions,
        },
        "sales": {
            "total_revenue": sales_total,
            "recent": recent_sales,
        },
        "product_listings": listings,
        "kol_plans": kol_plans,
        "contracts": contracts,
    })
