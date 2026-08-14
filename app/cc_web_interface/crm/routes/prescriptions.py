"""
처방 관리 API 라우트
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Prescription, PatientCompliance, Company, Contact

# 제품A 처방관리 원본 데이터만 사용 (prescription_code가 있는 레코드)
# Lucas/재처방리스트 중복 데이터 제외
_RX_VALID = Prescription.prescription_code.isnot(None)
from app.cc_web_interface.crm.schemas import (
    PrescriptionCreate, PrescriptionUpdate, PrescriptionRead,
    PrescriptionStats, ComplianceRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/prescriptions", tags=["처방관리"])


@router.get("", response_model=SuccessResponse)
async def list_prescriptions(
    search: Optional[str] = Query(None),
    hospital_id: Optional[int] = Query(None),
    doctor_id: Optional[int] = Query(None),
    prescription_type: Optional[str] = Query(None),
    hospital_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    sort_by: str = Query("prescribed_date", description="정렬 필드"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """처방 목록 조회"""
    query = select(Prescription).where(_RX_VALID)

    if hospital_id:
        query = query.where(Prescription.hospital_id == hospital_id)
    if doctor_id:
        query = query.where(Prescription.doctor_id == doctor_id)
    if prescription_type:
        query = query.where(Prescription.prescription_type == prescription_type)
    if hospital_type:
        # tertiary = 상급종합/종합병원/병원, clinic = 의원
        if hospital_type == "tertiary":
            query = query.join(Company, Prescription.hospital_id == Company.id).where(
                Company.hospital_type.in_(["상급종합", "종합병원", "병원"])
            )
        elif hospital_type == "clinic":
            query = query.join(Company, Prescription.hospital_id == Company.id).where(
                Company.hospital_type == "의원"
            )
    if status:
        query = query.where(Prescription.status == status)
    if year:
        query = query.where(extract("year", Prescription.prescribed_date) == year)
    if month:
        query = query.where(extract("month", Prescription.prescribed_date) == month)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                Prescription.prescription_code.ilike(pattern),
                Prescription.patient_id.ilike(pattern),
            )
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    sort_col = getattr(Prescription, sort_by, Prescription.prescribed_date)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    prescriptions = result.scalars().all()

    items = []
    for rx in prescriptions:
        item = PrescriptionRead.model_validate(rx)
        # 병원명/의사명 조회
        if rx.hospital_id:
            h = await db.get(Company, rx.hospital_id)
            if h:
                item.hospital_name = h.name
        if rx.doctor_id:
            d = await db.get(Contact, rx.doctor_id)
            if d:
                item.doctor_name = d.first_name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.get("/stats", response_model=SuccessResponse)
async def get_prescription_stats(
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """처방 통계 (제품A 처방관리 원본 기준)"""
    base = select(Prescription).where(_RX_VALID)
    if year:
        base = base.where(extract("year", Prescription.prescribed_date) == year)

    # 총 건수
    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    # NP/NR 건수
    np_count = (await db.execute(
        select(func.count(Prescription.id)).where(_RX_VALID, Prescription.prescription_type == "NP")
    )).scalar() or 0
    nr_count = (await db.execute(
        select(func.count(Prescription.id)).where(_RX_VALID, Prescription.prescription_type == "NR")
    )).scalar() or 0

    # 유니크 병원/의사/환자
    unique_hospitals = (await db.execute(
        select(func.count(func.distinct(Prescription.hospital_id))).where(_RX_VALID)
    )).scalar() or 0
    unique_doctors = (await db.execute(
        select(func.count(func.distinct(Prescription.doctor_id))).where(_RX_VALID)
    )).scalar() or 0
    unique_patients = (await db.execute(
        select(func.count(func.distinct(Prescription.patient_id))).where(_RX_VALID)
    )).scalar() or 0

    # 월별 추이
    monthly_q = await db.execute(
        select(
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            func.count(Prescription.id).label("cnt"),
        )
        .where(_RX_VALID, Prescription.prescribed_date.isnot(None))
        .group_by("y", "m")
        .order_by("y", "m")
    )
    monthly_trend = [
        {"year": int(r.y), "month": int(r.m), "count": r.cnt}
        for r in monthly_q.all() if r.y
    ]

    # 병원별 Top 10
    hospital_q = await db.execute(
        select(
            Company.name,
            func.count(Prescription.id).label("cnt"),
        )
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .group_by(Company.name)
        .order_by(func.count(Prescription.id).desc())
        .limit(10)
    )
    top_hospitals = [{"name": r[0], "count": r[1]} for r in hospital_q.all()]

    # 의사별 Top 10
    doctor_q = await db.execute(
        select(
            Contact.first_name,
            func.count(Prescription.id).label("cnt"),
        )
        .join(Contact, Prescription.doctor_id == Contact.id)
        .where(_RX_VALID)
        .group_by(Contact.first_name)
        .order_by(func.count(Prescription.id).desc())
        .limit(10)
    )
    top_doctors = [{"name": r[0], "count": r[1]} for r in doctor_q.all()]

    stats = PrescriptionStats(
        total_prescriptions=total,
        np_count=np_count,
        nr_count=nr_count,
        unique_hospitals=unique_hospitals,
        unique_doctors=unique_doctors,
        unique_patients=unique_patients,
        monthly_trend=monthly_trend,
        top_hospitals=top_hospitals,
        top_doctors=top_doctors,
    )

    return SuccessResponse(data=stats)


@router.post("", response_model=SuccessResponse)
async def create_prescription(data: PrescriptionCreate, db: AsyncSession = Depends(get_db)):
    """처방 생성"""
    rx = Prescription(**data.model_dump())
    db.add(rx)
    await db.flush()
    return SuccessResponse(data=PrescriptionRead.model_validate(rx))


@router.get("/compliance", response_model=SuccessResponse)
async def get_compliance_report(
    hospital_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """순응도 리포트"""
    query = select(PatientCompliance)
    if hospital_id:
        query = query.where(PatientCompliance.hospital_id == hospital_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(PatientCompliance.compliance_rate.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = [ComplianceRead.model_validate(c) for c in result.scalars().all()]

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.get("/{prescription_id}", response_model=SuccessResponse)
async def get_prescription(prescription_id: int, db: AsyncSession = Depends(get_db)):
    """처방 상세 조회"""
    rx = await db.get(Prescription, prescription_id)
    if not rx:
        return ErrorResponse(message="처방을 찾을 수 없습니다.")

    item = PrescriptionRead.model_validate(rx)
    if rx.hospital_id:
        h = await db.get(Company, rx.hospital_id)
        if h:
            item.hospital_name = h.name
    if rx.doctor_id:
        d = await db.get(Contact, rx.doctor_id)
        if d:
            item.doctor_name = d.first_name

    return SuccessResponse(data=item)


@router.put("/{prescription_id}", response_model=SuccessResponse)
async def update_prescription(
    prescription_id: int,
    data: PrescriptionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """처방 수정"""
    rx = await db.get(Prescription, prescription_id)
    if not rx:
        return ErrorResponse(message="처방을 찾을 수 없습니다.")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(rx, key, value)
    await db.flush()
    return SuccessResponse(data=PrescriptionRead.model_validate(rx))


@router.delete("/{prescription_id}", response_model=SuccessResponse)
async def delete_prescription(prescription_id: int, db: AsyncSession = Depends(get_db)):
    """처방 삭제"""
    rx = await db.get(Prescription, prescription_id)
    if not rx:
        return ErrorResponse(message="처방을 찾을 수 없습니다.")
    await db.delete(rx)
    await db.flush()
    return SuccessResponse(data={"deleted": prescription_id})
